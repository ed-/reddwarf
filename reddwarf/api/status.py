# Copyright 2012 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
Common database instance status code used across DBaaS API
"""

from nova import log as logging
from nova.compute import power_state
from nova.exception import InstanceNotFound

from reddwarf.db import api as dbapi
from reddwarf.guest.db import models

LOG = logging.getLogger('reddwarf.api.status')
import types

dbaas_mapping = {
    None: 'BUILD',
    power_state.NOSTATE: 'BUILD',
    power_state.RUNNING: 'ACTIVE',
    power_state.SHUTDOWN: 'SHUTDOWN',
    power_state.BUILDING: 'BUILD',
    power_state.FAILED: 'FAILED',

    power_state.BLOCKED: 'BLOCKED',
    power_state.PAUSED: 'SHUTDOWN',
    power_state.SHUTOFF: 'SHUTDOWN',
    power_state.CRASHED: 'SHUTDOWN',
    power_state.SUSPENDED: 'FAILED',
}

class InstanceStatus(object):
    
    def __init__(self, 
                #vm_state=None,
                #power_state=None,
                guest_state=None,
                guest_status=None,
                server_status=None,
                ):
        #self.vm_state = vm_state
        #self.power_state = power_stated
        self.guest_state = guest_state or power_state.SHUTDOWN
        self.guest_status = guest_status
        # TODO(ed-): incorporate volume status.
        self.server_status = server_status

        assert isinstance(self.guest_state, int) or isinstance(self.guest_state, long)
        assert isinstance(self.server_status, types.StringTypes)
        assert self.guest_state in dbaas_mapping


    @staticmethod
    def load_from_db(server):
        try:
            result = dbapi.guest_status_get(instance.id).state
            return result
        except InstanceNotFound:
            pass
        try:
            local_id = dbapi.localid_from_uuid(instance.id)
            result = dbapi.guest_status_get(local_id).state
            return result
        except InstanceNotFound:
            return None

    @property
    def is_sql_running(self):
        responsive = [
            power_state.RUNNING,
            ]
        return self.guest_state in responsive

    @property
    def status(self):
        if self.server_status == 'ERROR':
            return 'ERROR'
        # TODO(ed-) Possibly a mapping error resulting in this function
        # returning a None. Should raise an exception instead
        return dbaas_mapping[self.guest_state]

    def get_guest_status(self):
        """Build out the guest status information"""
        result = {}
        if self.guest_status is not None:
            result['created_at'] = self.guest_status.created_at
            result['deleted'] = self.guest_status.deleted
            result['deleted_at'] = self.guest_status.deleted_at
            result['instance_id'] = self.guest_status.instance_id
            result['state'] = self.guest_status.state
            result['state_description'] = self.guest_status.state_description
            result['updated_at'] = self.guest_status.updated_at
        return result

class InstanceStatusLookup(object):
    def __init__(self, guest_ids):
        self.local_ids = guest_ids
        lookup = dbapi.guest_status_get_list(self.local_ids).all()
        self.guest_status_mapping = dict([(r.instance_id, r) for r in lookup])

    def get_status_from_server(self, server):
        # We're expecting the server dictionary as returned by the servers API.
        if server['id'] not in self.local_ids:
            raise InstanceNotFound(instance_id=server['id'])
        guest_status = self.guest_status_mapping.get(server['id'])
        guest_state = None
        if guest_status is not None:
                guest_state = guest_status.state
        server_status = server['status']
        return InstanceStatus(guest_state=guest_state,
                              guest_status=guest_status,
                              server_status=server_status)
        
