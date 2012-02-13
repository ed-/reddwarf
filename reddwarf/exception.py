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

from webob import exc

from nova import exception as nova_exception


class BadRequest(exc.HTTPBadRequest, Exception):
    def __init__(self, message="The server could not comply with the request "
                       "since it is either malformed or otherwise incorrect."):
        self.explanation = message
        self.code = 400
        errstr = '%s: %s' % (self.code, self.explanation)
        super(BadRequest, self).__init__(errstr)


class Unauthorized(exc.HTTPUnauthorized, Exception):
    def __init__(self, message="The server could not verify that you are "
                               "authorized to access the requested resource"):
        self.explanation = message
        self.code = 401
        errstr = '%s: %s' % (self.code, self.explanation)
        super(Unauthorized, self).__init__(errstr)


class NotFound(exc.HTTPNotFound, Exception):
    def __init__(self, message="The resource could not be found"):
        self.explanation = message
        self.code = 404
        errstr = '%s: %s' % (self.code, self.explanation)
        super(NotFound, self).__init__(errstr)


class UnprocessableEntity(exc.HTTPUnprocessableEntity, Exception):
    def __init__(self, message="Unable to process the contained request"):
        self.explanation = message
        self.code = 422
        errstr = '%s: %s' % (self.code, self.explanation)
        super(UnprocessableEntity, self).__init__(errstr)


class InstanceFault(exc.HTTPServerError, Exception):
    def __init__(self, message="The server has either erred or is incapable "
                               "of performing the requested operation."):
        self.explanation = message
        self.code = 500
        errstr = '%s: %s' % (self.code, self.explanation)
        super(InstanceFault, self).__init__(errstr)


class NotImplemented(exc.HTTPNotImplemented, Exception):
    def __init__(self, message="The requested method is not implemented"):
        self.explanation = message
        self.code = 501
        errstr = '%s: %s' % (self.code, self.explanation)
        super(NotImplemented, self).__init__(errstr)


class ServiceUnavailable(exc.HTTPServiceUnavailable, Exception):
    def __init__(self, message="The service is not available at this time"):
        self.explanation = message
        self.code = 503
        errstr = '%s: %s' % (self.code, self.explanation)
        super(ServiceUnavailable, self).__init__(errstr)


class ConfigNotFound(nova_exception.NotFound):
    message = _("Configuration %(key)s not found.")


class RsDnsRecordNotFound(nova_exception.NotFound):
    message = _("RsDnsRecord with name= %(name)s not found.")


class DuplicateConfigEntry(nova_exception.NotFound):
    message = _("Configuration %(key)s already exists.")


class DuplicateRecordEntry(nova_exception.NotFound):
    message = _("Record with name %(name) or id=%(id) already exists.")


class DevicePathInvalidForUuid(nova_exception.NotFound):
    message = _("Could not get a UUID from device path %(device_path).")


class VolumeProvisioningError(nova_exception.NotFound):
    message = _("An error occured provisioning volume %(volume_id)s.")


class ISCSITargetNotDiscoverable(nova_exception.NotFound):
    message = _("Target for volume %(volume_id)s not found.")


class OutOfInstanceMemory(nova_exception.NovaException):
    message = _("Scheduler unable to find a host with memory left for an "
                "instance needing %(instance_memory_mb)s MB of RAM.")

class GuestError(nova_exception.NovaException):
    message = _("An error occurred communicating with the guest: "
                "%(original_message.")

class PollTimeOut(nova_exception.NovaException):
    message = _("Polling request timed out.")
