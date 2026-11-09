"""Offline CLIP embeddings for the explicitly authorized local test corpus.

This is a development connector, not an internal Milvus or media gateway.
The same frozen model is used to build image vectors and encode text queries.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import threading

from .errors import ServiceError
from .integration_models import IndexBinding, QueryVector
from .local_test_media import LocalTestMediaCorpus


def _model_components(model_path):
    try:
        import torch
        from transformers import (CLIPModel, CLIPProcessor,
                                  ChineseCLIPModel, ChineseCLIPProcessor)
    except ImportError as exc:
        raise ServiceError('CLIP_RUNTIME_UNAVAILABLE', 'Install local CLIP runtime dependencies', 503) from exc
    path = Path(model_path).resolve()
    if not path.is_dir() or not (path / 'config.json').is_file():
        raise ServiceError('CLIP_MODEL_UNAVAILABLE', 'Local CLIP model is unavailable', 503)
    weight = next((path / name for name in ('model.safetensors', 'pytorch_model.bin')
                   if (path / name).is_file()), None)
    if weight is None:
        raise ServiceError('CLIP_MODEL_UNAVAILABLE', 'Local CLIP weights are unavailable', 503)
    try:
        model_type = json.loads((path / 'config.json').read_text())['model_type']
    except (OSError, ValueError, KeyError) as exc:
        raise ServiceError('CLIP_MODEL_UNAVAILABLE', 'Invalid local CLIP config', 503) from exc
    classes = {'clip': (CLIPModel, CLIPProcessor),
               'chinese_clip': (ChineseCLIPModel, ChineseCLIPProcessor)}
    if model_type not in classes:
        raise ServiceError('CLIP_MODEL_UNAVAILABLE', 'Unsupported local CLIP model type', 503)
    identity = hashlib.sha256()
    identity_files = [path / name for name in (
        'config.json', 'preprocessor_config.json', 'processor_config.json',
        'tokenizer_config.json', 'special_tokens_map.json', 'tokenizer.json',
        'vocab.json', 'vocab.txt', 'merges.txt') if (path / name).is_file()]
    identity_files.append(weight)
    for file in identity_files:
        identity.update(file.name.encode())
        with file.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                identity.update(chunk)
    try:
        model_class, processor_class = classes[model_type]
        model = model_class.from_pretrained(str(path), local_files_only=True).eval()
        processor = processor_class.from_pretrained(str(path), local_files_only=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ServiceError('CLIP_MODEL_UNAVAILABLE', 'Local CLIP model could not be loaded', 503) from exc
    return torch, model, processor, 'local-clip:' + identity.hexdigest()[:24]


class LocalCLIPEncoder:
    def __init__(self, model_path):
        self.torch, self.model, self.processor, self.encoder_ref = _model_components(model_path)
        self._lock = threading.RLock()
        self._cache = {}
        self.dimension = int(self.model.config.projection_dim)

    @staticmethod
    def _normalized(values):
        # Transformers 5 returns BaseModelOutputWithPooling here; 4 returned a tensor.
        if hasattr(values, 'pooler_output'):
            values = values.pooler_output
        result = values.detach().cpu().float()
        result = result / result.norm(dim=-1, keepdim=True).clamp_min(1e-12)
        return result.tolist()

    def encode_query(self, text, binding: IndexBinding):
        if (binding.encoder_ref != self.encoder_ref or binding.vector_dimension != self.dimension
                or binding.metric != 'COSINE'):
            raise ServiceError('ENCODER_MISMATCH', 'CLIP index uses a different model', 409)
        if not isinstance(text, str) or not text.strip():
            raise ServiceError('QUERY_UNSUPPORTED', 'Text query is empty', 422)
        with self._lock:
            result = self._cache.get(text)
            if result is None:
                try:
                    inputs = self.processor(text=[text], return_tensors='pt', truncation=True)
                    with self.torch.inference_mode():
                        features = self.model.get_text_features(**inputs)
                    result = tuple(round(v, 7) for v in self._normalized(features)[0])
                except (RuntimeError, ValueError, IndexError) as exc:
                    raise ServiceError('QUERY_ENCODING_FAILED',
                                       'Local CLIP could not encode this query', 503) from exc
                if not any(result):
                    raise ServiceError('QUERY_ENCODING_FAILED',
                                       'Local CLIP returned a zero query vector', 503)
                if len(self._cache) >= 512:
                    self._cache.clear()
                self._cache[text] = result
        return QueryVector(self.encoder_ref, result)

    def encode_image_bytes(self, content: bytes, binding: IndexBinding):
        if (binding.encoder_ref != self.encoder_ref or binding.vector_dimension != self.dimension
                or binding.metric != 'COSINE'):
            raise ServiceError('ENCODER_MISMATCH', 'CLIP index uses incompatible image encoding', 409)
        from PIL import Image, UnidentifiedImageError
        cache_key = 'image:' + hashlib.sha256(content).hexdigest()
        with self._lock:
            result = self._cache.get(cache_key)
            if result is None:
                image = None
                try:
                    with Image.open(io.BytesIO(content)) as source:
                        image = source.convert('RGB')
                        inputs = self.processor(images=[image], return_tensors='pt')
                        with self.torch.inference_mode():
                            features = self.model.get_image_features(**inputs)
                        result = tuple(round(v, 7) for v in self._normalized(features)[0])
                except (UnidentifiedImageError, OSError, RuntimeError, ValueError, IndexError) as exc:
                    raise ServiceError('QUERY_ENCODING_FAILED',
                                       'Local CLIP could not encode the reference image', 503) from exc
                finally:
                    if image is not None:
                        image.close()
                if not any(result) or len(result) != self.dimension:
                    raise ServiceError('QUERY_ENCODING_FAILED',
                                       'Local CLIP returned an invalid image vector', 503)
                if len(self._cache) >= 512:
                    self._cache.clear()
                self._cache[cache_key] = result
        return QueryVector(self.encoder_ref, result)

    def encode_images(self, paths, *, batch_size=16):
        from PIL import Image
        output = []
        with self._lock, self.torch.inference_mode():
            for start in range(0, len(paths), batch_size):
                images = []
                try:
                    for path in paths[start:start + batch_size]:
                        with Image.open(path) as source:
                            images.append(source.convert('RGB'))
                    inputs = self.processor(images=images, return_tensors='pt')
                    features = self.model.get_image_features(**inputs)
                    output.extend(tuple(round(v, 7) for v in row)
                                  for row in self._normalized(features))
                finally:
                    for image in images:
                        image.close()
        return output


def build_local_clip_fixture(manifest_path, out_dir, model_path, *, batch_size=16):
    out = Path(out_dir)
    if any((out / name).exists() for name in ('catalog.json', 'capabilities.json', 'fixture.json')):
        raise ServiceError('CLIP_INDEX_EXISTS', 'Refusing to overwrite an existing local index', 409)
    corpus = LocalTestMediaCorpus(manifest_path)
    encoder = LocalCLIPEncoder(model_path)
    if not 1 <= batch_size <= 64:
        raise ValueError('batch_size must be 1..64')
    vectors = encoder.encode_images([corpus.root / row['file'] for row, _ in corpus.items],
                                    batch_size=batch_size)
    if len(vectors) != len(corpus.items):
        raise ServiceError('CLIP_INDEX_INVALID', 'Image vector count mismatch', 500)
    LocalTestMediaCorpus(manifest_path)  # Reject files changed while embeddings were computed.
    manifest_digest = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()[:16]
    binding = {'binding_id': 'commons-local-clip', 'library_id': 'commons-test',
               'index_version': 'clip-' + encoder.encoder_ref[-12:] + '-' + manifest_digest,
               'encoder_ref': encoder.encoder_ref,
               'vector_dimension': encoder.dimension, 'metric': 'COSINE'}
    catalog = {'schema': 'local-catalog@1', 'libraries': [{
        'library_id': 'commons-test', 'description': '本地 500 张测试图片：真实 CLIP 图文向量',
        'media_type': 'image', 'bindings': [binding]}],
        'grants': [{'tenant_id': 'tenant-a', 'owner_id': 'user-a',
                    'project_id': 'demo', 'library_ids': ['commons-test'],
                    'can_configure': True}]}
    capabilities = {'schema': 'text-query-capabilities@1', 'items': [{
        'binding': binding, 'media_type': 'image',
        'filter_template_ref': 'test-public', 'max_batch_size': 20}]}
    records = []
    for position, ((row, ref), vector) in enumerate(zip(corpus.items, vectors), 1):
        records.append({'binding_id': binding['binding_id'], 'vector_id': position,
            'values': vector, 'scope': 'public', 'media': [{
                'media_unit_id': row['media_unit_id'], 'asset_version': row['asset_version'],
                'source_namespace': row['source_namespace'], 'source_asset_id': row['source_asset_id'],
                'authorized_media_ref': ref, 'content_digest': row['content_digest']}]})
    fixture = {'schema': 'local-first-search@1', 'max_depth': 100,
               'filter_templates': {'test-public': ['public']},
               'query_vectors': [], 'records': records}
    out.mkdir(parents=True, exist_ok=True)
    for name, value in (('catalog.json', catalog), ('capabilities.json', capabilities),
                        ('fixture.json', fixture)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build real CLIP vectors for local test media')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--batch-size', type=int, default=16)
    args = parser.parse_args(argv)
    path = build_local_clip_fixture(args.manifest, args.out, args.model,
                                    batch_size=args.batch_size)
    print(f'Built local CLIP image index: {path}')


if __name__ == '__main__':
    main()
