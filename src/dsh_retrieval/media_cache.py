"""Bounded bytes cache. Callers must authorize every access before using it."""
from collections import OrderedDict
from threading import RLock


class ByteCache:
    def __init__(self, max_bytes=64 * 1024 * 1024, max_items=128):
        self.max_bytes, self.max_items = max_bytes, max_items
        self.items = OrderedDict()
        self.bytes = 0
        self.lock = RLock()

    def get_or_create(self, key, create):
        with self.lock:
            if key in self.items:
                value = self.items.pop(key)
                self.items[key] = value
                return value
            value = create()
            if len(value) > self.max_bytes:
                return value
            self.items[key] = value
            self.bytes += len(value)
            while len(self.items) > self.max_items or self.bytes > self.max_bytes:
                _, old = self.items.popitem(last=False)
                self.bytes -= len(old)
            return value
