#    Copyright 2012 OpenStack LLC
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
Tests for Instance Status API calls
"""

import mox
import json
import stubout
import webob
from paste import urlmap

import nova
from nova import context
from nova import test
from nova.compute import vm_states
from nova.compute import power_state
import nova.exception as nova_exception


import reddwarf
from reddwarf import exception
from reddwarf.api import instances
from reddwarf.api import status
from reddwarf.db import api as dbapi
from reddwarf.db import models
from reddwarf.exception import NotFound
from reddwarf.exception import UnprocessableEntity
from reddwarf.tests import util

from nose.tools import raises

instances_url = util.v1_instances_prefix

class fake_status(object):
    def __init__(self):
        self.created_at = 0
        self.deleted_at = 0
        self.updated_at = 0

        self.deleted = False
        self.instance_id = 0
        self.state = 0
        self.guest_status = 'active'
        self.state_description = 'this is a fake status object.'

def fake_localid_from_uuid(id):
    return id

fake_instance = {
    'id': 0,
    'power_state': power_state.RUNNING,
    'vm_state': 'active',
    'instance_id': 0,
    'status': 'active',
    'guest_status': fake_status(),
}

def fake_instance_get(context, id):
    return fake_instance

def fake_guest_status_get_list(id_list=None):
    class GSGL(object):
        def __init__(self):
            self.all_things = []
            if id_list is not None:
                self.all_things = [fake_status()]
        def all(self):
            return self.all_things
    return GSGL()

class StatusApiTest(test.TestCase):

    def setUp(self):
        super(StatusApiTest, self).setUp()

        self.stubs.Set(reddwarf.db.api, "localid_from_uuid", fake_localid_from_uuid)
        self.stubs.Set(reddwarf.db.api, "guest_status_get_list", fake_guest_status_get_list)
        self.stubs.Set(nova.db, "instance_get", fake_instance_get)

        self.context = context.get_admin_context()
        self.id_list = [0]
        self.status = status.InstanceStatusLookup(self.id_list)

    def tearDown(self):
        self.stubs.UnsetAll()
        super(StatusApiTest, self).tearDown()

    @raises(UnprocessableEntity)
    def test_empty_instance_status(self):
        empty = status.InstanceStatus()
        self.assertFalse(empty.is_sql_running)
        empty.can_perform_action_on_instance()

    def test_status(self):
        empty = status.InstanceStatus()
        self.assertEqual(empty.status, 'SHUTDOWN')
        empty.server_status = 'ERROR'
        self.assertEqual(empty.status, 'ERROR')
        paused = status.InstanceStatus(guest_state=power_state.PAUSED)
        self.assertEqual(paused.status, 'REBOOT')

    def test_guest_status(self):
        empty = status.InstanceStatus()
        self.assertEqual({}, empty.get_guest_status())

        nonempty = status.InstanceStatus(
            guest_state=power_state.RUNNING, 
            guest_status=fake_status())
        gs = nonempty.get_guest_status()
        expected_fields = [
            'created_at',
            'deleted',
            'deleted_at',
            'instance_id',
            'state',
            'state_description',
            'updated_at',
        ]
        for f in expected_fields:
            print f
            self.assertTrue(f in gs.keys())

    def test_shutdown_instance_status(self):
        shutdown = status.InstanceStatus(guest_state=power_state.SHUTDOWN)
        self.assertFalse(shutdown.is_sql_running)
        
    @raises(NotFound)
    def test_load_from_db_404(self):
        lookup = status.InstanceStatus.load_from_db(self.context, 1)
        self.assertTrue(lookup.is_sql_running)

    def test_get_status_from_server(self):
        lookup = status.InstanceStatusLookup([fake_instance['instance_id']])
        server = lookup.get_status_from_server(fake_instance)
