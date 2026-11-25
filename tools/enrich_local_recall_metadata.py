"""Join trusted original titles into a NEW local fixture; never infer captions."""
from copy import deepcopy
import argparse
import json
from pathlib import Path


def enrich(fixture, manifest):
    titles = {}
    for item in manifest['items']:
        key = (item['media_unit_id'], item['asset_version'], item['content_digest'])
        title = item.get('source_title')
        if title is not None and not isinstance(title, str):
            raise ValueError('source title must be text')
        if title and title.strip():
            titles.setdefault(key, set()).add(title.strip())
    result = deepcopy(fixture)
    enriched = 0
    for record in result['records']:
        values = set()
        for media in record['media']:
            key = (media['media_unit_id'], media['asset_version'], media['content_digest'])
            values.update(titles.get(key, ()))
        # Preserve explicitly curated text. Never use the search-intent label,
        # media URL, file name, or an unmatched/stale annotation as a caption.
        if record.get('search_text'):
            continue
        if values:
            text = ' '.join(sorted(values))
            if len(text) > 10000:
                raise ValueError('combined title exceeds search text limit')
            record['search_text'] = text
            enriched += 1
    return result, {'records': len(result['records']), 'enriched': enriched,
                    'with_text': sum(bool(r.get('search_text')) for r in result['records']),
                    'source_field': 'source_title', 'identity_check': 'id+version+content_digest'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve() in {args.fixture.resolve(), args.manifest.resolve()}:
        p.error('output must be a new file; promotion is a separate operation')
    result, report = enrich(json.loads(args.fixture.read_text()), json.loads(args.manifest.read_text()))
    with args.output.open('x') as f:
        json.dump(result, f, ensure_ascii=False)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
