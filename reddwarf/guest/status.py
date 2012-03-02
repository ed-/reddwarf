# Copyright 2011 OpenStack LLC.
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

"""Status of the application on the guest."""

class GuestStatus(object):
    _lookup = {}

    def __init__(self, code, description):
        self._code = code
        self._description = description
        GuestStatus._lookup[code] = self

    @property
    def code(self):
        return self._code

    @property
    def description(self):
        return self._description

    def __eq__(self, other):
        if isinstance(other, GuestStatus):
            return self.code == other.code
        if type(other) == int:
            return self.code == other
        if type(other) == str:
            return self.description == other

    @staticmethod
    def from_code(code):
        if code not in GuestStatus._lookup:
            msg = 'Status code %d is not a valid GuestStatus.'
            raise ValueError(msg % code)
        return GuestStatus._lookup[code]

    @staticmethod
    def from_description(desc):
        all_items = GuestStatus._lookup.items()
        status_codes = [code for (code, status) in all_items if status == desc]
        if not status_codes:
            msg = 'Status description %s is not a valid GuestStatus.'
            raise ValueError(msg % desc)
        return GuestStatus._lookup[status_codes[0]]

    @staticmethod
    def is_valid_code(code):
        return code in GuestStatus._lookup

RUNNING  = GuestStatus(0x01, 'RUNNING')
BLOCKED  = GuestStatus(0x02, 'BLOCKED')
PAUSED   = GuestStatus(0x03, 'PAUSED')
SHUTDOWN = GuestStatus(0x04, 'SHUTDOWN')
CRASHED  = GuestStatus(0x06, 'CRASHED')
FAILED   = GuestStatus(0x08, 'FAILED')
BUILDING = GuestStatus(0x09, 'BUILDING')
UNKNOWN  = GuestStatus(0x16, 'UNKNOWN')

GuestStatus.__init__ = None
