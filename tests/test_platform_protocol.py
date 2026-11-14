from copy import deepcopy
from types import SimpleNamespace
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.platform_protocol import DeclarativeProtocol


def config():
    ops = {name: {'path': '/verified/'+name, 'method': 'POST', 'fields': {}}
           for name in ('exchange','projects','project_datasets','user_datasets','encode_query','search','resolve_hits','media_version','preview')}
    for name in ('projects','project_datasets','user_datasets'):
        ops[name].update(items_path='items', total_path='total', page_parameter='page')
    ops['projects']['fields'] = {'id': 'project_id', 'name': 'display_name'}
    ops['search'].update(version_scope_path='filter.versions', body={'filter': {'versions': '$versions'}})
    return {'schema': 'platform-wire@1', 'user_id_path': 'user.id', 'operations': ops,
            'libraries': [], 'capabilities': [], 'media_origins': ['https://objects.invalid']}


class ProtocolTest(unittest.TestCase):
    def test_paginated_project_list_complete_and_normalized(self):
        protocol = DeclarativeProtocol(config())
        requests = []
        class Client:
            def post(_, path, token, body, **kwargs):
                requests.append(body)
                return {'items':[{'project_id':body['page'], 'display_name':'Project'}], 'total': 2}
        rows = protocol.hooks()['list_projects'](Client(), SimpleNamespace(user_id='a'), 'personal-token')
        self.assertEqual([row['id'] for row in rows], [1,2])
        self.assertEqual([request['page'] for request in requests], [1,2])

    def test_incomplete_or_changing_pagination_fails_closed(self):
        protocol = DeclarativeProtocol(config())
        class Client:
            def post(_, *args, **kwargs):
                return {'items':[], 'total': 2}
        with self.assertRaises(ServiceError):
            protocol.rows('projects', Client(), 'token', {})

    def test_version_filter_required_before_any_remote_search(self):
        protocol = DeclarativeProtocol(config())
        calls = []
        class Client:
            def post(_, path, token, body, **kwargs):
                calls.append(body)
                return {}
        protocol.call('search', Client(), 'token', {'versions':['v1']}, project='101')
        self.assertEqual(calls, [{'filter': {'versions':['v1']}}])
        protocol.config['operations']['search']['body']['filter']['versions'] = ['other-project-version']
        with self.assertRaises(ServiceError):
            protocol.call('search', Client(), 'token', {'versions':['v1']}, project='101')
        self.assertEqual(len(calls), 1)

    def test_invalid_response_never_falls_back_to_browser_identity(self):
        protocol = DeclarativeProtocol(config())
        with self.assertRaises(ServiceError):
            protocol.hooks()['extract_user_id']({'id':'admin'})

    def test_alias_and_type_mapping(self):
        protocol = DeclarativeProtocol(config())
        self.assertEqual(protocol.mapped({'fields': {'version_id': {'path':'version.id','type':'string'}}},
                                          {'version':{'id':88}}), {'version_id':'88'})

    def test_placeholder_endpoints_and_unbounded_scope_rejected(self):
        value = config()
        value['operations']['search']['path'] = '/<UNCONFIRMED>'
        with self.assertRaises(ValueError):
            DeclarativeProtocol(value)
        value = config()
        del value['operations']['search']['version_scope_path']
        with self.assertRaises(ValueError):
            DeclarativeProtocol(value)

    def test_explicit_unpaged_user_dataset_union(self):
        value = config()
        op = value['operations']['user_datasets']
        op.update(unpaged=True, items_path='', fields={'library_id':'library', 'version_id': {'path':'version','type':'string'}})
        del op['total_path']
        del op['page_parameter']
        protocol = DeclarativeProtocol(value)
        class Client:
            def post(_, *args, **kwargs):
                return [{'library':'library-a', 'version':88}]
        rows = protocol.rows('user_datasets', Client(), 'personal-token', {})
        self.assertEqual(rows, [{'library_id':'library-a','version_id':'88'}])

    def test_reference_encoding_uses_authorized_immutable_upload(self):
        from contextvars import ContextVar
        from base64 import b64decode
        from dsh_retrieval.models import Actor
        from dsh_retrieval.integration_models import AccessContext, IndexBinding
        value = config()
        value['operations']['encode_reference'] = {'path':'/verified/reference', 'method':'POST',
            'body':{'image':'$image_base64','binding':'$binding'},
            'fields':{'encoder_ref':'encoder','values':'vector'}}
        protocol = DeclarativeProtocol(value)
        seen = []
        class Client:
            def post(_, path, token, body, **kwargs):
                self.assertEqual(b64decode(body['image']), b'picture')
                self.assertEqual(token, 'personal-token')
                return {'encoder':'encoder-v1','vector':[1.,0.]}
        actor = Actor('platform','a')
        app = SimpleNamespace(client=Client(), projects=SimpleNamespace(_request=ContextVar('request',default=None)),
            credentials=SimpleNamespace(credential_for_actor=lambda actor:'personal-token'),
            repository=SimpleNamespace(get=lambda actor, task:SimpleNamespace(project_id='101')),
            service=SimpleNamespace(query_images=SimpleNamespace(content=lambda *args: seen.append(args) or b'picture')))
        binding = IndexBinding('binding','library','index','encoder-v1',2,'COSINE')
        vector = protocol.hooks()['encode_reference'](app,AccessContext(actor,'task','search','current'),
            {'query_image_id':'upload','digest':'sha256:expected'},binding)
        self.assertEqual(vector.values,(1.,0.))
        self.assertEqual(seen,[(actor,'101','upload','sha256:expected')])
