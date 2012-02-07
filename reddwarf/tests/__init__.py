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
import os
import shutil

from nova.api import openstack
from nova import flags
from nova.db import migration as nova_migration


database_file = "reddwarf_test.sqlite"
clean_db = "clean.sqlite"
reddwarf_db_version = 6

FLAGS = flags.FLAGS


def setup():
    FLAGS.Reset()
    FLAGS['sql_connection'].SetDefault("sqlite:///%s" % database_file)
    FLAGS['allow_admin_api'].SetDefault("True")
    if os.path.exists(database_file):
        os.remove(database_file)
    if os.path.exists(clean_db):
        os.remove(clean_db)
    nova_migration.db_sync()
    shutil.copy(database_file, clean_db)

    from reddwarf.tests import util
    util.reset_database()
    util.db_sync()
