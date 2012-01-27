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

"""
API Interface for reddwarf datastore operations
"""

import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql import func
from sqlalchemy.sql import text

from nova import exception as nova_exception
from nova import flags
from nova import log as logging
from nova.exception import DBError
from nova.db.sqlalchemy import api as nova_db
from nova.db.sqlalchemy import models as nova_models
from nova.db.sqlalchemy.api import require_admin_context
from nova.db.sqlalchemy.api import require_context
from nova.db.sqlalchemy.models import FixedIp
from nova.db.sqlalchemy.models import Instance
from nova.db.sqlalchemy.models import InstanceTypes
from nova.db.sqlalchemy.models import Service
from nova.db.sqlalchemy.models import Volume
from nova.db.sqlalchemy.session import get_session
from nova.compute import power_state

from reddwarf import exception
from reddwarf.db import models

FLAGS = flags.FLAGS
LOG = logging.getLogger('reddwarf.db.api')

def guest_status_create(instance_id):
    """Create a new guest status for the instance

    :param instance_id: instance id for the guest
    """
    guest_status = models.GuestStatus()
    state = power_state.BUILDING
    guest_status.update({'instance_id': instance_id,
                         'state': state,
                         'state_description': power_state.name(state)})

    session = get_session()
    with session.begin():
        guest_status.save(session=session)
    return guest_status


def guest_status_get(instance_id, session=None):
    """Get the status of the guest

    :param instance_id: instance id for the guest
    :param session: pass in a active session if available
    """
    if not session:
        session = get_session()
    result = session.query(models.GuestStatus).\
                         filter_by(instance_id=instance_id).\
                         filter_by(deleted=False).\
                         first()
    if not result:
        raise nova_exception.InstanceNotFound(instance_id=instance_id)
    return result

def guest_status_get_list(instance_ids, session=None):
    """Get the status of the given guests

    :param instance_ids: list of instance ids for the guests
    :param session: pass in a active session if available
    """
    if not session:
        session = get_session()
    ids = [str(id) for id in instance_ids]
    result = session.query(models.GuestStatus).\
                         filter(models.GuestStatus.instance_id.in_(ids)).\
                         filter_by(deleted=False)
    if not result:
        raise nova_exception.InstanceNotFound(instance_id=instance_ids)
    return result

def guest_status_update(instance_id, state, description=None):
    """Update the state of the guest with one of the valid states
       along with the description

    :param instance_id: instance id for the guest
    :param state: state id
    :param description: description of the state
    """
    if not description:
        description = power_state.name(state)

    session = get_session()
    with session.begin():
        session.query(models.GuestStatus).\
                filter_by(instance_id=instance_id).\
                update({'state': state,
                        'state_description': description})


def guest_status_delete(instance_id):
    """Set the specified instance state as deleted

    :param instance_id: instance id for the guest
    """
    state = power_state.SHUTDOWN
    session = get_session()
    with session.begin():
        session.query(models.GuestStatus).\
                filter_by(instance_id=instance_id).\
                update({'deleted': True,
                        'deleted_at': datetime.datetime.utcnow(),
                        'state': state,
                        'state_description': power_state.name(state)})

@require_admin_context
def show_instances_on_host(context, id):
    """Show all the instances that are on the given host id."""
    LOG.debug("show_instances_on_host id = %s" % str(id))
    session = get_session()
    with session.begin():
        count = session.query(Service).\
                        filter_by(host=id).\
                        filter_by(deleted=False).\
                        filter_by(disabled=False).count()
        if not count:
            raise nova_exception.HostNotFound(host=id)
        result = session.query(Instance).\
                        filter_by(host=id).\
                        filter_by(deleted=False).all()
    return result

@require_admin_context
def instances_mgmt_index(context, deleted=None):
    session = get_session()
    instances = session.query(Instance)
    if deleted is not None:
        instances = instances.filter_by(deleted=deleted)

    # Join to get the flavor types.
    # TODO(ed-): The join works, but the model doesn't hand over the columns.
    #instances = instances.join((InstanceTypes,
    #    Instance.instance_type_id == InstanceTypes.id))

    # Fetch the instance_types, or "flavors"
    flavors = session.query(InstanceTypes)

    # Fetch the IPs for mapping.
    ips = session.query(FixedIp).filter(FixedIp.instance_id != None)

    return instances.all(), flavors.all(), ips.all()

@require_admin_context
def instance_get_by_state_and_updated_before(context, state, time):
    """Finds instances in a specific state updated before some time."""
    session = get_session()
    result = session.query(Instance).\
                      filter_by(deleted=False).\
                      filter_by(state=state).\
                      filter(Instance.updated_at < time).\
                      all()
    if not result:
        return []
    return result


@require_admin_context
def instance_get_memory_sum_by_host(context, hostname):
    session = get_session()
    result = session.query(Instance).\
                      filter_by(host=hostname).\
                      filter_by(deleted=False).\
                      value(func.sum(Instance.memory_mb))
    if not result:
        return 0
    return result

@require_admin_context
def show_instances_by_account(context, id):
    """Show all the instances that are on the given account id."""
    LOG.debug("show_instances_by_account id = %s" % str(id))
    # This is the management API, so we want all the instances,
    # regardless of status.
    session = get_session()
    with session.begin():
        return session.query(Instance).\
                        filter_by(user_id=id).\
                        filter_by(deleted=False).\
                        order_by(Instance.host).all()
    raise nova_exception.UserNotFound(user_id=id)


@require_admin_context
def volume_get_by_state_and_updated_before(context, state, time):
    """Finds instances in a specific state updated before some time."""
    session = get_session()
    result = session.query(Instance).\
                      filter_by(deleted=False).\
                      filter_by(state=state).\
                      filter(Instance.updated_at < time).\
                      all()
    if not result:
        return []
    return result

@require_admin_context
def volume_get_orphans(context, latest_time):
    session = get_session()
    result = session.query(Volume).\
                     filter_by(deleted=False).\
                     filter_by(instance_id=None).\
                     filter(Volume.status=='available').\
                     filter(Volume.updated_at < latest_time).\
                     all()                     
    return result

def get_root_enabled_history(context, id):
    """
    Returns the timestamp recorded when root was first enabled for the
    given instance.
    """
    LOG.debug("Get root enabled timestamp for instance %s" % id)
    session = get_session()
    try:
        result = session.query(models.RootEnabledHistory).\
                     filter_by(instance_id=id).one()
    except NoResultFound:
        LOG.debug("No root enabled timestamp found for instance %s." % id)
        return None
    LOG.debug("Found root enabled timestamp for instance %s: %s by %s"
              % (id, result.created_at, result.user_id))
    return result

def record_root_enabled_history(context, id, user):
    """
    Records the current time in nova.root_enabled_history so admins can see
    when and if root was ever enabled for a database on an instance.
    """
    LOG.debug("Record root enabled timestamp for instance %s" % id)
    old_record = get_root_enabled_history(context, id)
    if old_record is not None:
        LOG.debug("Found existing root enabled history: %s" % old_record)
        return old_record
    LOG.debug("Creating new root enabled timestamp.")
    new_record = models.RootEnabledHistory()
    new_record.update({'instance_id': id, 'user_id': user})
    session = get_session()
    with session.begin():
        new_record.save()
    LOG.debug("New root enabled timestamp: %s" % new_record)
    return new_record

def config_create(key, value=None, description=None):
    """Create a new configuration entry

    :param key: configuration entry name
    :param value: configuration entry value
    :param description: configuration entry optional description
    """
    config = models.Config()
    config.update({'key': key,
                   'value': value,
                   'description': description})

    session = get_session()
    try:
        with session.begin():
            config.save(session=session)
        return config
    except Exception:
        raise exception.DuplicateConfigEntry(key=key)


def config_get(key, session=None):
    """Get the specified configuration item

    :param key: configuration entry name
    :param session: pass in a active session if available
    """
    if not session:
        session = get_session()
    result = session.query(models.Config).\
                         filter_by(key=key).\
                         filter_by(deleted=False).\
                         first()
    if not result:
        raise exception.ConfigNotFound(key=key)
    return result


def config_get_all(session=None):
    """Get all the active configuration values

    :param session: pass in a active session if available
    """
    if not session:
        session = get_session()
    result = session.query(models.Config).\
                         filter_by(deleted=False)
    if not result:
        raise exception.ConfigNotFound(key="All config values")
    return result


def config_update(key, value=None, description=None):
    """Update an existing configuration value

    :param key: configuration entry name
    :param value: configuration entry value
    :param description: configuration entry optional description
    """
    session = get_session()
    update_dict = {'value': value}
    if description:
        update_dict['description'] = description
    with session.begin():
        session.query(models.Config).\
                filter_by(key=key).\
                update(update_dict)


def config_delete(key):
    """Delete the specified configuration item

    :param key: configuration entry name
    """
    session = get_session()
    with session.begin():
        session.query(models.Config).\
                filter_by(key=key).\
                delete()


def localid_from_uuid(uuid):
    """
    Given an instance's uuid, retrieve the local instance_id for compatibility
    with nova. When nova uses uuids exclusively, this function will not be
    needed.
    """
    LOG.debug("Retrieving local id for instance %s" % uuid)
    session = get_session()
    try:
        result = session.query(Instance).filter_by(uuid=uuid).one()
        return result['id']
    except NoResultFound:
        LOG.debug("No such instance found.")
        raise exception.NotFound()


def rsdns_record_create(name, id):
    """
    Stores a record name / ID pair in the table rsdns_records.
    """
    LOG.debug("Storing RSDNS record information (id=%s, name=%s)."
              % (id, name))
    record = models.RsDnsRecord()
    record.update({'name': name,
                   'id': id})

    session = get_session()
    try:
        with session.begin():
            record.save(session=session)
        return record
    except DBError:
        raise exception.DuplicateRecordEntry(name=name, id=id)


def rsdns_record_get(name):
    """
    Stores a record name / ID pair in the table rsdns_records.
    """
    LOG.debug("Fetching RSDNS record with name=%s." % name)
    session = get_session()
    result = session.query(models.RsDnsRecord).\
                         filter_by(name=name).\
                         filter_by(deleted=False).\
                         first()
    if not result:
        raise exception.RsDnsRecordNotFound(name=name)
    return result


def rsdns_record_delete(name):
    """
    Deletes a dns record.
    """
    session = get_session()
    with session.begin():
        session.query(models.RsDnsRecord).\
                filter_by(name=name).\
                delete()


def rsdns_record_list():
    """
    Stores a record name / ID pair in the table rsdns_records.
    """
    LOG.debug("Fetching all RSDNS records.")
    session = get_session()
    if not session:
        session = get_session()
    result = session.query(models.RsDnsRecord)
    if not result:
        raise exception.RsDnsRecordNotFound(name=name)
    return result


@require_admin_context
def service_get_all_compute_memory(context):
    """Return a list of service nodes and the memory used at each.

    Most available memory is returned first.

    """
    session = get_session()
    with session.begin():
        # NOTE(tim.simpson): Identical to service_get_all_compute_sorted,
        #                    except memory_mb is retrieved instead of
        #                    instances.vcpus.
        topic = 'compute'
        label = 'instance_cores'
        subq = session.query(Instance.host,
                             func.sum(Instance.memory_mb).
                             label(label)).\
                             filter_by(deleted=False).\
                             group_by(Instance.host).\
                             subquery()
        return nova_db._service_get_all_topic_subquery(context, session, topic,
                                                       subq, label)


@require_context
def fixed_ip_get_by_instance_for_network(context, instance_id, bridge_name):
    session = get_session()
    rv = session.query(nova_models.FixedIp).\
                       options(joinedload('floating_ips')).\
                       filter_by(instance_id=instance_id).\
                       filter_by(deleted=False).\
                       join(nova_models.Network).\
                       filter_by(bridge=bridge_name).\
                       all()
    if not rv:
        raise nova_exception.FixedIpNotFoundForInstance(instance_id=instance_id)
    return rv


@require_context
def instance_state_get_all_filtered(context):
    """Returns a dictionary mapping instance IDs to their state."""
    session = get_session()
    query = session.query(nova_models.Instance).filter_by(deleted=False)

    if not context.is_admin:
        if context.project_id:
            results = query.filter_by(project_id=context.project_id).all()
        else:
            results = query.filter_by(user_id=context.user_id).all()
    else:
        results = query.all()

    return dict((result['id'], result['power_state']) for result in results)
