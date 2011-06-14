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

from webob import exc

from nova import compute
from nova import exception
from nova import log as logging
from nova.api.openstack import faults
from nova.api.platform.dbaas import common
from nova.api.platform.dbaas import deserializer
from nova.guest import api as guest_api
from nova.guest.db import models


LOG = logging.getLogger('nova.api.platform.dbaas.databases')
LOG.setLevel(logging.DEBUG)


class Controller(common.DBaaSController):
    """ The Database Controller for the DBaaS API """

    _serialization_metadata = {
        'application/xml': {
            'attributes': {
                'database': ["name", "character_set", "collate"]
            },
        },
    }

    def __init__(self):
        self.guest_api = guest_api.API()
        self.compute_api = compute.API()
        super(Controller, self).__init__()

    def index(self, req, dbcontainer_id):
        """ Returns a list of Databases for the DBContainer """
        LOG.info("Call to Databases index - %s", dbcontainer_id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        common.instance_exists(ctxt, dbcontainer_id, self.compute_api)
        result = self.guest_api.list_databases(ctxt, dbcontainer_id)
        LOG.debug("LIST DATABASES RESULT - %s", str(result))
        databases = {'databases':[]}
        for database in result:
            mysql_database = models.MySQLDatabase()
            mysql_database.deserialize(database)
            databases['databases'].append({'name': mysql_database.name})
        LOG.debug("LIST DATABASES RETURN - %s", databases)
        return databases

    def delete(self, req, dbcontainer_id, id):
        """ Deletes a Database """
        LOG.info("Call to Delete Database - %s for container %s",
                 id, dbcontainer_id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        common.instance_exists(ctxt, dbcontainer_id, self.compute_api)
        mydb = models.MySQLDatabase()
        mydb.name = id

        self.guest_api.delete_database(ctxt, dbcontainer_id, mydb.serialize())
        return exc.HTTPAccepted()

    def create(self, req, dbcontainer_id):
        """ Creates a new Database in the specified container """
        LOG.info("Call to Create Databases for container %s", dbcontainer_id)
        LOG.debug("%s - %s", req.environ, req.body)
        ctxt = req.environ['nova.context']
        common.instance_exists(ctxt, dbcontainer_id, self.compute_api)

        body = self._deserialize_create(req)
        if not body:
            return faults.Fault(exc.HTTPUnprocessableEntity())

        databases = common.populate_databases(body.get('databases', ''))
        self.guest_api.create_database(ctxt, dbcontainer_id, databases)
        return exc.HTTPAccepted("")

    def _deserialize_create(self, request):
        """
        Deserialize a create request

        Overrides normal behavior in the case of xml content
        """
        if request.content_type == "application/xml":
            deser = deserializer.RequestXMLDeserializer()
            body = deser.deserialize_databases(request.body)
        else:
            body = self._deserialize(request.body, request.get_content_type())

        # Add any checks for required elements/attributes/keys
        if not body.get('databases', ''):
            raise exception.ApiError("Required element/key 'databases' " \
                                         "was not specified")
        for database in body.get('databases'):
            if not database.get('name', ''):
                raise exception.ApiError("Required attribute/key 'name' " \
                                         "was not specified")
        return body
