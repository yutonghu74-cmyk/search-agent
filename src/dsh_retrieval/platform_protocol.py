"""Declarative wire adapter; private verified field mappings, no executable templates.

Missing endpoint/schema information is configuration, not an invented protocol.
"""
from dataclasses import asdict
import json
import re
import base64
from pathlib import Path
from urllib.parse import urlencode

from .errors import ServiceError
from .integration_models import (IndexBinding, Library, QueryVector, SearchHit,
                                 SearchPage, MediaRecord, MediaResolution)
from .physical_query_preparation import TextQueryCapability
from .models import Actor
from .platform_media import PlatformMediaReader
from .platform_resources import DatasetVersionRef


OPS = ('exchange', 'projects', 'project_datasets', 'user_datasets', 'encode_query',
       'search', 'resolve_hits', 'media_version', 'preview')


def select(value, path):
    if path == '':
        return value
    try:
        for part in path.split('.'):
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, TypeError, IndexError, ValueError):
        raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'verified platform response mapping failed', 503) from None


def render(value, variables):
    if isinstance(value, str) and value.startswith('$'):
        return select(variables, value[1:])
    if isinstance(value, list):
        return [render(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render(item, variables) for key, item in value.items()}
    return value


class DeclarativeProtocol:
    def __init__(self, config):
        if re.search(r"<[A-Z][A-Z0-9_]*>", json.dumps(config)):
            raise ValueError("unresolved platform wire placeholders")
        if (set(config) != {'schema', 'user_id_path', 'operations', 'libraries', 'capabilities', 'media_origins'}
                or config['schema'] != 'platform-wire@1' or not set(OPS) <= set(config['operations']) or set(config['operations']) - set(OPS) - {'encode_reference'}):
            raise ValueError('complete verified platform wire mapping required')
        self.config = config
        for name, op in config['operations'].items():
            if (not {'path', 'method', 'fields'} <= set(op) or op['method'] not in ('GET', 'POST')
                    or not isinstance(op['path'], str) or not op['path'].startswith('/')
                    or any(char in op['path'] for char in '<>#')
                    or not isinstance(op['fields'], dict)):
                raise ValueError('invalid platform wire operation: ' + name)
        if config['operations']['exchange']['method'] != 'POST':
            raise ValueError('refresh exchange must use POST')
        for name in ('projects', 'project_datasets', 'user_datasets'):
            op = config['operations'][name]
            if not ('items_path' in op and (op.get('unpaged') is True or {'total_path', 'page_parameter'} <= set(op))):
                raise ValueError('complete pagination contract required for ' + name)
        if not config['operations']['search'].get('version_scope_path'):
            raise ValueError('search must declare its pre-recall dataset version filter')
        self.libraries = tuple(Library(**{**row, 'bindings': tuple(IndexBinding(**binding) for binding in row['bindings'])})
                               for row in config['libraries'])
        self.capabilities = tuple(TextQueryCapability(**{**row, 'binding': IndexBinding(**row['binding'])})
                                  for row in config['capabilities'])
        bindings = {binding for library in self.libraries for binding in library.bindings}
        if any(cap.binding not in bindings for cap in self.capabilities):
            raise ValueError('query capabilities must reference registered library bindings')
        self.reader = PlatformMediaReader(self.preview_url, allowed_origins=config['media_origins'])

    def mapped(self, op, row):
        result = {}
        for key, field in op['fields'].items():
            if isinstance(field, str):
                result[key] = select(row, field)
                continue
            if isinstance(field, dict) and set(field) == {'literal'}:
                result[key] = field['literal']
                continue
            if not isinstance(field, dict) or set(field) - {'path', 'type', 'values'}:
                raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'invalid field mapping', 503)
            value = select(row, field['path'])
            if 'values' in field:
                value = field['values'][str(value)]
            if 'type' in field:
                if field['type'] not in ('string', 'integer', 'number') or isinstance(value, bool):
                    raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'invalid field type mapping', 503)
                value = {'string': str, 'integer': int, 'number': float}[field['type']](value)
            result[key] = value
        return result

    def call(self, name, client, token, variables, *, project=None, page=None):
        op = self.config['operations'][name]
        body = render(op.get('body', {}), variables)
        if name == 'search' and select(body, op['version_scope_path']) != variables['versions']:
            raise ServiceError('PLATFORM_SCOPE_INVALID', 'search requires exact authorized versions before recall', 503)
        if page is not None:
            body[op['page_parameter']] = page
        if op['method'] == 'GET':
            path = op['path'] + ('?' + urlencode(body, doseq=True) if body else '')
            return client.get(path, token, project_id=project)
        return client.post(op['path'], token, body, project_id=project, credential_exchange=name == 'exchange',
                           max_body_bytes=32 * 1024 * 1024 if name == 'encode_reference' else 1_000_000)

    def rows(self, name, client, token, variables, *, project=None):
        op = self.config['operations'][name]
        if op.get('unpaged') is True:
            data = self.call(name, client, token, variables, project=project)
            items = select(data, op['items_path'])
            if not isinstance(items, list) or len(items) > 10000:
                raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'invalid complete authorization listing', 503)
            return [self.mapped(op, row) for row in items]
        rows = []
        total = None
        for page in range(1, 101):
            data = self.call(name, client, token, variables, project=project, page=page)
            items, count = select(data, op['items_path']), select(data, op['total_path'])
            if not isinstance(items, list) or type(count) is not int or count < 0 or total not in (None, count):
                raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'platform pagination changed or is invalid', 503)
            total = count
            rows.extend(self.mapped(op, row) for row in items)
            if len(rows) == total:
                return rows
            if not items or len(rows) > total:
                break
        raise ServiceError('PLATFORM_PAGINATION_INCOMPLETE', 'complete current authorization listing required', 503)

    @staticmethod
    def credentials(app, actor, project=None):
        # Use request credential when available, otherwise durable personal delegation.
        current = app.projects._request.get()
        if current is not None:
            if current[0] != actor or (project is not None and current[1] != project):
                raise ServiceError('ACCESS_DENIED', 'platform wire request context mismatch', 403)
            return current[2]
        return app.credentials.credential_for_actor(actor)

    def exchange(self, client, refresh):
        op = self.config['operations']['exchange']
        value = self.mapped(op, self.call('exchange', client, None, {'refresh_key': refresh}))
        return value['token'], value['expires_in']

    def dataset_refs(self, app, actor, project=None):
        name = 'project_datasets' if project is not None else 'user_datasets'
        rows = self.rows(name, app.client, self.credentials(app, actor, project),
                         {'project_id': project, 'user_id': actor.user_id}, project=project)
        return tuple(DatasetVersionRef(**row) for row in rows)

    def encode(self, app, context, text, binding):
        task = app.repository.get(context.actor, context.task_id)
        data = self.call('encode_query', app.client, self.credentials(app, context.actor, task.project_id),
            {'project_id': task.project_id, 'text': text, 'binding': asdict(binding)}, project=task.project_id)
        mapped = self.mapped(self.config['operations']['encode_query'], data)
        return QueryVector(mapped['encoder_ref'], tuple(mapped['values']))

    def encode_reference(self, app, context, query_input, binding):
        task = app.repository.get(context.actor, context.task_id)
        content = app.service.query_images.content(context.actor, task.project_id,
            query_input['query_image_id'], query_input['digest'])
        data = self.call('encode_reference', app.client, self.credentials(app, context.actor, task.project_id),
            {'project_id': task.project_id, 'image_base64': base64.b64encode(content).decode(),
             'binding': asdict(binding)}, project=task.project_id)
        mapped = self.mapped(self.config['operations']['encode_reference'], data)
        return QueryVector(mapped['encoder_ref'], tuple(mapped['values']))

    def search(self, app, context, request, project, versions):
        variables = {'project_id': project, 'versions': sorted(versions), 'request': asdict(request)}
        data = self.call('search', app.client, self.credentials(app, context.actor, project), variables, project=project)
        mapped = self.mapped(self.config['operations']['search'], data)
        hit_op = {'fields': self.config['operations']['search'].get('hit_fields', {'vector_id':'vector_id','score':'score'})}
        rows = [self.mapped(hit_op, row) for row in mapped.pop('hits')]
        hits = tuple(SearchHit(request.binding, row['vector_id'], row['score']) for row in rows)
        return SearchPage(hits=hits, **mapped)

    def resolve(self, app, context, hits, project):
        data = self.call('resolve_hits', app.client, self.credentials(app, context.actor, project),
            {'project_id': project, 'hits': [asdict(hit) for hit in hits]}, project=project)
        op = self.config['operations']['resolve_hits']
        rows = select(data, op.get('items_path', ''))
        if not isinstance(rows, list) or len(rows) != len(hits):
            raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'hit mapping cardinality mismatch', 503)
        result = []
        for hit, row in zip(hits, rows):
            mapped = self.mapped(op, row)
            if mapped.pop('vector_id') != hit.vector_id:
                raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'hit mapping identity mismatch', 503)
            result.append(MediaResolution(hit, MediaRecord(**mapped), 'resolved'))
        return tuple(result)

    def media_version(self, app, actor, project, ref):
        data = self.call('media_version', app.client, self.credentials(app, actor, project),
                         {'project_id': project, 'media_ref': ref}, project=project)
        return DatasetVersionRef(**self.mapped(self.config['operations']['media_version'], data))

    def preview_url(self, value, project, ref):
        app, context = value
        data = self.call('preview', app.client, self.credentials(app, context.actor, project),
                         {'project_id': project, 'media_ref': ref}, project=project)
        return self.mapped(self.config['operations']['preview'], data)['url']

    def hooks(self):
        hooks = {'extract_user_id': lambda data: select(data, self.config['user_id_path']),
            'exchange': self.exchange,
            'list_projects': lambda client, actor, token: self.rows('projects', client, token, {'user_id': actor.user_id}),
            'project_datasets': lambda app, actor, project: self.dataset_refs(app, actor, project),
            'user_datasets': lambda app, actor: self.dataset_refs(app, actor),
            'libraries': lambda *args: self.libraries,
            'capabilities': lambda app: self.capabilities,
            'encode_query': self.encode, 'search': self.search, 'resolve_hits': self.resolve,
            'media_version': self.media_version,
            'open_media': lambda app, context, project, ref, limit: self.reader((app, context), project, ref, limit)}
        if 'encode_reference' in self.config['operations']:
            hooks['encode_reference'] = self.encode_reference
        def safe(callback):
            def invoke(*args, **kwargs):
                try:
                    return callback(*args, **kwargs)
                except ServiceError as error:
                    if error.status == 401 and len(args) >= 2 and hasattr(args[0], 'credentials'):
                        actor = getattr(args[1], 'actor', args[1])
                        if isinstance(actor, Actor):
                            args[0].credentials.revoke(actor)
                    raise
                except (KeyError, TypeError, ValueError, IndexError, ArithmeticError):
                    raise ServiceError('PLATFORM_SCHEMA_MISMATCH', 'verified platform wire mapping failed', 503) from None
            return invoke
        return {key: safe(callback) for key, callback in hooks.items()}



def create(config):
    raw = Path(config['protocol_config']).read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError('platform wire configuration exceeds size limit')
    return DeclarativeProtocol(json.loads(raw)).hooks()
