from dataclasses import asdict, replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.error

from dsh_retrieval.adapters.retrieval.http_embedding import (
    EmbeddingEndpoint, HTTPEmbeddingProvider, load_embedding_endpoints,
)
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import AccessContext, IndexBinding, QueryVector
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_index_registry import PhysicalIndex, PhysicalIndexRegistry


class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


def response(model, values):
    return {'object': 'list', 'model': model,
            'data': [{'object': 'embedding', 'index': 0, 'embedding': values}]}


class HTTPEmbeddingProviderTest(unittest.TestCase):
    def setUp(self):
        self.dog = IndexBinding('dogs-v1', 'dogs', 'v1', 'dog-encoder@1', 3, 'COSINE')
        self.cat = IndexBinding('cats-v1', 'cats', 'v1', 'cat-encoder@1', 3, 'COSINE')
        self.dog_target = PhysicalIndex(self.dog, 'cluster-a', 'media', 'dogs', 'id', 'vector',
            'dog-model', '2026-01', 'text-v1', 'dogs-filter', 'iterator', 'live', 10, 100)
        self.cat_target = replace(self.dog_target, binding=self.cat, collection='cats',
                                  model_id='cat-model')
        self.registry = PhysicalIndexRegistry((self.dog_target, self.cat_target))
        self.dog_endpoint = EmbeddingEndpoint(self.dog, 'dog-model', '2026-01', 'text-v1',
                                              'https://embedding.invalid/v1', 'private-dog-key')
        self.cat_endpoint = EmbeddingEndpoint(self.cat, 'cat-model', '2026-01', 'text-v1',
                                              'https://embedding.invalid/v1', 'private-cat-key')
        self.context = AccessContext(Actor('tenant', 'owner'), 'task', 'search', 'auth-v1')

    def provider(self, replies, authorization=None):
        transport = FakeTransport(replies)
        if authorization is None:
            class Authority:
                def require_access(self, context, ref, action):
                    if ref not in ('library:dogs', 'library:cats') or action != 'search':
                        raise ServiceError('ACCESS_DENIED', 'Denied', 403)
            authorization = Authority()
        return HTTPEmbeddingProvider(self.registry, authorization,
            (self.dog_endpoint, self.cat_endpoint), opener=transport), transport

    def test_each_index_uses_its_own_model_and_credential(self):
        provider, transport = self.provider([response('dog-model', [1, 0, 0]),
                                             response('cat-model', [0, 1, 0])])
        self.assertEqual(provider.encode_query(self.context, '狗', self.dog),
                         QueryVector('dog-encoder@1', (1.0, 0.0, 0.0)))
        self.assertEqual(provider.encode_query(self.context, '猫', self.cat),
                         QueryVector('cat-encoder@1', (0.0, 1.0, 0.0)))
        self.assertEqual([json.loads(request.data)['model'] for request, _ in transport.requests],
                         ['dog-model', 'cat-model'])
        self.assertEqual([request.get_header('Authorization') for request, _ in transport.requests],
                         ['Bearer private-dog-key', 'Bearer private-cat-key'])
        self.assertTrue(all(timeout == 10 for _, timeout in transport.requests))

    def test_no_network_for_unknown_binding_or_authorization_denial(self):
        class Denied:
            def require_access(self, context, ref, action):
                raise ServiceError('ACCESS_DENIED', 'Denied', 403)
        provider, transport = self.provider([], Denied())
        with self.assertRaises(ServiceError) as caught:
            provider.encode_query(self.context, '狗', self.dog)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')
        with self.assertRaises(ServiceError):
            provider.encode_query(self.context, '狗', replace(self.dog, encoder_ref='other'))
        self.assertFalse(transport.requests)

    def test_reauthorization_after_network_failure_closes_result(self):
        class Revoked:
            calls = 0

            def require_access(self, context, ref, action):
                self.calls += 1
                if self.calls == 2:
                    raise ServiceError('ACCESS_DENIED', 'Revoked', 403)
        provider, transport = self.provider([response('dog-model', [1, 0, 0])], Revoked())
        with self.assertRaises(ServiceError) as caught:
            provider.encode_query(self.context, '狗', self.dog)
        self.assertEqual(caught.exception.code, 'ACCESS_DENIED')
        self.assertEqual(len(transport.requests), 1)

    def test_response_model_dimension_and_numeric_fail_closed(self):
        bad = [response('cat-model', [1, 0, 0]), response('dog-model', [1, 0]),
               response('dog-model', [True, 0, 0]), response('dog-model', [float('nan'), 0, 0]),
               response('dog-model', [0, 0, 0]), {'model': 'dog-model', 'data': []}]
        for item in bad:
            with self.subTest(item=item):
                provider, _ = self.provider([item])
                with self.assertRaises(ServiceError) as caught:
                    provider.encode_query(self.context, '狗', self.dog)
                self.assertEqual(caught.exception.code, 'ENCODER_MISMATCH')

    def test_transport_error_is_sanitized_and_not_retried(self):
        provider, transport = self.provider([urllib.error.URLError('private endpoint failed')])
        with self.assertRaises(ServiceError) as caught:
            provider.encode_query(self.context, '狗', self.dog)
        self.assertEqual(caught.exception.code, 'EMBEDDING_REQUEST_FAILED')
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(len(transport.requests), 1)

    def test_private_loader_binds_registry_and_uses_env_reference(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'embedding.json'
            entry = {'binding': asdict(self.dog), 'model_id': 'dog-model',
                     'model_version': '2026-01', 'preprocessing_version': 'text-v1',
                     'base_url': 'https://embedding.invalid/v1', 'api_key_env': 'DOG_EMBEDDING_KEY'}
            data = {'schema': 'embedding-endpoints@1', 'entries': [entry]}
            path.write_text(json.dumps(data))
            endpoints = load_embedding_endpoints(path, self.registry,
                                                  environ={'DOG_EMBEDDING_KEY': 'secret'})
            self.assertEqual(endpoints, (replace(self.dog_endpoint, api_key='secret'),))
            self.assertNotIn('secret', repr(endpoints))
            for broken in ({**data, 'entries': [{**entry, 'model_id': 'cat-model'}]},
                           {**data, 'entries': [{**entry, 'base_url': 'http://embedding.invalid/v1'}]},
                           {**data, 'entries': [entry, entry]}):
                path.write_text(json.dumps(broken))
                with self.assertRaises(ServiceError):
                    loaded = load_embedding_endpoints(path, self.registry,
                                                       environ={'DOG_EMBEDDING_KEY': 'secret'})
                    HTTPEmbeddingProvider(self.registry, object(), loaded)
            path.write_text(json.dumps(data))
            with self.assertRaises(ServiceError):
                load_embedding_endpoints(path, self.registry, environ={})


if __name__ == '__main__':
    unittest.main()
