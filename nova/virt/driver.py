# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Justin Santa Barbara
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

"""
Driver base-classes:

    (Beginning of) the contract that compute drivers must follow, and shared
    types that support that contract
"""

from nova.compute import power_state


class InstanceInfo(object):
    def __init__(self, name, state):
        self.name = name
        assert state in power_state.valid_states(), "Bad state: %s" % state
        self.state = state


class ComputeDriver(object):
    """Base class for compute drivers.

    Lots of documentation is currently on fake.py.
    """

    def init_host(self, host):
        """Adopt existing VM's running here"""
        raise NotImplementedError()

    def get_info(self, instance_name):
        """Get the current status of an instance, by name (not ID!)

        Returns a dict containing:
        :state:           the running state, one of the power_state codes
        :max_mem:         (int) the maximum memory in KBytes allowed
        :mem:             (int) the memory in KBytes used by the domain
        :num_cpu:         (int) the number of virtual CPUs for the domain
        :cpu_time:        (int) the CPU time used in nanoseconds
        """
        raise NotImplementedError()

    def list_instances(self):
        raise NotImplementedError()

    def list_instances_detail(self):
        """Return a list of InstanceInfo for all registered VMs"""
        raise NotImplementedError()

    def spawn(self, instance, network_info=None):
        """Launch a VM for the specified instance"""
        raise NotImplementedError()

    def destroy(self, instance, cleanup=True):
        """Destroy (shutdown and delete) the specified instance.

        The given parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.

        If the instance is not found (for example if networking failed), this
        function should still succeed.  It's probably a good idea to log a
        warning in that case.

        """
        raise NotImplementedError()

    def reboot(self, instance):
        """Reboot specified VM"""
        raise NotImplementedError()

    def snapshot_instance(self, context, instance_id, image_id):
        raise NotImplementedError()

    def get_console_pool_info(self, console_type):
        raise NotImplementedError()

    def get_console_output(self, instance):
        raise NotImplementedError()

    def get_ajax_console(self, instance):
        raise NotImplementedError()

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics"""
        raise NotImplementedError()

    def get_host_ip_addr(self):
        raise NotImplementedError()

    def attach_volume(self, context, instance_id, volume_id, mountpoint):
        raise NotImplementedError()

    def detach_volume(self, context, instance_id, volume_id):
        raise NotImplementedError()

    def compare_cpu(self, context, cpu_info):
        raise NotImplementedError()

    def migrate_disk_and_power_off(self, instance, dest):
        """Transfers the VHD of a running instance to another host, then shuts
        off the instance copies over the COW disk"""
        raise NotImplementedError()

    def use_volume(self, mountpoint):
        """Use the provided volume and deal with it"""
        raise  NotImoplementedError()

    def snapshot(self, instance, image_id):
        """Create snapshot from a running VM instance."""
        raise NotImplementedError()

    def finish_resize(self, instance, disk_info):
        """Completes a resize, turning on the migrated instance"""
        raise NotImplementedError()

    def revert_resize(self, instance):
        """Reverts a resize, powering back on the instance"""
        raise NotImplementedError()

    def pause(self, instance, callback):
        """Pause VM instance"""
        raise NotImplementedError()

    def unpause(self, instance, callback):
        """Unpause paused VM instance"""
        raise NotImplementedError()

    def suspend(self, instance, callback):
        """suspend the specified instance"""
        raise NotImplementedError()

    def resume(self, instance, callback):
        """resume the specified instance"""
        raise NotImplementedError()

    def rescue(self, instance, callback):
        """Rescue the specified instance"""
        raise NotImplementedError()

    def unrescue(self, instance, callback):
        """Unrescue the specified instance"""
        raise NotImplementedError()

    def update_available_resource(self, ctxt, host):
        """Updates compute manager resource info on ComputeNode table.

        This method is called when nova-compute launches, and
        whenever admin executes "nova-manage service update_resource".

        :param ctxt: security context
        :param host: hostname that compute manager is currently running

        """
        raise NotImplementedError()

    def live_migration(self, ctxt, instance_ref, dest,
                       post_method, recover_method):
        """Spawning live_migration operation for distributing high-load.

        :params ctxt: security context
        :params instance_ref:
            nova.db.sqlalchemy.models.Instance object
            instance object that is migrated.
        :params dest: destination host
        :params post_method:
            post operation method.
            expected nova.compute.manager.post_live_migration.
        :params recover_method:
            recovery method when any exception occurs.
            expected nova.compute.manager.recover_live_migration.

        """
        raise NotImplementedError()

    def refresh_security_group_rules(self, security_group_id):
        raise NotImplementedError()

    def refresh_security_group_members(self, security_group_id):
        raise NotImplementedError()

    def reset_network(self, instance):
        """reset networking for specified instance"""
        raise NotImplementedError()

    def ensure_filtering_rules_for_instance(self, instance_ref):
        """Setting up filtering rules and waiting for its completion.

        To migrate an instance, filtering rules to hypervisors
        and firewalls are inevitable on destination host.
        ( Waiting only for filtering rules to hypervisor,
        since filtering rules to firewall rules can be set faster).

        Concretely, the below method must be called.
        - setup_basic_filtering (for nova-basic, etc.)
        - prepare_instance_filter(for nova-instance-instance-xxx, etc.)

        to_xml may have to be called since it defines PROJNET, PROJMASK.
        but libvirt migrates those value through migrateToURI(),
        so , no need to be called.

        Don't use thread for this method since migration should
        not be started when setting-up filtering rules operations
        are not completed.

        :params instance_ref: nova.db.sqlalchemy.models.Instance object

        """
        raise NotImplementedError()

    def unfilter_instance(self, instance):
        """Stop filtering instance"""
        raise NotImplementedError()

    def set_admin_password(self, context, instance_id, new_pass=None):
        """Set the root/admin password for an instance on this server."""
        raise NotImplementedError()

    def inject_file(self, instance, b64_path, b64_contents):
        """Create a file on the VM instance. The file path and contents
        should be base64-encoded.
        """
        raise NotImplementedError()

    def inject_network_info(self, instance):
        """inject network info for specified instance"""
        raise NotImplementedError()

    def poll_rescued_instances(self, timeout):
        """Poll for rescued instances"""
        raise NotImplementedError()
