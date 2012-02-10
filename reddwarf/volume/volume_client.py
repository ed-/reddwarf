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

import time


from nova import db
from nova import flags
from nova import log as logging
from nova import utils
from reddwarf.volume.api import API
from nova.db.base import Base


LOG = logging.getLogger("nova.volume.volume_client")
FLAGS = flags.FLAGS


class VolumeClient(Base):
    """A helper class to perform the volume activities on a compute node."""

    def __init__(self, volume_driver=FLAGS.volume_driver, *args, **kwargs):
        super(VolumeClient, self).__init__(*args, **kwargs)
        # Add a driver
        if isinstance(volume_driver, basestring):
            self.driver = utils.import_object(volume_driver)
        else:
            self.driver = volume_driver
        self.driver.db = self.db
        # Do initialization check if one is available.
        if hasattr(self.driver, "check_for_client_setup_error"):
            self.driver.check_for_client_setup_error()
        self.volume_api = API()

    def get_uuid(self, device_path):
        """Returns a UUID for a device given its mount point."""
        return self.driver.get_volume_uuid(device_path)

    def initialize(self, context, volume_id, host):
        """Discover the volume, format / mount it. Store UUID."""
        self.volume_api.assign_to_compute(context, volume_id, host)
        dev_path = self._setup_volume(context, volume_id, host)
        if not db.volume_get(context, volume_id)['uuid']:
            self._format(dev_path)
            uuid = self.get_uuid(dev_path)
            self.db.volume_update(context, volume_id, {'uuid': uuid})
        return dev_path

    def refresh(self, context, volume_id):
        """Discover and update volume information in database."""

    def _setup_volume(self, context, volume_id, host):
        """Setup remote volume on compute host.

        Returns path to device."""
        context = context.elevated()
        volume_ref = db.volume_get(context, volume_id)
        if volume_ref['host'] == host and FLAGS.use_local_volumes:
            path = self.driver.local_path(volume_ref)
        else:
            path = self.driver.discover_volume(context, volume_ref)
        return path

    def remove_volume(self, context, volume_id, host):
        """Remove remote volume on compute host."""
        context = context.elevated()
        volume_ref = db.volume_get(context, volume_id)
        if volume_ref['host'] == host and FLAGS.use_local_volumes:
            return True
        else:
            self.driver.undiscover_volume(volume_ref)
        self.volume_api.unassign_from_compute(context, volume_id, host)

    def _format(self, device_path):
        """Format the specified device"""
        self.driver.format(device_path)

    def _mount(self, device_path, mount_point):
        """Mount the specified device at the mount point"""
        self.driver.mount(device_path, mount_point)

    def _unmount(self, mount_point):
        """Unmount the filesystem at the mount point."""
        self.driver.unmount(mount_point)

    def resize_fs(self, context, volume_id):
        """
        Complete the volume resize operation on the compute host, by rescanning
        if necessary and resizing the filesystem
        """
        volume_ref = db.volume_get(context, volume_id)
        device_path = self.driver.rescan(volume_ref)
        self.driver.resize_fs(device_path)
