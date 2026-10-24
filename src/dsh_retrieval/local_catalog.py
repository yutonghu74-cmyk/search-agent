"""Explicit local catalogue grants; never a production identity/connection provider."""
import json
from pathlib import Path

from .errors import ServiceError
from .integration_models import IndexBinding, Library
from .text_catalog_config import TextCatalogConfigService


class LocalCatalogSource:
    def __init__(self, path):
        self.path = Path(path)
        self.load()

    def load(self):
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError()
            data = json.loads(raw)
            if set(data) != {'schema', 'libraries', 'grants'} or data['schema'] != 'local-catalog@1':
                raise ValueError()
            if not isinstance(data['libraries'], list) or len(data['libraries']) > 256:
                raise ValueError()
            libraries = []
            for item in data['libraries']:
                if set(item) != {'library_id', 'description', 'media_type', 'bindings'}:
                    raise ValueError()
                if not isinstance(item['bindings'], list) or len(item['bindings']) > 64:
                    raise ValueError()
                libraries.append(Library(item['library_id'], item['description'], item['media_type'],
                    tuple(IndexBinding(**binding) for binding in item['bindings'])))
            ids = [lib.library_id for lib in libraries]
            if any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids):
                raise ValueError()
            if not isinstance(data['grants'], list) or len(data['grants']) > 1000:
                raise ValueError()
            scopes = set()
            for grant in data['grants']:
                if set(grant) != {'tenant_id', 'owner_id', 'project_id', 'library_ids', 'can_configure'}:
                    raise ValueError()
                key = tuple(grant[field] for field in ('tenant_id', 'owner_id', 'project_id'))
                if (not all(isinstance(v, str) and 0 < len(v.strip()) <= 128 for v in key)
                        or key in scopes or type(grant['can_configure']) is not bool
                        or not isinstance(grant['library_ids'], list)
                        or not all(isinstance(v, str) for v in grant['library_ids'])
                        or len(set(grant['library_ids'])) != len(grant['library_ids'])
                        or not set(grant['library_ids']) <= set(ids)):
                    raise ValueError()
                scopes.add(key)
            return tuple(libraries), data['grants']
        except (OSError, ValueError, TypeError, KeyError):
            raise ServiceError('CATALOG_CONFIG_UNAVAILABLE', 'Trusted local catalog is invalid or unavailable', 503) from None

    def inventory(self, scope):
        return self.load()[0]

    def authorize(self, scope, action, library_id):
        for grant in self.load()[1]:
            if (grant['tenant_id'], grant['owner_id'], grant['project_id']) == scope.key():
                if action == 'configure':
                    return grant['can_configure']
                return action in ('discover', 'search') and library_id in grant['library_ids']
        return False


def build_local_catalog(path, ledger, runtime):
    source = LocalCatalogSource(path)
    return TextCatalogConfigService(ledger, runtime, source.inventory, source.authorize)
