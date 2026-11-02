"""Small, explicitly judged retrieval sentinels for a frozen local CLIP corpus.

The judgments are deliberately partial.  This reports ranks of known positives
and negatives, never corpus-wide precision or recall from unjudged images.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .integration_models import IndexBinding
from .local_clip import LocalCLIPEncoder
from .local_test_media import LocalTestMediaCorpus


def evaluate_sentinels(manifest_path, catalog_path, fixture_path, judgments_path, encoder, *, depth=20):
    if depth < 1:
        raise ValueError('depth must be positive')
    corpus = LocalTestMediaCorpus(manifest_path)
    manifest = {row['file']: row for row, _ in corpus.items}
    corpus_by_id = {row['media_unit_id']: row for row, _ in corpus.items}
    fixture = json.loads(Path(fixture_path).read_text())
    catalog = json.loads(Path(catalog_path).read_text())
    judgments = json.loads(Path(judgments_path).read_text())
    if judgments.get('schema') != 'local-clip-sentinels@1':
        raise ValueError('unsupported sentinel schema')
    assets = judgments.get('assets')
    cases = judgments.get('queries')
    if not isinstance(assets, list) or not assets or not isinstance(cases, list) or not cases:
        raise ValueError('sentinels require assets and queries')
    id_to_file = {}
    for asset in assets:
        file = asset['file']
        row = manifest.get(file)
        if row is None or row['content_digest'] != asset['content_digest']:
            raise ValueError(f'sentinel media missing or changed: {file}')
        media_id = row['media_unit_id']
        if media_id in id_to_file:
            raise ValueError(f'duplicate sentinel media: {media_id}')
        id_to_file[media_id] = file
    bindings = [entry for library in catalog['libraries'] for entry in library['bindings']]
    if len(bindings) != 1:
        raise ValueError('sentinel catalog requires one binding')
    index_binding = IndexBinding(**bindings[0])
    binding = index_binding.binding_id
    records = []
    for record in fixture['records']:
        if record['binding_id'] != binding:
            raise ValueError('sentinel index spans multiple bindings')
        media = record['media']
        if len(media) != 1:
            raise ValueError('sentinel index requires one image per vector')
        source = corpus_by_id.get(media[0]['media_unit_id'])
        if source is None or source['content_digest'] != media[0]['content_digest']:
            raise ValueError('sentinel index media does not match corpus')
        values = record['values']
        if len(values) != encoder.dimension or not all(math.isfinite(v) for v in values):
            raise ValueError('invalid sentinel index vector')
        records.append((media[0]['media_unit_id'], values))
    if not records:
        raise ValueError('sentinel index has no records')
    if len(records) != len(corpus.items) or len({media_id for media_id, _ in records}) != len(records):
        raise ValueError('sentinel index does not cover corpus exactly once')
    if not set(id_to_file).issubset({media_id for media_id, _ in records}):
        raise ValueError('sentinel media not present in index')
    output = {'schema': 'local-clip-sentinel-report@1', 'corpus_count': len(records),
              'judged_asset_count': len(assets), 'depth': depth, 'queries': []}
    for case in cases:
        query = case['text']
        relevance = case['relevance']
        if set(relevance) != set(id_to_file) or any(type(value) is not bool
                                                    for value in relevance.values()):
            raise ValueError(f'incomplete or invalid relevance judgments: {query}')
        positive = {media_id for media_id, value in relevance.items() if value}
        negative = set(id_to_file) - positive
        if not positive or not negative:
            raise ValueError(f'sentinel query needs positive and negative examples: {query}')
        vector = encoder.encode_query(query, index_binding).values
        if len(vector) != encoder.dimension:
            raise ValueError('query vector dimension mismatch')
        ranking = sorted(((sum(a * b for a, b in zip(vector, values)), media_id)
                          for media_id, values in records), key=lambda item: (-item[0], item[1]))
        ranks = {media_id: position for position, (_, media_id) in enumerate(ranking, 1)}
        top = [media_id for _, media_id in ranking[:depth]]
        fully_judged_top = len(top) == depth and all(item in relevance for item in top)
        output['queries'].append({
            'text': query,
            'known_positive_count': len(positive),
            'known_positive_found_at_depth': sum(ranks[item] <= depth for item in positive),
            'known_negative_at_depth': sum(item in negative for item in top),
            'fully_judged_top': fully_judged_top,
            'precision_at_depth': (sum(item in positive for item in top) / depth
                                   if fully_judged_top else None),
            'positive_ranks': {id_to_file[item]: ranks[item] for item in sorted(positive)},
            'negative_ranks': {id_to_file[item]: ranks[item] for item in sorted(negative)},
            'top': [{'rank': rank, 'media_unit_id': media_id,
                     'file': corpus_by_id[media_id]['file'],
                     'judgment': relevance.get(media_id)}
                    for rank, media_id in enumerate(top, 1)],
        })
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description='Evaluate partial human-reviewed local CLIP sentinels')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--judgments', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--depth', type=int, default=20)
    args = parser.parse_args(argv)
    encoder = LocalCLIPEncoder(args.model)
    result = evaluate_sentinels(args.manifest, args.catalog, args.fixture, args.judgments, encoder,
                                depth=args.depth)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
