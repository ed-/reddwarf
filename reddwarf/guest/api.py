# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2011 OpenStack, LLC.
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
Handles all request to the Platform or Guest VM
"""


from nova import flags
from nova import log as logging
from nova import rpc
from nova.db import api as dbapi
from nova.db import base

from reddwarf import rpc as reddwarf_rpc
from reddwarf import exception

FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.guest.api')


class API(base.Base):
    """API for interacting with the guest manager."""

    def __init__(self, **kwargs):
        super(API, self).__init__(**kwargs)

    def _get_routing_key(self, context, id):
        """Create the routing key based on the container id"""
        instance_ref = dbapi.instance_get(context, id)
        return "guest.%s" % instance_ref['hostname'].split(".")[0]

    def create_user(self, context, id, users):
        """Make an asynchronous call to create a new database user"""
        LOG.debug("Creating Users for Instance %s", id)
        rpc.cast(context, self._get_routing_key(context, id),
                 {"method": "create_user",
                  "args": {"users": users}
                 })

    def list_users(self, context, id):
        """Make an asynchronous call to list database users"""
        LOG.debug("Listing Users for Instance %s", id)
        return rpc.call(context, self._get_routing_key(context, id),
                 {"method": "list_users"})

    def delete_user(self, context, id, user):
        """Make an asynchronous call to delete an existing database user"""
        LOG.debug("Deleting user %s for Instance %s",
                  user, id)
        rpc.cast(context, self._get_routing_key(context, id),
                 {"method": "delete_user",
                  "args": {"user": user}
                 })

    def create_database(self, context, id, databases):
        """Make an asynchronous call to create a new database
           within the specified container"""
        LOG.debug("Creating databases for Instance %s", id)
        rpc.cast(context, self._get_routing_key(context, id),
                 {"method": "create_database",
                  "args": {"databases": databases}
                 })

    def list_databases(self, context, id):
        """Make an asynchronous call to list database users"""
        LOG.debug("Listing Users for Instance %s", id)
        return rpc.call(context, self._get_routing_key(context, id),
                 {"method": "list_databases"})

    def delete_database(self, context, id, database):
        """Make an asynchronous call to delete an existing database
           within the specified container"""
        LOG.debug("Deleting database %s for Instance %s",
                  database, id)
        rpc.cast(context, self._get_routing_key(context, id),
                 {"method": "delete_database",
                  "args": {"database": database}
                 })

    def enable_root(self, context, id):
        """Make a synchronous call to enable the root user for
           access from anywhere"""
        LOG.debug("Enable root user for Instance %s", id)
        return rpc.call(context, self._get_routing_key(context, id),
                 {"method": "enable_root"})

    def disable_root(self, context, id):
        """Make a synchronous call to disable the root user for
           access from anywhere"""
        LOG.debug("Disable root user for Instance %s", id)
        return rpc.call(context, self._get_routing_key(context, id),
                 {"method": "disable_root"})

    def is_root_enabled(self, context, id):
        """Make a synchronous call to check if root access is
           available for the container"""
        LOG.debug("Check root access for Instance %s", id)
        return rpc.call(context, self._get_routing_key(context, id),
                 {"method": "is_root_enabled"})

    def get_diagnostics(self, context, id):
        """Make a synchronous call to get diagnostics for the container"""
        LOG.debug("Check diagnostics on Instance %s", id)
        return rpc.call(context, self._get_routing_key(context, id),
                 {"method": "get_diagnostics"})

    def prepare(self, context, id, memory_mb, databases):
        """Make an asynchronous call to prepare the guest
           as a database container"""
        LOG.debug(_("Sending the call to prepare the Guest"))
        reddwarf_rpc.cast_with_consumer(context, self._get_routing_key(context, id),
                 {"method": "prepare",
                  "args": {"databases": databases,
                           "memory_mb":memory_mb}
                 })

    def restart(self, context, id):
        """Restart the MySQL server."""
        LOG.debug(_("Sending the call to restart MySQL on the Guest."))
        rpc.call(context, self._get_routing_key(context, id),
                 {"method": "restart",
                  "args": {}
                 })

    def start_mysql_with_conf_changes(self, context, id, updated_memory_size):
        """Start the MySQL server."""
        LOG.debug(_("Sending the call to start MySQL on the Guest."))
        try:
            rpc.call(context, self._get_routing_key(context, id),
                    {"method": "start_mysql_with_conf_changes",
                     "args": {'updated_memory_size':updated_memory_size}
                    })
        except Exception as e:
            LOG.error(e)
            raise exception.GuestError(original_message=str(e))

    def stop_mysql(self, context, id):
        """Stop the MySQL server."""
        LOG.debug(_("Sending the call to stop MySQL on the Guest."))
        try:
            rpc.call(context, self._get_routing_key(context, id),
                    {"method": "stop_mysql",
                     "args": {}
                    })
        except Exception as e:
            LOG.error(e)
            raise exception.GuestError(original_message=str(e))

    def upgrade(self, context, id):
        """Make an asynchronous call to self upgrade the guest agent"""
        topic = self._get_routing_key(context, id)
        LOG.debug("Sending an upgrade call to nova-guest %s", topic)
        reddwarf_rpc.cast_with_consumer(context, topic, {"method": "upgrade"})
