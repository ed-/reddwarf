# Copyright 2011 OpenStack LLC.
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

import gettext
import os
import json
import re
import sys
import time
import unittest
from tests import util

GROUP="dbaas.guest"
GROUP_START="dbaas.guest.initialize"
GROUP_TEST="dbaas.guest.test"
GROUP_STOP="dbaas.guest.shutdown"


from datetime import datetime
from nose.plugins.skip import SkipTest
from nose.tools import assert_true
from novaclient.exceptions import NotFound, ClientException
from nova import context
from nova import db
from nova.api.platform.dbaas.dbcontainers import _dbaas_mapping
from nova.compute import power_state
from reddwarf.db import api as dbapi

from proboscis.decorators import expect_exception
from proboscis.decorators import time_out
from proboscis import test
from tests.util import test_config
from tests.util import check_database
from tests.util import create_dns_entry
from tests.util import create_test_client
from tests.util import process
from tests.util.users import Requirements
from tests.util import string_in_list

try:
    import rsdns
except Exception:
    rsdns = None


class ContainerTestInfo(object):
    """Stores new container information used by dependent tests."""

    def __init__(self):
        self.dbaas = None  # The rich client instance used by these tests.
        self.dbaas_flavor = None # The flavor object of the container.
        self.dbaas_flavor_href = None  # The flavor of the container.
        self.dbaas_image = None  # The image used to create the container.
        self.dbaas_image_href = None  # The link of the image.
        self.id = None  # The ID of the instance in the database.
        self.initial_result = None # The initial result from the create call.
        self.user_ip = None  # The IP address of the instance, given to user.
        self.infra_ip = None # The infrastructure network IP address.
        self.result = None  # The container info returned by the API
        self.name = None  # Test name, generated each test run.
        self.pid = None # The process ID of the instance.
        self.user = None  # The user instance who owns the container.
        self.volume = None # The volume the container will have.
        self.storage = None # The storage device info for the volumes.

    def check_database(self, dbname):
        return check_database(self.id, dbname)

    def expected_dns_entry(self):
        """Returns expected DNS entry for this container.

        :rtype: Instance of :class:`DnsEntry`.

        """
        return create_dns_entry(container_info.user.auth_user,
                                container_info.id)


# The two variables are used below by tests which depend on a container
# existing.
container_info = ContainerTestInfo()
dbaas = None  # Rich client used throughout this test.


@test(groups=[GROUP, GROUP_START, 'dbaas.setup'], depends_on_groups=["services.initialize"])
class Setup(unittest.TestCase):
    """Makes sure the client can hit the ReST service.

    This test also uses the API to find the image and flavor to use.

    """

    def setUp(self):
        """Sets up the client."""
        global dbaas
        container_info.user = test_config.users.find_user(Requirements(is_admin=True))
        dbaas = create_test_client(container_info.user)

    def test_auth_token(self):
        print("Auth Token: %s" % dbaas.client.auth_token)
        print("Service URL: %s" % dbaas.client.management_url)
        self.assertNotEqual(dbaas.client.auth_token, None)
        self.assertEquals(dbaas.client.management_url, test_config.dbaas_url)

    def test_find_image(self):
        result = dbaas.find_image_and_self_href(test_config.dbaas_image)
        container_info.dbaas_image, container_info.dbaas_image_href = result

    def test_find_flavor(self):
        result = dbaas.find_flavor_and_self_href(flavor_id=1)
        container_info.dbaas_flavor, container_info.dbaas_flavor_href = result

    def test_create_container_name(self):
        container_info.name = "TEST_" + str(datetime.now())


@test(depends_on_classes=[Setup], groups=[GROUP, GROUP_START, 'dbaas.mgmt.hosts'])
class ContainerHostCheck(unittest.TestCase):
    """Class to run tests after Setup"""

    def test_empty_index_host_list(self):
        host_index_result = dbaas.hosts.index()
        self.assertNotEqual(host_index_result, None,
                            "list hosts call should not be empty")
        print("result : %r" % str(host_index_result))
        self.assertTrue(len(host_index_result) > 0,
                        "list hosts length should not be empty")
        print("test_index_host_list result: %r" % host_index_result[0])
        print("instance count for host : %r" % host_index_result[0].instanceCount)
        self.assertEquals(int(host_index_result[0].instanceCount), 0,
                          "instance count of 'host' should have 0 running instances")
        print("test_index_host_list result instance_count: %r" %
              host_index_result[0].instanceCount)
        self.assertEquals(len(host_index_result), 1,
                          "The host result list is expected to be of length 1")
        for host in list(enumerate(host_index_result, start=1)):
            print("%r host: %r" % (host[0], host[1]))
            container_info.host = host[1]

    def test_empty_index_host_list_single(self):
        print("container_info.host : %r" % container_info.host)
        host_index_result = dbaas.hosts.get(container_info.host)
        self.assertNotEqual(host_index_result, None,
                            "list hosts should not be empty")
        print("test_index_host_list_single result: %r" %
              host_index_result.__dict__)
        self.assertTrue(host_index_result.percentUsed == 0,
                        "percentUsed should be 0 : %r" % host_index_result.percentUsed)
        self.assertTrue(host_index_result.totalRAM,
                        "totalRAM should exist > 0 : %r" % host_index_result.totalRAM)
        self.assertTrue(host_index_result.usedRAM == 0,
                        "usedRAM should be 0 : %r" % host_index_result.usedRAM)
        self.assertTrue(container_info.name
                        not in [dbc.name for dbc
                                in host_index_result.dbcontainers])
        container_info.host_info = host_index_result
        for container in list(enumerate(host_index_result.dbcontainers, start=1)):
            print("%r dbcontainer: %r" % (container[0], container[1]))

    @expect_exception(NotFound)
    def test_host_not_found(self):
        container_info.myresult = dbaas.hosts.get('host@$%3dne')

    def test_storage_on_host(self):
        storage = dbaas.storage.index()
        print("storage : %r" % storage)
        self.assertTrue(hasattr(storage, 'name'))
        self.assertTrue(hasattr(storage, 'availablesize'))
        self.assertTrue(hasattr(storage, 'totalsize'))
        print("storage.name : %r" % storage.name)
        print("storage.availablesize : %r" % storage.availablesize)
        print("storage.totalsize : %r" % storage.totalsize)
        container_info.storage = storage

    @expect_exception(NotFound)
    def test_no_details_bogus_account(self):
        dbaas.accounts.show('asd#4#@fasdf')

    def test_no_details_empty_account(self):
        account_info = dbaas.accounts.show(container_info.user.auth_user)
        self.assertEqual(0, len(account_info.hosts))

@test(depends_on_classes=[ContainerHostCheck], groups=[GROUP, GROUP_START])
class CreateContainer(unittest.TestCase):
    """Test to create a Database Container

    If the call returns without raising an exception this test passes.

    """

    @expect_exception(ClientException)
    def test_blank_size_create_error(self):
        dbaas.dbcontainers.create('blank_volume_value',
                                  container_info.dbaas_flavor_href,
                                  {'size': ''}, [])

    def test_create(self):
        global dbaas
        # give the services some time to start up
        time.sleep(2)

        databases = []
        databases.append({"name": "firstdb", "character_set": "latin2",
                          "collate": "latin2_general_ci"})
        container_info.volume = {'size': 2}

        container_info.initial_result = dbaas.dbcontainers.create(
                                            container_info.name,
                                            container_info.dbaas_flavor_href,
                                            container_info.volume,
                                            databases)
        result = container_info.initial_result
        container_info.id = result.id

        self.assertEqual(result.status, _dbaas_mapping[power_state.BUILDING])

        # checks to be sure these are not found in the result
        for attr in ['hostId', 'imageRef', 'metadata', 'adminPass', 'uuid',
                     'volumes', 'addresses']:
            self.assertFalse(hasattr(result, attr),
                            "Create response should not contain %r." % attr)
        # checks to be sure these are found in the result
        for attr in ['flavor', 'id', 'name', 'status', 'links', 'volume']:
            self.assertTrue(hasattr(result, attr),
                            "Create response should contain %r attribute." % attr)

    def test_security_groups_created(self):
        if not db.security_group_exists(context.get_admin_context(), "dbaas", "tcp_3306"):
            self.assertFalse(True, "Security groups did not get created")


@test(depends_on_classes=[CreateContainer], groups=[GROUP, GROUP_START, 'dbaas.mgmt.hosts_post_install'])
class AccountMgmtData(unittest.TestCase):
    def test_account_details_available(self):
        account_info = dbaas.accounts.show(container_info.user.auth_user)
        self.assertNotEqual(0, len(account_info.hosts))
        # Now check the results.
        self.assertEqual(account_info.name, container_info.user.auth_user)
        # Containers: Here we know we've only created one host.
        self.assertEqual(1, len(account_info.hosts))
        self.assertEqual(1, len(account_info.hosts[0]['dbcontainers']))
        # We know that the host should contain only one container.
        container = account_info.hosts[0]['dbcontainers'][0]['dbcontainer']
        print("dbcontainers in account: %s" % container)
        self.assertEqual(container['id'], container_info.id)
        self.assertEqual(container['name'], container_info.name)

@test(depends_on_classes=[CreateContainer], groups=[GROUP, GROUP_START])
class VerifyGuestStarted(unittest.TestCase):
    """
        Test to verify the guest container is started and we can get the init
        process pid.
    """

    @time_out(60 * 8)
    def test_container_created(self):
        while True:
            status, err = process("sudo vzctl status %s | awk '{print $5}'"
                                  % str(container_info.id))

            if not string_in_list(status, ["running"]):
                time.sleep(5)
            else:
                self.assertEqual("running", status.strip())
                break


    @time_out(60 * 10)
    def test_get_init_pid(self):
        while True:
            out, err = process("pstree -ap | grep init | cut -d',' -f2 | vzpid - | grep %s | awk '{print $1}'"
                                % str(container_info.id))
            container_info.pid = out.strip()
            if not container_info.pid:
                time.sleep(10)
            else:
                break

    def test_guest_status_db_building(self):
        result = dbapi.guest_status_get(container_info.id)
        self.assertEqual(result.state, power_state.BUILDING)

    def test_guest_started_get_container(self):
        result = dbaas.dbcontainers.get(container_info.id)
        self.assertEqual(_dbaas_mapping[power_state.BUILDING], result.status)


@test(depends_on_classes=[VerifyGuestStarted], groups=[GROUP, GROUP_START])
class WaitForGuestInstallationToFinish(unittest.TestCase):
    """
        Wait until the Guest is finished installing.  It takes quite a while...
    """

    @time_out(60 * 8)
    def test_container_created(self):
        #/vz/private/1/var/log/nova/nova-guest.log
        while True:
            status, err = process(
                """grep "Dbaas" /vz/private/%s/var/log/nova/nova-guest.log"""
                % str(container_info.id))
            guest_status = dbapi.guest_status_get(container_info.id)
            if guest_status.state != power_state.RUNNING:
                result = dbaas.dbcontainers.get(container_info.id)
                # I think there's a small race condition which can occur
                # between the time you grab "guest_status" and "result," so
                # RUNNING is allowed in addition to BUILDING.
                self.assertTrue(
                    result.status == _dbaas_mapping[power_state.BUILDING] or
                    result.status == _dbaas_mapping[power_state.RUNNING],
                    "Result status was %s" % result.status)
                time.sleep(5)
            else:
                break


@test(depends_on_classes=[WaitForGuestInstallationToFinish], groups=[GROUP, GROUP_START])
class TestGuestProcess(unittest.TestCase):
    """
        Test that the guest process is started with all the right parameters
    """

    @time_out(60 * 10)
    def test_guest_process(self):
        init_proc = re.compile("[\w\W\|\-\s\d,]*nova-guest --flagfile=/etc/nova/nova.conf nova[\W\w\s]*")
        guest_proc = re.compile("[\w\W\|\-\s]*/usr/bin/nova-guest --flagfile=/etc/nova/nova.conf[\W\w\s]*")
        apt = re.compile("[\w\W\|\-\s]*apt-get[\w\W\|\-\s]*")
        while True:
            guest_process, err = process("pstree -ap %s | grep nova-guest"
                                            % container_info.pid)
            if not string_in_list(guest_process, ["nova-guest"]):
                time.sleep(10)
            else:
                if apt.match(guest_process):
                    time.sleep(10)
                else:
                    init = init_proc.match(guest_process)
                    guest = guest_proc.match(guest_process)
                    if init and guest:
                        self.assertTrue(True, init.group())
                    else:
                        self.assertFalse(False, guest_process)
                    break

    @time_out(130)
    def test_guest_status_db_running(self):
        state = power_state.BUILDING
        while state != power_state.RUNNING:
            time.sleep(10)
            result = dbapi.guest_status_get(container_info.id)
            state = result.state
        time.sleep(1)
        self.assertEqual(state, power_state.RUNNING)


    def test_guest_status_get_container(self):
        result = dbaas.dbcontainers.get(container_info.id)
        self.assertEqual(_dbaas_mapping[power_state.RUNNING], result.status)


@test(depends_on_classes=[CreateContainer], groups=[GROUP, GROUP_START, "nova.volumes.container"])
class TestVolume(unittest.TestCase):
    """Make sure the volume is attached to container correctly."""

    def test_db_should_have_instance_to_volume_association(self):
        """The compute manager should associate a volume to the instance."""
        volumes = db.volume_get_all_by_instance(context.get_admin_context(), 
                                                container_info.id)
        self.assertEqual(1, len(volumes))
        description = "Volume ID: %s assigned to Instance: %s" \
                        % (volumes[0]['id'], container_info.id)
        self.assertEqual(description, volumes[0]['display_description'])


@test(depends_on_classes=[WaitForGuestInstallationToFinish], groups=[GROUP, GROUP_START, "dbaas.listing"])
class TestContainerListing(unittest.TestCase):
    """ Test the listing of the container information """

    def test_detail_list(self):
        containers = dbaas.dbcontainers.details()
        for container in containers:
            self._detail_dbcontainers_exist(container)
            self._dbcontainers_attributes_should_not_exist(container)

    def test_index_list(self):
        containers = dbaas.dbcontainers.index()
        for container in containers:
            self._index_dbcontainers_exist(container)
            self._index_dbcontainers_attrs_should_not_exist(container)
            self._dbcontainers_attributes_should_not_exist(container)

    def test_get_container(self):
        container = dbaas.dbcontainers.get(container_info.id)
        self._assert_dbcontainers_exist(container)
        self._dbcontainers_attributes_should_not_exist(container)

    def test_get_container_status(self):
        result = dbaas.dbcontainers.get(container_info.id)
        self.assertEqual(_dbaas_mapping[power_state.RUNNING], result.status)

    def test_get_legacy_status(self):
        result = dbaas.dbcontainers.get(container_info.id)
        self.assertTrue(result is not None)

    def test_get_legacy_status_notfound(self):
        self.assertRaises(NotFound, dbaas.dbcontainers.get, -2)

    # Calling has_key on the container triggers a lazy load.
    # We don't want that here, so the next two methods use the _info dict.

    def _check_attr_in_dbcontainers(self, container, attrs):
        for attr in attrs:
            msg = "Missing attribute %r" % attr
            self.assertTrue(container._info.has_key(attr), msg)

    def _check_should_not_show_attr_in_dbcontainers(self, container, attrs):
        for attr in attrs:
            msg = "Attribute %r should not be returned" % attr
            self.assertFalse(container._info.has_key(attr), msg)

    def _detail_dbcontainers_exist(self, container):
        attrs = ['status', 'name', 'links', 'id', 'volume', 'rootEnabled']
        self._check_attr_in_dbcontainers(container, attrs)

    def _dbcontainers_attributes_should_not_exist(self, container):
        attrs = ['hostId', 'imageRef', 'metadata', 'adminPass', 'uuid',
                 'volumes', 'addresses']
        self._check_should_not_show_attr_in_dbcontainers(container, attrs)

    def _index_dbcontainers_exist(self, container):
        attrs = ['id', 'name', 'links', 'status']
        self._check_attr_in_dbcontainers(container, attrs)

    def _index_dbcontainers_attrs_should_not_exist(self, container):
        attrs = ['flavorRef', 'rootEnabled', 'volume']
        self._check_should_not_show_attr_in_dbcontainers(container, attrs)

    def test_volume_found(self):
        container = dbaas.dbcontainers.get(container_info.id)
        self.assertEqual(container_info.volume['size'], container.volume['size'])

    def _assert_dbcontainers_exist(self, container):
        self.assertEqual(container_info.id, container.id)
        attrs = ['name', 'links', 'id', 'flavor', 'rootEnabled', 'status',
                 'volume']
        self._check_attr_in_dbcontainers(container, attrs)
        dns_entry = container_info.expected_dns_entry()
        if dns_entry:
            self.assertEqual(dns_entry.name, container.hostname)


@test(depends_on_classes=[CreateContainer], groups=[GROUP, "dbaas.mgmt.listing"])
class MgmtHostCheck(unittest.TestCase):
    def test_index_host_list(self):
        myresult = dbaas.hosts.index()
        self.assertNotEqual(myresult, None,
                            "list hosts should not be empty")
        self.assertTrue(len(myresult) > 0,
                        "list hosts should not be empty")
        print("test_index_host_list result: %s" % str(myresult))
        print("test_index_host_list result instance_count: %d" %
              myresult[0].instanceCount)
        self.assertEquals(myresult[0].instanceCount, 1,
                          "instance count of 'host' should have 1 running instances")
        self.assertEquals(len(myresult), 1,
                          "The result list is expected to be of length 1")
        for index, host in enumerate(myresult, start=1):
            print("%d host: %s" % (index, host))
            container_info.host = host

    def test_index_host_list_single(self):
        myresult = dbaas.hosts.get(container_info.host)
        self.assertNotEqual(myresult, None,
                            "list hosts should not be empty")
        print("test_index_host_list_single result: %s" %
              str(myresult))
        self.assertTrue(len(myresult.dbcontainers) > 0,
                        "dbcontainer list on the host should not be empty")
        self.assertTrue(myresult.totalRAM == container_info.host_info.totalRAM,
                        "totalRAM should be the same as before : %r == %r" %
                        (myresult.totalRAM, container_info.host_info.totalRAM))
        diff = container_info.host_info.usedRAM + container_info.dbaas_flavor.ram
        self.assertTrue(myresult.usedRAM == diff,
                        "usedRAM should be : %r == %r" %
                        (myresult.usedRAM, diff))
        calc = round(1.0 * myresult.usedRAM / myresult.totalRAM * 100)
        self.assertTrue(myresult.percentUsed == calc,
                        "percentUsed should be : %r == %r" %
                        (myresult.percentUsed, calc))
        print("test_index_host_list_single result dbcontainers: %s" %
              str(myresult.dbcontainers))
        for index, container in enumerate(myresult.dbcontainers, start=1):
            print("%d dbcontainer: %s" % (index, container))

    def test_storage_on_host(self):
        storage = dbaas.storage.index()
        print("storage : %r" % storage)
        self.assertTrue(hasattr(storage, 'name'))
        self.assertTrue(hasattr(storage, 'availablesize'))
        self.assertTrue(hasattr(storage, 'totalsize'))
        print("storage : %r" % storage.__dict__)
        print("container_info.dbaas_flavor : %r" % container_info.dbaas_flavor.__dict__)
        print("container_info.storage : %r" % container_info.storage.__dict__)
        self.assertEquals(storage.name, container_info.storage.name)
        self.assertEquals(storage.totalsize, container_info.storage.totalsize)
        avail = container_info.storage.availablesize - container_info.volume['size']
        self.assertEquals(storage.availablesize, avail)

    def test_account_details_available(self):
        account_info = dbaas.accounts.show(container_info.user.auth_user)
        self.assertNotEqual(0, len(account_info.hosts))

@test(depends_on_groups=[GROUP_TEST], groups=[GROUP, GROUP_STOP])
class DeleteContainer(unittest.TestCase):
    """ Delete the created container """

    @time_out(3 * 60)
    def test_delete(self):
        global dbaas
        if not hasattr(container_info, "initial_result"):
            raise SkipTest("Container was never created, skipping test...")
        dbaas.dbcontainers.delete(container_info.id)

        attempts = 0
        try:
            time.sleep(1)
            result = True
            while result is not None:
                attempts += 1
                result = dbaas.dbcontainers.get(container_info.id)
                self.assertEqual(_dbaas_mapping[power_state.SHUTDOWN], result.status)
        except NotFound:
            pass
        except Exception as ex:
            self.fail("A failure occured when trying to GET container %s"
                      " for the %d time: %s" %
                      (str(container_info.id), attempts, str(ex)))


@test(depends_on_classes=[DeleteContainer], groups=[GROUP, GROUP_STOP])
class ContainerHostCheck2(ContainerHostCheck):
    """Class to run tests after delete"""

    @expect_exception(Exception)
    def test_host_not_found(self):
        container_info.myresult = dbaas.hosts.get('host-dne')

    @expect_exception(Exception)
    def test_host_not_found(self):
        container_info.myresult = dbaas.hosts.get('host@$%3dne')

    def test_no_details_empty_account(self):
        account_info = dbaas.accounts.show(container_info.user.auth_user)
        # Containers were created and then deleted or crashed.
        # In the process, one host was created.
        self.assertEqual(1, len(account_info.hosts))


@test(depends_on_classes=[CreateContainer, VerifyGuestStarted,
    WaitForGuestInstallationToFinish], groups=[GROUP, GROUP_START])
def management_callback():
    global mgmt_details
    mgmt_details = dbaas.management.show(container_info.id)


@test(depends_on=[management_callback], groups=[GROUP])
class VerifyContainerMgmtInfo(unittest.TestCase):

    def _assert_key(self, k, expected):
        v = getattr(mgmt_details, k)
        err = "Key %r does not match expected value of %r (was %r)." % (k, expected, v)
        self.assertEqual(str(v), str(expected), err)

    def test_id_matches(self):
        self._assert_key('id', container_info.id)

    def test_bogus_container_mgmt_data(self):
        # Make sure that a management call to a bogus API 500s.
        # The client reshapes the exception into just an OpenStackException.
        #self.assertRaises(nova.exception.InstanceNotFound, dbaas.management.show, -1)
        self.assertRaises(NotFound, dbaas.management.show, -1)
    
    def test_mgmt_data(self):
        # Test that the management API returns all the values we expect it to.
        info = container_info
        ir = info.initial_result
        cid = ir.id
        volumes = db.volume_get_all_by_instance(context.get_admin_context(), cid)
        self.assertEqual(len(volumes), 1)
        volume = volumes[0]

        expected = {
            'id': str(ir.id),
            'name': ir.name,
            'account_id': info.user.auth_user,
            # TODO(hub-cap): fix this since its a flavor object now
            #'flavorRef': info.dbaas_flavor_href,
            'databases': [{
                'name': 'firstdb',
                'character_set': 'latin2',
                'collate': 'latin2_general_ci',
                }],
            'users': [], # TODO(ed-) Surely I can't guarantee this.
            'volume': {
                'id': volume.id,
                'name': volume.display_name,
                'size': volume.size,
                'description': volume.display_description,
                },
            }

        expected_entry = info.expected_dns_entry()
        if expected_entry:
            expected['hostname'] = expected_entry.name

        self.assertTrue(mgmt_details is not None)
        failures = []
        for (k,v) in expected.items():
            self.assertTrue(hasattr(mgmt_details, k), "Attr %r is missing." % k)
            self.assertEqual(getattr(mgmt_details, k), v,
                "Attr %r expected to be %r but was %r." %
                (k, v, getattr(mgmt_details, k)))

