# (C) Datadog, Inc. 2023-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
from datadog_checks.openstack_controller.api.api import Api


class ApiSdk(Api):
    def __init__(self, config, logger, http):
        pass

    def get_identity_response_time(self, project_id):
        pass  # pragma: no cover

    def get_identity_projects(self):
        pass  # pragma: no cover

    def get_identity_users(self):
        pass  # pragma: no cover

    def get_identity_groups(self):
        pass  # pragma: no cover

    def get_identity_group_users(self, group_id):
        pass  # pragma: no cover

    def get_identity_services(self):
        pass  # pragma: no cover

    def get_auth_projects(self):
        pass  # pragma: no cover

    def get_compute_response_time(self, project_id):
        pass  # pragma: no cover

    def get_compute_limits(self, project_id):
        pass  # pragma: no cover

    def get_compute_quota_set(self, project_id):
        pass  # pragma: no cover

    def get_compute_servers(self, project_id):
        pass  # pragma: no cover

    def get_compute_flavors(self, project_id):
        pass  # pragma: no cover

    def get_compute_hypervisors(self, project_id):
        pass  # pragma: no cover

    def get_compute_os_aggregates(self, project_id):
        pass  # pragma: no cover

    def get_network_response_time(self, project):
        pass  # pragma: no cover

    def get_network_quotas(self, project):
        pass  # pragma: no cover

    def get_baremetal_response_time(self, project):
        pass  # pragma: no cover