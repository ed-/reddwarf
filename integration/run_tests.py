#!/usr/bin/env python
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# # Copyright (c) 2011 OpenStack, LLC.
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


"""Runs the tests.

This script imports all other tests to make them known to Proboscis before
passing control to proboscis.TestProgram.

"""


from __future__ import absolute_import
import atexit
import gettext
import os
import sys


def add_support_for_localization():
    """Adds support for localization in the logging.

    If ../nova/__init__.py exists, add ../ to Python search path, so that
    it will override what happens to be installed in
    /usr/(local/)lib/python...

    """
    path = os.path.join(os.path.abspath(sys.argv[0]), os.pardir, os.pardir)
    possible_topdir = os.path.normpath(path)
    if os.path.exists(os.path.join(possible_topdir, 'nova', '__init__.py')):
        sys.path.insert(0, possible_topdir)

    gettext.install('nova', unicode=1)


def _clean_up():
    """Shuts down any services this program has started."""
    from tests.util.services import get_running_services
    for service in get_running_services():
        sys.stderr.write("Stopping service " + str(service.cmd) + "...\n\r")
        service.stop()

if __name__ == '__main__':

    add_support_for_localization()

    # Strip non-nose arguments out before passing this to nosetests
    nose_args = []
    conf_file = "~/nemesis.conf"
    groups = []
    for arg in sys.argv[1:]:
        if arg[:7] == "--conf=":
            conf_file = os.path.expanduser(arg[7:])
            print("Setting NEMESIS_CONF to " + conf_file)
            os.environ["NEMESIS_CONF"] = conf_file
        elif arg[:8] == "--group=":
            groups.append(arg[8:])
        elif arg[:11] == "--flagfile=":
            pass  # Don't append this...
        else:
            nose_args.append(arg)

    # TODO(tim.simpson):
    # Before Proboscis was at its current state, it was necessary to load the
    # configuration as an environment variable because decorators where used
    # to show tests depended on running services, and these needed the
    # configuration values.  However, now there is probably a better way to
    # handle this than forcing this to be imported after the environment
    # variable is set.

    # Set up the flag file values, which we need to call certain Nova code.
    from tests.util import test_config
    nova_conf = test_config.values["nova_conf"]

    from nova import utils
    utils.default_flagfile(str(nova_conf))

    from nova import flags
    FLAGS = flags.FLAGS
    FLAGS(sys.argv)

    # Now that the FlagFiles and other args have been parsed, time to import
    # everything.

    import proboscis
    from tests import initialize
    from tests.dbaas import dbaas_ovz
    from tests.dbaas import dns

    from tests.guest import dbaas_tests
    from tests.guest import pkg_tests
    
    from tests.volumes import direct

    proboscis.register(groups=["host.ovz"], depends_on_groups=[
        "dbaas.guest",
        "dbaas.guest.dns"
        ])
    proboscis.register(groups=["guest"], depends_on_groups=[
        pkg_tests.GROUP,
        dbaas_tests.GROUP
        ])
    atexit.register(_clean_up)
    proboscis.TestProgram(argv=nose_args, groups=groups)

