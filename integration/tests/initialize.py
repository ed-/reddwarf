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

import unittest
import os

from nose.plugins.skip import SkipTest

from proboscis import test
from tests.util import test_config
from tests.util.services import Service
from tests.util.services import start_proc
from tests.util.services import WebService
from tests.util.test_config import glance_code_root
from tests.util.test_config import glance_images_directory
from tests.util.test_config import nova_code_root
from tests.util.test_config import python_cmd_list

dbaas_image = None
container_name = None
success_statuses = ["build", "active"]


def dbaas_url():
    return str(test_config.values.get("dbaas_url"))

def glance_conf():
    return str(test_config.values.get("glance_conf"))

def nova_conf():
    return str(test_config.values.get("nova_conf"))

def nova_url():
    return str(test_config.values.get("nova_url"))

def either_web_service_is_up():
    return test_config.dbaas.is_service_alive() or \
           test_config.nova.is_service_alive()

install_image = False

@test(groups=["services.initialize", "services.initialize.glance"])
class GlanceRegistry(unittest.TestCase):
    """Starts the glance registry."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/glance-registry" % glance_code_root(),
                                glance_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()

@test(groups=["services.initialize", "services.initialize.glance"],
      depends_on_classes=[GlanceRegistry])
class GlanceApi(unittest.TestCase):
    """Starts the glance registry."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/glance-api" % glance_code_root(),
                                glance_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()

@test(groups=["services.initialize", "services.initialize.glance"],
      depends_on_classes=[GlanceApi])
class AddGlanceImage(unittest.TestCase):
    """Starts the glance registry."""

    def test_start(self):
        if os.environ.get("INSTALL_GLANCE_IMAGE", "False") == 'True':
            proc = start_proc(["%s/bin/glance-upload" % glance_code_root(),
                               "--type=raw",
                               "%s/ubuntu-10.04-x86_64-openvz.tar.gz" %
                               glance_images_directory(),
                               "ubuntu-10.04-x86_64-openvz"])
            proc.communicate()



@test(groups=["services.initialize"], depends_on_classes=[GlanceApi])
class Network(unittest.TestCase):
    """Starts the network service."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/nova-network" % nova_code_root(),
                                "--flagfile=%s" % nova_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()


@test(groups=["services.initialize"], depends_on_classes=[GlanceApi])
class Dns(unittest.TestCase):
    """Starts the network service."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/nova-dns" % nova_code_root(),
                                "--flagfile=%s" % nova_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()


@test(groups=["services.initialize"])
class Scheduler(unittest.TestCase):
    """Starts the scheduler service."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/nova-scheduler" % nova_code_root(),
                                "--flagfile=%s" % nova_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()


@test(groups=["services.initialize"], depends_on_classes=[GlanceApi, Network])
class Compute(unittest.TestCase):
    """Starts the compute service."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/nova-compute" % nova_code_root(),
                                "--flagfile=%s" % nova_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()


@test(groups=["services.initialize"], depends_on_classes=[Scheduler])
class Volume(unittest.TestCase):
    """Starts the volume service."""

    def setUp(self):
        self.service = Service(python_cmd_list() +
                               ["%s/bin/nova-volume" % nova_code_root(),
                                "--flagfile=%s" % nova_conf() ])

    def test_start(self):
        if not either_web_service_is_up():
            self.service.start()


@test(groups=["services.initialize"],
      depends_on_classes=[Compute, Network, Scheduler, Volume])
class Api(unittest.TestCase):
    """Starts the compute service."""

    def setUp(self):
        self.service = test_config.nova

    def test_start(self):
        if not self.service.is_service_alive():
            self.service.start(time_out=60)


@test(groups=["services.initialize"],
      depends_on_classes=[Compute, Network, Scheduler, Volume])
class PlatformApi(unittest.TestCase):
    """Starts the compute service."""

    def setUp(self):
        self.service = test_config.dbaas

    def test_start(self):
        if not self.service.is_service_alive():
            self.service.start(time_out=60)


@test(groups=["services.initialize"],
      depends_on_classes=[Compute, Network, Scheduler, Volume])
class WaitForTopics(unittest.TestCase):
    """Starts the compute service."""

    def test_start(self):
        from tests.util.topics import hosts_up
        hosts = None
        while not hosts:
            hosts = hosts_up("compute")


@test(groups=["start_and_wait"],
      depends_on_groups=["services.initialize"],
      ignore=(os.environ.get("SERVICE_WAIT", 'False') != 'True'))
class StartAndWait(unittest.TestCase):

    def test(self):
        import time
        while(True):
            time.sleep(2)

