"""File-versioned media lookup for the local search fixture, not a grant cache."""

import json
from pathlib import Path
from threading import Lock


class LocalMediaIndex:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = Lock()
        self._version = None
        self._bindings = {}

    def _file_version(self):
        stat = self.path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)

    def bindings_for(self, media_ref):
        with self._lock:
            version = self._file_version()
            if version != self._version:
                data = json.loads(self.path.read_bytes())
                bindings = {}
                for entry in data['records']:
                    for media in entry['media']:
                        bindings.setdefault(media['authorized_media_ref'], []).append(
                            entry['binding_id'])
                if self._file_version() != version:
                    raise ValueError('local media fixture changed while reading')
                self._bindings = {key: tuple(value) for key, value in bindings.items()}
                self._version = version
            return self._bindings.get(media_ref, ())
