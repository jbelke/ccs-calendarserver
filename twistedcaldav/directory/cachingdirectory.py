##
# Copyright (c) 2009 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
import time
import types

"""
Caching directory service implementation.
"""

__all__ = [
    "CachingDirectoryService",
    "CachingDirectoryRecord",
    "DictRecordTypeCache",
]

class RecordTypeCache(object):
    """
    Abstract class for a record type cache. We will likely have dict and memcache implementations of this.
    """
    
    def __init__(self, directoryService, recordType):
        
        self.directoryService = directoryService
        self.recordType = recordType

    def addRecord(self, record):
        raise NotImplementedError()
    
    def removeRecord(self, record):
        raise NotImplementedError()
        
    def replaceRecord(self, oldRecord, newRecord):
        raise NotImplementedError()

    def findRecord(self, indexType, indexKey):
        raise NotImplementedError()
        
class DictRecordTypeCache(RecordTypeCache):
    """
    Cache implementation using a dict. Does not share the cache with other instances.
    """
    
    def __init__(self, directoryService, recordType):
        
        super(DictRecordTypeCache, self).__init__(directoryService, recordType)
        self.records = set()
        self.recordsIndexedBy = {
            CachingDirectoryService.INDEX_TYPE_GUID     : {},
            CachingDirectoryService.INDEX_TYPE_SHORTNAME: {},
            CachingDirectoryService.INDEX_TYPE_EMAIL    : {},
        }

    def addRecord(self, record):
        
        self.records.add(record)
        for indexType in self.directoryService.indexTypes():
            try:
                indexData = getattr(record, CachingDirectoryService.indexTypeToRecordAttribute[indexType])
            except AttributeError:
                continue
            if isinstance(indexData, str):
                indexData = (indexData,)
            if type(indexData) in (types.ListType, types.TupleType, set):
                for item in indexData:
                    self.recordsIndexedBy[indexType][item] = record
            elif indexData is None:
                pass
            else:
                raise AssertionError("Data from record attribute must be str, list or tuple")
    
    def removeRecord(self, record):
        
        if record in self.records:
            self.records.remove(record)
            for indexType in self.directoryService.indexTypes():
                try:
                    indexData = getattr(record, CachingDirectoryService.indexTypeToRecordAttribute[indexType])
                except AttributeError:
                    continue
                if isinstance(indexData, str):
                    indexData = (indexData,)
                if type(indexData) in (types.ListType, types.TupleType):
                    for item in indexData:
                        try:
                            del self.recordsIndexedBy[indexType][item]
                        except KeyError:
                            raise AssertionError("Missing record index item")
                else:
                    raise AssertionError("Data from record attribute must be str, list or tuple")
        
    def replaceRecord(self, oldRecord, newRecord):
        self.removeRecord(oldRecord)
        self.addRecord(newRecord)

    def findRecord(self, indexType, indexKey):
        return self.recordsIndexedBy[indexType].get(indexKey)

class CachingDirectoryService(DirectoryService):
    """
    Caching Directory implementation of L{IDirectoryService}.
    
    This is class must be overridden to provide a concrete implementation.
    """

    INDEX_TYPE_GUID      = "guid"
    INDEX_TYPE_SHORTNAME = "shortname"
    INDEX_TYPE_EMAIL     = "email"

    indexTypeToRecordAttribute = {
        "guid"     : "guid",
        "shortname": "shortNames",
        "email"    : "emailAddresses",
    }

    def __init__(
        self,
        cacheTimeout=30,
        cacheClass=DictRecordTypeCache,
    ):
        """
        @param cacheTimeout: C{int} number of minutes before cache is invalidated.
        """
        
        self.cacheTimeout = cacheTimeout * 60

        self._initCaches(cacheClass)

    def _initCaches(self, cacheClass):
        self._recordCaches = dict([
            (recordType, cacheClass(self, recordType))
            for recordType in self.recordTypes()
        ])
            
        self._disabledKeys = dict([(indexType, dict()) for indexType in self.indexTypes()])

    def indexTypes(self):
        
        return (
            CachingDirectoryService.INDEX_TYPE_GUID,
            CachingDirectoryService.INDEX_TYPE_SHORTNAME,
            CachingDirectoryService.INDEX_TYPE_EMAIL,
        )

    def recordCacheForType(self, recordType):
        return self._recordCaches[recordType]

    def listRecords(self, recordType):
        return self.recordCacheForType(recordType).records

    def recordWithShortName(self, recordType, shortName):
        return self._lookupRecord((recordType,), CachingDirectoryService.INDEX_TYPE_SHORTNAME, shortName)

    def recordWithEmailAddress(self, emailAddress):
        return self._lookupRecord(None, CachingDirectoryService.INDEX_TYPE_EMAIL, emailAddress)

    def recordWithGUID(self, guid):
        return self._lookupRecord(None, CachingDirectoryService.INDEX_TYPE_GUID, guid)

    recordWithUID = recordWithGUID

    def _lookupRecord(self, recordTypes, indexType, indexKey, cacheOnMiss=True):
        
        if recordTypes is None:
            recordTypes = self.recordTypes()

        def lookup():
            for recordType in recordTypes:
                record = self.recordCacheForType(recordType).findRecord(indexType, indexKey)
                if record:
                    if (time.time() - record.cachedTime > self.cacheTimeout):
                        return None
                    else:
                        return record
            else:
                return None

        record = lookup()
        if record:
            return record

        if cacheOnMiss:
            
            # Check negative cache (take cache entry timeout into account)
            try:
                disabledTime = self._disabledKeys[indexType][indexKey]
                if time.time() - disabledTime < self.cacheTimeout:
                    return None
            except KeyError:
                pass
            
            # Try query
            self.log_debug("Faulting record for attribute '%s' with value '%s'" % (indexType, indexKey,))
            self.queryDirectory(recordTypes, indexType, indexKey)
            
            # Now try again from cache
            record = lookup()
            if record:
                self.log_debug("Found record for attribute '%s' with value '%s'" % (indexType, indexKey,))
                return record
            
            # Add to negative cache with timestamp
            self.log_debug("Failed to fault record for attribute '%s' with value '%s'" % (indexType, indexKey,))
            self._disabledKeys[indexType][indexKey] = time.time()
            
        return None

    def queryDirectory(self, recordTypes, indexType, indexKey):
        raise NotImplementedError()

class CachingDirectoryRecord(DirectoryRecord):

    def __init__(
        self, service, recordType, guid, shortNames=(), authIDs=set(),
        fullName=None, firstName=None, lastName=None, emailAddresses=set(),
        calendarUserAddresses=set(), autoSchedule=False,
        enabledForCalendaring=None, uid=None,
    ):
        super(CachingDirectoryRecord, self).__init__(
            service               = service,
            recordType            = recordType,
            guid                  = guid,
            shortNames            = shortNames,
            authIDs               = authIDs,
            fullName              = fullName,
            firstName             = firstName,
            lastName              = lastName,
            emailAddresses        = emailAddresses,
            calendarUserAddresses = calendarUserAddresses,
            autoSchedule          = autoSchedule,
            enabledForCalendaring = enabledForCalendaring,
            uid                   = uid,
        )
        
        self.cachedTime = time.time()
