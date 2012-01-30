# vim: tabstop=4 shiftwidth=4 softtabstop=4

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

import copy
import json
from webob import exc

from nova import db
from nova import exception as nova_exception
from nova import flags
from nova import log as logging
from nova.api.openstack import common as nova_common
from nova.api.openstack import faults
from nova.api.openstack import servers
from nova.api.openstack import wsgi
from nova.compute import power_state
from nova.compute import vm_states
from nova.notifier import api as notifier

from reddwarf import compute
from reddwarf import exception
from reddwarf import volume
from reddwarf.api import common
from reddwarf.api import deserializer
from reddwarf.api.status import InstanceStatusLookup
from reddwarf.api.views import instances
from reddwarf.db import api as dbapi
from reddwarf.guest import api as guest_api


LOG = logging.getLogger('reddwarf.api.instances')
LOG.setLevel(logging.DEBUG)


FLAGS = flags.FLAGS
flags.DEFINE_string('reddwarf_mysql_data_dir', '/var/lib/mysql',
                    'Mount point within the instance for MySQL data.')
flags.DEFINE_string('reddwarf_volume_description',
                    'Volume ID: %s assigned to Instance: %s',
                    'Default description populated for volumes')
flags.DEFINE_integer('reddwarf_max_accepted_volume_size', 128,
                    'Maximum accepted volume size (in gigabytes) when creating'
                    ' an instance.')


def publisher_id(host=None):
    return notifier.publisher_id("reddwarf-api", host)


class Controller(object):
    """ The Instance API controller for the Platform API """

    def __init__(self):
        self.compute_api = compute.API()
        self.guest_api = guest_api.API()
        self.server_controller = servers.ControllerV11()
        self.volume_api = volume.API()
        self.view = instances.ViewBuilder()
        super(Controller, self).__init__()

    def action(self, req, id, body):
        """Multi-purpose method used to take actions on an instance."""
        ctxt = req.environ['nova.context']
        common.instance_exists(ctxt, id, self.compute_api)

        _actions = {
            'reboot': self._action_reboot,
            'resize': self._action_resize
            }

        for key in body:
            if key in _actions:
                return _actions[key](body, req, id)
            else:
                msg = _("There is no such server action: %s") % (key,)
                raise exception.BadRequest(msg)

        msg = _("Invalid request body")
        raise exception.BadRequest(msg)

    def _action_reboot(self, input_dict, req, id):
        LOG.info("Call to reboot for instance %s", id)
        reboot_type = self._get_reboot_type(input_dict)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        local_id = dbapi.localid_from_uuid(id)
        if reboot_type == "HARD":
            self._action_reboot_hard(ctxt, local_id)
        else:
            self._action_reboot_soft(ctxt, local_id)

    def _action_reboot_hard(self, ctxt, local_id):
        try:
            self.compute_api.reboot(ctxt, local_id)
            return exc.HTTPAccepted()
        except Exception, e:
            LOG.exception(_("Error in reboot %s"), e)
            raise exception.UnprocessableEntity()

    def _action_reboot_soft(self, ctxt, local_id):
        try:
            self.compute_api.restart(ctxt, local_id)
            return exc.HTTPAccepted()
        except Exception as err:
            LOG.error(err)
            raise exception.InstanceFault("Error restarting MySQL.")

    @staticmethod
    def _get_reboot_type(input_dict):
        """Fetches the the reboot type from a dict or raises an exception.

        Return value is either "HARD" or "SOFT".

        """
        if 'reboot' in input_dict and 'type' in input_dict['reboot']:
            valid_reboot_types = ['HARD', 'SOFT']
            reboot_type = input_dict['reboot']['type'].upper()
            if not valid_reboot_types.count(reboot_type):
                msg = _("Argument 'type' for reboot is not HARD or SOFT")
                LOG.exception(msg)
                raise exception.BadRequest(msg)
            return reboot_type
        else:
            msg = _("Missing argument 'type' for reboot")
            LOG.exception(msg)
            raise exception.BadRequest(explanation=msg)

    def _action_resize(self, body, req, id):
        LOG.info("Call to resize instance %s" % id)
        LOG.debug("%s - %s", req.environ, req.body)
        context = req.environ['nova.context']
        local_id = dbapi.localid_from_uuid(id)
        volumes = db.volume_get_all_by_instance(context, local_id)
        assert len(volumes) == 1
        volume_ref = volumes[0]

        Controller._validate_resize(body, volume_ref['size'])
        # Initiate the resizing of the volume
        new_size = body['resize']['volume']['size']
        self.volume_api.resize(context, volume_ref['id'], new_size)
        # Kickoff rescaning and resizing the filesystem
        self.compute_api.resize_volume(context, volume_ref['id'])
        # TODO(rnirmal): Need to figure out how to set the instance state
        # during volume resize, as the compute driver and guest agent will
        # rewrite those states. We may have to include the volume state at
        # some point.
        return exc.HTTPAccepted()

    def index(self, req):
        """ Returns a list of instance names and ids for a given user """
        LOG.info("Call to Instances index")
        LOG.debug("%s - %s", req.environ, req.body)
        servers_response = self.server_controller.index(req)
        server_list = servers_response['servers']
        context = req.environ['nova.context']

        # Instances need the status for each instance in all circumstances,
        # unlike servers.
        server_states = dbapi.instance_state_get_all_filtered(context)
        for server in server_list:
            state = server_states[server['id']]
            server['status'] = nova_common.status_from_state(state)

        id_list = [server['id'] for server in server_list]
        status_lookup = InstanceStatusLookup(id_list)
        instances = [self.view.build_index(server, req, status_lookup)
                        for server in server_list]
        return {'instances': instances}

    def detail(self, req):
        """ Returns a list of instance details for a given user """
        LOG.debug("%s - %s", req.environ, req.body)
        server_list = self.server_controller.detail(req)['servers']
        id_list = [server['id'] for server in server_list]
        status_lookup = InstanceStatusLookup(id_list)
        instances = [self.view.build_detail(server, req, status_lookup)
                        for server in server_list]
        return {'instances': instances}

    def show(self, req, id):
        """ Returns instance details by instance id """
        LOG.info("Get Instance by ID - %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        instance_id = dbapi.localid_from_uuid(id)
        server_response = self.server_controller.show(req, instance_id)
        if isinstance(server_response, Exception):
            return server_response  # Just return the exception to throw it
        context = req.environ['nova.context']
        server = server_response['server']

        status_lookup = InstanceStatusLookup([server['id']])
        databases = None
        root_enabled = None
        if status_lookup.get_status_from_server(server).is_sql_running:
            databases, root_enabled = self._get_guest_info(context, server['id'])
        instance = self.view.build_single(server,
                                          req,
                                          status_lookup,
                                          databases=databases,
                                          root_enabled=root_enabled)
        LOG.debug("instance - %s" % instance)
        return {'instance': instance}

    def delete(self, req, id):
        """ Destroys an instance """
        LOG.info("Delete Instance by ID - %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        context = req.environ['nova.context']
        instance_id = dbapi.localid_from_uuid(id)

        # Checking the server state to see if it is building or not
        try:
            instance = self.compute_api.get(context, instance_id)
            #TODO(tim.simpson): Try to get this fixed for real in Nova.
            if instance['vm_state'] in [vm_states.SUSPENDED, vm_states.ERROR]:
                # SUSPENDED and ERROR are not valid 'shut_down' states to the
                # Compute API. But we want our customers to be able to delete
                # things in the event of failure, in which case we set the state to
                # SUSPENDED. Additionally as of 2011-10-12 ERROR is used in two
                # places: 1, the compute manager when a resize fails, or 2. by our
                # very own UnforgivingMemoryScheduler. So as of today ERROR is
                # also a viable state for deletion.
                db.instance_update(context, id, {'vm_state': vm_states.ACTIVE,})

            compute_response = self.compute_api.get(context, instance_id)
        except nova_exception.NotFound:
            raise exception.NotFound()
        LOG.debug("server_response - %s", compute_response)
        build_states = [
             nova_common.vm_states.REBUILDING,
             nova_common.vm_states.BUILDING,
        ]
        if compute_response['vm_state'] in build_states:
            # what if guest_state is failed and vm_state is still building
            # need to be able to delete instance still
            deletable_states = [
                power_state.FAILED,
            ]
            status = dbapi.guest_status_get(instance_id).state
            if not status in deletable_states:
                LOG.debug("guest status(%s) will not allow delete" % status)
                # If the state is building then we throw an exception back
                raise exception.UnprocessableEntity("Instance %s is not ready."
                                                    % id)

        self.server_controller.delete(req, instance_id)
        #TODO(rnirmal): Use a deferred here to update status
        dbapi.guest_status_delete(instance_id)
        return exc.HTTPAccepted()

    def create(self, req, body):
        """ Creates a new Instance for a given user """
        self._validate(body)

        LOG.info("Create Instance")
        LOG.debug("%s - %s", req.environ, body)

        context = req.environ['nova.context']

        # Create the Volume before hand
        volume_ref = self.create_volume(context, body)
        # Setup Security groups
        self._setup_security_groups(context,
                                    FLAGS.default_firewall_rule_name,
                                    FLAGS.default_guest_mysql_port)

        server = self._create_server_dict(body['instance'],
                                          volume_ref['id'],
                                          FLAGS.reddwarf_mysql_data_dir)

        # Add any extra data that's required by the servers api
        server_req_body = {'server':server}
        server_resp = self._try_create_server(req, server_req_body)
        instance_id = str(server_resp['server']['uuid'])
        local_id = server_resp['server']['id']
        dbapi.guest_status_create(str(local_id))

        status_lookup = InstanceStatusLookup([local_id])
        instance = self.view.build_single(server_resp['server'], req,
                                          status_lookup, create=True)

        # add the volume information to response
        LOG.debug("adding the volume information to the response...")
        instance['volume'] = {'size': volume_ref['size']}
        return { 'instance': instance }

    def create_volume(self, context, body):
        """Creates the volume for the instance and returns its ID."""
        volume_size = body['instance']['volume']['size']
        name = body['instance'].get('name', None)
        description = FLAGS.reddwarf_volume_description % (None, None)

        return self.volume_api.create(context, size=volume_size,
                                      snapshot_id=None,
                                      name=name,
                                      description=description)

    def _try_create_server(self, req, body):
        """Handle the call to create a server through the openstack servers api.

        Separating this so we could do retries in the future and other
        processing of the result etc.
        """
        try:
            server = self.server_controller.create(req, body)
            if not server or isinstance(server, faults.Fault) \
                          or isinstance(server, exc.HTTPClientError):
                if isinstance(server, faults.Fault):
                    LOG.error("%s: %s", server.wrapped_exc,
                              server.wrapped_exc.detail)
                if isinstance(server, exc.HTTPClientError):
                    LOG.error("a 400 error occurred %s" % server)
                raise exception.InstanceFault("Could not complete the request."
                          " Please try again later or contact Customer Support")
            return server
        except (TypeError, AttributeError, KeyError) as e:
            LOG.error(e)
            raise exception.UnprocessableEntity()

    @staticmethod
    def _create_server_dict(instance, volume_id, mount_point):
        """Creates a server dict from the request instance dict."""
        server = copy.copy(instance)
        # Append additional stuff to create.
        # Add image_ref
        try:
            server['imageRef'] = dbapi.config_get("reddwarf_imageref").value
        except exception.ConfigNotFound:
            msg = "Cannot find the reddwarf_imageref config value, " \
                  "using default of 1"
            LOG.warn(msg)
            notifier.notify(publisher_id(), "reddwarf.image", notifier.WARN,
                            msg)
            server['imageRef'] = 1
        # Add security groups
        security_groups = [{'name': FLAGS.default_firewall_rule_name}]
        server['security_groups'] = security_groups
        # Add volume id
        if not 'metadata' in instance:
            server['metadata'] = {}
        server['metadata']['volume_id'] = str(volume_id)
        # Add mount point
        server['metadata']['mount_point'] = str(mount_point)
        # Add databases
        # We create these once and throw away the result to take advantage
        # of the validators.
        db_list = common.populate_databases(instance.get('databases', []))
        server['metadata']['database_list'] = json.dumps(db_list)
        return server

    def _setup_security_groups(self, context, group_name, port):
        """ Setup a default firewall rule for reddwarf.

        We are using the existing infrastructure of security groups in nova
        used by the ec2 api and piggy back on it. Reddwarf by default will have
        one rule which will allow access to the specified tcp port, the default
        being 3306 from anywhere. For this the group_id and parent_id are the
        same, we are not doing any hierarchical rules yet.
        Here's how it would look in iptables.

        -A nova-compute-inst-<id> -p tcp -m tcp --dport 3306 -j ACCEPT
        """
        self.compute_api.ensure_default_security_group(context)

        if not db.security_group_exists(context, context.project_id,
                                        group_name):
            LOG.debug('Creating a new firewall rule %s for project %s'
                        % (group_name, context.project_id))
            values = {'name': group_name,
                      'description': group_name,
                      'user_id': context.user_id,
                      'project_id': context.project_id}
            security_group = db.security_group_create(context, values)
            rules = {'group_id': security_group['id'],
                     'parent_group_id': security_group['id'],
                     'cidr': '0.0.0.0/0',
                     'protocol': 'tcp',
                     'from_port': port,
                     'to_port': port}
            db.security_group_rule_create(context, rules)
            self.compute_api.trigger_security_group_rules_refresh(context,
                                                          security_group['id'])

    def _get_guest_info(self, context, id):
        """Get the list of databases on a instance"""
        try:
            result = self.guest_api.list_databases(context, id)
            databases = [{'name': db['_name'],
                         'collate': db['_collate'],
                         'character_set': db['_character_set']}
                         for db in result]
            root_enabled = self.guest_api.is_root_enabled(context, id)
            return databases, root_enabled
        except Exception as err:
            LOG.error(err)
            LOG.error("guest not responding on instance %s" % id)
            return None, None

    @staticmethod
    def _validate_empty_body(body):
        """Check that the body is not empty"""
        if not body:
            raise exception.BadRequest("The request contains an empty body")

    @staticmethod
    def _validate_volume_size(size):
        """Validate the various possible errors for volume size"""
        try:
            volume_size = float(size)
        except (ValueError, TypeError) as err:
            LOG.error(err)
            raise exception.BadRequest("Required element/key - instance volume"
                                       "'size' was not specified as a number")
        if int(volume_size) != volume_size or int(volume_size) < 1:
            raise exception.BadRequest("Volume 'size' needs to be a positive "
                                       "integer value, %s cannot be accepted."
                                       % volume_size)
        max_size = FLAGS.reddwarf_max_accepted_volume_size
        if int(volume_size) > max_size:
            raise exception.BadRequest("Volume 'size' cannot exceed maximum "
                                       "of %d Gb, %s cannot be accepted."
                                       % (max_size, volume_size))

    @staticmethod
    def _validate(body):
        """Validate that the request has all the required parameters"""
        Controller._validate_empty_body(body)
        try:
            body['instance']
            body['instance']['flavorRef']
            volume_size = body['instance']['volume']['size']
        except KeyError as e:
            LOG.error("Create Instance Required field(s) - %s" % e)
            raise exception.BadRequest("Required element/key - %s was not "
                                       "specified" % e)
        Controller._validate_volume_size(volume_size)

    @staticmethod
    def _validate_resize(body, old_volume_size):
        """
        Currently we only support volume resizing, so we are going to check
        if that's present.
        """
        Controller._validate_empty_body(body)
        try:
            body['resize']
            body['resize']['volume']
            new_volume_size = body['resize']['volume']['size']
        except KeyError as e:
            LOG.error("Resize Instance Required field(s) - %s" % e)
            raise exception.BadRequest("Required element/key - %s was not "
                                       "specified" % e)
        Controller._validate_volume_size(new_volume_size)
        if int(new_volume_size) <= old_volume_size:
            raise exception.BadRequest("The new volume 'size' cannot be less "
                                       "than the current volume size of '%s'"
                                       % old_volume_size)


def create_resource(version='1.0'):
    controller = {
        '1.0': Controller,
    }[version]()

    metadata = {
        'attributes': {
            'instance': ['created', 'hostname', 'id', 'name', 'rootEnabled',
                         'status', 'updated'],
            'dbtype': ['name', 'version'],
            'flavor': ['id'],
            'link': ['rel', 'href'],
            'volume': ['size'],
            'database': ['name', 'collate', 'character_set'],
        },
    }

    xmlns = {
        '1.0': common.XML_NS_V10,
    }[version]

    serializers = {
        'application/xml': wsgi.XMLDictSerializer(metadata=metadata,
                                                  xmlns=xmlns),
    }

    deserializers = {
        'application/xml': deserializer.InstanceXMLDeserializer(),
    }

    response_serializer = wsgi.ResponseSerializer(body_serializers=serializers)
    request_deserializer = wsgi.RequestDeserializer(deserializers)
    return wsgi.Resource(controller, deserializer=request_deserializer,
                         serializer=response_serializer)
