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

from proboscis import after_class
from proboscis import before_class
from proboscis import test
from proboscis.asserts import assert_equal
from proboscis.asserts import assert_false
from proboscis.asserts import assert_not_equal
from proboscis.asserts import assert_raises
from proboscis.asserts import assert_true
from sqlalchemy import create_engine
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy.sql.expression import text

from nova.compute import power_state
from reddwarf.api.common import dbaas_mapping
from reddwarf.guest.dbaas import LocalSqlClient
from reddwarf.utils import poll_until
from reddwarfclient.instances import REBOOT_HARD
import tests
from tests.api.instances import GROUP as INSTANCE_GROUP
from tests.api.instances import GROUP_START
from tests.api.instances import instance_info
from tests import util


GROUP = "dbaas.api.instances.actions"
MYSQL_USERNAME = "test_user"
MYSQL_PASSWORD = "abcde"


class MySqlConnection(object):

    def __init__(self, host):
        self.host = host

    def connect(self):
        """Connect to MySQL database."""
        self.client = LocalSqlClient(util.init_engine(
            MYSQL_USERNAME, MYSQL_PASSWORD, self.host), use_flush=False)

    def is_connected(self):
        try:
            with self.client:
                self.client.execute(text("""SELECT "Hello.";"""))
            return True
        except (sqlalchemy_exc.OperationalError,
                sqlalchemy_exc.DisconnectionError,
                sqlalchemy_exc.TimeoutError):
            return False
        except Exception as ex:
            print("EX WAS:")
            print(type(ex))
            print(ex)
            raise ex


TIME_OUT_TIME = 4 * 60

class RebootTestBase(object):
    """Tests restarting MySQL."""

    def call_reboot(self):
        raise NotImplementedError()

    @property
    def instance(self):
        return self.dbaas.instances.get(self.instance_id)

    @property
    def instance_local_id(self):
        return instance_info.get_local_id()

    @property
    def instance_id(self):
        return instance_info.id

    def find_mysql_proc_on_instance(self):
        return util.find_mysql_procid_on_instance(self.instance_local_id)

    def set_up(self):
        address = instance_info.get_address()
        assert_equal(1, len(address), "Instance must have one fixed ip.")
        self.connection = MySqlConnection(address[0])
        self.dbaas = instance_info.dbaas

    def create_user(self):
        """Create a MySQL user we can use for this test."""

        users = [{"name": MYSQL_USERNAME, "password": MYSQL_PASSWORD,
                  "database": MYSQL_USERNAME}]
        self.dbaas.users.create(instance_info.id, users)

    def ensure_mysql_is_running(self):
        """Make sure MySQL is accessible before restarting."""
        self.connection.connect()
        assert_true(self.connection.is_connected(), "Able to connect to MySQL.")
        self.proc_id = self.find_mysql_proc_on_instance()
        assert_true(self.proc_id is not None, "MySQL process can be found.")
        instance = self.instance
        assert_false(instance is None)
        assert_equal(instance.status, dbaas_mapping[power_state.RUNNING],
                     "REST API reports MySQL as RUNNING.")

    def wait_for_broken_connection(self):
        """Wait until our connection breaks."""
        poll_until(self.connection.is_connected,
                   lambda connected : not connected, time_out = TIME_OUT_TIME)

    def wait_for_successful_restart(self):
        """Wait until status becomes running."""
        def is_finished_rebooting():
            instance = self.instance
            if instance.status == "REBOOT":
                return False
            assert_equal("ACTIVE", instance.status)
            return True

        poll_until(is_finished_rebooting, time_out = TIME_OUT_TIME)

    def assert_mysql_proc_is_different(self):
        new_proc_id = self.find_mysql_proc_on_instance()
        assert_not_equal(new_proc_id, self.proc_id,
                         "MySQL process ID should be different!")

    def successful_restart(self):
        """Restart MySQL via the REST API successfully."""
        self.fix_mysql()
        self.call_reboot()
        self.wait_for_broken_connection()
        self.wait_for_successful_restart()
        self.assert_mysql_proc_is_different()

    def mess_up_mysql(self):
        """Ruin MySQL's ability to restart."""
        self.fix_mysql() # kill files
        cmd = """sudo vzctl exec %d 'echo "hi" > /var/lib/mysql/ib_logfile%d'"""
        for index in range(2):
            util.process(cmd % (self.instance_local_id, index))

    def fix_mysql(self):
        """Fix MySQL's ability to restart."""
        cmd = "sudo vzctl exec %d rm /var/lib/mysql/ib_logfile%d"
        for index in range(2):
            util.process(cmd % (self.instance_local_id, index))

    def wait_for_failure_status(self):
        """Wait until status becomes running."""
        def is_finished_rebooting():
            instance = self.instance
            if instance.status == "REBOOT":
                return False
            assert_equal("SHUTDOWN", instance.status)
            return True

        poll_until(is_finished_rebooting, time_out = TIME_OUT_TIME)

    def unsuccessful_restart(self):
        """Restart MySQL via the REST when it should fail, assert it does."""
        self.mess_up_mysql()
        self.call_reboot()
        self.wait_for_broken_connection()
        self.wait_for_failure_status()

    def restart_normally(self):
        """Fix iblogs and reboot normally."""
        self.fix_mysql()
        self.test_successful_restart()


@test(groups=[tests.INSTANCES, INSTANCE_GROUP, GROUP],
      depends_on_groups=[GROUP_START])
class RestartTests(RebootTestBase):
    """Tests restarting MySQL."""

    def call_reboot(self):
        self.instance.restart()

    @before_class
    def test_set_up(self):
        self.set_up()
        self.create_user()

    @test
    def test_ensure_mysql_is_running(self):
        """Make sure MySQL is accessible before restarting."""
        self.ensure_mysql_is_running()

    @test(depends_on=[test_ensure_mysql_is_running])
    def test_unsuccessful_restart(self):
        """Restart MySQL via the REST when it should fail, assert it does."""
        self.unsuccessful_restart()

    @after_class(always_run=True)
    def test_successful_restart(self):
        """Restart MySQL via the REST API successfully."""
        self.successful_restart()


@test(groups=[tests.INSTANCES, INSTANCE_GROUP, GROUP],
      depends_on_groups=[GROUP_START], depends_on=[RestartTests])
class RebootTests(RebootTestBase):
    """Tests restarting instance."""

    def call_reboot(self):
        instance_info.dbaas_admin.management.reboot(self.instance_id)

    @before_class
    def test_set_up(self):
        self.set_up()

    @test
    def test_ensure_mysql_is_running(self):
        """Make sure MySQL is accessible before restarting."""
        self.ensure_mysql_is_running()

    @test(depends_on=[test_ensure_mysql_is_running])
    def test_unsuccessful_restart(self):
        """Restart MySQL via the REST when it should fail, assert it does."""
        self.unsuccessful_restart()

    @after_class(always_run=True)
    def test_successful_restart(self):
        """Restart MySQL via the REST API successfully."""
        self.successful_restart()

@test(groups=[tests.INSTANCES, INSTANCE_GROUP, GROUP],
      depends_on_groups=[GROUP_START], depends_on=[RebootTests])
class ResizeInstanceTest(object):
    """
    Test cases for resize instance/volume
    1. {resize: {volume: {size: 2}}} - pass
    2. {resize: {volume: {}}}        - fail
    3. {resize: {}}                  - fail
    4. {resize: {flavorRef: 2}}      - pass
    5. {resize: {flavorRef: 2,
                 volume: {size: 2}}} - fail
    """
    @test
    def test_instance_resize(self):
        """Do some testing of resizing the instance."""
        pass
