"""Trusted, per-index OpenAI-compatible text embedding adapter.

This is an embedding port, not a search gateway. Endpoint declarations are
private deployment input; neither a Chat request nor an index alias selects a
URL or model. The physical registry remains the vector-space authority.
"""

from dataclasses import dataclass, field
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ...errors import ServiceError
from ...integration_models import AccessContext, IndexBinding, QueryVector
from ...intent_model import NoRedirect
from ...physical_index_registry import PhysicalIndexRegistry


def _invalid():
    raise ServiceError('EMBEDDING_CONFIG_INVALID', 'Invalid trusted embedding configuration', 422)


@dataclass(frozen=True, slots=True)
class EmbeddingEndpoint:
    binding: IndexBinding
    model_id: str
    model_version: str
    preprocessing_version: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)

    def validate(self, registry: PhysicalIndexRegistry):
        if type(self.binding) is not IndexBinding:
            _invalid()
        target = registry.resolve(self.binding)
        if (self.model_id != target.model_id
                or self.model_version != target.model_version
                or self.preprocessing_version != target.preprocessing_version
                or not isinstance(self.api_key, str) or not self.api_key
                or any(char in self.api_key for char in '\r\n')
                or not isinstance(self.base_url, str)):
            _invalid()
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.rstrip('/').endswith('/v1')):
            _invalid()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def load_embedding_endpoints(path, registry: PhysicalIndexRegistry, *, environ=None):
    """Load bounded declarations; the JSON names env vars, never contains keys."""
    env = os.environ if environ is None else environ
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            _invalid()
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {'schema', 'entries'}
                or value['schema'] != 'embedding-endpoints@1'
                or type(value['entries']) is not list or not 1 <= len(value['entries']) <= 256):
            _invalid()
        endpoints = []
        for item in value['entries']:
            if (type(item) is not dict or set(item) != {'binding', 'model_id', 'model_version',
                    'preprocessing_version', 'base_url', 'api_key_env'}
                    or type(item['binding']) is not dict
                    or set(item['binding']) != set(IndexBinding.__dataclass_fields__)
                    or not isinstance(item['api_key_env'], str)
                    or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item['api_key_env']) is None):
                _invalid()
            binding = IndexBinding(**item['binding'])
            endpoint = EmbeddingEndpoint(binding, item['model_id'], item['model_version'],
                item['preprocessing_version'], item['base_url'], env.get(item['api_key_env'], ''))
            endpoint.validate(registry)
            endpoints.append(endpoint)
        return tuple(endpoints)
    except ServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _invalid()


class HTTPEmbeddingProvider:
    """Encode one text against the exact authorized physical index model.

    No cache or automatic retry: a retry can duplicate billable calls. The
    caller must persist attempt accounting before enabling this in production.
    """

    def __init__(self, registry: PhysicalIndexRegistry, authorization, endpoints,
                 *, opener=None, timeout=10):
        if (type(registry) is not PhysicalIndexRegistry or type(endpoints) is not tuple
                or not endpoints or type(timeout) not in (int, float)
                or not 0 < timeout <= 30):
            _invalid()
        selected = {}
        for endpoint in endpoints:
            if type(endpoint) is not EmbeddingEndpoint:
                _invalid()
            endpoint.validate(registry)
            binding_id = endpoint.binding.binding_id
            if binding_id in selected:
                _invalid()
            selected[binding_id] = endpoint
        self.registry = registry
        self.authorization = authorization
        self.endpoints = MappingProxyType(selected)
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    def encode_query(self, context: AccessContext, text: str, binding: IndexBinding) -> QueryVector:
        # This validates exact binding, task/actor/version scope and input size.
        self.registry.query_cache_key(context, text, binding)
        endpoint = self.endpoints.get(binding.binding_id)
        if endpoint is None or endpoint.binding != binding:
            raise ServiceError('QUERY_UNSUPPORTED', 'No trusted encoder for this index', 422)
        self.authorization.require_access(context, 'library:' + binding.library_id, 'search')
        request = urllib.request.Request(endpoint.base_url.rstrip('/') + '/embeddings',
            data=json.dumps({'model': endpoint.model_id, 'input': text,
                             'encoding_format': 'float'}, ensure_ascii=False).encode(),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + endpoint.api_key}, method='POST')
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError('oversized response')
            payload = json.loads(raw)
            data = payload['data']
            if (payload.get('object') != 'list' or payload.get('model') != endpoint.model_id
                    or type(data) is not list or len(data) != 1
                    or data[0].get('object') != 'embedding' or data[0].get('index') != 0):
                raise ValueError('wrong model or result shape')
            values = data[0]['embedding']
            if (type(values) is not list or len(values) != binding.vector_dimension
                    or any(type(item) not in (int, float) or not math.isfinite(item) for item in values)
                    or binding.metric == 'COSINE' and not any(item != 0 for item in values)):
                raise ValueError('incompatible vector')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                ConnectionError, OSError, HTTPException):
            raise ServiceError('EMBEDDING_REQUEST_FAILED', 'Trusted embedding request failed', 502) from None
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
            raise ServiceError('ENCODER_MISMATCH', 'Embedding response incompatible with index', 502) from None
        self.authorization.require_access(context, 'library:' + binding.library_id, 'search')
        return QueryVector(binding.encoder_ref, tuple(float(item) for item in values))
