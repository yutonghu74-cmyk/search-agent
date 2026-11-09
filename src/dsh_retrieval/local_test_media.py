"""Trusted, development-only adapter for a downloaded local image manifest."""
from contextlib import contextmanager
from pathlib import Path
from io import BytesIO
import argparse
import hashlib
import json
import math
import re

from .errors import ServiceError
from .integration_models import AccessContext


LABELS = {
    'dog': ('狗', '犬'), 'dog_person': ('狗', '人狗合影'),
    'cat': ('猫',), 'fox': ('狐狸',), 'parrot': ('鹦鹉',),
    'tiger': ('老虎',), 'horse': ('马',), 'rabbit': ('兔子',),
    'elephant': ('大象',), 'deer': ('鹿',),
}
MAX_FILE_BYTES = 10 * 1024 * 1024


def _invalid(message='Invalid local test media manifest'):
    raise ServiceError('LOCAL_TEST_MEDIA_INVALID', message, 422)


class LocalTestMediaCorpus:
    def __init__(self, manifest_path, service=None):
        self.root = Path(manifest_path).resolve().parent
        self.service = service
        from .media_cache import ByteCache
        self.byte_cache = ByteCache()
        self.thumbnail_cache = ByteCache(max_bytes=16 * 1024 * 1024)
        try:
            raw = Path(manifest_path).read_bytes()
            if len(raw) > 8_000_000:
                _invalid()
            manifest = json.loads(raw)
            if (type(manifest) is not dict or manifest.get('schema') != 'test-media-manifest@1'
                    or type(manifest.get('items')) is not list
                    or not 1 <= len(manifest['items']) <= 1000):
                _invalid()
            self.items = []
            self.by_ref = {}
            seen_ids, seen_digests = set(), set()
            for row in manifest['items']:
                if (type(row) is not dict or not all(isinstance(row.get(k), str) and row[k]
                        for k in ('media_unit_id', 'asset_version', 'source_namespace',
                                  'source_asset_id', 'file', 'content_digest',
                                  'label_search_intent', 'source_url', 'license'))
                        or row['label_search_intent'] not in LABELS
                        or not row['source_url'].startswith((
                            'https://commons.wikimedia.org/wiki/File:',
                            'https://commons.wikimedia.org/wiki/File%3A'))
                        or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['content_digest'])):
                    _invalid()
                relative = Path(row['file'])
                path = (self.root / relative).resolve()
                if (relative.is_absolute() or '..' in relative.parts
                        or not path.is_relative_to(self.root)
                        or path.suffix.lower() not in ('.jpg', '.jpeg', '.png')):
                    _invalid('Local test media path is outside the corpus')
                content = path.read_bytes()
                if (not content or len(content) > MAX_FILE_BYTES
                        or not (content.startswith(b'\xff\xd8\xff')
                                or content.startswith(b'\x89PNG\r\n\x1a\n'))
                        or 'sha256:' + hashlib.sha256(content).hexdigest() != row['content_digest']):
                    _invalid('Local test media content does not match manifest')
                identity = (row['source_namespace'], row['source_asset_id'],
                            row['media_unit_id'], row['asset_version'])
                if identity in seen_ids or row['content_digest'] in seen_digests:
                    _invalid('Duplicate local test media')
                seen_ids.add(identity)
                seen_digests.add(row['content_digest'])
                ref = 'media:local-test:' + row['source_asset_id'] + '@' + row['asset_version']
                if ref in self.by_ref:
                    _invalid('Duplicate local test media ref')
                self.by_ref[ref] = (path, row['content_digest'])
                self.items.append((row, ref))
        except ServiceError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ServiceError('LOCAL_TEST_MEDIA_INVALID', 'Local test media unavailable', 503) from exc

    def build_fixture(self, out_dir, *, max_depth=100, batch_size=20):
        """Write LOCAL_EXACT configs. Rankings are label-proxy, not image embeddings."""
        if type(max_depth) is not int or not 1 <= max_depth <= 1000:
            raise ServiceError('LOCAL_TEST_MEDIA_INVALID', 'max_depth must be 1..1000', 422)
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ServiceError('LOCAL_TEST_MEDIA_INVALID', 'batch_size must be 1..100', 422)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        binding = {'binding_id': 'commons-test-v1', 'library_id': 'commons-test',
                   'index_version': 'commons-test-v1', 'encoder_ref': 'label-proxy-v1',
                   'vector_dimension': 2, 'metric': 'COSINE'}
        catalog = {'schema': 'local-catalog@1', 'libraries': [{
            'library_id': 'commons-test', 'description': '本地测试图片：动物、人狗合影',
            'media_type': 'image', 'bindings': [binding]}],
            'grants': [{'tenant_id': 'tenant-a', 'owner_id': 'user-a',
                        'project_id': 'demo', 'library_ids': ['commons-test'],
                        'can_configure': True}]}
        capabilities = {'schema': 'text-query-capabilities@1', 'items': [{
            'binding': binding, 'media_type': 'image',
            'filter_template_ref': 'test-public', 'max_batch_size': batch_size}]}
        vectors = {label: [round(math.cos(2 * math.pi * i / len(LABELS)), 6),
                           round(math.sin(2 * math.pi * i / len(LABELS)), 6)]
                   for i, label in enumerate(LABELS)}
        vectors['dog_person'] = vectors['dog']
        rules = [{'binding_id': binding['binding_id'], 'tokens': list(tokens),
                  'values': vectors[label]} for label, tokens in LABELS.items()]
        records = []
        for position, (row, ref) in enumerate(self.items, 1):
            records.append({'binding_id': binding['binding_id'], 'vector_id': position,
                'values': vectors[row['label_search_intent']],
                'scope': 'public', 'media': [{
                    'media_unit_id': row['media_unit_id'],
                    'asset_version': row['asset_version'],
                    'source_namespace': row['source_namespace'],
                    'source_asset_id': row['source_asset_id'],
                    'authorized_media_ref': ref,
                    'content_digest': row['content_digest']}],
                })
        fixture = {'schema': 'local-first-search@1', 'max_depth': max_depth,
                   'filter_templates': {'test-public': ['public']},
                   'query_vectors': [{'binding_id': binding['binding_id'],
                                      'text': '找图片', 'values': [1.0, 0.0]}],
                   'query_rules': rules, 'records': records}
        for name, value in (('catalog.json', catalog),
                            ('capabilities.json', capabilities),
                            ('fixture.json', fixture)):
            (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        return out

    def export_metadata(self, media_ref):
        for row, ref in self.items:
            if ref == media_ref:
                return {key: row[key] for key in ('source_url', 'license', 'author', 'attribution') if key in row}
        raise ServiceError('ACCESS_DENIED', 'Media export metadata unavailable', 403)

    @contextmanager
    def open_read(self, context, media_ref, *, max_bytes):
        if (self.service is None or type(context) is not AccessContext
                or context.purpose not in ('cleaning_input', 'candidate_preview', 'delivery_export')
                or media_ref not in self.by_ref):
            raise ServiceError('ACCESS_DENIED', 'Local test media access denied', 403)
        reader = getattr(self.service, 'task_read_summary', self.service.get_task)
        task = reader(context.actor, context.task_id)
        if task['project_id'] != 'demo':
            raise ServiceError('ACCESS_DENIED', 'Local test media project mismatch', 403)
        path, expected = self.by_ref[media_ref]
        stat = path.stat()
        version = (str(path), expected, stat.st_dev, stat.st_ino, stat.st_size,
                   stat.st_mtime_ns, stat.st_ctime_ns)
        def load():
            content = path.read_bytes()
            if (len(content) > MAX_FILE_BYTES
                    or 'sha256:' + hashlib.sha256(content).hexdigest() != expected
                    or path.stat() != stat):
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Local test media changed', 409)
            return content
        content = self.byte_cache.get_or_create(version, load)
        if len(content) > min(max_bytes, MAX_FILE_BYTES):
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Local test media exceeds limit', 409)
        yield BytesIO(content)

    def thumbnail(self, context, media_ref, *, max_bytes):
        # open_read always rechecks task/project authorization and source stat.
        with self.open_read(context, media_ref, max_bytes=max_bytes) as stream:
            content = stream.read()
        key = (hashlib.sha256(content).digest(), 640, 'thumbnail-v1')
        def create():
            try:
                from PIL import Image, ImageOps, UnidentifiedImageError
            except ImportError:
                # Minimal installs keep the existing original-byte preview.
                return content
            try:
                source = Image.open(BytesIO(content))
            except (UnidentifiedImageError, OSError, ValueError) as error:
                raise ServiceError('MEDIA_PREVIEW_UNAVAILABLE', 'Image cannot be decoded', 422) from error
            with source as image:
                if max(image.size) <= 640:
                    return content
                image = ImageOps.exif_transpose(image)
                image.thumbnail((640, 640))
                output = BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=82)
                return output.getvalue()
        return self.thumbnail_cache.get_or_create(key, create)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Validate downloaded images and build LOCAL_EXACT test fixture')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--max-depth', type=int, default=100)
    parser.add_argument('--batch-size', type=int, default=20)
    args = parser.parse_args(argv)
    corpus = LocalTestMediaCorpus(args.manifest)
    out = corpus.build_fixture(args.out, max_depth=args.max_depth, batch_size=args.batch_size)
    print(f'Validated {len(corpus.items)} images; wrote fixture to {out.resolve()}')


if __name__ == '__main__':
    main()
