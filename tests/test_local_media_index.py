import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dsh_retrieval.local_media_index import LocalMediaIndex
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.integration_models import AccessContext
from dsh_retrieval.models import Actor
from dsh_retrieval.errors import ServiceError


class LocalMediaIndexTest(unittest.TestCase):
    def test_warm_media_lookup_does_not_cache_library_grants(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_text(json.dumps({'records': [
                {'binding_id': 'a', 'media': [{'authorized_media_ref': 'media:one'}]},
            ]}))
            library = SimpleNamespace(bindings=(
                SimpleNamespace(binding_id='a', library_id='lib-a'),))
            source = SimpleNamespace(allowed=True, load=lambda: ((library,), []))
            source.authorize = lambda *args: source.allowed
            authority = LocalFirstSearch._authority(
                SimpleNamespace(source=source, fixture_path=path))
            context = AccessContext(Actor('tenant-a', 'user-a'), 'task', 'retrieval', 'current')
            authority.require_access(context, 'media:one', 'read')
            source.allowed = False
            with self.assertRaises(ServiceError) as error:
                authority.require_access(context, 'media:one', 'read')
            self.assertEqual(error.exception.code, 'ACCESS_DENIED')

    def test_lookup_reuses_file_version_and_invalidates_removed_media(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_text(json.dumps({'records': [
                {'binding_id': 'a', 'media': [{'authorized_media_ref': 'media:one'}]},
                {'binding_id': 'b', 'media': [{'authorized_media_ref': 'media:one'}]},
            ]}))
            index = LocalMediaIndex(path)
            self.assertEqual(index.bindings_for('media:one'), ('a', 'b'))
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('reparsed')):
                self.assertEqual(index.bindings_for('media:one'), ('a', 'b'))
            path.write_text('{"records": []}')
            self.assertEqual(index.bindings_for('media:one'), ())
            path.write_text('broken')
            with self.assertRaises(ValueError):
                index.bindings_for('media:one')
            path.unlink()
            with self.assertRaises(OSError):
                index.bindings_for('media:one')
