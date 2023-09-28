# (C) Datadog, Inc. 2023-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import contextlib
import datetime
import inspect
import time
from typing import Callable, Dict

import psycopg

from datadog_checks.base import AgentCheck


class ConnectionPoolFullError(Exception):
    def __init__(self, size, timeout):
        self.size = size
        self.timeout = timeout

    def __str__(self):
        return "Could not insert connection in pool size {} within {} seconds".format(self.size, self.timeout)


class ConnectionInfo:
    def __init__(
        self,
        connection: psycopg.Connection,
        deadline: int,
        active: bool,
        last_accessed: int,
        thread: threading.Thread,
        persistent: bool,
    ):
        self.connection = connection
        self.deadline = deadline
        self.active = active
        self.last_accessed = last_accessed
        self.thread = thread
        self.persistent = persistent


class MultiDatabaseConnectionPool(object):
    """
    Manages a connection pool across many logical databases with a maximum of 1 conn per
    database. Traditional connection pools manage a set of connections to a single database,
    however the usage patterns of the Agent application should aim to have minimal footprint
    and reuse a single connection as much as possible.

    Even when limited to a single connection per database, an instance with hundreds of
    databases still present a connection overhead risk. This class provides a mechanism
    to prune connections to a database which were not used in the time specified by their
    TTL.

    If max_conns is specified, the connection pool will limit concurrent connections.
    """

    class Stats(object):
        def __init__(self):
            self.connection_opened = 0
            self.connection_pruned = 0
            self.connection_closed = 0
            self.connection_closed_failed = 0

        def __repr__(self):
            return str(self.__dict__)

        def reset(self):
            self.__init__()

    def __init__(self, check: AgentCheck, connect_fn: Callable[[str], None], max_conns: int = None):
        self._check = check
        self._log = check.log
        self._config = check._config
        self.max_conns: int = max_conns
        self._stats = self.Stats()
        self._conns: Dict[str, ConnectionInfo] = {}

        if hasattr(inspect, 'signature'):
            connect_sig = inspect.signature(connect_fn)
            if not (len(connect_sig.parameters) >= 1):
                raise ValueError(
                    "Invalid signature for the connection function. "
                    "Expected parameters: dbname, min_pool_size, max_pool_size. "
                    "Got signature: {}".format(connect_sig)
                )
        self.connect_fn = connect_fn

    def _get_connection_raw(
        self,
        dbname: str,
        ttl_ms: int,
        timeout: int = None,
        startup_fn: Callable[[psycopg.Connection], None] = None,
        persistent: bool = False,
    ) -> psycopg.Connection:
        """
        Return a connection pool for the requested database from the managed pool.
        Pass a function to startup_func if there is an action needed with the connection
        when re-establishing it.
        """
        if not timeout:
            # We don't want the wait indefinitely for the connection pool to free up
            # if timeout is not specified, so we set it to the default connection timeout
            timeout = self._config.connection_timeout
        start = datetime.datetime.now()
        self.prune_connections()
        conn = self._conns.pop(dbname, None)
        db = conn.connection if conn else None
        if db is None or db.closed or db.broken:
            # db.info.status checks if the connection is still active on the server
            if self.max_conns is not None:
                # try to free space until we succeed
                while len(self._conns) >= self.max_conns:
                    self.prune_connections()
                    self.evict_lru()
                    if timeout is not None and (datetime.datetime.now() - start).total_seconds() > timeout:
                        raise ConnectionPoolFullError(self.max_conns, timeout)
                    time.sleep(0.01)
                    continue
            self._stats.connection_opened += 1
            db = self.connect_fn(dbname)
            if startup_fn:
                startup_fn(db)
        else:
            # if already in pool, retain persistence status
            persistent = conn.persistent

        deadline = datetime.datetime.now() + datetime.timedelta(milliseconds=ttl_ms)
        self._conns[dbname] = ConnectionInfo(
            connection=db,
            deadline=deadline,
            active=True,
            last_accessed=datetime.datetime.now(),
            persistent=persistent,
        )
        return db

    @contextlib.contextmanager
    def get_connection(self, dbname: str, ttl_ms: int, timeout: int = None, persistent: bool = False):
        """
        Grab a connection from the pool if the database is already connected.
        If max_conns is specified, and the database isn't already connected,
        make a new connection if the max_conn limit hasn't been reached.
        Blocks until a connection can be added to the pool,
        and optionally takes a timeout in seconds.
        """
        interrupted_error_occurred = False

        db = self._get_connection_raw(dbname, ttl_ms, timeout, startup_fn, persistent)
        try:
            yield db
        except InterruptedError:
            # if the thread is interrupted, we don't want to mark the connection as inactive
            # instead we want to take it out of the pool so it can be closed
            interrupted_error_occurred = True
            self._terminate_connection_unsafe(dbname)
            raise
        else:
            if not interrupted_error_occurred:
                if db.broken:
                    self._terminate_connection_unsafe(dbname)
                else:
                    try:
                        self._conns[dbname].active = False
                    except KeyError:
                        # if self._get_connection_raw hit an exception, self._conns[dbname] didn't get populated
                        pass

    def prune_connections(self):
        """
        This function should be called periodically to prune all connections which have not been
        accessed since their TTL. This means that connections which are actually active on the
        server can still be closed with this function. For instance, if a connection is opened with
        ttl 1000ms, but the query it's running takes 5000ms, this function will still try to close
        the connection mid-query.
        """
        now = datetime.datetime.now()
        for conn_name, conn in list(self._conns.items()):
            if conn.deadline < now and not conn.active and not conn.persistent:
                self._stats.connection_pruned += 1
                self._terminate_connection_unsafe(conn_name)

    def close_all_connections(self) -> bool:
        """
        Close all connections in the pool.
        """
        success = True
        for dbname in list(self._conns):
            if not self._terminate_connection_unsafe(dbname):
                success = False
        return success

    def evict_lru(self) -> str:
        """
        Evict and close the inactive connection which was least recently used.
        Return the dbname connection that was evicted or None if we couldn't evict a connection.
        """
        sorted_conns = sorted(self._conns.items(), key=lambda i: i[1].last_accessed)
        for name, conn_info in sorted_conns:
            if not conn_info.active and not conn_info.persistent:
                self._terminate_connection_unsafe(name)
                return name

        # Could not evict a candidate; return None
        return None

    def _terminate_connection_unsafe(self, dbname: str) -> bool:
        if dbname not in self._conns:
            return True

        db = self._conns.pop(dbname).connection
        try:
            db.close()
            self._stats.connection_closed += 1
        except Exception:
            self._stats.connection_closed_failed += 1
            self._log.exception("failed to close DB connection for db=%s", dbname)
            return False

        return True

    def get_main_db(self):
        """
        Returns a persistent psycopg connection to `self.dbname`.
        :return: a psycopg connection
        """
        return self.get_connection(self._config.dbname, self._config.idle_connection_timeout, persistent=True)
