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
Tests for reddwarf.compute.manager.
"""

import webob
from paste import urlmap

from nova import db
from nova import context
from nova import exception
from nova import flags
from nova import test
from nova import utils
from nova.compute import instance_types
from nova.compute import vm_states
from reddwarf import exception as reddwarf_exception
from reddwarf.compute.api import API
from reddwarf.compute import manager
from reddwarf.compute.manager import ReddwarfComputeManager
from reddwarf.db import api as dbapi
from reddwarf.tests import util


FLAGS = flags.FLAGS


class RdComputeManagerResizeInPlaceTest(test.TestCase):
    """Tests the resize_in_place method of compute.api.

    Something not captured here is that in the event the driver operations
    work, but the guest fails, the vm_state is set to ACTIVE. That's because
    the guest_status will still be SHUTDOWN or CRASHED and cause the status
    returned from the API to show the bad state, which might be fixed by
    ops intervention.

    """

    def setUp(self):
        super(RdComputeManagerResizeInPlaceTest, self).setUp()
        self.flags(connection_type='openvz',
            compute_manager="reddwarf.compute.manager.ReddwarfComputeManager",
                   stub_network=True,
                   notification_driver='nova.notifier.test_notifier',
                   network_manager='nova.network.manager.FlatManager')
        self.rd_compute = utils.import_object(FLAGS.compute_manager)
        self.ctxt = context.get_admin_context()
        self.old_instance_type_id = 555
        self.new_instance_type_id = 777
        self.new_memory_size=1024 * 2
        self.instance_id = 12345
        self.instance_ref = {
            'id':self.instance_id,
            'instance_type_id': self.old_instance_type_id,
        }
        self.user_id = 'fake'
        self.mox.StubOutWithMock(self.rd_compute.db, "instance_get")
        self.rd_compute.db.instance_get(self.ctxt, self.instance_id)\
            .AndReturn(self.instance_ref)
        self.mox.StubOutWithMock(manager, "notify_of_failure")

    def tearDown(self):
        super(RdComputeManagerResizeInPlaceTest, self).tearDown()

    def expect_driver_reset_instance_size_fails(self):
        ex = exception.InstanceUnacceptable()
        self.mox.StubOutWithMock(self.rd_compute.driver, "reset_instance_size")
        self.rd_compute.driver.reset_instance_size(self.instance_ref). \
            AndRaise(ex)
        return ex


    def expect_driver_reset_instance_size_works(self):
        ex = exception.InstanceUnacceptable()
        self.mox.StubOutWithMock(self.rd_compute.driver, "reset_instance_size")
        self.rd_compute.driver.reset_instance_size(self.instance_ref)

    def expect_driver_resize_in_place_fails(self):
        self.mox.StubOutWithMock(self.rd_compute.driver, "resize_in_place")
        ex = exception.InstanceUnacceptable()
        self.rd_compute.driver.resize_in_place(self.instance_ref,
            self.new_instance_type_id). \
            AndRaise(ex)
        self.expect_notify_of_failure_1(ex)

    def expect_driver_resize_in_place_works(self):
        self.mox.StubOutWithMock(self.rd_compute.driver, "resize_in_place")
        self.rd_compute.driver.resize_in_place(self.instance_ref,
                                               self.new_instance_type_id)
        self.mox.StubOutWithMock(instance_types, "get_instance_type")
        instance_types.get_instance_type(self.new_instance_type_id).\
            AndReturn({'memory_mb':self.new_memory_size})

    def expect_guest_api_start_mysql_fails(self, updated_memory_size):
        self.mox.StubOutWithMock(self.rd_compute.guest_api,
                                 "start_mysql_with_conf_changes")
        guest_error = reddwarf_exception.GuestError(original_message="BAD")
        self.rd_compute.guest_api.start_mysql_with_conf_changes(self.ctxt,
            self.instance_id, updated_memory_size).AndRaise(guest_error)
        return guest_error

    def expect_guest_api_start_mysql_works(self, updated_memory_size):
        self.mox.StubOutWithMock(self.rd_compute.guest_api,
                                 "start_mysql_with_conf_changes")
        self.rd_compute.guest_api.start_mysql_with_conf_changes(self.ctxt,
            self.instance_id, updated_memory_size)

    def expect_guest_api_stop_mysql_fails(self):
        self.mox.StubOutWithMock(self.rd_compute.guest_api, "stop_mysql")
        guest_error = reddwarf_exception.GuestError(original_message="BAD")
        self.rd_compute.guest_api.stop_mysql(self.ctxt, self.instance_id).\
            AndRaise(guest_error)
        return guest_error

    def expect_guest_api_stop_mysql_works(self):
        self.mox.StubOutWithMock(self.rd_compute.guest_api, "stop_mysql")
        self.rd_compute.guest_api.stop_mysql(self.ctxt, self.instance_id)

    def expect_notify_of_failure_1(self, expected_exception):
        manager.notify_of_failure(self.ctxt,
            event_type='reddwarf.instance.resize_in_place_1',
            exception=expected_exception,
            audit_msg=_("Aborting instance %(instance_id)d resize operation."),
            err_values = { 'instance_id':self.instance_id,
                           'new_instance_type_id':self.new_instance_type_id })

    def expect_notify_of_failure_2(self, expected_exception, final_vm_state):
        manager.notify_of_failure(self.ctxt,
            event_type='reddwarf.instance.resize_in_place_2',
            exception=expected_exception,
            audit_msg=_("Aborting instance %(instance_id)d resize operation."),
            err_values = { 'instance_id':self.instance_id,
                           'new_instance_type_id':self.new_instance_type_id,
                           'final_vm_state':final_vm_state })

    def expect_instance_update_to_type(self, actual_vm_state, instance_type_id):
        self.mox.StubOutWithMock(self.rd_compute, "_instance_update")
        self.rd_compute._instance_update(self.ctxt, self.instance_id,
            instance_type_id=instance_type_id,
            vm_state=actual_vm_state, task_state=None)

    def test_when_stop_mysql_raises(self):
        guest_error = self.expect_guest_api_stop_mysql_fails()
        self.expect_notify_of_failure_2(guest_error, vm_states.ACTIVE)
        self.expect_instance_update_to_type(vm_states.ACTIVE,
                                            self.old_instance_type_id)
        self.mox.ReplayAll()

        self.assertRaises(reddwarf_exception.GuestError,
                          self.rd_compute.resize_in_place,
                          self.ctxt, self.instance_id,
                          self.new_instance_type_id)

    def test_when_reset_instance_size_raises(self):
        """When resize_in_place fails, then reset_instance_size also fails."""
        self.expect_guest_api_stop_mysql_works()
        self.expect_driver_resize_in_place_fails()
        ex = self.expect_driver_reset_instance_size_fails()
        self.expect_notify_of_failure_2(ex, vm_states.ERROR)
        self.expect_instance_update_to_type(vm_states.ERROR,
                                            self.old_instance_type_id)
        self.mox.ReplayAll()

        self.assertRaises(exception.InstanceUnacceptable,
                          self.rd_compute.resize_in_place,
                          self.ctxt, self.instance_id,
                          self.new_instance_type_id)

    def test_when_start_mysql_raises_after_we_cant_resize(self):
        """
        resize_in_place fails, we can reset the size but not start mysql.
        """
        self.expect_guest_api_stop_mysql_works()
        self.expect_driver_resize_in_place_fails()
        self.expect_driver_reset_instance_size_works()
        ex = self.expect_guest_api_start_mysql_fails(updated_memory_size=None)
        self.expect_notify_of_failure_2(ex, vm_states.ACTIVE)
        self.expect_instance_update_to_type(vm_states.ACTIVE,
                                            self.old_instance_type_id)
        self.mox.ReplayAll()

        self.assertRaises(reddwarf_exception.GuestError,
                          self.rd_compute.resize_in_place,
                          self.ctxt, self.instance_id,
                          self.new_instance_type_id)

    def test_when_resize_in_place_fails_but_we_recover(self):
        """
        resize_in_place fails, but we can reset the size and start mysql.
        """
        self.expect_guest_api_stop_mysql_works()
        self.expect_driver_resize_in_place_fails()
        self.expect_driver_reset_instance_size_works()
        self.expect_guest_api_start_mysql_works(updated_memory_size=None)
        self.expect_instance_update_to_type(vm_states.ACTIVE,
                                            self.old_instance_type_id)
        self.mox.ReplayAll()

        self.rd_compute.resize_in_place(self.ctxt, self.instance_id,
                                        self.new_instance_type_id)

    def test_when_start_mysql_raises_after_we_resize_successfully(self):
        """
        resize_in_place fails, we can reset the size but not start mysql.
        """
        self.expect_guest_api_stop_mysql_works()
        self.expect_driver_resize_in_place_works()
        ex = self.expect_guest_api_start_mysql_fails(self.new_memory_size)
        self.expect_notify_of_failure_2(ex, vm_states.ACTIVE)
        self.expect_instance_update_to_type(vm_states.ACTIVE,
                                            self.new_instance_type_id)
        self.mox.ReplayAll()

        self.assertRaises(reddwarf_exception.GuestError,
                          self.rd_compute.resize_in_place,
                          self.ctxt, self.instance_id,
                          self.new_instance_type_id)


    def test_when_everything_works(self):
        self.expect_guest_api_stop_mysql_works()
        self.expect_driver_resize_in_place_works()
        self.expect_guest_api_start_mysql_works(self.new_memory_size)
        self.expect_instance_update_to_type(vm_states.ACTIVE,
                                            self.new_instance_type_id)
        self.mox.ReplayAll()

        self.rd_compute.resize_in_place(self.ctxt, self.instance_id,
                                        self.new_instance_type_id)
