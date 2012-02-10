#    Copyright 2012 OpenStack LLC
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

from nova import exception
from nova import flags
from nova import log as logging
from nova import rpc
from nova.compute import api as nova_compute_api
from nova.compute import instance_types
from nova.compute import task_states
from nova.compute import vm_states
from nova.scheduler import api as scheduler_api
from reddwarf import exception as reddwarf_exception
from reddwarf.db import api as dbapi


FLAGS = flags.FLAGS

LOG = logging.getLogger(__name__)


class API(nova_compute_api.API):
    """Modified Compute API supporting reddwarf specific features"""

    def __init__(self, *args, **kwargs):
        super(API, self).__init__(*args, **kwargs)

    @scheduler_api.reroute_compute("resize_in_place")
    def resize_in_place(self, context, instance_id, new_instance_type_id):
        """Resize an instance on its host."""
        instance_ref = self._get_instance(context, instance_id,
                                          'resize_in_place')
        def get_memory_mb(instance_type_id):
            inst_type = instance_types.get_instance_type(instance_type_id)
            return inst_type['memory_mb']

        old_size = get_memory_mb(instance_ref['instance_type_id'])
        new_size = get_memory_mb(new_instance_type_id)
        diff_size = new_size - old_size
        if diff_size < 0:
            raise exception.CannotResizeToSmallerSize()
        host = instance_ref['host']
        host_mem_used = dbapi.instance_get_memory_sum_by_host(context, host)
        if host_mem_used + diff_size > FLAGS.max_instance_memory_mb:
            raise reddwarf_exception.OutOfInstanceMemory()
        self.update(context, instance_id, vm_state=vm_states.RESIZING)
        params={'new_instance_type_id': new_instance_type_id}
        self._cast_compute_message("resize_in_place", context, instance_id,
                                   params=params)

    def resize_volume(self, context, volume_id):
        """
        Rescan and resize the attached volume filesystem once the actual volume
        resizing has been completed.
        """
        context = context.elevated()
        instance = self.db.volume_get_instance(context, volume_id)
        if not instance:
            raise exception.Error(_("Volume isn't attached to anything!"))
        self.update(context,
                    instance['id'],
                    vm_state=vm_states.RESIZING,
                    task_state=task_states.RESIZE_PREP)
        rpc.cast(context,
                 self.db.queue_get_for(context, FLAGS.compute_topic,
                                       instance['host']),
                 {"method": "resize_volume",
                 "args": {"instance_id": instance['id'],
                          "volume_id": volume_id}})

    @scheduler_api.reroute_compute("restart")
    def restart(self, context, instance_id):
        """Reboot the given instance."""
        self.update(context,
                    instance_id,
                    vm_state=vm_states.ACTIVE,
                    task_state=task_states.REBOOTING)
        self._cast_compute_message('restart', context, instance_id)
