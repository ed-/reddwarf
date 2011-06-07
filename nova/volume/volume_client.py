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

import collections

from nova import db
from nova import exception
from nova import flags

LOG = logging.getLogger("nova.volume.volume_client")
FLAGS = flags.FLAGS
flags.DEFINE_string('volume_option', 'raw',
                    'Default type of the volume to be provided to compute driver')

VolumeOption = namedtuple("VolumeOption", ['format', 'mount'])

VOLUME_OPTIONS = {"raw": VolumeOption(False, False),
                "format": VolumeOption(True, False),
                "mount": VolumeOption(False, True),
                "format_mount": VolumeOption(True, True)}


class VolumeClient(object):
    """A helper class to perform the volume related activities on a compute node"""

    def __init__(self, volume_driver=FLAGS.volume_driver):
        # Add a driver
        self.driver = utils.import_object(volume_driver)
        if FLAGS.volume_option not in VOLUME_OPTIONS:
            raise ValueError("'%s' is not a valid volume_option"
                                    % FLAGS.volume_type)
        self.option = VOLUME_OPTIONS[FLAGS.volume_option]

    def setup_volume(self, context, volume_id):
        """Setup remote volume on compute host.

        Returns path to device."""
        context = context.elevated()
        volume_ref = db.volume_get(context, volume_id)
        return self.driver.discover_volume(context, volume_ref)

    def remove_volume(self, context, volume_id):
        """Remove remote volume on compute host."""
        context = context.elevated()
        volume_ref = db.volume_get(context, volume_id)
        self.driver.undiscover_volume(volume_ref)

    def format(self, device_path):
        """Format the specified device"""
        pass

    def mount(self, device_path, mountpoint):
        """Mount the specified device at the mountpoint"""
        pass
