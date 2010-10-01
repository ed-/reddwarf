# vim: tabstop=4 shiftwidth=4 softtabstop=4

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
Admin API controller, exposed through http via the api worker.
"""

import base64

from nova import db
from nova import exception
from nova.auth import manager


def user_dict(user, base64_file=None):
    """Convert the user object to a result dict"""
    if user:
        return {
            'username': user.id,
            'accesskey': user.access,
            'secretkey': user.secret,
            'file': base64_file}
    else:
        return {}


def project_dict(project):
    """Convert the project object to a result dict"""
    if project:
        return {
            'projectname': project.id,
            'project_manager_id': project.project_manager_id,
            'description': project.description}
    else:
        return {}


def host_dict(host):
    """Convert a host model object to a result dict"""
    if host:
        return host.state
    else:
        return {}


class AdminController(object):
    """
    API Controller for users, hosts, nodes, and workers.
    """

    def __str__(self):
        return 'AdminController'

    def describe_user(self, _context, name, **_kwargs):
        """Returns user data, including access and secret keys."""
        return user_dict(manager.AuthManager().get_user(name))

    def describe_users(self, _context, **_kwargs):
        """Returns all users - should be changed to deal with a list."""
        return {'userSet':
            [user_dict(u) for u in manager.AuthManager().get_users()] }

    def register_user(self, _context, name, **_kwargs):
        """Creates a new user, and returns generated credentials."""
        return user_dict(manager.AuthManager().create_user(name))

    def deregister_user(self, _context, name, **_kwargs):
        """Deletes a single user (NOT undoable.)
           Should throw an exception if the user has instances,
           volumes, or buckets remaining.
        """
        manager.AuthManager().delete_user(name)

        return True

    def describe_roles(self, context, project_roles=True, **kwargs):
        """Returns a list of allowed roles."""
        roles = manager.AuthManager().get_roles(project_roles)
        return { 'roles': [{'role': r} for r in roles]}

    def describe_user_roles(self, context, user, project=None, **kwargs):
        """Returns a list of roles for the given user.
           Omitting project will return any global roles that the user has.
           Specifying project will return only project specific roles.
        """
        roles = manager.AuthManager().get_user_roles(user, project=project)
        return { 'roles': [{'role': r} for r in roles]}

    def modify_user_role(self, context, user, role, project=None,
                         operation='add', **kwargs):
        """Add or remove a role for a user and project."""
        if operation == 'add':
            manager.AuthManager().add_role(user, role, project)
        elif operation == 'remove':
            manager.AuthManager().remove_role(user, role, project)
        else:
            raise exception.ApiError('operation must be add or remove')

        return True

    def generate_x509_for_user(self, _context, name, project=None, **kwargs):
        """Generates and returns an x509 certificate for a single user.
           Is usually called from a client that will wrap this with
           access and secret key info, and return a zip file.
        """
        if project is None:
            project = name
        project = manager.AuthManager().get_project(project)
        user = manager.AuthManager().get_user(name)
        return user_dict(user, base64.b64encode(project.get_credentials(user)))

    def describe_project(self, context, name, **kwargs):
        """Returns project data, including member ids."""
        return project_dict(manager.AuthManager().get_project(name))

    def describe_projects(self, context, user=None, **kwargs):
        """Returns all projects - should be changed to deal with a list."""
        return {'projectSet':
            [project_dict(u) for u in
            manager.AuthManager().get_projects(user=user)]}

    def register_project(self, context, name, manager_user, description=None,
                         member_users=None, **kwargs):
        """Creates a new project"""
        return project_dict(
            manager.AuthManager().create_project(
                name,
                manager_user,
                description=None,
                member_users=None))

    def deregister_project(self, context, name):
        """Permanently deletes a project."""
        manager.AuthManager().delete_project(name)
        return True

    def describe_project_members(self, context, name, **kwargs):
        project = manager.AuthManager().get_project(name)
        result = {
            'members': [{'member': m} for m in project.member_ids]}
        return result

    def modify_project_member(self, context, user, project, operation, **kwargs):
        """Add or remove a user from a project."""
        if operation =='add':
            manager.AuthManager().add_to_project(user, project)
        elif operation == 'remove':
            manager.AuthManager().remove_from_project(user, project)
        else:
            raise exception.ApiError('operation must be add or remove')
        return True

    # FIXME(vish): these host commands don't work yet, perhaps some of the
    #              required data can be retrieved from service objects?
    def describe_hosts(self, _context, **_kwargs):
        """Returns status info for all nodes. Includes:
            * Disk Space
            * Instance List
            * RAM used
            * CPU used
            * DHCP servers running
            * Iptables / bridges
        """
        return {'hostSet': [host_dict(h) for h in db.host_get_all()]}

    def describe_host(self, _context, name, **_kwargs):
        """Returns status info for single node."""
        return host_dict(db.host_get(name))