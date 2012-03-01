# Copyright 2010 OpenStack LLC.
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

from webob import exc

from nova import exception as nova_exception
from nova import flags
from nova import log as logging

from nova.api.openstack import servers
from nova.api.openstack import wsgi
from nova.compute import power_state

from reddwarf import compute
from reddwarf import exception
from reddwarf import volume
from reddwarf.api import common
from reddwarf.api.status import InstanceStatusLookup
from reddwarf.api.views import instances
from reddwarf.db import api as dbapi
from reddwarf.guest import api as guest

LOG = logging.getLogger('reddwarf.api.management')
LOG.setLevel(logging.DEBUG)

FLAGS = flags.FLAGS

def create_resource(version='1.0'):
    controller = {
        '1.0': Controller,
    }[version]()

    metadata = {
            'attributes': {
                'instance': ['account_id',
                             'created',
                             'created_at',
                             'deleted',
                             'deleted_at',
                             'flavorid',
                             'host',
                             'hostname',
                             'id',
                             'name',
                             'root_enabled_at',
                             'root_enabled_by',
                             'server_state_description',
                             'status',
                             'updated'],
                'network': ['id'],
                'ip': ['addr',
                       'address',
                       'version',
                       'virtual_interface_id'],
                'flavor': ['id'],
                'guest_status': ['created_at',
                                 'deleted',
                                 'deleted_at',
                                 'instance_id',
                                 'state',
                                 'state_description',
                                 'updated_at'],
                'link': ['rel',
                         'href'],
                'database': ['name',
                             'collate',
                             'character_set'],
                'user': ['name'],
                'volume': ['id',
                           'size',
                           'description',
                           'name'],
                'root_enabled_history': ['id',
                                         'root_enabled_at',
                                         'root_enabled_by'],
            },
    }

    xmlns = {
        '1.0': common.XML_NS_V10,
    }[version]

    serializers = {
        'application/xml': wsgi.XMLDictSerializer(metadata=metadata,
                                                  xmlns=xmlns),
    }

    response_serializer = wsgi.ResponseSerializer(body_serializers=serializers)
    return wsgi.Resource(controller, serializer=response_serializer)


class Controller(object):
    """ The Instance API controller for the Management API """

    def __init__(self):
        self.compute_api = compute.API()
        self.server_controller = servers.ControllerV11()
        self.volume_api = volume.API()
        self.guest_api = guest.API()
        self.instance_view = instances.MgmtViewBuilder()
        super(Controller, self).__init__()

    @common.verify_admin_context
    def show(self, req, id):
        """ Returns instance details by instance id """
        LOG.info("Get Instance Detail by ID - %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        instance_id = dbapi.localid_from_uuid(id)
        server_response = self.server_controller.show(req, instance_id)
        if isinstance(server_response, Exception):
            return server_response  # Just return the exception to throw it
        context = req.environ['nova.context']
        server = server_response['server']

        # Use the compute api response to add additional information
        try:
            instance_ref = self.compute_api.get(context, instance_id)
        except nova_exception.InstanceNotFound:
            LOG.error("Could not find an instance with id %s" % id)
            raise exception.NotFound("No instance with id %s" % id)

        status_lookup = InstanceStatusLookup([instance_id])
        instance = self.instance_view.build_mgmt_single(server,
                                                        instance_ref,
                                                        req,
                                                        status_lookup)
        try:
            status = status_lookup.get_status_from_server(server)
            instance = self._get_guest_info(context, instance_id, status,
                                            instance)

        except Exception as err:
            msg = "Unable to retrieve information from the guest"
            LOG.error(err)
            LOG.error(msg)
            raise exception.InstanceFault(msg)

        return {'instance': instance}

    @common.verify_admin_context
    def index(self, req):
        """ Returns all local instances, optionally filtered by deleted status."""
        LOG.info("Get all Instances")
        LOG.debug("%s - %s", req.environ, req.body)
        dfilter = req.GET.get('deleted', None)
        deleted = None
        if dfilter is not None:
            if  dfilter.lower() in ['true']:
                deleted = True
            elif dfilter.lower() in ['false']:
                deleted = False

        context = req.environ['nova.context']
        instances, flavors, ips = dbapi.instances_mgmt_index(context, deleted)
        result = []
        for instance in instances:
            details = {
                'account_id': instance['project_id'],
                'id': instance['uuid'],
                'host': instance['host'],
                'status': instance['vm_state'],
                'created_at': instance['created_at'],
                'deleted_at': instance['deleted_at'],
                'deleted': instance['deleted'],
            }
            # Associate the flavor.
            # TODO(ed-): Should probably make the relational database do all the data relating.
            flavor = [f for f in flavors if f['id'] == instance['instance_type_id']]
            if len(flavor) > 0:
                flavor = flavor[0]
                details['flavorid'] = flavor['flavorid']

            # Now associate the IPs.
            # TODO(ed-): A join would be preferable.
            details['ips'] = [{
                'address': ip['address'],
                'virtual_interface_id': ip['virtual_interface_id'],
                } for ip in ips]
            result.append(details)

        return {"instances": result}

    def _get_guest_info(self, context, id, status, instance):
        """Get all the guest details and add it to the response"""
        dbs = None
        users = None
        if status.is_sql_running:
            db_list = self.guest_api.list_databases(context, id)

            LOG.debug("DBS: %r" % db_list)
            dbs = [{
                    'name': db['_name'],
                    'collate': db['_collate'],
                    'character_set': db['_character_set']
                    } for db in db_list]
            users = self.guest_api.list_users(context, id)
            users = [{'name': user['_name']} for user in users]

        root_access = dbapi.get_root_enabled_history(context, id)

        instance = self.instance_view.build_guest_info(instance, status=status,
                                                       dbs=dbs, users=users,
                                                       root_enabled=root_access)
        return instance

    @common.verify_admin_context
    def root_enabled_history(self, req, id):
        """ Checks the root_enabled_history table to see if root access
            was ever enabled for this instance, and if so, when and by who. """
        LOG.info("Call to root_enabled_history for instance %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        local_id = dbapi.localid_from_uuid(id)
        common.instance_exists(ctxt, id, self.compute_api)
        try:
            result = dbapi.get_root_enabled_history(ctxt, local_id)
            root_history = self.instance_view.build_root_history(id, result)
            return {'root_enabled_history': root_history}
        except Exception as err:
            LOG.error(err)
            raise exception.InstanceFault("Error determining root access history")

    @common.verify_admin_context
    def action(self, req, id, body):
        """Multi-purpose method used to take actions on an instance."""
        ctxt = req.environ['nova.context']
        try:
            common.instance_exists(ctxt, id, self.compute_api)
        except nova_exception.NotFound:
            # Reddwarf NotFound bears an HTTP 404 response payload.
            raise exception.NotFound()

        _actions = {
            'reboot': self._action_reboot,
            'update': self._action_update,
        }

        for key in body:
            if key in _actions:
                return _actions[key](body, req, id)
            else:
                msg = _("There is no such server action: %s") % (key,)
                raise exception.BadRequest(msg)

        msg = _("Invalid request body")
        raise exception.BadRequest(msg)

    def _action_reboot(self, body, req, id):
        LOG.info("Call to reboot for instance %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        local_id = dbapi.localid_from_uuid(id)

        try:
            self.compute_api.reboot(ctxt, local_id)
            return exc.HTTPAccepted()
        except Exception as err:
            LOG.exception(_("Error in reboot %s"), err)
            raise exception.UnprocessableEntity()

    def _action_update(self, body, req, id):
        LOG.info("Call to update guest agent for instance %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        local_id = dbapi.localid_from_uuid(id)

        try:
            self.compute_api.update_guest(ctxt, local_id)
            return exc.HTTPAccepted()
        except Exception as err:
            LOG.exception(_("Error in update guest agent: %s"), err)
            raise exception.NotFound()
