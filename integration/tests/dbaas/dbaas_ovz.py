import gettext
import os
import subprocess
import sys
import time
import re
import unittest

from sqlalchemy import create_engine
from sqlalchemy.sql.expression import text

from reddwarfclient import Dbaas
from nova.guest.dbaas import LocalSqlClient
from novaclient.exceptions import NotFound
from proboscis import test
from proboscis.decorators import expect_exception
from tests.dbaas.containers import container_info
from tests.dbaas.containers import GROUP_START
from tests.dbaas.containers import GROUP_TEST
from tests.util import check_database
from tests.util import init_engine
from tests.util import process
from tests.util import string_in_list
from tests.util import test_config
from tests.util.users import Requirements
from tests import util

dbaas = None
success_statuses = ["build", "active"]


@test(depends_on_groups=[GROUP_START])
class Setup(unittest.TestCase):
    """Creates the client."""

    def test_create_dbaas_client(self):
        """Sets up the client."""
        global dbaas
        dbaas = util.create_dbaas_client(container_info.user)


@test(depends_on_classes=[Setup], groups=[GROUP_TEST, "dbaas.guest.mysql"])
class TestMysqlAccess(unittest.TestCase):
    """
        Test Access to the mysql server as os_admin and root
    """

    def setUp(self):
        ip, err = process("""sudo vzctl exec %s ifconfig eth0 | grep 'inet addr' """
                           """| awk '{gsub(/addr:/, "");print $2}' """
                            % container_info.id)
        if err:
            self.assertFalse(True, err)
        container_info.ip = ip.strip()

    def _mysql_error_handler(self, err):
        pos_error = re.compile("ERROR 1130 \(HY000\): Host '[\w\.]*' is not allowed to connect to this MySQL server")
        pos_error1 = re.compile("ERROR 1045 \(28000\): Access denied for user '[\w]*'@'[\w\.]*' \(using password: (YES|NO)\)")
        neg_error = re.compile("ERROR 2003 \(HY000\): Can't connect to MySQL server on *")
        if pos_error.match(err) or pos_error1.match(err):
            self.assertTrue(True, err)
        elif neg_error.match(err):
            self.assertFalse(True, err)
        else:
            raise RuntimeError(err)

    def test_mysql_admin(self):
        while True:
            mysqld, err = process("pstree -a %s | grep mysqld" % container_info.pid)
            if not string_in_list(mysqld, ["mysqld"]):
                time.sleep(10)
            else:
                time.sleep(10)
                out, err = process("mysql -h %s -u os_admin -pasdfd-asdf234"
                                    % container_info.ip)
                self._mysql_error_handler(err)
                break

    def test_mysql_root(self):
        out, err = process("mysql -h %s -u root -pdsfgnear" % container_info.ip)
        self._mysql_error_handler(err)

    def test_zfirst_db(self):
        if container_info.check_database("firstdb"):
            self.assertTrue(True)
        else:
            self.assertFalse(True)


@test(depends_on_classes=[TestMysqlAccess], groups=[GROUP_TEST,
                                                    "dbaas.guest.mysql"])
class TestDatabases(unittest.TestCase):
    """
    Test the creation and deletion of additional MySQL databases
    """

    dbname = "third #?@some_-"
    dbname_regex = "third\s\#\?\@some\_\-"
    dbname_urlencoded = "third%20%23%3F%40some_-"

    dbname2 = "seconddb"
    created_dbs = [dbname, dbname2]
    system_dbs = ['information_schema','mysql']

    def test_create_database(self):
        databases = list()
        databases.append({"name": self.dbname, "charset": "latin2",
                          "collate": "latin2_general_ci"})
        databases.append({"name": self.dbname2})

        dbaas.databases.create(container_info.id, databases)
        time.sleep(5)

        if container_info.check_database(self.dbname_regex):
            self.assertTrue(True)
        else:
            self.assertFalse(True)

    def test_create_database_list(self):
        databases = dbaas.databases.list(container_info.id)
        found = False
        for db in self.created_dbs:
            for result in databases:
                if result.name == db:
                    found = True
            self.assertTrue(found, "Database '%s' not found in result" %db)
            found = False

    def test_create_database_list_system(self):
        #Databases that should not be returned in the list
        databases = dbaas.databases.list(container_info.id)
        found = False
        for db in self.system_dbs:
            found = any(result.name == db for result in databases)
            self.assertFalse(found, "Database '%s' SHOULD NOT be found in result" %db)
            found = False
            
    @expect_exception(NotFound)
    def test_create_database_on_missing_container(self):
        databases = [{"name": "invalid_db", "charset": "latin2",
                      "collate": "latin2_general_ci"}]
        dbaas.databases.create(-1, databases)

    @expect_exception(NotFound)
    def test_delete_database_on_missing_container(self):
        global dbaas
        dbaas.databases.delete(-1,  self.dbname_urlencoded)

    def test_delete_database(self):
        global dbaas
        dbaas.databases.delete(container_info.id, self.dbname_urlencoded)
        time.sleep(5)

        if not container_info.check_database(self.dbname):
            self.assertTrue(True)
        else:
            self.assertFalse(True)


@test(depends_on_classes=[TestDatabases], groups=[GROUP_TEST,
                                                  "dbaas.guest.mysql"])
class TestUsers(unittest.TestCase):
    """
    Test the creation and deletion of users
    """

    username = "tes!@#tuser"
    username_urlencoded = "tes%21%40%23tuser"
    password = "testpa$^%ssword"
    username1 = "anous*&^er"
    username1_urlendcoded = "anous%2A%26%5Eer"
    password1 = "anopas*?.sword"
    db1 = "firstdb"
    db2 = "seconddb"

    created_users = [username, username1]
    system_users = ['root', 'debian_sys_maint']

    def test_create_users(self):
        users = list()
        users.append({"name": self.username, "password": self.password,
                      "database": self.db1})
        users.append({"name": self.username1, "password": self.password1,
                     "databases": [{"name": self.db1}, {"name": self.db2}]})

        global dbaas
        dbaas.users.create(container_info.id, users)
        time.sleep(5)

        self.check_database_for_user(self.username, self.password,
                                    [self.db1])
        self.check_database_for_user(self.username1, self.password1,
                                    [self.db1, self.db2])

    def test_create_users_list(self):
        #tests for users that should be listed
        users = dbaas.users.list(container_info.id)
        found = False
        for user in self.created_users:
            for result in users:
                if user == result.name:
                    found = True
            self.assertTrue(found, "User '%s' not found in result" %user)
            found = False

    def test_create_users_list_system(self):
        #tests for users that should not be listed
        users = dbaas.users.list(container_info.id)
        found = False
        for user in self.system_users:
            found = any(result.name == user for result in users)
            self.assertFalse(found, "User '%s' SHOULD NOT BE found in result" %user)
            found = False

    def test_delete_users(self):
        global dbaas
        dbaas.users.delete(container_info.id, self.username_urlencoded)
        dbaas.users.delete(container_info.id, self.username1_urlendcoded)
        time.sleep(5)

        self._check_connection(self.username, self.password)
        self._check_connection(self.username1, self.password1)

    def check_database_for_user(self, user, password, dbs):
        dblist, err = process("sudo mysql    -h %s -u '%s' -p'%s' -e 'show databases;'"
                                % (container_info.ip, user, password))
        if err:
            self.assertFalse(True, err)
        for db in dbs:
            default_db = re.compile("[\w\n]*%s[\w\n]*" % db)
            if not default_db.match(dblist):
                self.assertFalse(True, dblist)
        self.assertTrue(True)

    def _check_connection(self, username, password):
        pos_error = re.compile("ERROR 1130 \(HY000\): Host '[\w\.]*' is not allowed to connect to this MySQL server")
        dblist, err = process("sudo mysql -h %s -u '%s' -p'%s' -e 'show databases;'"
                                % (container_info.ip, username, password))
        if pos_error.match(err):
            self.assertTrue(True)
        else:
            self.assertFalse(True, err)

    def _root(self):
        global dbaas
        global root_password
        host = "%"
        user, password = dbaas.root.create(container_info.id)

        engine = init_engine(user, password, container_info.ip)
        client = LocalSqlClient(engine)
        with client:
            t = text("""SELECT User, Host FROM mysql.user WHERE User=:user AND Host=:host;""")
            result = client.execute(t, user=user, host=host)
            for row in result:
                self.assertEquals(user, row['User'])
                self.assertEquals(host, row['Host'])
        root_password = password

    def test_enable_root(self):
        self._root()

    def test_reset_root(self):
        self._root()

    def test_reset_root_user_enabled(self):
        created_users= ['root']
        self.system_users.remove('root')
        users = dbaas.users.list(container_info.id)
        found = False
        for user in created_users:
            found = any(result.name == user for result in users)
            self.assertTrue(found, "User '%s' not found in result" %user)
            found = False

        found = False
        for user in self.system_users:
            found = any(result.name == user for result in users)
            self.assertFalse(found, "User '%s' SHOULD NOT BE found in result" %user)
            found = False


    def test_zdisable_root(self):
        global dbaas
        global root_password
        user = "root"
        host = "%"
        dbaas.root.delete(container_info.id)

        try:
            engine = init_engine(user, root_password, container_info.ip)
            client = LocalSqlClient(engine)
            with client:
                t = text("""SELECT * FROM mysql.user where User=:user, Host=:host;""")
                client.execute(t, user=user, host=host)
            self.fail("Should have raised exception.")
        except:
            pass
