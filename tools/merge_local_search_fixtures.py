"""Combine two explicitly trusted LOCAL_EXACT fixtures for one preview service.

Keeps existing bindings so persisted plans remain readable while adding a
downloaded test-media binding. This is a development-only configuration tool.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path, schema: str) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('schema') != schema:
        raise ValueError(f'Unexpected schema: {path}')
    return value


def merge(base: Path, extra: Path, output: Path) -> None:
    files = (
        ('catalog.json', 'local-catalog@1'),
        ('capabilities.json', 'text-query-capabilities@1'),
        ('fixture.json', 'local-first-search@1'),
    )
    values = {name: (read(base / name, schema), read(extra / name, schema))
              for name, schema in files}
    catalog_a, catalog_b = values['catalog.json']
    capability_a, capability_b = values['capabilities.json']
    fixture_a, fixture_b = values['fixture.json']
    libraries = catalog_a['libraries'] + catalog_b['libraries']
    bindings = [binding['binding_id'] for library in libraries
                for binding in library['bindings']]
    if len(set(bindings)) != len(bindings):
        raise ValueError('Binding IDs overlap')
    grants = {}
    for grant in catalog_a['grants'] + catalog_b['grants']:
        identity = (grant['tenant_id'], grant['owner_id'], grant['project_id'])
        current = grants.setdefault(identity, dict(grant, library_ids=[]))
        if current['can_configure'] != grant['can_configure']:
            raise ValueError('Grant permissions disagree')
        current['library_ids'] = sorted(set(current['library_ids']) | set(grant['library_ids']))
    templates = dict(fixture_a['filter_templates'])
    for name, scopes in fixture_b['filter_templates'].items():
        if name in templates and templates[name] != scopes:
            raise ValueError('Filter templates disagree')
        templates[name] = scopes
    merged = {
        'catalog.json': dict(catalog_a, libraries=libraries, grants=list(grants.values())),
        'capabilities.json': dict(capability_a, items=capability_a['items'] + capability_b['items']),
        'fixture.json': dict(fixture_a, max_depth=max(fixture_a['max_depth'], fixture_b['max_depth']),
                             filter_templates=templates,
                             query_vectors=fixture_a['query_vectors'] + fixture_b['query_vectors'],
                             query_rules=fixture_a['query_rules'] + fixture_b['query_rules'],
                             records=fixture_a['records'] + fixture_b['records']),
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, value in merged.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--extra', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    merge(args.base, args.extra, args.out)


if __name__ == '__main__':
    main()
