"""Small process-local cache; identity and membership are never cached."""
from collections import OrderedDict
import threading
import time

class PhotoListCache:
    def __init__(self, ttl=5.0, max_bytes=8*1024*1024, max_entries=64, clock=time.monotonic):
        self.ttl=ttl
        self.max_bytes=max_bytes
        self.max_entries=max_entries
        self.clock=clock
        self.entries=OrderedDict()
        self.bytes=0
        self.generation=0
        self.lock=threading.Lock()

    def get(self,key):
        with self.lock:
            entry=self.entries.get(key)
            if entry:
                expires,body=entry
                if expires>self.clock():
                    self.entries.move_to_end(key)
                    return body,self.generation
                self.bytes-=len(body)
                del self.entries[key]
            return None,self.generation

    def put(self,key,body,generation):
        with self.lock:
            # A write completed while the database query ran: do not repopulate
            # the cache with a pre-write result.
            if generation!=self.generation or len(body)>self.max_bytes:
                return
            previous=self.entries.pop(key,None)
            if previous:self.bytes-=len(previous[1])
            while self.entries and (self.bytes+len(body)>self.max_bytes or len(self.entries)>=self.max_entries):
                _,(_,old)=self.entries.popitem(last=False)
                self.bytes-=len(old)
            self.entries[key]=(self.clock()+self.ttl,body)
            self.bytes+=len(body)

    def invalidate(self,family):
        with self.lock:
            self.generation+=1
            for key in list(self.entries):
                if key[0]==family:
                    self.bytes-=len(self.entries.pop(key)[1])
