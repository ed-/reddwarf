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

from nova import compute
from nova import db
from nova import exception
from nova import flags
from nova import log as logging
from nova import utils
from nova import volume
from nova.api.openstack import common as nova_common
from nova.api.openstack import faults
from nova.api.openstack import servers
from nova.api.openstack import wsgi
from nova.api.openstack.views.servers import ViewBuilder as servers_view
from nova.api.platform.dbaas import common
from nova.api.platform.dbaas import deserializer
from nova.compute import power_state
from nova.exception import InstanceNotFound
from nova.guest import api as guest_api
from reddwarf.db import api as dbapi


LOG = logging.getLogger('nova.api.platform.dbaas.dbcontainers')
LOG.setLevel(logging.DEBUG)


FLAGS = flags.FLAGS
flags.DEFINE_string('reddwarf_imageRef', 'http://localhost:8775/v1.0/images/1',
                    'Default image for reddwarf')
flags.DEFINE_string('reddwarf_mysql_data_dir', '/var/lib/mysql',
                    'Mount point within the container for MySQL data.')
flags.DEFINE_string('reddwarf_volume_description',
                    'Volume ID: %s assigned to Instance: %s',
                    'Default description populated for volumes')

_dbaas_mapping = {
    None: 'BUILD',
    power_state.NOSTATE: 'BUILD',
    power_state.RUNNING: 'ACTIVE',
    power_state.SHUTDOWN: 'SHUTDOWN',
    power_state.BUILDING: 'BUILD',
    power_state.FAILED: 'FAILED'
}

class Controller(object):
    """ The DBContainer API controller for the Platform API """

    def __init__(self):
        self.compute_api = compute.API()
        self.dns_entry_factory = \
            utils.import_object(FLAGS.dns_instance_entry_factory)
        self.guest_api = guest_api.API()
        self.server_controller = servers.ControllerV11()
        self.volume_api = volume.API()
        super(Controller, self).__init__()

    def index(self, req):
        """ Returns a list of dbcontainer names and ids for a given user """
        LOG.info("Call to DBContainers index")
        LOG.debug("%s - %s", req.environ, req.body)
        servers_response = self.server_controller.index(req)
        server_list = servers_response['servers']
        context = req.environ['nova.context']

        # DbContainers need the status for each instance in all circumstances,
        # unlike servers.
        server_states = db.instance_state_get_all_by_user(context,
                                                          context.user_id)
        for server in server_list:
            state = server_states[server['id']]
            server['status'] = nova_common.status_from_power_state(state)

        id_list = [server['id'] for server in server_list]
        guest_state_mapping = self.get_guest_state_mapping(id_list)
        dbcontainers = [self._create_dbcontainer_dict(context, server,
                                                      guest_state_mapping)
                        for server in server_list]
        return {'dbcontainers': dbcontainers}

    @staticmethod
    def get_guest_state_mapping(id_list):
        """Returns a dictionary of guest statuses keyed by guest ids."""
        results = dbapi.guest_status_get_list(id_list)
        return dict([(r.instance_id, r.state) for r in results])

    def detail(self, req):
        """ Returns a list of dbcontainer details for a given user """
        LOG.debug("%s - %s", req.environ, req.body)
        server_list = self.server_controller.detail(req)['servers']
        context = req.environ['nova.context']
        id_list = [server['id'] for server in server_list]
        guest_state_mapping = self.get_guest_state_mapping(id_list)
        dbcontainers = [self._create_detailed_dbcontainer_dict(context, server,
                                                           guest_state_mapping)
                        for server in server_list]
        return { 'dbcontainers' : dbcontainers }

    def show(self, req, id):
        """ Returns dbcontainer details by container id """
        LOG.info("Get Container by ID - %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        server_response = self.server_controller.show(req, id)
        LOG.debug("server_response - %s", server_response)
        if isinstance(server_response, Exception):
            return server_response  # Just return the exception to throw it
        context = req.environ['nova.context']
        server = server_response['server']
        LOG.debug("server - %s", server)
        dbcontainer = self._create_detailed_dbcontainer_dict(context, server)
        LOG.debug("dbcontainer - %s", dbcontainer)
        return {'dbcontainer': dbcontainer}

    def delete(self, req, id):
        """ Destroys a dbcontainer """
        LOG.info("Delete Container by ID - %s", id)
        LOG.debug("%s - %s", req.environ, req.body)
        context = req.environ['nova.context']

        self.server_controller.delete(req, id)
        #TODO(rnirmal): Use a deferred here to update status
        dbapi.guest_status_delete(id)
        try:
            for volume_ref in db.volume_get_all_by_instance(context, id):
                self.volume_api.delete_volume_when_available(context,
                                                             volume_ref['id'],
                                                             time_out=60)
        except exception.VolumeNotFoundForInstance:
            LOG.info("Skipping as no volumes are associated with the instance")

    def create(self, req, body):
        """ Creates a new DBContainer for a given user """
        self._validate(body)

        LOG.info("Create Container")
        LOG.debug("%s - %s", req.environ, body)

        context = req.environ['nova.context']

        # Create the Volume before hand
        volume_ref = self.create_volume(context, body)
        # Setup Security groups
        self._setup_security_groups(context,
                                    FLAGS.default_firewall_rule_name,
                                    FLAGS.default_guest_mysql_port)

        server = self._create_server_dict(body['dbcontainer'],
                                          volume_ref['id'],
                                          FLAGS.reddwarf_mysql_data_dir)

        # Add any extra data that's required by the servers api
        server_req_body = {'server':server}
        server_resp = self._try_create_server(req, server_req_body)
        server_id = str(server_resp['server']['id'])
        dbapi.guest_status_create(server_id)

        dbcontainer = self._create_dbcontainer_dict(context,
                                                    server_resp['server'])
        # Update volume description
        self.update_volume_info(context, volume_ref, dbcontainer)

        # add the volume information to response
        LOG.debug("adding the volume information to the response...")
        dbcontainer['volume'] = {'size': volume_ref['size']}
        return { 'dbcontainer': dbcontainer }

    def create_volume(self, context, body):
        """Creates the volume for the container and returns its ID."""
        volume_size = body['dbcontainer']['volume']['size']
        name = body['dbcontainer'].get('name', None)
        description = FLAGS.reddwarf_volume_description % (None, None)

        return self.volume_api.create(context, size=volume_size,
                                      snapshot_id=None,
                                      name=name,
                                      description=description)

    def update_volume_info(self, context, volume_ref, dbcontainer):
        """Update the volume description with the available dbcontainer info"""
        description = FLAGS.reddwarf_volume_description \
                            % (volume_ref['id'], dbcontainer['id'])
        self.volume_api.update(context, volume_ref['id'],
                               {'display_description': description})

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
                raise exception.Error("Could not complete the request. " \
                                      "Please try again later or contact " \
                                      "Customer Support")
            return server
        except (TypeError, AttributeError, KeyError) as e:
            LOG.error(e)
            raise exception.Error(exc.HTTPUnprocessableEntity())

    @staticmethod
    def _create_dbvolume_from_server(server):
        """Given a server dict returns the dbcontainer volume dict."""
        try:
            volumes = server['volumes']
            volume_dict = volumes[0]
        except (KeyError, IndexError):
            return None
        if len(volumes) > 1:
            raise exception.Error("> 1 volumes in the underlying container!")
        return {'size': volume_dict['size']}

    def _create_dbcontainer_dict(self, context, server, guest_states=None):
        """Given a server (obtained from the servers API) returns a container.

        We copy all elements from the server and then delete some, erring on
        the side of copying too many instead of too few.
        "guest_states" is a dictionary mapping guest IDs to their state. If
        it is None the state is queried.

        """
        server_only_keys = ['hostId', 'imageRef', 'metadata', 'adminPass',
                            'uuid', 'volumes', 'status', 'addresses']
        dbcontainer = dict((key, server[key]) for key in server.keys()
                           if key not in server_only_keys)
        # Add DNS hostname
        user_id = context.user_id
        instance_info = {"id": dbcontainer["id"], "user_id": user_id}
        dns_entry = self.dns_entry_factory.create_entry(instance_info)
        if dns_entry:
            dbcontainer["hostname"] = dns_entry.name
        # Add volume information
        dbvolume = self._create_dbvolume_from_server(server)
        if dbvolume:
            dbcontainer['volume'] = dbvolume
        # Add status
        dbcontainer['status'] = self._get_dbcontainer_status(server,
                                                             guest_states)
        return dbcontainer

    @staticmethod
    def _get_dbcontainer_status(server, guest_states=None):
        """Figures out what the dbcontainer status should be.

        First looks at the server status, then to a dictionary mapping guest
        IDs to their states.

        """
        id = server['id']
        if server['status'] == 'ERROR':
            return 'ERROR'
        else:
            try:
                if guest_states:
                    state = guest_states[id]
                else:
                    state = dbapi.guest_status_get(id).state
            except (KeyError, InstanceNotFound):
                # we set the state to shutdown if not found
                state = power_state.SHUTDOWN
            return _dbaas_mapping[state]

    def _create_detailed_dbcontainer_dict(self, context, server,
                                          guest_states=None):
        """Creates a dbcontainer dictionary to be used in a response
        """
        dbcontainer = self._create_dbcontainer_dict(context, server,
                                                    guest_states)
        # Add rootEnabled info.
        enabled = self._determine_root(context, dbcontainer)
        if enabled is not None:
            dbcontainer['rootEnabled'] = enabled
        return dbcontainer

    @staticmethod
    def _create_server_dict(dbcontainer, volume_id, mount_point):
        """Creates a server dict from the request dbcontainer dict."""
        server = copy.copy(dbcontainer)
        # Append additional stuff to create.
        # Add image_ref
        server['imageRef'] = FLAGS.reddwarf_imageRef
        # Add Firewall rules
        firewall_rules = [FLAGS.default_firewall_rule_name]
        server['firewallRules'] = firewall_rules
        # Add volume id
        if not 'metadata' in dbcontainer:
            server['metadata'] = {}
        server['metadata']['volume_id'] = str(volume_id)
        # Add mount point
        server['metadata']['mount_point'] = str(mount_point)
        # Add databases
        db_list = dbcontainer.get('databases', [])
        # We create these once and throw away the result to take advantage
        # of the validators.
        common.populate_databases(db_list)
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

    def _determine_root(self, context, container):
        """ Determine if root is enabled for a given container. """
        # If we can't determine if root is enabled for whatever reason,
        # including if the container isn't ACTIVE, rootEnabled isn't
        # available.
        running = _dbaas_mapping[power_state.RUNNING]
        if container['status'] == running:
            try:
                return self.guest_api.is_root_enabled(context, container['id'])
            except Exception as err:
                LOG.error(err)
                LOG.error("rootEnabled for %s could not be determined." % id)
        return

    @staticmethod
    def _validate(body):
        """Validate that the request has all the required parameters"""
        if not body:
            raise exc.HTTPUnprocessableEntity()

        try:
            body['dbcontainer']
            body['dbcontainer']['flavorRef']
            volume_size = float(body['dbcontainer']['volume']['size'])
            if int(volume_size) != volume_size or int(volume_size) < 1:
                msg = "Volume 'size' needs to be a positive integer value, %s"\
                      " cannot be accepted." % volume_size
                raise exception.ApiError(msg)
        except (KeyError, ValueError) as e:
            # KeyError thrown if any of the fields are missing from the dict.
            # ValueError is thrown if volume_size isn't created properly.
            LOG.error("Create Container Required field(s) - %s" % e)
            raise exception.ApiError("Required element/key - %s " \
                                      "was not specified" % e)


def create_resource(version='1.0'):
    controller = {
        '1.0': Controller,
    }[version]()

    metadata = {
        "attributes": {
            "dbcontainer": ["id", "name", "status", "flavorRef", "rootEnabled"],
            "dbtype": ["name", "version"],
            "link": ["rel", "type", "href"],
            "volume": ["size"],
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
        'application/xml': deserializer.DBContainerXMLDeserializer(),
    }

    response_serializer = wsgi.ResponseSerializer(body_serializers=serializers)
    request_deserializer = wsgi.RequestDeserializer(deserializers)
    return wsgi.Resource(controller, deserializer=request_deserializer,
                         serializer=response_serializer)
