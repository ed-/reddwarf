
from numbers import Number
import os
import time
import unittest

import pexpect

from nova import context
from nova import volume
from nova.exception import VolumeNotFound

from proboscis import test
from proboscis.decorators import expect_exception
from tests import initialize
from tests.volumes import VOLUMES_DIRECT
from tests.util import test_config

# Add volume
# Check for volume by connecting to it
# delete volume
# check its gone


class StoryDetails(object):

    def __init__(self):
        self.api = volume.API()
        self.client = volume.Client()
        self.device_path = None
        self.volume_desc = None
        self.volume_id = None
        self.volume_name = None
        self.volume = None

    @property
    def mount_point(self):
        return "%s/%s" % (LOCAL_MOUNT_PATH, self.volume_id)

    @property
    def test_mount_file_path(self):
        return "%s/test.txt" % self.mount_point


story = None

LOCAL_MOUNT_PATH = "/testsmnt"

class VolumeTest(unittest.TestCase):
    """This test tells the story of a volume, from cradle to grave."""

    def __init__(self, *args, **kwargs):
        unittest.TestCase.__init__(self, *args, **kwargs)        

    def setUp(self):
        global story
        self.story = story

    def assert_volume_as_expected(self, volume):
        self.assertTrue(isinstance(volume["id"], Number))
        self.assertTrue(volume["display_name"], self.story.volume_name)
        self.assertTrue(volume["display_description"], self.story.volume_desc)

@test(groups=VOLUMES_DIRECT, depends_on_classes=[initialize.Volume])
class SetUp(VolumeTest):

    def test_go(self):
        global story
        story = StoryDetails()
        if os.path.exists(LOCAL_MOUNT_PATH):
            os.rmdir(LOCAL_MOUNT_PATH)
        os.mkdir(LOCAL_MOUNT_PATH)
        # Give some time for the services to startup
        time.sleep(10)


@test(groups=VOLUMES_DIRECT, depends_on_classes=[SetUp])
class AddVolume(VolumeTest):

    def test_add(self):
        self.assertEqual(None, self.story.volume_id)
        name = "TestVolume"
        desc = "A volume that was created for testing."
        self.story.volume_name = name
        self.story.volume_desc = desc
        volume = self.story.api.create(context.get_admin_context(), size = 1,
                                       name=name, description=desc)
        self.assert_volume_as_expected(volume)
        self.story.volume = volume
        self.story.volume_id = volume["id"]


@test(groups=VOLUMES_DIRECT, depends_on_classes=[AddVolume])
class AfterVolumeIsAdded(VolumeTest):
    """Check that the volume can be retrieved via the API, and setup.

    All we want to see returned is a list-like with an initial string.

    """

    def test_api_get(self):
        volume = self.story.api.get(self.story.volume_id)
        self.assert_volume_as_expected(volume)

    def test_check(self):
        self.assertNotEqual(None, self.story.volume_id)
        device = self.story.client.setup_volume(context.get_admin_context(),
                                                self.story.volume_id)
        self.assertTrue(isinstance(device, basestring))
        self.story.device_path = device


@test(groups=VOLUMES_DIRECT, depends_on_classes=[AfterVolumeIsAdded])
class FormatVolume(VolumeTest):

    def test_format(self):
        self.assertNotEqual(None, self.story.device_path)
        self.story.client.format(self.story.device_path)


@test(groups=VOLUMES_DIRECT, depends_on_classes=[FormatVolume])
class MountVolume(VolumeTest):

    def test_mount(self):
        self.story.client.mount(self.story.device_path, self.story.mount_point)
        with open(self.story.test_mount_file_path, 'w') as file:
            file.write("Yep, it's mounted alright.")
        self.assertTrue(os.path.exists(self.story.test_mount_file_path))


@test(groups=VOLUMES_DIRECT, depends_on_classes=[MountVolume])
class Unmount(VolumeTest):

    def test_unmount(self):
        self.story.client.unmount(self.story.mount_point)
        child = pexpect.spawn("mount %s" % self.story.mount_point)
        child.expect("mount: can't find %s in" % self.story.mount_point)


@test(groups=VOLUMES_DIRECT, depends_on_classes=[Unmount])
class RemoveVolume(VolumeTest):

    def test_remove(self):
        self.story.client.remove(context.get_admin_context(),
                                 self.story.volume_id)
        self.assertRaises(Exception,
                          self.story.client.format, self.story.device_path)


@test(groups=VOLUMES_DIRECT, depends_on_classes=[RemoveVolume])
class DeleteVolume(VolumeTest):

    def test_delete(self):
        self.story.api.delete(context.get_admin_context(), self.story.volume_id)


@test(groups=VOLUMES_DIRECT, depends_on_classes=[DeleteVolume])
class ConfirmMissing(VolumeTest):

    @expect_exception(VolumeNotFound)
    def test_get_missing_volume(self):
        self.story.api.get(self.story.volume_id)

    @expect_exception(Exception)
    def test_setup_missing_volume(self):
        self.story.client.setup_volume(context.get_admin_context(),
                                       self.story.volume_id)

    def test_discover_should_fail(self):
        self.fail("TODO: Add code to call the driver discover_volume method.")

