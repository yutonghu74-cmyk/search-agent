"""Backend-only text configuration of existing indexes; never provisions resources."""
from contextlib import closing
from dataclasses import asdict, dataclass
import json
import sqlite3

from .errors import ServiceError
from .integration_models import IndexBinding, Library
from .query_spec import digest


def fail(code='INVALID_CATALOG_CONFIG', status=422):
    raise ServiceError(code, 'Catalog configuration validation failed', status)


def bounded(value, limit):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


@dataclass(frozen=True)
class CatalogConfigScope:
    tenant_id: str
    owner_id: str
    project_id: str

    def key(self):
        values = (self.tenant_id, self.owner_id, self.project_id)
        if not all(bounded(value, 128) for value in values):
            fail()
        return values


class TextCatalogConfigService:
    """Trusted callers supply authenticated scope, inventory and live authorization.

    authorize(scope, action, library_id_or_None) must return True explicitly.
    Actions: configure (project), discover/search (library). Exceptions propagate.
    This is not a public endpoint or a replacement for CatalogRepository.
    """

    def __init__(self, path, runtime, inventory, authorize):
        self.path = str(path)
        if self.path == ':memory:':
            raise ValueError('Persistent file required')
        self.runtime, self.inventory, self.authorize = runtime, inventory, authorize
        with closing(self._connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS catalog_configs (
                tenant TEXT, owner TEXT, project TEXT, version INTEGER NOT NULL,
                proposal TEXT NOT NULL, proposal_digest TEXT NOT NULL,
                PRIMARY KEY (tenant, owner, project))''')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _access(self, scope):
        if type(scope) is not CatalogConfigScope:
            fail()
        scope.key()
        if self.authorize(scope, 'configure', None) is not True:
            fail('ACCESS_DENIED', 403)

    def _catalog(self, scope):
        self._access(scope)
        rows = self.inventory(scope)
        if not isinstance(rows, tuple) or len(rows) > 256:
            fail()
        libraries, binding_ids = {}, set()
        for row in rows:
            if not isinstance(row, Library) or not bounded(row.library_id, 128):
                fail()
            # No descriptions or bindings of denied libraries reach the LLM.
            if self.authorize(scope, 'discover', row.library_id) is not True:
                continue
            if self.authorize(scope, 'search', row.library_id) is not True:
                continue
            if (row.library_id in libraries or row.media_type not in ('image', 'video', 'text')
                    or not bounded(row.description, 2000) or not isinstance(row.bindings, tuple)
                    or not 1 <= len(row.bindings) <= 64):
                fail()
            for binding in row.bindings:
                if (not isinstance(binding, IndexBinding) or binding.library_id != row.library_id
                        or not all(bounded(value, 128) for value in (binding.binding_id,
                            binding.index_version, binding.encoder_ref, binding.metric))
                        or type(binding.vector_dimension) is not int
                        or not 1 <= binding.vector_dimension <= 65536
                        or binding.binding_id in binding_ids):
                    fail()
                binding_ids.add(binding.binding_id)
            libraries[row.library_id] = Library(row.library_id, row.description, row.media_type,
                tuple(sorted(row.bindings, key=lambda b: b.binding_id)))
        return dict(sorted(libraries.items()))

    @staticmethod
    def _snapshot(catalog):
        return digest([asdict(row) for row in catalog.values()])

    @staticmethod
    def _entries(entries, catalog):
        if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
            fail()
        seen, result = set(), []
        for entry in entries:
            if (not isinstance(entry, dict)
                    or set(entry) != {'library_id', 'description', 'aliases', 'binding_ids'}):
                fail()
            library_id = entry['library_id']
            if (not isinstance(library_id, str) or library_id not in catalog or library_id in seen
                    or not bounded(entry['description'], 2000)):
                fail()
            seen.add(library_id)
            aliases, ids = entry['aliases'], entry['binding_ids']
            if (not isinstance(aliases, list) or len(aliases) > 30
                    or not all(bounded(v, 80) for v in aliases)
                    or len({v.strip().casefold() for v in aliases}) != len(aliases)
                    or not isinstance(ids, list) or not 1 <= len(ids) <= 64
                    or not all(isinstance(v, str) for v in ids)
                    or len(set(ids)) != len(ids)
                    or not set(ids) <= {b.binding_id for b in catalog[library_id].bindings}):
                fail()
            result.append({'library_id': library_id, 'description': entry['description'].strip(),
                           'aliases': [v.strip() for v in aliases], 'binding_ids': sorted(ids)})
        return sorted(result, key=lambda row: row['library_id'])

    def _row(self, db, scope):
        return db.execute('SELECT version, proposal, proposal_digest FROM catalog_configs '
                          'WHERE tenant=? AND owner=? AND project=?', scope.key()).fetchone()

    def preview(self, scope, text, llm_id):
        if not bounded(text, 4000):
            fail()
        catalog = self._catalog(scope)
        if not catalog:
            fail('NO_AVAILABLE_INDEX', 422)
        with closing(self._connect()) as db:
            row = self._row(db, scope)
        # Physical addresses, credentials and encoder parameters are never model outputs.
        candidates = [{'library_id': lib.library_id, 'description': lib.description,
                       'media_type': lib.media_type,
                       'binding_ids': [b.binding_id for b in lib.bindings]} for lib in catalog.values()]
        output = self.runtime.suggest_catalog(text, llm_id, candidates)
        entries = self._entries(output, catalog)
        if self._snapshot(catalog) != self._snapshot(self._catalog(scope)):
            fail('CATALOG_CHANGED', 409)
        proposal = {'schema': 'text-catalog-config@1', 'scope': asdict(scope),
                    'base_version': row[0] if row else 0,
                    'catalog_digest': self._snapshot(catalog), 'entries': entries}
        return {'proposal': proposal, 'proposal_digest': digest(proposal)}

    def save(self, scope, proposal, proposal_digest):
        catalog = self._catalog(scope)
        if (not isinstance(proposal, dict) or set(proposal) != {
                'schema', 'scope', 'base_version', 'catalog_digest', 'entries'}
                or proposal.get('schema') != 'text-catalog-config@1'
                or proposal.get('scope') != asdict(scope)
                or type(proposal.get('base_version')) is not int or proposal['base_version'] < 0
                or digest(proposal) != proposal_digest):
            fail()
        if proposal['catalog_digest'] != self._snapshot(catalog):
            fail('CATALOG_CHANGED', 409)
        if self._entries(proposal['entries'], catalog) != proposal['entries']:
            fail()
        # SQLite serializes compare-and-swap across independent processes/connections.
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if proposal['catalog_digest'] != self._snapshot(self._catalog(scope)):
                fail('CATALOG_CHANGED', 409)
            row = self._row(db, scope)
            if row and row[2] == proposal_digest:
                return row[0]
            version = row[0] if row else 0
            if version != proposal['base_version']:
                fail('CONFIG_VERSION_CONFLICT', 409)
            db.execute('INSERT OR REPLACE INTO catalog_configs VALUES (?,?,?,?,?,?)',
                       (*scope.key(), version + 1, json.dumps(proposal, ensure_ascii=False), proposal_digest))
            return version + 1

    def read(self, scope):
        catalog = self._catalog(scope)
        with closing(self._connect()) as db:
            row = self._row(db, scope)
        if row is None:
            return None
        proposal = json.loads(row[1])
        if digest(proposal) != row[2] or proposal['catalog_digest'] != self._snapshot(catalog):
            fail('CATALOG_CHANGED', 409)
        entries = self._entries(proposal['entries'], catalog)
        self._access(scope)
        return {'version': row[0], 'entries': entries, 'proposal_digest': row[2],
                'catalog_digest': proposal['catalog_digest']}

    def configured_libraries(self, scope):
        """Return existing Library objects; caller still enforces task-level authorization."""
        saved = self.read(scope)
        if saved is None:
            return ()
        catalog = self._catalog(scope)
        if self._snapshot(catalog) != saved['catalog_digest']:
            fail('CATALOG_CHANGED', 409)
        # Re-read to fence changes while obtaining the technical bindings.
        if self.read(scope) != saved:
            fail('CONFIG_VERSION_CONFLICT', 409)
        return tuple(Library(e['library_id'], e['description'] + '\n' + ' '.join(e['aliases']),
            catalog[e['library_id']].media_type,
            tuple(b for b in catalog[e['library_id']].bindings if b.binding_id in e['binding_ids']))
            for e in saved['entries'])
