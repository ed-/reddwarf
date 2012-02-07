#    Copyright 2011 OpenStack LLC
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
Tests for reddwarf.compute.api.
"""

import webob
from paste import urlmap

from nova import db
from nova import context
from nova import exception
from nova import test
from nova.compute import instance_types
from nova.compute import vm_states
from reddwarf import exception as reddwarf_exception
from reddwarf.compute.api import API
from reddwarf.db import api as dbapi
from reddwarf.tests import util


class InstanceTypes(object):

    def __init__(self):
        self.types = instance_types.get_all_types()

    def get(self, memory_mb):
        for name in self.types:
            type = self.types[name]
            if type['memory_mb'] == memory_mb:
                return type


# Auto incrementing flavor id used to create instances.
next_flavor_id = 1449

def create_instance_type(name, memory):
    global next_flavor_id
    next_flavor_id += 1
    instance_types.create(name, memory, 1, 1, next_flavor_id)
    it = instance_types.get_instance_type_by_flavor_id(next_flavor_id)
    return it


def setup(self):
    util.reset_database()
    util.db_sync()


class ResizeInPlaceTest(test.TestCase):
    """Tests the resize_in_place method of compute.api."""

    def setUp(self):
        super(ResizeInPlaceTest, self).setUp()
        self.api = API()
        self.ctxt = context.get_admin_context()
        self.instance_id = None
        self.user_id = 'fake'
        self.project_id = 'fake'
        types = InstanceTypes()
        self.inst_type_small = create_instance_type("test_small", 512)
        self.inst_type_big = create_instance_type("test_big", 1024)
        self.inst_type_too_big = create_instance_type("test_too_big", 1024 * 15)

    def tearDown(self):
        if self.instance_id:
            db.instance_destroy(self.ctxt, self.instance_id)
        instance_types.purge(self.inst_type_small['name'])
        instance_types.purge(self.inst_type_big['name'])
        instance_types.purge(self.inst_type_too_big['name'])
        self.stubs.UnsetAll()
        super(ResizeInPlaceTest, self).tearDown()

    def create_instance(self, instance_type):
        inst = {
            'image_ref':1,
            'user_id':self.user_id,
            'host':'fake_host',
            'project_id':self.project_id,
            'instance_type_id':instance_type['id']
        }
        instance = db.instance_create(self.ctxt, inst)
        self.assertNotEqual(vm_states.RESIZING, instance['vm_state'])
        return instance['id']

    def test_when_instance_not_found(self):
        self.assertRaises(exception.NotFound, self.api.resize_in_place,
                          self.ctxt, -1, self.inst_type_small['id'])

    def test_when_new_flavor_not_found(self):
        self.instance_id = self.create_instance(self.inst_type_small)
        self.assertRaises(exception.InstanceTypeNotFound,
                          self.api.resize_in_place, self.ctxt,
                          self.instance_id, -1)

    def test_when_new_flavor_is_smaller(self):
        self.instance_id = self.create_instance(self.inst_type_big)
        self.assertRaises(exception.CannotResizeToSmallerSize,
                          self.api.resize_in_place, self.ctxt,
                          self.instance_id, self.inst_type_small['id'])

    def test_when_new_flavor_is_too_big(self):
        self.instance_id = self.create_instance(self.inst_type_small)
        # Stub out call to get available space on host.
        self.mox.StubOutWithMock(dbapi, "instance_get_memory_sum_by_host")
        dbapi.instance_get_memory_sum_by_host(self.ctxt, 'fake_host')\
            .AndReturn(1024 * 10)
        self.mox.ReplayAll()
        # Perform actual call.
        self.assertRaises(reddwarf_exception.OutOfInstanceMemory,
                          self.api.resize_in_place, self.ctxt,
                          self.instance_id, self.inst_type_too_big['id'])

    def test_successful(self):
        self.instance_id = self.create_instance(self.inst_type_small)
        # Stub out call to get available space on host.
        self.mox.StubOutWithMock(dbapi, "instance_get_memory_sum_by_host")
        dbapi.instance_get_memory_sum_by_host(self.ctxt, 'fake_host')\
            .AndReturn(1024 * 10)
        self.mox.StubOutWithMock(self.api, "_cast_compute_message")
        # Stub out final cast call.
        mock_params = {'new_instance_type_id':self.inst_type_big['id']}
        self.api._cast_compute_message("resize_in_place", self.ctxt,
                                       self.instance_id,
                                       params=mock_params)
        self.mox.ReplayAll()
        # Perform actual call.
        self.api.resize_in_place(self.ctxt, self.instance_id,
                                 self.inst_type_big['id'])
        instance = self.api.get(self.ctxt, self.instance_id)
        self.assertEqual(vm_states.RESIZING, instance['vm_state'])
