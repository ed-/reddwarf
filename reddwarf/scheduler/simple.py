# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010 Openstack, LLC.
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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
Simple Scheduler
"""

from nova import db
from nova import flags
from nova import utils
from nova import log as logging
from nova.compute import power_state
from nova.compute import vm_states
from nova.notifier import api as notifier
from nova.scheduler import driver
from nova.scheduler import chance

from reddwarf.db import api as db_api
from reddwarf.exception import OutOfInstanceMemory

FLAGS = flags.FLAGS
flags.DEFINE_integer("max_cores", 16,
                     "maximum number of instance cores to allow per host")
flags.DEFINE_integer("max_gigabytes", 10000,
                     "maximum number of volume gigabytes to allow per host")
flags.DEFINE_integer("max_networks", 1000,
                     "maximum number of networks to allow per host")
flags.DEFINE_integer("max_instance_memory_mb", 1024 * 15,
                     "maximum amount of memory a host can use on instances")

LOG = logging.getLogger('nova.scheduler.simple')


def publisher_id(host=None):
    return notifier.publisher_id("scheduler", host)


class SimpleScheduler(chance.ChanceScheduler):
    """Implements Naive Scheduler that tries to find least loaded host."""

    @staticmethod
    def _availability_zone_is_set(context, instance_ref):
        return (instance_ref['availability_zone']
                and ':' in instance_ref['availability_zone']
                and context.is_admin)

    def _schedule_based_on_availability_zone(self, context, instance_ref):
        _zone, _x, host = instance_ref['availability_zone'].partition(':')
        service = db.service_get_by_args(context.elevated(), host,
                                         'nova-compute')
        if not self.service_is_up(service):
            raise driver.WillNotSchedule(_("Host %s is not alive") % host)
        return self._schedule_now_on_host(context, host, instance_ref['id'])

    def _schedule_based_on_resources(self, context, instance_ref):
        results = db.service_get_all_compute_sorted(context)
        for result in results:
            (service, instance_cores) = result
            if instance_cores + instance_ref['vcpus'] > FLAGS.max_cores:
                raise driver.NoValidHost(_("All hosts have too many cores"))
            if self.service_is_up(service):
                # NOTE(vish): this probably belongs in the manager, if we
                #             can generalize this somehow
                return self._schedule_now_on_host(context, service['host'],
                                                  instance_ref['id'])
        raise driver.NoValidHost(_("Scheduler was unable to locate a host"
                                   " for this request. Is the appropriate"
                                   " service running?"))

    def schedule_run_instance(self, context, instance_id, *_args, **_kwargs):
        return self._schedule_instance(context, instance_id, *_args, **_kwargs)

    def schedule_start_instance(self, context, instance_id, *_args, **_kwargs):
        return self._schedule_instance(context, instance_id, *_args, **_kwargs)

    @staticmethod
    def _schedule_now_on_host(context, host, instance_id):
        """Schedule the instance to run now on the given host."""
        # TODO(vish): this probably belongs in the manager, if we
        #             can generalize this somehow
        now = utils.utcnow()
        db.instance_update(context, instance_id,
                           {'host': host, 'scheduled_at': now})
        return host

    def _schedule_instance(self, context, instance_id, *_args, **_kwargs):
        """Picks a host that is up and has the fewest running instances."""
        instance_ref = db.instance_get(context, instance_id)
        if self._availability_zone_is_set(context, instance_ref):
            return self._schedule_based_on_availability_zone(context,
                                                             instance_ref)
        else:
            return self._schedule_based_on_resources(context, instance_ref)

    def schedule_create_volume(self, context, volume_id, *_args, **_kwargs):
        """Picks a host that is up and has the fewest volumes."""
        volume_ref = db.volume_get(context, volume_id)
        if (volume_ref['availability_zone']
            and ':' in volume_ref['availability_zone']
            and context.is_admin):
            _zone, _x, host = volume_ref['availability_zone'].partition(':')
            service = db.service_get_by_args(context.elevated(), host,
                                             'nova-volume')
            if not self.service_is_up(service):
                raise driver.WillNotSchedule(_("Host %s not available") % host)

            # TODO(vish): this probably belongs in the manager, if we
            #             can generalize this somehow
            now = utils.utcnow()
            db.volume_update(context, volume_id, {'host': host,
                                                  'scheduled_at': now})
            return host
        results = db.service_get_all_volume_sorted(context)
        for result in results:
            (service, volume_gigabytes) = result
            if volume_gigabytes + volume_ref['size'] > FLAGS.max_gigabytes:
                raise driver.NoValidHost(_("All hosts have too many "
                                           "gigabytes"))
            if self.service_is_up(service):
                # NOTE(vish): this probably belongs in the manager, if we
                #             can generalize this somehow
                now = utils.utcnow()
                db.volume_update(context,
                                 volume_id,
                                 {'host': service['host'],
                                  'scheduled_at': now})
                return service['host']
        raise driver.NoValidHost(_("Scheduler was unable to locate a host"
                                   " for this request. Is the appropriate"
                                   " service running?"))

    def schedule_set_network_host(self, context, *_args, **_kwargs):
        """Picks a host that is up and has the fewest networks."""

        results = db.service_get_all_network_sorted(context)
        for result in results:
            (service, instance_count) = result
            if instance_count >= FLAGS.max_networks:
                raise driver.NoValidHost(_("All hosts have too many networks"))
            if self.service_is_up(service):
                return service['host']
        raise driver.NoValidHost(_("Scheduler was unable to locate a host"
                                   " for this request. Is the appropriate"
                                   " service running?"))


class MemoryScheduler(SimpleScheduler):
    """Implements Naive Scheduler to find a host with the most free memory."""

    def _schedule_based_on_resources(self, context, instance_ref):
        results = db_api.service_get_all_compute_memory(context)
        for result in results:
            (service, memory_mb) = result
            needed_memory = memory_mb + instance_ref['memory_mb']
            if needed_memory <= FLAGS.max_instance_memory_mb and \
               self.service_is_up(service):
                LOG.debug("Scheduling instance %s" % 
                          instance_ref['display_name'])
                return self._schedule_now_on_host(context, service['host'],
                                                  instance_ref['id'])
        LOG.debug("Error scheduling %s" % instance_ref['display_name'])
        raise driver.NoValidHost(_("Insufficient memory on all hosts."))


class UnforgivingMemoryScheduler(MemoryScheduler):
    """When NoValidHosts is thrown, this sets the instance state to FAILED.

    We could just issue a notification in the MemoryScheduler when throwing
    NoValidHost, but I'm concerned is that the manager might be changed so that
    NoValidHost means "try again later." Since we're setting the instance to
    failed we don't want it to ever try again and we raise OutOfInstanceMemory.

    """

    def schedule_run_instance(self, context, instance_id, *_args, **_kwargs):
        base = super(UnforgivingMemoryScheduler, self)
        try:
            return base.schedule_run_instance(context, instance_id,
                                              *_args, **_kwargs)
        except driver.NoValidHost:
            db.instance_update(context, 
                               instance_id, 
                               {'power_state': power_state.FAILED,
                                'vm_state': vm_states.ERROR})
            memory_mb = db.instance_get(context, instance_id)['memory_mb']
            notifier.notify(publisher_id(), 'out.of.instance.memory',
                            notifier.ERROR,
                            {"requested_instance_memory_mb": memory_mb})
            raise OutOfInstanceMemory(instance_memory_mb=memory_mb)

#    def schedule_resize_in_place(self, topic, instance_id, instance_type_id):
#        """
#        Schedule to resize the instance type (flavor id)
#
#        Workflow:
#        look up host
#        check for space on host
#        if not enough space:
#            call compute api update status to ACTIVE
#        we have space to resize:
#            call compute api resize_on_host
#
#        """
#        LOG.debug("reddwarf unforgiving memory scheduler")
#        instance_ref = db.instance_get(context, instance_id)
#        host = instance_ref['host']
#
#        self.assert_compute_node_has_enough_memory(context, instance_ref, host)
#
#        return "host"