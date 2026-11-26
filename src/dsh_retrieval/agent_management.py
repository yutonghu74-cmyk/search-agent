"""Opt-in project catalog over trusted, installed Agent releases.

Authorization and qualification evidence come from backend integrations. Nothing
in a request can install code, supply a connection, or declare itself qualified.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import sqlite3

from .errors import ServiceError, invalid
from .harness.contracts import RoleDefinition, RuntimeRelease
from .processing_requirements import identifier
from .query_spec import digest
from .service import require_key


@dataclass(frozen=True)
class InstalledAgent:
    implementation_ref: str
    display_name: str
    version: str
    role: RoleDefinition
    runtime: RuntimeRelease
    connection_config_ref: str

    def __post_init__(self):
        if (not all(identifier(v) for v in (self.implementation_ref, self.version, self.connection_config_ref))
                or not isinstance(self.display_name, str) or not 1 <= len(self.display_name) <= 100
                or type(self.role) is not RoleDefinition or type(self.runtime) is not RuntimeRelease):
            raise invalid('invalid installed Agent descriptor')

    def snapshot(self):
        return dict(implementation_ref=self.implementation_ref, display_name=self.display_name,
            version=self.version, role=self.role.role, role_release_ref=self.role.release_ref,
            role_digest=self.role.release_digest, output_contract=self.role.output_contract,
            runtime_release_ref=self.runtime.release_ref, runtime_digest=self.runtime.release_digest,
            artifact_digest=self.runtime.artifact_digest, adapter_id=self.runtime.adapter_id,
            connection_config_ref=self.connection_config_ref)


class AgentManagement:
    def __init__(self, path, *, installed, roles, adapters, authorize, authorize_release, qualify):
        """authorize(actor, project, operation) must raise on denial.

        qualify(actor, project, InstalledAgent, adapter) returns a trusted evidence
        reference after checking tools, cancellation, deadline, model/resource
        limits and unknown completion. probe alone is not that evidence.
        """
        if not all(callable(v) for v in (authorize, authorize_release, qualify)):
            raise ValueError('backend authorization and qualification required')
        self.path, self.adapters = str(path), adapters
        if self.path == ':memory:': raise ValueError('persistent catalog path required')
        self.installed = dict(installed)
        if any(type(v) is not InstalledAgent or k != v.implementation_ref for k, v in self.installed.items()):
            raise invalid('invalid installed Agent directory')
        if any(v.role.role not in roles or roles[v.role.role].release_digest != v.role.release_digest
               for v in self.installed.values()):
            raise invalid('installed Agent role/output contract differs from runner')
        self.authorize, self.authorize_release, self.qualify = authorize, authorize_release, qualify
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS agent_catalog (
                    tenant TEXT, project TEXT, ref TEXT, snapshot TEXT NOT NULL,
                    evidence TEXT, PRIMARY KEY(tenant, project, ref));
                CREATE TABLE IF NOT EXISTS agent_project_routes (
                    tenant TEXT, project TEXT, version INTEGER NOT NULL, routes TEXT NOT NULL,
                    PRIMARY KEY(tenant, project));
                CREATE TABLE IF NOT EXISTS agent_management_receipts (
                    tenant TEXT, project TEXT, user TEXT, key TEXT, request_digest TEXT NOT NULL,
                    response TEXT NOT NULL, PRIMARY KEY(tenant, project, user, key));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def _scope(self, actor, project, operation):
        if not identifier(project): raise invalid('invalid project')
        self.authorize(actor, project, operation)
        return actor.tenant_id, project

    def _installed(self, actor, project, ref, snapshot=None):
        item = self.installed.get(ref) if isinstance(ref, str) else None
        if item is None or snapshot is not None and item.snapshot() != snapshot:
            raise ServiceError('AGENT_IMPLEMENTATION_UNAVAILABLE', 'Installed Agent release is unavailable or changed', 409)
        self.authorize_release(actor, project, item)
        return item

    def _row(self, db, scope, ref):
        row = db.execute('SELECT * FROM agent_catalog WHERE tenant=? AND project=? AND ref=?', (*scope, ref)).fetchone()
        if row is None: raise ServiceError('AGENT_IMPLEMENTATION_NOT_FOUND', 'Agent implementation not found', 404)
        return row

    def _command(self, actor, project, key, operation, body, apply):
        scope = self._scope(actor, project, operation)
        require_key(key)
        request_digest = digest({'operation': operation, 'body': body})
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt_key = (*scope, actor.user_id, key)
            row = db.execute('SELECT * FROM agent_management_receipts WHERE tenant=? AND project=? AND user=? AND key=?', receipt_key).fetchone()
            if row:
                if row['request_digest'] != request_digest:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'Management request changed', 409)
                return json.loads(row['response']), True
            result = apply(db, scope)
            db.execute('INSERT INTO agent_management_receipts VALUES (?,?,?,?,?,?)',
                (*receipt_key, request_digest, json.dumps(result)))
            return result, False

    def register(self, actor, project, key, body):
        if (type(body) is not dict or set(body) != {'implementation_ref'}
                or not identifier(body['implementation_ref'])):
            raise invalid('only an installed implementation reference is accepted')
        def apply(db, scope):
            item = self._installed(actor, project, body['implementation_ref'])
            snapshot = item.snapshot()
            row = db.execute('SELECT snapshot FROM agent_catalog WHERE tenant=? AND project=? AND ref=?',
                             (*scope, item.implementation_ref)).fetchone()
            if row and json.loads(row['snapshot']) != snapshot:
                raise ServiceError('AGENT_RELEASE_IMMUTABLE', 'Register a new implementation version', 409)
            db.execute('INSERT OR IGNORE INTO agent_catalog VALUES (?,?,?,?,NULL)',
                       (*scope, item.implementation_ref, json.dumps(snapshot)))
            return {'implementation': snapshot}
        return self._command(actor, project, key, 'register', body, apply)

    def publish(self, actor, project, key, body):
        if type(body) is not dict or set(body) != {'implementation_ref'} or not identifier(body['implementation_ref']):
            raise invalid('implementation reference required')
        def apply(db, scope):
            row = self._row(db, scope, body['implementation_ref'])
            snapshot = json.loads(row['snapshot'])
            item = self._installed(actor, project, row['ref'], snapshot)
            if row['evidence'] is None:
                adapter = self.adapters.resolve(replace(item.runtime, required_capabilities=
                    item.runtime.required_capabilities | {'model_call_admission'}))
                evidence = self.qualify(actor, project, item, adapter)
                if not identifier(evidence):
                    raise ServiceError('AGENT_QUALIFICATION_FAILED', 'Verified qualification evidence required', 409)
                db.execute('UPDATE agent_catalog SET evidence=? WHERE tenant=? AND project=? AND ref=?',
                           (evidence, *scope, row['ref']))
            else: evidence = row['evidence']
            return {'implementation': snapshot, 'status': 'published', 'evidence_ref': evidence}
        return self._command(actor, project, key, 'publish', body, apply)

    def discover(self, actor, project):
        scope = self._scope(actor, project, 'read')
        with self._db() as db:
            items = []
            for row in db.execute('SELECT * FROM agent_catalog WHERE tenant=? AND project=? ORDER BY ref', scope):
                snapshot = json.loads(row['snapshot'])
                installed = self.installed.get(row['ref'])
                items.append({'implementation': snapshot, 'status': 'published' if row['evidence'] else 'registered',
                    'evidence_ref': row['evidence'],
                    'installed': installed is not None and installed.snapshot() == snapshot})
            return {'schema_version': 'agent-management@1', 'implementations': items,
                    'binding': self._binding(db, scope)}

    def _binding(self, db, scope):
        row = db.execute('SELECT version, routes FROM agent_project_routes WHERE tenant=? AND project=?', scope).fetchone()
        return {'version': row['version'], 'routes': json.loads(row['routes'])} if row else {'version': 0, 'routes': {}}

    def bind(self, actor, project, key, body):
        if (type(body) is not dict or set(body) != {'expected_version', 'routes'}
                or type(body['expected_version']) is not int or body['expected_version'] < 0
                or type(body['routes']) is not dict or len(body['routes']) > 32
                or any(not identifier(k) or not identifier(v) for k, v in body['routes'].items())):
            raise invalid('expected version and role-to-implementation routes required')
        def apply(db, scope):
            old = self._binding(db, scope)
            if old['version'] != body['expected_version']:
                raise ServiceError('AGENT_BINDING_CONFLICT', 'Project binding changed; reload before editing', 409)
            for role, ref in body['routes'].items():
                row = self._row(db, scope, ref)
                item = self._installed(actor, project, ref, json.loads(row['snapshot']))
                if row['evidence'] is None or item.role.role != role:
                    raise ServiceError('AGENT_BINDING_DENIED', 'Published compatible Agent required', 409)
                self.adapters.resolve(replace(item.runtime, required_capabilities=
                    item.runtime.required_capabilities | {'model_call_admission'}))
            result = {'version': old['version'] + 1, 'routes': body['routes']}
            db.execute('INSERT OR REPLACE INTO agent_project_routes VALUES (?,?,?,?)',
                       (*scope, result['version'], json.dumps(result['routes'])))
            return result
        return self._command(actor, project, key, 'bind', body, apply)

    def validate_runner(self, runner):
        """Ensure reference resolution cannot substitute another semantic release."""
        for item in self.installed.values():
            role = runner.roles.get(item.role.role)
            runtime = runner.releases.get(item.runtime.release_ref)
            if (role is None or role.release_digest != item.role.release_digest
                    or runtime is None or runtime.release_digest != item.runtime.release_digest
                    or item.role.role not in runner.routes):
                raise ServiceError('AGENT_ROUTE_CONFIGURATION_INVALID', 'Managed release differs from installed runner', 503)

    def project_routes(self, actor, task):
        """Plug into AgentPlanningService.project_routes; no implicit defaults.

        Existing runner role/output contracts and installed releases remain the
        authority; mismatches fail rather than silently using another release.
        """
        scope = self._scope(actor, task.project_id, 'read')
        with self._db() as db:
            routes = {}
            for role, ref in self._binding(db, scope)['routes'].items():
                row = self._row(db, scope, ref)
                item = self._installed(actor, task.project_id, ref, json.loads(row['snapshot']))
                if row['evidence'] is None or item.role.role != role:
                    raise ServiceError('AGENT_BINDING_DENIED', 'Project Agent binding is invalid', 409)
                routes[role] = item.runtime.release_ref
            return routes
