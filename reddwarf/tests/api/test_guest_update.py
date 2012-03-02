# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2012 OpenStack, LLC.
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

from nose.tools import raises

import reddwarf
from reddwarf.api import instances
from reddwarf.db import models
from reddwarf import exception
from reddwarf.tests import util

base_url = util.v1_prefix
instances_url = util.v1_instances_prefix
mgmt_url = util.v1_mgmt_prefix

def request_obj(url, method, body={}):
    req = webob.Request.blank(url)
    req.method = method
    if method in ['POST', 'PUT']:
        req.body = json.dumps(body)
    req.headers["content-type"] = "application/json"
    return req

def compute_get_exception(self, ctxt, id):
    raise nova_exception.NotFound(message="WHOOPS")

def localid_from_uuid(id):
    return id

def instance_exists(context, id, compute_api):
    if id != '1':
        raise nova_exception.NotFound()

class GuestUpdateTest(test.TestCase):

    def setUp(self):
        super(GuestUpdateTest, self).setUp()
        self.stubs.Set(reddwarf.db.api, "localid_from_uuid", localid_from_uuid)
        self.stubs.Set(reddwarf.api.common, "instance_exists", instance_exists)

        self.context = context.RequestContext('fake', 'fake', 
                                              auth_token=True, is_admin=False)
        self.admin_context = context.RequestContext('fake', 'fake', 
                                              auth_token=True, is_admin=True)

    def tearDown(self):
        self.stubs.UnsetAll()
        super(GuestUpdateTest, self).tearDown()

    def test_guest_not_found(self):
        req = webob.Request.blank(mgmt_url + 'instances/99/action')
        req.method='POST'
        req.body = json.dumps({'update': {}})
        req.headers["content-type"] = "application/json"
        res = req.get_response(util.wsgi_app(
                               fake_auth_context=self.admin_context))
        self.assertEqual(res.status_int, 404)

    def test_guest_update_failure(self):
        req = webob.Request.blank(mgmt_url + 'instances/1/action')
        req.method='POST'
        req.body = json.dumps({'update': {}})
        req.headers["content-type"] = "application/json"
        res = req.get_response(util.wsgi_app(
                               fake_auth_context=self.admin_context))
        self.assertEqual(res.status_int, 404)
        
    def test_guest_update(self):
        m = mox.Mox()
        def fake_update_guest():
            pass
        m.StubOutWithMock(reddwarf.compute.api.API, 'update_guest', fake_update_guest)
        reddwarf.compute.api.API.update_guest(mox.IgnoreArg(), mox.IgnoreArg())
        m.ReplayAll()

        req = webob.Request.blank(mgmt_url + 'instances/1/action')
        req.method='POST'
        req.body = json.dumps({'update': {}})
        req.headers["content-type"] = "application/json"
        res = req.get_response(util.wsgi_app(
                               fake_auth_context=self.admin_context))
        self.assertEqual(res.status_int, 202)
        m.UnsetStubs()
        m.VerifyAll()
