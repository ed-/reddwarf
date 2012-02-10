# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 OpenStack LLC.
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
A driver specific to OpenVz as the support for Ovz in libvirt
is sketchy at best.
"""

import os
import fnmatch
import socket
from nova import db
from nova import exception
from nova import flags
from nova import log as logging
from nova import utils
from nova import context
from nova.auth import manager
from nova.network import linux_net
from nova.compute import power_state
from nova.compute import instance_types
from nova.exception import ProcessExecutionError
from nova.virt import images
from nova.virt import driver
from nova.virt.vif import VIFDriver
from nova.network import linux_net
from Cheetah.Template import Template

FLAGS = flags.FLAGS
flags.DEFINE_string('ovz_template_path',
                    '/var/lib/vz/template/cache',
                    'Path to use for local storage of OVz templates')
flags.DEFINE_string('ovz_ve_private_dir',
                    '/var/lib/vz/private',
                    'Path where VEs will get placed')
flags.DEFINE_string('ovz_ve_root_dir',
                    '/var/lib/vz/root',
                    'Path where the VEs root is')
flags.DEFINE_string('ovz_ve_host_mount_dir',
                    '/mnt',
                    'Path where outside mounts go')
flags.DEFINE_string('ovz_image_template_dir',
                    '/var/lib/vz/template/cache',
                    'Path where OpenVZ images are')
flags.DEFINE_string('ovz_config_dir',
                    '/etc/vz/conf',
                    'Where the OpenVZ configs are stored')
flags.DEFINE_string('ovz_bridge_device',
                    'br100',
                    'Bridge device to map veth devices to')
flags.DEFINE_bool('ovz_use_cpuunit',
                  True,
                  'Use OpenVz cpuunits for guaranteed minimums')
flags.DEFINE_bool('ovz_use_cpulimit',
                  True,
                  'Use OpenVz cpulimit for maximum cpu limits')
flags.DEFINE_bool('ovz_use_cpus',
                  True,
                  'Use OpenVz cpus for max cpus available to the container')
flags.DEFINE_bool('ovz_use_ioprio',
                  True,
                  'Use IO fair scheduling')
flags.DEFINE_integer('ovz_ioprio_limit',
                     7,
                     'Limit for IO priority weighting')
flags.DEFINE_bool('ovz_disk_space_oversub',
                  True,
                  'Allow over subscription of local disk')
flags.DEFINE_float('ovz_disk_space_oversub_percent',
                   1.10,
                   'Local disk over subscription percentage')
flags.DEFINE_string('ovz_disk_space_increment',
                    'G',
                    'Disk subscription increment')
flags.DEFINE_bool('ovz_use_disk_quotas',
                  True,
                  'Use disk quotas to contain disk usage')
flags.DEFINE_string('ovz_vif_driver',
                    'nova.virt.openvz_conn.OVZNetworkBridgeDriver',
                    'The openvz VIF driver to configures the VIFs')
flags.DEFINE_bool('ovz_use_veth_devs',
                  True,
                  'Use veth devices rather than venet')
flags.DEFINE_bool('ovz_use_dhcp',
                  False,
                  'Use dhcp for network configuration')
flags.DEFINE_string('ovz_mount_options',
                    'defaults',
                    'Mount options for external filesystems')
flags.DEFINE_integer('ovz_kmemsize_percent_of_memory',
                    20,
                    'Percent of memory of the container to \
                    allow to be used by the kernel')
flags.DEFINE_integer('ovz_kmemsize_barrier_differential',
                    10,
                    'Difference of kmemsize barrier vs limit')

LOG = logging.getLogger('nova.virt.openvz')


def get_connection(read_only):
    return OpenVzConnection(read_only)


class OpenVzConnection(driver.ComputeDriver):
    def __init__(self, read_only):
        """
        I create an instance of the openvz connection.
        """
        self.utility = {
                'CTIDS': {},
                'TOTAL': 0,
                'UNITS': 0,
                'MEMORY_MB': 0,
                'CPULIMIT': 0
            }
        self.read_only = read_only
        self.vif_driver = utils.import_object(FLAGS.ovz_vif_driver)
        LOG.debug(_('__init__ complete in OpenVzConnection'))

    @classmethod
    def instance(cls):
        """
        This is borrowed from the fake driver.
        """
        if not hasattr(cls, '_instance'):
            cls._instance = cls()
        return cls._instance

    def init_host(self, host=socket.gethostname()):
        """
        Initialize anything that is necessary for the driver to function,
        including catching up with currently running VE's on the given host.
        """
        ctxt = context.get_admin_context()

        LOG.debug(_('Hostname: %s') % host)
        LOG.debug(_('Instances: %s') % db.instance_get_all_by_host(ctxt, host))

        for instance in db.instance_get_all_by_host(ctxt, host):
            try:
                LOG.debug(_('Checking state of %s') % instance['name'])
                state = self.get_info(instance['name'])['state']
            except exception.NotFound:
                state = power_state.SHUTOFF

            LOG.debug(_('Current state of %(name)s was %(power_state)s') %
                    {'name': instance['name'], 'power_state': state})

            if state == power_state.SHUTOFF:
                db.instance_destroy(ctxt, instance['id'])

        LOG.debug(_('Determining the computing power of the host'))

        self._get_cpuunits_capability()
        self._get_cpulimit()
        self._get_memory()

        LOG.debug(_('init_host complete in OpenVzConnection'))

    def list_instances(self):
        """
        Return the names of all the instances known to the container
        layer, as a list.
        """
        try:
            out, err = utils.execute('vzlist', '--all', '--no-header',
                                     '--output', 'ctid', run_as_root=True)
            if err:
                LOG.error(_('Stderr output from vzlist: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzlist: %s') % err)
            raise exception.Error(_('Failed to list VZs'))

        ctids = []
        for line in out.splitlines():
            ctid = line.split()[0]
            ctids.append(ctid)

        return ctids

    def list_instances_detail(self):
        """
        Satisfy the requirement for this method in the manager codebase.
        This fascilitates the regular status polls that happen within the
        manager code.

        I execute the command:

        vzlist --all -o name -H

        If I fail to run an exception is raised because a failure to run is
        disruptive to the driver's ability to support the instances on
        the host through nova's interface.
        """

        # TODO(imsplitbit): need to ask around if this is the best way to do
        # this.  This causes some redundant vzlist commands as get_info is run
        # on every item returned from this command but it didn't make sense
        # to re-implement get_info as get_info_all.
        infos = []
        try:
            # get a list of CT names which will be nova friendly.
            # NOTE: This can be an issue if nova decides to change
            # the format of names.  We would need to have a migration process
            # to change the names in the name field of the CTs.
            out, err = utils.execute('vzlist', '--all', '-o',
                                     'name', '-H', run_as_root=True)
            if err:
                LOG.error(_('Stderr output from vzlist: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzlist: %s') % err)
            raise exception.Error(_('Problem listing Vzs'))

        for name in out.splitlines():
            name = name.split()[0]
            status = self.get_info(name)
            infos.append(driver.InstanceInfo(name, status['state']))

        return infos

    def spawn(self, context, instance, network_info=None,
              block_device_mapping=None):
        """
        Create a new virtual environment on the container platform.

        The given parameter is an instance of nova.compute.service.Instance.
        This function should use the data there to guide the creation of
        the new instance.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.

        Once this successfully completes, the instance should be
        running (power_state.RUNNING).

        If this fails, any partial instance should be completely
        cleaned up, and the container platform should be in the state
        that it was before this call began.
        """

        # Update state to inform the nova stack that the VE is launching
        db.instance_update(context,
                           instance['id'],
                           {'power_state': power_state.BUILDING})
        LOG.debug(_('instance %s: is building') % instance['name'])

        # Get current usages and resource availablity.
        self._get_cpuunits_usage()

        # Go through the steps of creating a container
        # TODO(imsplitbit): Need to add conditionals around this stuff to make
        # it more durable during failure. And roll back changes made leading
        # up to the error.
        self._cache_image(context, instance)
        self._create_vz(instance)
        self._set_vz_os_hint(instance)
        self._configure_vz(instance)
        self._set_name(instance)
        self.plug_vifs(instance, network_info)
        self._set_hostname(instance)
        self._set_instance_size(instance)
        self._attach_volumes(instance)
        self._set_onboot(instance)
        self._start(instance)
        self._initial_secure_host(instance)
        self._gratuitous_arp_all_addresses(instance, network_info)

        # Begin making our looping async call
        timer = utils.LoopingCall(f=None)

        # I stole this from the libvirt driver but it is appropriate to
        # have this looping timer call so that if a VE doesn't start right
        # away we can defer all of this.
        def _wait_for_boot():
            try:
                state = self.get_info(instance['name'])['state']
                db.instance_update(context,
                                   instance['id'], {'power_state': state})
                if state == power_state.RUNNING:
                    LOG.debug(_('instance %s: booted') % instance['name'])
                    timer.stop()

            except:
                LOG.exception(_('instance %s: failed to boot') %
                              instance['name'])
                db.instance_update(context, instance['id'],
                                   {'power_state': power_state.SHUTDOWN})
                timer.stop()

        timer.f = _wait_for_boot
        return timer.start(interval=0.5, now=True)

    def _create_vz(self, instance, ostemplate='ubuntu'):
        """
        Attempt to load the image from openvz's image cache, upon failure
        cache the image and then retry the load.

        I run the command:

        vzctl create <ctid> --ostemplate <image_ref>

        If I fail to execute an exception is raised because this is the first
        in a long list of many critical steps that are necessary for creating
        a working VE.
        """

        # TODO(imsplitbit): This needs to set an os template for the image
        # as well as an actual OS template for OpenVZ to know what config
        # scripts to use.  This can be problematic because there is no concept
        # of OS name, it is arbitrary so we will need to find a way to
        # correlate this to what type of disto the image actually is because
        # this is the clue for openvz's utility scripts.  For now we will have
        # to set it to 'ubuntu'

        # This will actually drop the os from the local image cache
        try:
            out, err = utils.execute('vzctl', 'create', instance['id'],
                                     '--ostemplate', instance['image_ref'],
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except exception.ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Failed creating VE %s from image cache') %
                                  instance['id'])
        return True

    def _set_vz_os_hint(self, instance, ostemplate='ubuntu'):
        """
        I exist as a stopgap because currently there are no os hints
        in the image managment of nova.  There are ways of hacking it in
        via image_properties but this requires special case code just for
        this driver.  I will be working to hack in an oshint feature once
        the driver is accepted into nova.

        I run the command:

        vzctl set <ctid> --save --ostemplate <ostemplate>

        Currently ostemplate defaults to ubuntu.  This facilitates setting
        the ostemplate setting in OpenVZ to allow the OpenVz helper scripts
        to setup networking, nameserver and hostnames.  Because of this, the
        openvz driver only works with debian based distros.

        If I fail to run an exception is raised as this is a critical piece
        in making openvz run a container.
        """

        # This sets the distro hint for OpenVZ to later use for the setting
        # of resolver, hostname and the like

        # TODO(imsplitbit): change the ostemplate default value to a flag
        try:
            out, err = utils.execute('vzctl', 'set', instance['id'],
                                     '--save', '--ostemplate', ostemplate,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except exception.ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(
                _('Cant set ostemplate to \'%(ostemplate)s\' for %(id)s') %
                {'ostemplate': ostemplate, 'id': instance['id']})

    def _cache_image(self, context, instance):
        """
        Create the disk image for the virtual environment.  This uses the
        image library to pull the image down the distro image into the openvz
        template cache.  This is the method that openvz wants to operate
        properly.
        """

        image_name = '%s.tar.gz' % instance['image_ref']
        full_image_path = '%s/%s' % (FLAGS.ovz_image_template_dir, image_name)

        if not os.path.exists(full_image_path):
            # These objects are required to retrieve images from the object
            # store. This is known only to work with glance so far but as I
            # understand it. glance's interface matches that of the other
            # object stores.
            user = manager.AuthManager().get_user(instance['user_id'])
            project = manager.AuthManager().get_project(instance['project_id'])

            # Grab image and place it in the image cache
            images.fetch(context, instance['image_ref'], full_image_path, user,
                         project)
            return True
        else:
            return False

    def _configure_vz(self, instance, config='basic'):
        """
        This adds the container root into the vz meta data so that
        OpenVz acknowledges it as a container.  Punting to a basic
        config for now.

        I run the command:

        vzctl set <ctid> --save --applyconfig <config>

        This sets the default configuration file for openvz containers.  This
        is a requisite step in making a container from an image tarball.

        If I fail to run successfully I raise an exception because the
        container I execute against requires a base config to start.
        """
        try:
            # Set the base config for the VE, this currently defaults to the
            # basic config.
            # TODO(imsplitbit): add guest flavor support here
            out, err = utils.execute('vzctl', 'set', instance['id'],
                                     '--save', '--applyconfig', config,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)

        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Failed to add %s to OpenVz')
                                  % instance['id'])

    def _set_onboot(self, instance):
        """
        Method to set the onboot status of the instance. This is done
        so that openvz does not handle booting, and instead the compute
        manager can handle initialization.
        
        I run the command:
        
        vzctl set <ctid> --onboot no --save
        
        If I fail to run an exception is raised.
        """
        try:
            # Set the onboot status for the vz
            out, err = utils.execute('vzctl', 'set', instance['id'],
                                     '--onboot', 'no', '--save',
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            LOG.error(_('Failed setting onboot'))

    def _start(self, instance):
        """
        Method to start the instance, I don't believe there is a nova-ism
        for starting so I am wrapping it under the private namespace and
        will call it from expected methods.  i.e. resume

        I run the command:

        vzctl start <ctid>

        If I fail to run an exception is raised.  I don't think it needs to be
        explained why.
        """
        try:
            # Attempt to start the VE.
            # NOTE: The VE will throw a warning that the hostname is invalid
            # if it isn't valid.  This is logged in LOG.error and is not
            # an indication of failure.
            out, err = utils.execute('vzctl', 'start', instance['id'],
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Failed to start %d') % instance['id'])

        # Set instance state as RUNNING
        db.instance_update(context.get_admin_context(), instance['id'],
                           {'power_state': power_state.RUNNING})
        return True

    def _stop(self, instance):
        """
        Method to stop the instance.  This doesn't seem to be a nova-ism but
        it is for openvz so I am wrapping it under the private namespace and
        will call it from expected methods.  i.e. pause

        I run the command:

        vzctl stop <ctid>

        If I fail to run an exception is raised for obvious reasons.
        """
        try:
            out, err = utils.execute('vzctl', 'stop', instance['id'],
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Failed to stop %s') % instance['id'])

        # Update instance state
        try:
            db.instance_update(context.get_admin_context(), instance['id'],
                               {'power_state': power_state.SHUTDOWN})
        except exception.DBError as err:
            LOG.error(_('Database Error: %s') % err)
            raise exception.Error(_('Failed to update db for %s')
                                  % instance['id'])

    def _set_hostname(self, instance, hostname=False):
        """
        I exist to set the hostname of a given container.  The option to pass
        a hostname to the method was added with the intention to allow the
        flexibility to override the hostname listed in the instance ref.  A
        good person wouldn't do this but it was needed for some testing and
        therefore remains for future use.

        I run the command:

        vzctl set <ctid> --save --hostname <hostname>

        If I fail to execute an exception is raised because the hostname is
        used in most cases for connecting to the guest.  While having the
        hostname not match the dns name is not a complete problem it can lead
        name mismatches.  One could argue that this should be a softer error
        and I might have a hard time arguing with that one.
        """
        if not hostname:
            hostname = instance['hostname']

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'],
                                     '--save', '--hostname', hostname,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError:
            raise exception.Error(_('Cannot set the hostname on %s') %
                                  instance['id'])

    def _gratuitous_arp_all_addresses(self, instance, network_info):
        """
        Iterate through all addresses assigned to the container and send
        a gratuitous arp over it's interface to make sure arp caches have
        the proper mac address.
        """
        # TODO(imsplitbit): send id, iface, container mac, container ip and
        # gateway to _send_garp
        for network in network_info:
            bridge_info = network[0]
            LOG.debug(_('bridge interface: %s') %
                      bridge_info['bridge_interface'])
            LOG.debug(_('bridge: %s') % bridge_info['bridge'])
            LOG.debug(_('address block: %s') % bridge_info['cidr'])
            address_info = network[1]
            LOG.debug(_('network label: %s') % address_info['label'])
            for address in address_info['ips']:
                LOG.debug(_('Address enabled: %s') % address['enabled'])
                LOG.debug(_('Address enabled type: %s') %
                          (type(address['enabled'])))
                if address['enabled'] == u'1':
                    LOG.debug(_('Address: %s') % address['ip'])
                    LOG.debug(
                        _('Running _send_garp(%(id)s %(ip)s %(bridge)s)') %
                        {'id': instance['id'], 'ip': address['ip'],
                         'bridge': bridge_info['bridge_interface']})
                    self._send_garp(instance['id'], address['ip'],
                                    bridge_info['bridge_interface'])

    def _send_garp(self, instance_id, ip_address, interface):
        """
        I exist because it is possible in nova to have a recently released
        ip address given to a new container.  We need to send a gratuitous arp
        on each interface for the address assigned.

        The command looks like this:

        arping -q -c 5 -A -I eth0 10.0.2.4

        If I fail to execute no exception is raised because even if the
        gratuitous arp fails the container will most likely be available as
        soon as the switching/routing infrastructure's arp cache clears.
        """
        try:
            LOG.debug(_('Sending arp for %(ip_address)s over %(interface)s') %
                      locals())
            out, err = utils.execute('vzctl', 'exec2', instance_id,
                                     'arping', '-q', '-c', '5', '-A',
                                     '-I', interface, ip_address,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            LOG.debug(_('Arp sent for %(ip_address)s over %(interface)s') %
                      locals())
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            LOG.error(_('Failed arping through VE'))

    def _set_name(self, instance):
        """
        I exist to store the name of an instance in the name field for
        openvz.  This is done to facilitate the get_info method which only
        accepts an instance name as an argument.

        I run the command:

        vzctl set <ctid> --save --name <name>

        If I fail to run an exception is raised.  This is due to the
        requirement of the get_info method to have the name field filled out.
        """

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'],
                                     '--save', '--name', instance['name'],
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)

        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Unable to save metadata for %s') %
                                  instance['id'])

    def _find_by_name(self, instance_name):
        """
        This method exists to facilitate get_info.  The get_info method only
        takes an instance name as it's argument.

        I run the command:

        vzlist -H --all --name <name>

        If I fail to run an exception is raised because if I cannot locate an
        instance by it's name then the driver will fail to work.
        """

        # The required method get_info only accepts a name so we need a way
        # to correlate name and id without maintaining another state/meta db
        try:
            out, err = utils.execute('vzlist', '-H', '--all', '--name',
                                     instance_name, run_as_root=True)
            LOG.debug(_('Stdout output from vzlist: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzlist: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzlist: %s') % err)
            raise exception.NotFound('Unable to load metadata for %s' %
                                  instance_name)

        # Break the output into usable chunks
        out = out.split()
        return {'name': out[4], 'id': out[0], 'state': out[2]}

    def _access_control(self, instance, host, mask=32, port=None,
                        protocol='tcp', access_type='allow'):
        """
        Does what it says.  Use this to interface with the
        linux_net.iptables_manager to allow/deny access to a host
        or network
        """

        if access_type == 'allow':
            access_type = 'ACCEPT'
        elif access_type == 'deny':
            access_type = 'REJECT'
        else:
            LOG.error(_('Invalid access_type: %s') % access_type)
            raise exception.Error(_('Invalid access_type: %s') % access_type)

        if port is None:
            port = ''
        else:
            port = '--dport %s' % port

        # Create our table instance
        tables = [
                linux_net.iptables_manager.ipv4['filter'],
                linux_net.iptables_manager.ipv6['filter']
        ]

        rule = '-s %s/%s -p %s %s -j %s' % \
               (host, mask, protocol, port, access_type)

        for table in tables:
            table.add_rule(str(instance['id']), rule)

        # Apply the rules
        linux_net.iptables_manager.apply()

    def _initial_secure_host(self, instance, ports=None):
        """
        Lock down the host in it's default state
        """

        # TODO(tim.simpson) This hangs if the "lock_path" FLAG value refers to
        #                   a directory which can't be locked.  It'd be nice
        #                   if we could somehow detect that and raise an error
        #                   instead.

        # Create our table instance and add our chains for the instance
        table_ipv4 = linux_net.iptables_manager.ipv4['filter']
        table_ipv6 = linux_net.iptables_manager.ipv6['filter']
        table_ipv4.add_chain(str(instance['id']))
        table_ipv6.add_chain(str(instance['id']))

        # As of right now there is no API call to manage security
        # so there are no rules applied, this really is just a pass.
        # The thought here is to allow us to pass a list of ports
        # that should be globally open and lock down the rest but
        # cannot implement this until the API passes a security
        # context object down to us.

        # Apply the rules
        linux_net.iptables_manager.apply()

    def resize_in_place(self, instance, instance_type_id,
                        restart_instance=False):
        """
        Making a public method for the API/Compute manager to get access
        to host based resizing.
        """
        try:
            self._set_instance_size(instance, instance_type_id)
            if restart_instance:
                self.reboot(instance, None)
            return True
        except Exception:
            raise exception.InstanceUnacceptable(_("Instance resize failed"))

    def reset_instance_size(self, instance, restart_instance=False):
        """
        Public method for changing an instance back to it's original
        flavor spec.  If this fails an exception is raised because this
        means that the instance flavor setting couldn't be rescued.
        """
        try:
            self._set_instance_size(instance)
            if restart_instance:
                self.reboot(instance)
            return True
        except Exception:
            raise exception.InstanceUnacceptable(
                _("Instance size reset FAILED"))

    def _set_instance_size(self, instance, instance_type_id = None):
        """
        Given that these parameters make up and instance's 'size' we are
        bundling them together to make resizing an instance on the host
        an easier task.
        """
        if not instance_type_id:
            instance_type = instance_types.get_instance_type(
                instance['instance_type_id'])
        else:
            instance_type = instance_types.get_instance_type(instance_type_id)

        instance_memory_bytes = ((int(instance_type['memory_mb'])
                                  * 1024) * 1024)
        instance_memory_pages = self._calc_pages(instance_type['memory_mb'])
        percent_of_resource = self._percent_of_resource(
            instance_type['memory_mb'])
        instance_vcpus = instance_type['vcpus']

        self._set_vmguarpages(instance, instance_memory_pages)
        self._set_privvmpages(instance, instance_memory_pages)
        self._set_kmemsize(instance, instance_memory_bytes)
        if FLAGS.ovz_use_cpuunit:
            self._set_cpuunits(instance, percent_of_resource)
        if FLAGS.ovz_use_cpulimit:
            self._set_cpulimit(instance, percent_of_resource)
        if FLAGS.ovz_use_cpus:
            self._set_cpus(instance, instance_vcpus)
        if FLAGS.ovz_use_ioprio:
            self._set_ioprio(instance, percent_of_resource)
        if FLAGS.ovz_use_disk_quotas:
            self._set_diskspace(instance, instance_type)

    def _set_vmguarpages(self, instance, num_pages):
        """
        Set the vmguarpages attribute for a container.  This number represents
        the number of 4k blocks of memory that are guaranteed to the container.
        This is what shows up when you run the command 'free' in the container.

        I run the command:

        vzctl set <ctid> --save --vmguarpages <num_pages>

        If I fail to run then an exception is raised because this affects the
        memory allocation for the container.
        """
        try:
            out, err = utils.execute('vzctl', 'set', instance['id'],
                                      '--save', '--vmguarpages', num_pages,
                                      run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Cannot set vmguarpages for %s') %
                                  instance['id'])

    def _set_privvmpages(self, instance, num_pages):
        """
        Set the privvmpages attribute for a container.  This represents the
        memory allocation limit.  Think of this as a bursting limit.  For now
        We are setting to the same as vmguarpages but in the future this can be
        used to thin provision a box.

        I run the command:

        vzctl set <ctid> --save --privvmpages <num_pages>

        If I fail to run an exception is raised as this is essential for the
        running container to operate properly within it's memory constraints.
        """
        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--privvmpages', num_pages,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Cannot set privvmpages for %s') %
                                  instance['id'])

    def _set_kmemsize(self, instance, instance_memory):
        """
        Set the kmemsize attribute for a container.  This represents the
        amount of the container's memory allocation that will be made
        available to the kernel.  This is used for tcp connections, unix
        sockets and the like.

        This runs the command:

        vzctl set <ctid> --save --kmemsize <barrier>:<limit>

        If this fails to run an exception is raised as this is essential for
        the container to operate under a normal load.  Defaults for this
        setting are completely inadequate for any normal workload.
        """
        # Now use the configuration flags to calculate the appropriate
        # values for both barrier and limit.
        kmem_limit = int(instance_memory * (
            float(FLAGS.ovz_kmemsize_percent_of_memory) / 100.0))
        kmem_barrier = int(kmem_limit * (
            float(FLAGS.ovz_kmemsize_barrier_differential) / 100.0))
        kmemsize = '%d:%d' % (kmem_barrier, kmem_limit)

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--kmemsize',
                                     kmemsize, run_as_root=True)
            LOG.debug(_('Stdout from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Error setting kmemsize: %s') % err)
            raise exception.Error(
                _('Error setting kmemsize to %(kmemsize) on %(id)s') %
                {'kmemsize': kmemsize, 'id': instance['id']})

    def _set_cpuunits(self, instance, percent_of_resource):
        """
        Set the cpuunits setting for the container.  This is an integer
        representing the number of cpu fair scheduling counters that the
        container has access to during one complete cycle.

        I run the command:

        vzctl set <ctid> --save --cpuunits <units>

        If I fail to run an exception is raised because this is the secret
        sauce to constraining each container within it's subscribed slice of
        the host node.
        """
        LOG.debug(_('Reported cpuunits %s') % self.utility['UNITS'])
        LOG.debug(_('Reported percent of resource: %s') %
                  percent_of_resource)
        units = int(self.utility['UNITS'] * percent_of_resource)
        # TODO(imsplitbit): This needs to be adjusted to not allow
        # subscription of more than available cpuunits.  For now we
        # won't let the obvious case of a container getting more than
        # the maximum cpuunits for the host.
        if units > self.utility['UNITS']:
            units = self.utility['UNITS']

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--cpuunits', units, run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Cannot set cpuunits for %s') %
                                  instance['id'])

    def _set_cpulimit(self, instance, percent_of_resource):
        """
        This is a number in % equal to the amount of cpu processing power
        the container gets.  NOTE: 100% is 1 logical cpu so if you have 12
        cores with hyperthreading enabled then 100% of the whole host machine
        would be 2400% or --cpulimit 2400.

        I run the command:

        vzctl set <ctid> --save --cpulimit <cpulimit>

        If I fail to run an exception is raised because this is the secret
        sauce to constraining each container within it's subscribed slice of
        the host node.
        """
        cpulimit = int(self.utility['CPULIMIT'] * percent_of_resource)
        # TODO(imsplitbit): Need to fix this so that we don't alocate
        # more than the current available resource limits.  This shouldn't
        # happen except in test cases but we should still protect
        # ourselves from it.  For now we just won't let it go higher
        # than the maximum cpulimit for the host on any one container.
        if cpulimit > self.utility['CPULIMIT']:
            cpulimit = self.utility['CPULIMIT']

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--cpulimit', cpulimit, run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Unable to set cpulimit for %s') %
                                  instance['id'])

    def _set_cpus(self, instance, vcpus, multiplier=2):
        """
        The number of logical cpus that are made available to the container.
        I default to showing 2 cpus to each container at a minimum.

        I run the command:

        vzctl set <ctid> --save --cpus <num_cpus>

        If I fail to run an exception is raised because this limits the number
        of cores that are presented to each container and if this fails to set
        *ALL* cores will be presented to every container, that be bad.
        """
        inst_typ = instance_types.get_instance_type(
            instance['instance_type_id']
        )
        vcpus = vcpus * multiplier
        # TODO(imsplitbit): We need to fix this to not allow allocation of
        # more than the maximum allowed cpus on the host.
        if vcpus > (self.utility['CPULIMIT'] / 100):
            vcpus = self.utility['CPULIMIT'] / 100

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--cpus', vcpus, run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Unable to set cpus for %s') %
                                  instance['id'])

    def _set_ioprio(self, instance, percent_of_resource):
        """
        Set the IO priority setting for a given container.  This is represented
        by an integer between 0 and 7.  If no priority is given one will be
        automatically calculated based on the percentage of allocated memory
        for the container.

        I run the command:

        vzctl set <ctid> --save --ioprio <iopriority>

        If I fail to run an exception is raised because all containers are
        given the same weight by default which will cause bad performance
        across all containers when there is input/outpu contention.
        """
        ioprio = int(float(FLAGS.ovz_ioprio_limit) * percent_of_resource)

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--ioprio', ioprio, run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Unable to set IO priority for %s') %
                instance['id'])

    def _set_diskspace(self, instance, instance_type):
        """
        Implement OpenVz disk quotas for local disk space usage.
        This method takes a soft and hard limit.  This is also the amount
        of diskspace that is reported by system tools such as du and df inside
        the container.  If no argument is given then one will be calculated
        based on the values in the instance_types table within the database.

        I run the command:

        vzctl set <ctid> --save --diskspace <soft_limit:hard_limit>

        If I fail to run an exception is raised because this command limits a
        container's ability to hijack all available disk space.
        """
        soft = int(instance_type['local_gb'])

        hard = int(instance_type['local_gb'] *
                    FLAGS.ovz_disk_space_oversub_percent)

        # Now set the increment of the limit.  I do this here so that I don't
        # have to do this in every line above.
        soft = '%s%s' % (soft, FLAGS.ovz_disk_space_increment)
        hard = '%s%s' % (hard, FLAGS.ovz_disk_space_increment)

        try:
            out, err = utils.execute('vzctl', 'set', instance['id'], '--save',
                                     '--diskspace', '%s:%s' % (soft, hard),
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Error setting diskspace quota for %s') %
                                  instance['id'])

    def plug_vifs(self, instance, network_info):
        """
        I plug vifs into networks and configure network devices in the
        container.  I am necessary to make multi-nic go.
        """
        interfaces = []
        interface_num = -1
        for (network, mapping) in network_info:
            self.vif_driver.plug(instance, network, mapping)
            interface_num += 1

            #TODO(imsplitbit): make this work for ipv6
            address_v6 = None
            gateway_v6 = None
            netmask_v6 = None
            if FLAGS.use_ipv6:
                address_v6 = mapping['ip6s'][0]['ip']
                netmask_v6 = mapping['ip6s'][0]['netmask']
                gateway_v6 = mapping['gateway6']

            interface_info = {
                'id': instance['id'],
                'interface_number': interface_num,
                'bridge': network['bridge'],
                'name': 'eth%d' % interface_num,
                'mac': mapping['mac'],
                'address': mapping['ips'][0]['ip'],
                'netmask': mapping['ips'][0]['netmask'],
                'gateway': mapping['gateway'],
                'broadcast': mapping['broadcast'],
                'dns': ' '.join(mapping['dns']),
                'address_v6': address_v6,
                'gateway_v6': gateway_v6,
                'netmask_v6': netmask_v6
            }

            interfaces.append(interface_info)

        ifaces_fh = OVZNetworkInterfaces(interfaces)
        ifaces_fh.add()

    def snapshot(self, instance, name):
        """
        Snapshots the specified instance.

        The given parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name.

        The second parameter is the name of the snapshot.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.
        """
        # TODO(imsplitbit): Need to implement vzdump
        pass

    def reboot(self, instance, network_info):
        """
        Reboot the specified instance.

        I run the command:

        vzctl restart <ctid>

        If I fail to run an exception is raised because the container given to
        this method will be in an inconsistent state.
        """

        # TODO(imsplitbit): make this an async call
        try:
            out, err = utils.execute('vzctl', 'restart', instance['id'],
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Failed to restart container: %d') %
                                  instance['id'])

    def set_admin_password(self, instance, new_pass):
        """
        Set the root password on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the value of the new password.

        The work will be done asynchronously.  This function returns a
        task that allows the caller to detect when it is complete.
        """
        pass

    def rescue(self, instance):
        """
        Rescue the specified instance.
        """
        pass

    def unrescue(self, instance):
        """
        Unrescue the specified instance.
        """
        pass

    def pause(self, instance, callback):
        """
        Pause the specified instance.
        """
        self._stop(instance)

    def unpause(self, instance, callback):
        """
        Unpause the specified instance.
        """
        self._start(instance)

    def suspend(self, instance, callback):
        """
        suspend the specified instance
        """
        self._stop(instance)

    def resume(self, instance, callback):
        """
        resume the specified instance
        """
        self._start(instance)

    def _clean_orphaned_files(self, instance_id):
        """
        When openvz deletes a container it leaves behind orphaned config
        files in /etc/vz/conf with the .destroyed extension.  We want these
        gone when we destroy a container.

        This runs a command that looks like this:

        rm -f /etc/vz/conf/<CTID>.conf.destroyed

        It this fails to execute no exception is raised but an log error
        event is triggered.
        """
        # first assemble a list of files that need to be cleaned up, then
        # do the deed.
        for file in os.listdir(FLAGS.ovz_config_dir):
            if fnmatch.fnmatch(file, '%s.*' % instance_id):
                try:
                    # minor protection for /
                    if FLAGS.ovz_config_dir == '/':
                        raise exception.Error(_('I refuse to operate on /'))

                    file = '%s/%s' % (FLAGS.ovz_config_dir, file)
                    LOG.debug(_('Deleting file: %s') % file)
                    out, err = utils.execute('rm', '-f', file,
                                             run_as_root=True)
                    LOG.debug(_('Stdout output from rm: %s') % out)
                    if err:
                        LOG.error(_('Stderr output from rm: %s') % err)
                except ProcessExecutionError as err:
                    LOG.error(_('Stderr output from rm: %s') % err)

    def _clean_orphaned_directories(self, instance_id):
        """
        When a container is destroyed we want to delete all mount directories
        in the mount root on the host that are associated with the container.

        This runs a command that looks like this:

        rm -rf /mnt/<CTID>

        If this fails to execute, no exception is raised but a log error event
        is triggered
        """
        mount_root = '%s/%s' % (FLAGS.ovz_ve_host_mount_dir, instance_id)
        mount_root = os.path.abspath(mount_root)

        # Because we are using an rm -rf command lets do some simple validation
        validation_failed = False
        if isinstance(instance_id, str):
            if not instance_id.isdigit():
                validation_failed = True
        elif not isinstance(instance_id, int):
            validation_failed = True

        if not FLAGS.ovz_ve_host_mount_dir:
            validation_failed = True

        if validation_failed:
            msg = _('Potentially invalid path to be deleted')
            LOG.error(msg)
            raise exception.Error(msg)

        try:
            out, err = utils.execute('rm', '-rf', mount_root, run_as_root=True)
            LOG.debug(_('Stdout from rm: %s') % out)
            if err:
                LOG.error(_('Stderr from rm: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr from rm: %s') % err)

    def destroy(self, instance, network_info, cleanup=True):
        """
        Destroy (shutdown and delete) the specified instance.

        I run the command:

        vzctl destroy <ctid>

        If I do not run successfully then an exception is raised.  This is
        because a failure to destroy would leave the database and container
        in a disparate state.
        """
        timer = utils.LoopingCall()
        def _wait_for_destroy():
            try:
                LOG.debug(_('Beginning _wait_for_destroy'))
                state = self.get_info(instance['name'])['state']
                LOG.debug(_('State is %s') % state)

                if state is power_state.RUNNING:
                    LOG.debug(_('Ve is running, stopping now.'))
                    self._stop(instance)
                    LOG.debug(_('Ve stopped'))

                LOG.debug(_('Attempting to destroy container'))
                out, err = utils.execute('vzctl', 'destroy', instance['id'],
                                     run_as_root=True)
                LOG.debug(_('Stdout output from vzctl: %s') % out)
                if err:
                    LOG.error(_('Stderr output from vzctl: %s') % err)
            except ProcessExecutionError as err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
                raise exception.Error(_('Error destroying %d') % instance['id'])
            except exception.NotFound:
                LOG.debug(_('Container not found, destroyed?'))
                timer.stop()
                LOG.debug(_('Timer stopped for _wait_for_destroy'))

        LOG.debug(_('Making timer'))
        timer.f = _wait_for_destroy
        LOG.debug(_('Starting timer'))
        running_delete = timer.start(interval=0.5, now=True)
        LOG.debug(_('Waiting for timer'))
        running_delete.wait()
        LOG.debug(_('Timer finished'))

        for (network, mapping) in network_info:
            LOG.debug('Unplugging vifs')
            self.vif_driver.unplug(instance, network, mapping)

        self._clean_orphaned_files(instance['id'])
        self._clean_orphaned_directories(instance['id'])

    def _attach_volumes(self, instance):
        """
        Iterate through all volumes and attach them all.  This is just a helper
        method for self.spawn so that all volumes in the db get added to a
        container before it gets started.

        This will only attach volumes that have a filesystem uuid.  This is
        a limitation that is currently imposed by nova not storing the device
        name in the volumes table so we have no point of reference for which
        device goes where.
        """
        if instance['volumes']:
            for volume in instance['volumes']:
                if volume['uuid']:
                    self.attach_volume(instance['name'], None,
                                       volume['mountpoint'])

    def attach_volume(self, instance_name, device_path, mountpoint):
        """
        Attach the disk at device_path to the instance at mountpoint.  For
        volumes being attached to OpenVz we require a filesystem be created
        already.  We have not included code to create filesystems here as it
        was assumed that would be the responsiblity of the volume server.

        The reddwarf team has extended the volume code to include a flag that
        will cause the volume server to create a filesystem and attach a
        uuid to the filesystem.  While a uuid is not required it makes
        migration easier as device names can change from host to host.
        """

        # Find the actual instance ref so we can see if it has a Reddwarf
        # friendly volume.  i.e. a formatted filesystem with UUID attribute
        # set.
        meta = self._find_by_name(instance_name)
        instance = db.instance_get(context.get_admin_context(), meta['id'])

        # Set volumes to none to account for the case that no volume is found
        volumes = None
        if instance['volumes']:
            for vol in instance['volumes']:
                if vol['mountpoint'] == mountpoint and vol['uuid']:
                    # Volume has a UUID so do all the mount magic using the
                    # UUID instead of the device name.
                    volumes = OVZVolumes(instance['id'], mountpoint, None,
                                         vol['uuid'])
                    LOG.debug(_('Adding volume %(uuid)s to %(id)s') %
                            {'uuid': vol['uuid'], 'id': instance['id']})
                elif vol['mountpoint'] == mountpoint and device_path:
                    volumes = OVZVolumes(instance['id'], mountpoint,
                                         device_path, None)
                    LOG.debug(_('Adding volume %(path)s to %(id)s') %
                            {'path': device_path, 'id': instance['id']})
        # If volumes is None we have problems.
        if volumes is None:
            LOG.error(_('No volume in the db for this instance'))
            LOG.error(_('Instance: %s') % instance_name)
            LOG.error(_('Device: %s') % device_path)
            LOG.error(_('Mount: %s') % mountpoint)
            raise exception.Error(_('No volume in the db for this instance'))
        else:
            # Run all the magic to make the mounts happen
            volumes.setup()
            volumes.attach()
            volumes.write_and_close()

    def detach_volume(self, instance_name, mountpoint):
        """
        Detach the disk attached to the instance at mountpoint
        """

        # Find the instance ref so we can pass it to the
        # _mount_script_modify method.
        LOG.debug(_('Looking up %(instance_name)s') % locals())
        meta = self._find_by_name(instance_name)
        LOG.debug(_('Found %(instance_name)s') % locals())
        LOG.debug(_('Fetching the instance from the db'))
        instance = db.instance_get(context.get_admin_context(), meta['id'])
        LOG.debug(_('Found instance %s') % instance['id'])
        volumes = OVZVolumes(instance['id'], mountpoint)
        volumes.setup()
        volumes.detach()
        volumes.write_and_close()

    def get_info(self, instance_name):
        """
        Get a block of information about the given instance.  This is returned
        as a dictionary containing 'state': The power_state of the instance,
        'max_mem': The maximum memory for the instance, in KiB, 'mem': The
        current memory the instance has, in KiB, 'num_cpu': The current number
        of virtual CPUs the instance has, 'cpu_time': The total CPU time used
        by the instance, in nanoseconds.

        This method should raise exception.NotFound if the hypervisor has no
        knowledge of the instance
        """
        try:
            meta = self._find_by_name(instance_name)
            instance = db.instance_get(context.get_admin_context(), meta['id'])
        except exception.NotFound as err:
            LOG.error(_('Output from db call: %s') % err)
            LOG.error(_('Instance %s Not Found') % instance_name)
            raise exception.NotFound('Instance %s Not Found' % instance_name)

        # Store the assumed state as the default
        state = instance['power_state']

        LOG.debug(_('Instance %(id)s is in state %(power_state)s') %
                {'id': instance['id'], 'power_state': state})

        if instance['power_state'] != power_state.NOSTATE:
            # NOTE(imsplitbit): This is not ideal but it looks like nova uses
            # codes returned from libvirt and xen which don't correlate to
            # the status returned from OpenVZ which is either 'running' or
            # 'stopped'.  There is some contention on how to handle systems
            # that were shutdown intentially however I am defaulting to the
            # nova expected behavior.
            if meta['state'] == 'running':
                state = power_state.RUNNING
            elif meta['state'] is None or meta['state'] == '-':
                state = power_state.NOSTATE
            else:
                state = power_state.SHUTDOWN

        # TODO(imsplitbit): Need to add all metrics to this dict.
        return {'state': state,
                'max_mem': 0,
                'mem': 0,
                'num_cpu': 0,
                'cpu_time': 0}

    def get_diagnostics(self, instance_name):
        pass

    def list_disks(self, instance_name):
        """
        Return the IDs of all the virtual disks attached to the specified
        instance, as a list.  These IDs are opaque to the caller (they are
        only useful for giving back to this layer as a parameter to
        disk_stats).  These IDs only need to be unique for a given instance.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return ['A_DISK']

    def list_interfaces(self, instance_name):
        """
        Return the IDs of all the virtual network interfaces attached to the
        specified instance, as a list.  These IDs are opaque to the caller
        (they are only useful for giving back to this layer as a parameter to
        interface_stats).  These IDs only need to be unique for a given
        instance.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return ['A_VIF']

    def block_stats(self, instance_name, disk_id):
        """
        Return performance counters associated with the given disk_id on the
        given instance_name.  These are returned as [rd_req, rd_bytes, wr_req,
        wr_bytes, errs], where rd indicates read, wr indicates write, req is
        the total number of I/O requests made, bytes is the total number of
        bytes transferred, and errs is the number of requests held up due to a
        full pipeline.

        All counters are long integers.

        This method is optional.  On some platforms (e.g. XenAPI) performance
        statistics can be retrieved directly in aggregate form, without Nova
        having to do the aggregation.  On those platforms, this method is
        unused.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return [0L, 0L, 0L, 0L, None]

    def interface_stats(self, instance_name, iface_id):
        """
        Return performance counters associated with the given iface_id on the
        given instance_id.  These are returned as [rx_bytes, rx_packets,
        rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop], where rx
        indicates receive, tx indicates transmit, bytes and packets indicate
        the total number of bytes or packets transferred, and errs and dropped
        is the total number of packets failed / dropped.

        All counters are long integers.

        This method is optional.  On some platforms (e.g. XenAPI) performance
        statistics can be retrieved directly in aggregate form, without Nova
        having to do the aggregation.  On those platforms, this method is
        unused.

        Note that this function takes an instance ID, not a
        compute.service.Instance, so that it can be called by compute.monitor.
        """
        return [0L, 0L, 0L, 0L, 0L, 0L, 0L, 0L]

    def get_console_output(self, instance):
        return 'FAKE CONSOLE OUTPUT'

    def get_ajax_console(self, instance):
        return 'http://fakeajaxconsole.com/?token=FAKETOKEN'

    def get_console_pool_info(self, console_type):
        return  {'address': '127.0.0.1',
                 'username': 'fakeuser',
                 'password': 'fakepassword'}

    def refresh_security_group_rules(self, security_group_id):
        """This method is called after a change to security groups.

        All security groups and their associated rules live in the datastore,
        and calling this method should apply the updated rules to instances
        running the specified security group.

        An error should be raised if the operation cannot complete.

        """
        return True

    def refresh_security_group_members(self, security_group_id):
        """This method is called when a security group is added to an instance.

        This message is sent to the virtualization drivers on hosts that are
        running an instance that belongs to a security group that has a rule
        that references the security group identified by `security_group_id`.
        It is the responsiblity of this method to make sure any rules
        that authorize traffic flow with members of the security group are
        updated and any new members can communicate, and any removed members
        cannot.

        Scenario:
            * we are running on host 'H0' and we have an instance 'i-0'.
            * instance 'i-0' is a member of security group 'speaks-b'
            * group 'speaks-b' has an ingress rule that authorizes group 'b'
            * another host 'H1' runs an instance 'i-1'
            * instance 'i-1' is a member of security group 'b'

            When 'i-1' launches or terminates we will recieve the message
            to update members of group 'b', at which time we will make
            any changes needed to the rules for instance 'i-0' to allow
            or deny traffic coming from 'i-1', depending on if it is being
            added or removed from the group.

        In this scenario, 'i-1' could just as easily have been running on our
        host 'H0' and this method would still have been called.  The point was
        that this method isn't called on the host where instances of that
        group are running (as is the case with
        :method:`refresh_security_group_rules`) but is called where references
        are made to authorizing those instances.

        An error should be raised if the operation cannot complete.

        """
        return True

    def update_available_resource(self, ctxt, host):
        """
        Added because now nova requires this method
        """
        return

    def _calc_pages(self, instance_memory_mb, block_size=4096):
        """
        Returns the number of pages for a given size of storage/memory
        """
        return ((instance_memory_mb * 1024) * 1024) / block_size

    def _percent_of_resource(self, memory_mb):
        """
        In order to evenly distribute resources this method will calculate a
        multiplier based on memory consumption for the allocated container and
        the overall host memory. This can then be applied to the cpuunits in
        self.utility to be passed as an argument to the self._set_cpuunits
        method to limit cpu usage of the container to an accurate percentage of
        the host.  This is only done on self.spawn so that later, should
        someone choose to do so, they can adjust the container's cpu usage
        up or down.
        """
        cont_mem_mb = memory_mb / \
                      float(self.utility['MEMORY_MB'])

        # We shouldn't ever have more than 100% but if for some unforseen
        # reason we do, lets limit it to 1 to make all of the other
        # calculations come out clean.
        if cont_mem_mb > 1:
            LOG.error(_('_percent_of_resource came up with more than 100%'))
            return 1
        else:
            return cont_mem_mb

    def _get_memory(self):
        """
        Gets the overall memory capacity of the host machine to be able to
        accurately compute how many cpuunits a container should get.  This is
        Linux specific code but because OpenVz only runs on linux this really
        isn't a problem.

        I run the command:

        cat /proc/meminfo

        If I fail to run an exception is raised as the returned value of this
        method is required for all resource isolation to work correctly.
        """
        try:
            out, err = utils.execute('cat', '/proc/meminfo', run_as_root=True)
            LOG.debug(_('Stdout output from cat: %s') % out)
            if err:
                LOG.error(_('Stderr output from cat: %s') % err)
            for line in out.splitlines():
                line = line.split()
                if line[0] == 'MemTotal:':
                    LOG.debug(_('Total memory for host %s MB') % line[1])
                    self.utility['MEMORY_MB'] = int(line[1]) / 1024
            return True

        except ProcessExecutionError as err:
            LOG.error(_('Cannot get memory info for host'))
            LOG.error(_('Stderr output from cat: %s') % err)
            raise exception.Error(_('Cannot get memory info for host'))

    def _get_cpulimit(self):
        """
        Fetch the total possible cpu processing limit in percentage to be
        divided up across all containers.  This is expressed in percentage
        being added up by logical processor.  If there are 24 logical
        processors then the total cpulimit for the host node will be
        2400.

        I run the command:

        cat /proc/cpuinfo

        If I fail to run an exception is raised because the returned value
        of this method is essential in calculating the number of cores
        available on the host to be carved up for the guests.
        """
        proc_count = 0
        try:
            out, err = utils.execute('cat', '/proc/cpuinfo', run_as_root=True)
            LOG.debug(_('Stdout output from cat %s') % out)
            if err:
                LOG.error(_('Stderr output from cat: %s') % err)

            for line in out.splitlines():
                line = line.split()
                if len(line) > 0:
                    if line[0] == 'processor':
                        proc_count += 1

            self.utility['CPULIMIT'] = proc_count * 100
            return True

        except ProcessExecutionError as err:
            LOG.error(_('Cannot get host node cpulimit'))
            LOG.error(_('Stderr output from cat: %s') % err)
            raise exception.Error(err)

    def _get_cpuunits_capability(self):
        """
        Use openvz tools to discover the total processing capability of the
        host node.  This is done using the vzcpucheck utility.

        I run the command:

        vzcpucheck

        If I fail to run an exception is raised because the output of this
        method is required to calculate the overall bean count available on the
        host to be carved up for guests to use.
        """
        try:
            out, err = utils.execute('vzcpucheck', run_as_root=True)
            LOG.debug(_('Stdout output from vzcpucheck %s') % out)
            if err:
                LOG.error(_('Stderr output from vzcpucheck: %s') % err)

            for line in out.splitlines():
                line = line.split()
                if len(line) > 0:
                    if line[0] == 'Power':
                        LOG.debug(_('Power of host: %s') % line[4])
                        self.utility['UNITS'] = int(line[4])

        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzcpucheck: %s') % err)
            raise exception.Error(_('Problem getting cpuunits for host'))

    def _get_cpuunits_usage(self):
        """
        Use openvz tools to discover the total used processing power. This is
        done using the vzcpucheck -v command.

        I run the command:

        vzcpucheck -v

        If I fail to run an exception should not be raised as this is a soft
        error and results only in the lack of knowledge of what the current
        cpuunit usage of each container.
        """
        try:
            out, err = utils.execute('vzcpucheck', '-v', run_as_root=True)
            LOG.debug(_('Stdout output from vzcpucheck %s') % out)
            if err:
                LOG.error(_('Stderr output from vzcpucheck: %s') % err)

            for line in out.splitlines():
                line = line.split()
                if len(line) > 0:
                    if line[0] == 'Current':
                        LOG.debug(_('Current usage of host: %s') % line[3])
                        self.utility['TOTAL'] = int(line[3])
                    elif line[0].isdigit():
                        LOG.debug(_('Usage for CTID %(id)s: %(usage)s') %
                                {'id': line[0], 'usage': line[1]})
                        self.utility['CTIDS'][line[0]] = line[1]

        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzcpucheck: %s') % err)

class OVZFile(object):
    """
    This is a generic file class for wrapping up standard file operations that
    may need to run on files associated with OpenVz
    """
    def __init__(self, filename):
        self.filename = filename
        self.contents = []

    def read(self):
        """
        Open the file for reading only and read it's contents into the instance
        attribute self.contents
        """
        try:
            with open(self.filename, 'r') as fh:
                self.contents = fh.readlines()
        except Exception as err:
            LOG.error(_('Output from open: %s') % err)
            raise exception.Error(_('Failed to read %s') % self.filename)

    def write(self):
        """
        Because self.contents may or may not get manipulated throughout the
        process, this is a method used to dump the contents of self.contents
        back into the file that this object represents.
        """
        try:
            with open(self.filename, 'w') as fh:
                fh.writelines('\n'.join(self.contents) + '\n')
        except Exception as err:
            LOG.error(_('Output from open: %s') % err)
            raise exception.Error(_('Failed to write %s') % self.filename)

    def touch(self):
        """
        There are certain conditions where we create an OVZFile object but that
        file may or may not exist and this provides us with a way to create
        that file if it doesn't exist.

        I run the command:

        touch <filename>

        If I do not run an exception is raised as a failure to touch a file
        when you intend to do so would cause a serious failure of procedure.
        """
        self.make_path()
        try:
            out, err = utils.execute('touch', self.filename, run_as_root=True)
            LOG.debug(_('Stdout output from touch: %s') % out)
            if err:
                LOG.error(_('Stderr output from touch: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from touch: %s') % err)
            raise exception.Error(_('Failed to touch %s') % self.filename)

    def append(self, contents):
        """
        Add the argument contents to the end of self.contents
        """
        if isinstance(contents, str):
            contents = [contents]
        self.contents = self.contents + contents

    def prepend(self, contents):
        """
        Add the argument contents to the beginning of self.contents
        """
        if isinstance(contents, str):
            contents = [contents]
        self.contents = contents + self.contents

    def delete(self, contents):
        """
        Delete the argument contents from self.contents if they exist.
        """
        if isinstance(contents, list):
            for line in contents:
                self.remove_line(line)
        else:
            self.remove_line(contents)

    def remove_line(self, line):
        """
        Simple helper method to actually do the removal of a line from an array
        """
        if line in self.contents:
            self.contents.remove(line)

    def set_permissions(self, permissions):
        """
        Because nova runs as an unprivileged user we need a way to mangle
        permissions on files that may be owned by root for manipulation

        I run the command:

        chmod <permissions> <filename>

        If I do not run an exception is raised because the permissions not
        being set to what the application believes they are set to can cause
        a failure of epic proportions.
        """
        try:
            out, err = utils.execute('chmod', permissions, self.filename,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from chmod: %s') % out)
            if err:
                LOG.error(_('Stderr output from chmod: %s') % err)
        except exception.ProcessExecutionError as err:
            LOG.error(_('Stderr output from chmod: %s') % err)
            raise exception.Error(_('Unable to set permissions on %s')
                                  % self.filename)

    def make_path(self, path=None):
        """
        Helper method for an OVZFile object to be able to create the path for
        self.filename if it doesn't exist before running touch()
        """
        if not path:
            path = self.filename
        basedir = os.path.dirname(path)
        self.make_dir(basedir)

    @staticmethod
    def make_dir(path):
        """
        This is the method that actually creates directories. This is used by
        make_path and can be called directly as a utility to create
        directories.

        I run the command:

        mkdir -p <path>

        If I do not run an exception is raised as this path creation is
        required to ensure that other file operations are successful. Such
        as creating the path for a file yet to be created.
        """
        try:
            if not os.path.exists(path):
                LOG.debug(_('Path %s doesnt exist, creating now') % path)
                out, err = utils.execute('mkdir', '-p', path, run_as_root=True)
                LOG.debug(_('Stdout output from mkdir: %s') % out)
                if err:
                    LOG.error(_('Stderr output from mkdir: %s') % err)
            else:
                LOG.debug(_('Path %s exists, skipping') % path)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from mkdir: %s') % err)
            raise exception.Error(_('Unable to make %s') % path)


class OVZMounts(OVZFile):
    """
    OVZMounts is a sub-class of OVZFile that applies mount/umount file specific
    operations to the object.
    """
    def __init__(self, filename, mount, instance_id, device=None, uuid=None):
        super(OVZMounts, self).__init__(filename)
        self.device = device
        self.uuid = uuid

        # Generate the mountpoint paths
        self.host_mount_container_root = '%s/%s' % \
                                    (FLAGS.ovz_ve_host_mount_dir, instance_id)
        self.host_mount_container_root = os.path.abspath(
            self.host_mount_container_root)
        self.container_mount = '%s/%s/%s' % \
                       (FLAGS.ovz_ve_private_dir, instance_id, mount)
        self.container_root_mount = '%s/%s/%s' % \
                            (FLAGS.ovz_ve_root_dir, instance_id, mount)
        self.host_mount = '%s/%s' % \
                        (self.host_mount_container_root, mount)
        # Fix mounts to remove duplicate slashes
        self.container_mount = os.path.abspath(self.container_mount)
        self.container_root_mount = os.path.abspath(self.container_root_mount)
        self.host_mount = os.path.abspath(self.host_mount)

    def format(self):
        """
        OpenVz mount and umount files must be properly formatted scripts.  I
        prepend the proper shell script header to the files
        """
        if len(self.contents) > 0:
            if not self.contents[0] == '#!/bin/sh':
                self.prepend('#!/bin/sh')
        else:
            self.prepend('#!/bin/sh')

    def delete_full_mount_path(self):
        """
        I felt like issuing an rm -rf was a little careless because it is
        possible for 2 filesystems to be mounted within each other.  For
        example, one filesystem could be mounted as /var/lib in the container
        and another be mounted as /var/lib/mysql.  An rmdir will return an
        error if we try to remove a directory not empty so it seems to me the
        best way to recursively delete a mount path is to actually start at the
        uppermost mount and work backwards.

        We will still need to put some safe guards in place to protect users
        from killing their machine but rmdir does a pretty good job of this
        already.
        """
        mount_path = self.host_mount
        while mount_path != self.host_mount_container_root:
            # Just a safeguard for root
            if mount_path == '/':
                # while rmdir would fail in this case, lets just break out
                # anyway to be safe.
                break

            if not self.delete_mount_path:
                # there was an error returned from rmdir.  It is assumed that
                # if this happened it is because the directory isn't empty
                # so we want to stop where we are.
                break

            # set the path to the directory sub of the current directory we are
            # working on.
            mount_path = os.path.dirname(mount_path)

    @staticmethod
    def delete_mount_path(self, mount_path):
        """
        After a volume has been detached and the mount statements have been
        removed from the mount configuration for a container we want to remove
        the paths created on the host system so as not to leave orphaned files
        and directories on the system.

        This runs a command like:
        sudo rmdir /mnt/100/var/lib/mysql
        """
        try:
            out, err = utils.execute('rmdir', mount_path,
                                     run_as_root=True)
            LOG.debug(_('Stdout from rmdir: %s') % out)
            if err:
                LOG.debug(_('Stderr from rmdir: %s') % err)
            return True
        except ProcessExecutionError as err:
            LOG.error(_('Stderr from rmdir: %s') % err)
            return False


class OVZMountFile(OVZMounts):
    """
    methods used to specifically interact with the /etc/vz/conf/CTID.mount file
    that handles all mounted filesystems for containers.
    """
    def host_mount_line(self):
        """
        OpenVz is unlike most hypervisors in that it cannot actually do
        anything with raw devices. When migrating containers from host to host
        you are not guaranteed to have the same device name on each host so we
        need a conditional that generates a mount line that can use a UUID
        attribute that can be added to a filesystem which allows us to be
        device name agnostic.
        """
        #TODO(imsplitbit): Add LABEL= to allow for disk labels as well
        if self.device:
            mount_line = 'mount -o %s %s %s' % \
                         (FLAGS.ovz_mount_options, self.device, self.host_mount)
        elif self.uuid:
            mount_line = 'mount -o %s UUID=%s %s' % \
                         (FLAGS.ovz_mount_options, self.uuid, self.host_mount)
        else:
            LOG.error(_('No device or uuid given'))
            raise exception.Error(_('No device or uuid given'))
        return mount_line

    def container_mount_line(self):
        """
        Generate a mount line that will allow OpenVz to mount a filesystem
        within the container's root filesystem.  This is done with the bind
        mount feature and is the prescribed method for OpenVz
        """
        return 'mount --bind %s %s' % \
               (self.host_mount, self.container_root_mount)

    def delete_mounts(self):
        """
        When detaching a volume from a container we need to also remove the
        mount statements from the CTID.mount file.
        """
        self.delete(self.host_mount_line())
        self.delete(self.container_mount_line())

    def add_container_mount_line(self):
        """
        Add the generated container mount line to the CTID.mount script
        """
        self.append(self.container_mount_line())

    def add_host_mount_line(self):
        """
        Add the generated host mount line to the CTID.mount script
        """
        self.append(self.host_mount_line())

    def make_host_mount_point(self):
        """
        Create the host mount point if it doesn't exist.  This is required
        to allow for container startup.
        """
        self.make_dir(self.host_mount)

    def make_container_mount_point(self):
        """
        Create the container private mount point if it doesn't exist.  This is
        required to happen before the container starts so that when it chroots
        in /vz/root/CTID the path will exist to match container_root_mount
        """
        self.make_dir(self.container_mount)

    def make_container_root_mount_point(self):
        """
        Unused at the moment but exists in case we reach a condition that the
        container starts and somehow make_container_mount_point didn't get run.
        """
        # TODO(imsplitbit): Look for areas this can be used.  Right now the
        # process is very prescibed so it doesn't appear necessary just yet.
        # We will need this in the future when we do more dynamic operations.
        self.make_dir(self.container_root_mount)


class OVZUmountFile(OVZMounts):
    """
    methods to be used for manipulating the CTID.umount files
    """
    def host_umount_line(self):
        """
        Generate a umount line to compliment the host mount line added for the
        filesystem in OVZMountFile
        """
        return self._umount_line(self.host_mount)

    def container_umount_line(self):
        """
        Generate a umount line to compliment the container mount line added for
        the filesystem in OVZMountFile
        """
        return self._umount_line(self.container_root_mount)

    @staticmethod
    def _umount_line(mount):
        """
        Helper method to assemble a umount line for CTID.umount.  This uses
        lazy and force to unmount the filesystem because in the condition that
        you are detaching a volume it is assumed that a potentially dirty
        filesystem isn't a concern and in the case that the container is just
        stopped the filesystem will already have all descriptors closed so the
        lazy forced unmount has no adverse affect.
        """
        return 'umount -l -f %s' % mount

    def add_host_umount_line(self):
        """
        Add the host umount line to the CTID.umount file
        """
        self.append(self.host_umount_line())

    def add_container_umount_line(self):
        """
        Add the container umount line to the CTID.umount file
        """
        self.append(self.container_umount_line())

    def delete_umounts(self):
        """
        In the case that we need to detach a volume from a container we need to
        remove the umount lines from the file object's contents.
        """
        self.delete(self.container_umount_line())
        self.delete(self.host_umount_line())

    def unmount_all(self):
        """
        Wrapper for unmounting both the container mounted filesystem and the
        original host mounted filesystem
        """
        # Unmount the container mount
        self.unmount(self.container_umount_line())

        # Now unmount the host mount
        self.unmount(self.host_umount_line)

    @staticmethod
    def unmount(mount_line):
        """
        Helper method to use nova commandline utilities to unmount the
        filesystem given as an argument.
        """
        try:
            out, err = utils.execute(mount_line.split())
            LOG.debug(_('Stdout output from umount: %s') % out)
            if err:
                LOG.error(_('Stderr output from umount: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from umount: %s') % err)
            raise exception.Error(_('Failed to umount: "%s"') % mount_line)


class OVZVolumes(object):
    """
    This Class is a helper class to manage the mount and umount files
    for a given container.
    """
    def __init__(self, instance_id, mount, dev=None, uuid=None):
        self.instance_id = instance_id
        self.mountpoint = mount
        self.device = dev
        self.uuid = uuid
        self.mountfile = '%s/%s.mount' % (FLAGS.ovz_config_dir,
                                          self.instance_id)
        self.umountfile = '%s/%s.umount' % (FLAGS.ovz_config_dir,
                                            self.instance_id)
        self.mountfile = os.path.abspath(self.mountfile)
        self.umountfile = os.path.abspath(self.umountfile)

    def setup(self):
        """
        Prep the paths and files for manipulation.
        """
        # Create the file objects
        self.mountfh = OVZMountFile(self.mountfile, self.mountpoint,
                                    self.instance_id, self.device, self.uuid)
        self.umountfh = OVZUmountFile(self.umountfile, self.mountpoint,
                                      self.instance_id, self.device, self.uuid)

        # Create the files if they don't exist
        self.mountfh.touch()
        self.umountfh.touch()

        # Fix the permissions on the files to allow this script to edit them
        self.mountfh.set_permissions(777)
        self.umountfh.set_permissions(777)

        # Now open the mount/umount files and read their contents
        self.mountfh.read()
        self.umountfh.read()

        # Fixup the mount and umount files to have proper shell script
        # header otherwise it will be rejected by vzctl
        self.mountfh.format()
        self.umountfh.format()

    def attach(self):
        # Create the mount point on the host node
        self.mountfh.make_host_mount_point()
        # Create a mount point for the device inside the root of the container
        self.mountfh.make_container_mount_point()

        # Add the host and container mount lines to the mount script
        self.mountfh.add_host_mount_line()
        self.mountfh.add_container_mount_line()

        # Add umount lines to the umount script
        self.umountfh.add_container_umount_line()
        self.umountfh.add_host_umount_line()

    def detach(self):
        # Unmount the storage if possible
        self.umountfh.unmount_all()

        # If the lines of the mount and unmount statements are in the
        # container mount and umount files, remove them.
        self.mountfh.delete_mounts()
        self.umountfh.delete_umounts()

        # Remove the mount directory
        self.umountfh.delete_full_mount_path()

    def write_and_close(self):
        # Reopen the files and write the contents to them
        self.mountfh.write()
        self.umountfh.write()

        # Finish by setting the permissions back to more secure permissions
        self.mountfh.set_permissions(755)
        self.umountfh.set_permissions(755)


class OVZNetworkBridgeDriver(VIFDriver):
    """
    VIF driver for a Linux Bridge
    """

    def plug(self, instance, network, mapping):
        """
        Ensure that the bridge exists and add a vif to it.
        """
        if (not network.get('should_create_bridge') and
            mapping.get('should_create_vlan')):
            if mapping.get('should_create_vlan'):
                LOG.debug(_('Ensuring bridge %(bridge)s and vlan %(vlan)s') %
                        {'bridge': network['bridge'],
                         'vlan': network['vlan']})
                linux_net.LinuxBridgeInterfaceDriver.ensure_vlan_bridge(
                        network['vlan'],
                        network['bridge'],
                        network['bridge_interface'])
            else:
                LOG.debug(_('Ensuring bridge %s') % network['bridge'])
                linux_net.LinuxBridgeInterfaceDriver.ensure_bridge(
                        network['bridge'],
                        network['bridge_interface'])

    def unplug(self, instance, network, mapping):
        """
        No manual unplugging required
        """
        pass


class OVZNetworkInterfaces(object):
    """
    Helper class for managing interfaces in OpenVz
    """
    #TODO(imsplitbit): fix this to work with redhat based distros
    def __init__(self, interface_info):
        """
        I exist to manage the network interfaces for your OpenVz containers.
        """
        self.interface_info = interface_info

    def add(self):
        """
        I add all interfaces and addresses to the container.
        """
        if FLAGS.ovz_use_veth_devs:
            for net_dev in self.interface_info:
                self._add_netif(net_dev['id'], net_dev['name'],
                                net_dev['bridge'], net_dev['mac'])

            self._load_template()
            self._fill_templates()
        else:
            for net_dev in self.interface_info:
                self._add_ip(net_dev['id'], net_dev['address'])

        self._set_nameserver(net_dev['id'], net_dev['dns'])

    def _load_template(self):
        """
        I load templates needed for network interfaces.
        """
        if FLAGS.ovz_use_veth_devs:
            if not FLAGS.ovz_use_dhcp:
                self.template = open(FLAGS.injected_network_template).read()
            else:
                #TODO(imsplitbit): make a cheetah template for DHCP interfaces
                # when using veth devices.
                self.template = None
        else:
            self.template = None

    def _fill_templates(self):
        """
        I iterate through each file necessary for creating interfaces on a
        given platform, open the file and write the contents of the template
        to the file.
        """
        for filename in self._filename_factory():
            network_file = OVZNetworkFile(filename)
            self.iface_file = str(
                Template(self.template,
                         searchList=[{'interfaces': self.interface_info,
                                      'use_ipv6': FLAGS.use_ipv6}]))
            network_file.append(self.iface_file.split('\n'))
            network_file.set_permissions(777)
            network_file.write()
            network_file.set_permissions(644)

    def _filename_factory(self, variant='debian'):
        """
        I generate a path for the file needed to implement an interface
        """
        #TODO(imsplitbit): Figure out how to introduce OS hints into nova
        # so we can generate support for redhat based distros.  This will
        # require an os hint to be placed in glance to use for making
        # decisions.  Then we can create a generator that will generate
        # redhat style interface paths like:
        #
        # /etc/sysconfig/network-scripts/ifcfg-eth0
        #
        # for now, we just return the debian path.

        redhat_path = '/etc/sysconfig/network-scripts/'
        debian_path = '/etc/network/interfaces'
        prefix = '%(private_dir)s/%(instance_id)s' % \
                 {'private_dir': FLAGS.ovz_ve_private_dir,
                  'instance_id': self.interface_info[0]['id']}
        prefix = os.path.abspath(prefix)

        #TODO(imsplitbit): fix this placeholder for RedHat compatibility.
        if variant == 'redhat':
            for net_dev in self.interface_info:
                path = prefix + redhat_path + ('ifcfg-%s' % net_dev['name'])
                path = os.path.abspath(path)
                LOG.debug(_('Generated filename %(path)s') % locals())
                yield path
        elif variant == 'debian':
            path = prefix + debian_path
            path = os.path.abspath(path)
            LOG.debug(_('Generated filename %(path)s') % locals())
            yield path
        else:
            raise exception.Error(_('Variant %(variant)s is not known',
                                    locals()))

    def _add_netif(self, instance_id, netif, bridge, host_mac):
        """
        I am a work around to add the eth devices the way OpenVZ
        wants them.

        When I work, I run a command similar to this:
        vzctl set 1 --save --netif_add \
            eth0,,veth1.eth0,11:11:11:11:11:11,br100
        """
        try:
            # Command necessary to create a bridge networking setup.
            # right now this is the only supported networking model
            # in the openvz connector.
            host_if = 'veth%s.%s' % (instance_id, netif)

            out, err = utils.execute('vzctl', 'set', instance_id,
                                     '--save', '--netif_add',
                                     '%s,,%s,%s,%s' %
                                     (netif, host_if, host_mac, bridge),
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)

        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(
                    'Error adding network device to container %s' %
                    instance_id)

    def _add_ip(self, instance_id, ip):
        """
        I add an IP address to a container if you are not using veth devices.

        I run the command:

        vzctl set <ctid> --save --ipadd <ip>

        If I fail to run an exception is raised as this indicates a failure to
        create a network available connection within the container thus making
        it unusable to all but local users and therefore unusable to nova.
        """
        try:
            out, err = utils.execute('vzctl', 'set', instance_id,
                                     '--save', '--ipadd', ip, run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)

        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Error adding %(ip)s to %(instance_id)s' %
                                    {'ip': ip, 'instance_id': instance_id}))

    def _set_nameserver(self, instance_id, dns):
        """
        Get the nameserver for the assigned network and set it using
        OpenVz's tools.

        I run the command:

        vzctl set <ctid> --save --nameserver <nameserver>

        If I fail to run an exception is raised as this will indicate
        the container's inability to do name resolution.
        """
        try:
            out, err = utils.execute('vzctl', 'set', instance_id,
                                     '--save', '--nameserver', dns,
                                     run_as_root=True)
            LOG.debug(_('Stdout output from vzctl: %s') % out)
            if err:
                LOG.error(_('Stderr output from vzctl: %s') % err)
        except ProcessExecutionError as err:
            LOG.error(_('Stderr output from vzctl: %s') % err)
            raise exception.Error(_('Unable to set nameserver for %s') %
            instance_id)


class OVZNetworkFile(OVZFile):
    """
    An abstraction for network files.  I am necessary for multi-platform
    support.  OpenVz runs on all linux distros and can host all linux distros
    but they don't all create interfaces the same way.  I make it easy to add
    interface files to all flavors of linux.
    """

    def __init__(self, filename):
        super(OVZNetworkFile, self).__init__(filename)
