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
Common database instance status code used across DBaaS API.
"""
import types

from nova import log as logging
from nova.api.openstack import common
from nova import db

from reddwarf.db import api as dbapi
from reddwarf.guest import status as Guest_status
from reddwarf.exception import NotFound
from reddwarf.exception import UnprocessableEntity

LOG = logging.getLogger('reddwarf.api.status')

class InstanceStatus(object):
    """The authoritative source of a Reddwarf Instance status."""
    
    def __init__(self, 
                #vm_state=None,
                #power_state=None,
                guest_state=None,
                guest_status=None,
                server_status=None,
                ):
        #self.vm_state = vm_state
        #self.power_state = power_state
        self.guest_state = guest_state or Guest_status.SHUTDOWN.code
        self.guest_status = guest_status
        # TODO(ed-): incorporate volume status.
        self.server_status = server_status or Guest_status.SHUTDOWN.description

        assert isinstance(self.guest_state, int) or isinstance(self.guest_state, long)
        assert isinstance(self.server_status, types.StringTypes)
        assert Guest_status.GuestStatus.is_valid_code(self.guest_state)

    @staticmethod
    def load_from_db(context, instance_id):
        """Loads everything from the database to find an InstanceStatus.

        If multiple instances must be found, it's more efficient to create an
        InstanceStatusLookup which will load all guest states in one db call
        and and return an object which will return an InstanceStatus when given
        an instance_ref or cloud server dictionary from the REST api code.

        """
        local_id = dbapi.localid_from_uuid(instance_id)
        lookup = InstanceStatusLookup([local_id])
        return lookup.get_status_from_id(context, local_id)

    @property
    def is_sql_running(self):
        responsive = [
            Guest_status.RUNNING.code,
            ]
        return self.guest_state in responsive

    def can_perform_action_on_instance(self):
        """
        Checks if the instance is in a state where an action can be performed.
        """
        valid_action_states = ['ACTIVE']
        if not self.status in valid_action_states:
            msg = "Instance is not currently available for an action to be performed. Status [%s]" % self.status
            LOG.debug(msg)
            raise UnprocessableEntity(msg)

    @property
    def status(self):
        if self.server_status in ["ERROR", "REBOOT", "RESIZE"]:
            return self.server_status
        # TODO(ed-) Possibly a mapping error resulting in this function
        # returning a None. Should raise an exception instead
        if Guest_status.PAUSED == self.guest_state: # Use GuestStatus' smarter comparator.
            return "REBOOT"
        return Guest_status.GuestStatus.from_description(self.guest_state).description

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
    """
    Stores several guest states to avoid looking them up each call, and can
    quickly return InstanceStatus objects when given the compute instance
    component.
    """
    def __init__(self, guest_ids):
        self.local_ids = guest_ids
        lookup = dbapi.guest_status_get_list(self.local_ids).all()
        self.guest_status_mapping = dict([(r.instance_id, r) for r in lookup])

    def get_status_from_id(self, context, id):
        """Loads a compute instance ref to grab the instance status."""
        instance_ref = db.instance_get(context, id)
        return self.get_status_from_instance_ref(instance_ref)

    def get_status_from_instance_ref(self, instance_ref):
        """Uses a compute instance ref to grab the instance status."""
        id = instance_ref['id']
        power_state = instance_ref['power_state']
        vm_state = instance_ref['vm_state']
        status = common.status_from_state(vm_state, power_state)
        return self.get_status_from_server_details(id, status)

    def get_status_from_server(self, server):
        # We're expecting the server dictionary as returned by the servers API.
        id = server['id']
        status = server['status']
        return self.get_status_from_server_details(id, status)

    def get_status_from_server_details(self, server_id, server_status):
        """
        Given a server id and its string status (the same as returned from the
        servers API) returns an Reddwarf InstanceStatus.

        Raises an exception if the server_id was not in the list of IDs used to
        originally create this InstanceStatusLookup.
        """
        if server_id not in self.local_ids:
            raise NotFound(message="Instance %s could not be found." % server_id)
        guest_status = self.guest_status_mapping.get(server_id)
        guest_state = None
        if guest_status is not None:
                guest_state = guest_status.state
        return InstanceStatus(guest_state=guest_state,
                              guest_status=guest_status,
                              server_status=server_status)
