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

"""Handles configuration options for the tests."""

import json
import os

from tests.util.services import WebService

__all__ = [
    "dbaas",
    "dbaas_image",
    "glance_code_root",
    "nova",
    "nova_code_root",
    "python_cmd_list",
    "typical_nova_image_name",
    "users",
    "use_venv",
    "values",
]


def load_configuration():
    """Loads and returns the configuration file as a dictionary.

    The file to load is found by looking for a value in the environment
    variable NEMESIS_CONF.  The file itself is stored as JSON.

    """
    if not "NEMESIS_CONF" in os.environ:
        raise RuntimeError("Please define an environment variable named " +
                           "NEMESIS_CONF with the location to a conf file.")
    file_path = os.path.expanduser(os.environ["NEMESIS_CONF"])
    if not os.path.exists(file_path):
        raise RuntimeError("Could not find NEMESIS_CONF at " + file_path + ".")
    file_contents = open(file_path, "r").read()
    try:
        return json.loads(file_contents)
    except Exception as exception:
        raise RuntimeError("Error loading conf file \"" + file_path + "\".",
                           exception)


def glance_code_root():
    """The file path to the Glance source code."""
    return str(values.get("glance_code_root"))


def glance_images_directory():
    """The path to images that will be uploaded by Glance."""
    return str(values.get("glance_images_directory"))


def nova_code_root():
    """The path to the Nova source code."""
    return str(values.get("nova_code_root"))


def python_cmd_list():
    """The start of a command list to use when running Python scripts."""
    global use_venv
    global nova_code_root
    commands = []
    if use_venv:
        commands.append("%s/tools/with_venv.sh" % nova_code_root)
        return list
    commands.append("python")
    return commands


def _setup():
    """Initializes the module."""
    from tests.util.users import Users
    global dbaas
    global nova
    global users
    global dbaas_image
    global typical_nova_image_name
    global use_venv
    global values
    values = load_configuration()
    use_venv = values.get("use_venv", True)
    dbaas_url = str(values.get("dbaas_url", "http://localhost:8775/v1.0"))
    nova_url = str(values.get("nova_url", "http://localhost:8774/v1.0"))
    nova_code_root = str(values["nova_code_root"])
    nova_conf = str(values["nova_conf"])
    if not nova_conf:
        raise ValueError("Configuration value \"nova_conf\" not found.")

    dbaas = WebService(cmd=python_cmd_list() +
                           ["%s/bin/reddwarf-api" % nova_code_root,
                            "--flagfile=%s" % nova_conf],
                        url=dbaas_url)
    nova = WebService(cmd=python_cmd_list() +
                          ["%s/bin/nova-api" % nova_code_root,
                           "--flagfile=%s" % nova_conf],
                      url=nova_url)
    users = Users(values["users"])
    dbaas_image = values.get("dbaas_image", None)
    typical_nova_image_name = values.get("typical_nova_image_name", None)


_setup()
