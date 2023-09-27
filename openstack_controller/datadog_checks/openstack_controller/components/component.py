# (C) Datadog, Inc. 2023-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)

import inspect
from enum import Enum, unique
from functools import wraps

from requests.exceptions import HTTPError

from datadog_checks.base import AgentCheck


def argument_value(arg_name, func, *args, **kwargs):
    # Get the position of target_arg in function's signature
    params = list(inspect.signature(func).parameters)
    try:
        position = params.index(arg_name) - (1 if 'self' in params else 0)
    except ValueError:
        position = None
    # If argument passed positionally
    if position is not None and position < len(args):
        return args[position]
    # If argument passed by name
    elif arg_name in kwargs:
        return kwargs[arg_name]
    return None


def generate_hash(func, *args, **kwargs):
    name = func.__name__
    args_str = ','.join(map(str, args))
    kwargs_str = ','.join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    combined = f"{name}({args_str},{kwargs_str})"
    return hash(combined)


class Component:
    registered_global_metric_methods = {}
    registered_project_metric_methods = {}

    @unique
    class Id(str, Enum):
        IDENTITY = 'identity'
        COMPUTE = 'compute'
        NETWORK = 'network'
        BLOCK_STORAGE = 'block-storage'
        BAREMETAL = 'baremetal'
        LOAD_BALANCER = 'load-balancer'

    @unique
    class Types(str, Enum):
        IDENTITY = ['identity']
        COMPUTE = ['compute']
        NETWORK = ['network']
        BLOCK_STORAGE = ['block-storage', 'volumev3']
        BAREMETAL = ['baremetal']
        LOAD_BALANCER = ['load-balancer']

    def http_error(service_check=False, error_message=None):
        def decorator_http_error(func):
            @wraps(func)  # Preserve function metadata
            def wrapper(self, *args, **kwargs):
                if service_check:
                    tags = argument_value('tags', func, *args, **kwargs)
                try:
                    result = func(self, *args, **kwargs)
                    if service_check:
                        tags = argument_value('tags', func, *args, **kwargs)
                        self.check.service_check(self.derived_class.service_check_id, AgentCheck.OK, tags=tags)
                    return result if result is not None else True
                except HTTPError as e:
                    self.check.log.error("%s: %s", error_message if error_message else "HTTPError", e.response)
                    if service_check:
                        self.check.service_check(
                            self.derived_class.service_check_id,
                            AgentCheck.CRITICAL,
                            tags=tags,
                        )
                except Exception as e:
                    self.check.log.error("%s: %s", error_message if error_message else "Exception", e)
                return None

            return wrapper

        return decorator_http_error

    @classmethod
    def register_global_metrics(cls, component_id):
        def decorator_register_metrics_method(func):
            @wraps(func)  # Preserve function metadata
            def wrapper(self, *args, **kwargs):
                self.check.log.warning(func.__name__)
                func_hash = generate_hash(func, *args, **kwargs)
                if func_hash not in self.reported_global_metrics:
                    if func(self, *args, **kwargs):
                        self.reported_global_metrics.append(func_hash)

            if component_id not in cls.registered_global_metric_methods:
                cls.registered_global_metric_methods[component_id] = []
            cls.registered_global_metric_methods[component_id].append(wrapper)
            return wrapper

        return decorator_register_metrics_method

    @classmethod
    def register_project_metrics(cls, component_id):
        def decorator_register_metrics_method(func):
            @wraps(func)  # Preserve function metadata
            def wrapper(self, *args, **kwargs):
                self.check.log.warning(func.__name__)
                func_hash = generate_hash(func, *args, **kwargs)
                if func_hash not in self.reported_project_metrics:
                    if func(self, *args, **kwargs):
                        self.reported_project_metrics.append(func_hash)

            if component_id not in cls.registered_project_metric_methods:
                cls.registered_project_metric_methods[component_id] = []
            cls.registered_project_metric_methods[component_id].append(wrapper)
            return wrapper

        return decorator_register_metrics_method

    def __init__(self, derived_class, check):
        self.derived_class = derived_class
        self.check = check
        self.found_in_catalog = False
        self.reported_global_metrics = []
        self.reported_project_metrics = []
        self.check.log.debug("created `%s` component", self.derived_class.component_id.value)

    def start_report(self):
        self.found_in_catalog = False
        self.reported_global_metrics.clear()
        self.reported_project_metrics.clear()

    def finish_report(self, tags):
        if not self.found_in_catalog:
            self.check.service_check(self.derived_class.service_check_id, AgentCheck.UNKNOWN, tags=tags)

    def report_global_metrics(self, tags):
        self.check.log.debug("reporting `%s` component global metrics", self.derived_class.component_id.value)
        self.check.log.debug("self.derived_class.component_types: %s", self.derived_class.component_types.value)
        if self.check.api.component_in_catalog(self.derived_class.component_types.value):
            self.found_in_catalog = True
            self.check.log.debug("`%s` component found in catalog", self.derived_class.component_id.value)
            if self.derived_class.component_id in Component.registered_global_metric_methods:
                for registered_method in Component.registered_global_metric_methods[self.derived_class.component_id]:
                    registered_method(self, tags)
            else:
                self.check.log.debug(
                    "`%s` component has not registered methods for global metrics",
                    self.derived_class.component_id.value,
                )
        else:
            self.check.log.debug("`%s` component not found in catalog", self.derived_class.component_id.value)

    def report_project_metrics(self, project_id, tags):
        self.check.log.debug("reporting `%s` component project metrics", self.derived_class.component_id.value)
        if self.check.api.component_in_catalog(self.derived_class.component_types.value):
            self.found_in_catalog = True
            self.check.log.debug("`%s` component found in catalog", self.derived_class.component_id.value)
            if self.derived_class.component_id in Component.registered_project_metric_methods:
                for registered_method in Component.registered_project_metric_methods[self.derived_class.component_id]:
                    registered_method(self, project_id, tags)
            else:
                self.check.log.debug(
                    "`%s` component has not registered methods for project metrics",
                    self.derived_class.component_id.value,
                )
        else:
            self.check.log.debug("`%s` component not found in catalog", self.derived_class.component_id.value)
