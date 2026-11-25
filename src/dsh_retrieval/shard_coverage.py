"""Offline shard coverage checks. No discovery, authorization or network calls."""
import json
from .query_spec import digest


def load_catalog(value):
    if (type(value) is not dict or set(value) != {'schema', 'datasets'}
            or value['schema'] != 'image-shard-catalog@1'
            or type(value['datasets']) is not list or not 1 <= len(value['datasets']) <= 256):
        raise ValueError('invalid shard catalog')
    seen = set()
    for row in value['datasets']:
        if (type(row) is not dict or set(row) != {'library_id', 'index_version', 'shards'}
                or any(type(row[k]) is not str or not row[k] for k in ('library_id', 'index_version'))
                or type(row['shards']) is not list or not 1 <= len(row['shards']) <= 10000):
            raise ValueError('invalid dataset declaration')
        key = row['library_id'], row['index_version']
        if key in seen:
            raise ValueError('duplicate dataset')
        seen.add(key)
        ids, files = set(), set()
        for shard in row['shards']:
            if (type(shard) is not dict or set(shard) != {'shard_id', 'file_name', 'status'}
                    or type(shard['shard_id']) is not str or not shard['shard_id']
                    or type(shard['file_name']) is not str or not shard['file_name'].endswith('.db')
                    or any(c in shard['file_name'] for c in ('/', '\\', '\x00', ','))
                    or shard['status'] not in ('ready', 'unavailable')
                    or shard['shard_id'] in ids or shard['file_name'] in files):
                raise ValueError('invalid or duplicate shard')
            ids.add(shard['shard_id']); files.add(shard['file_name'])
    return json.loads(json.dumps(value))


def coverage_report(catalog, endpoint_config):
    catalog = load_catalog(catalog)
    if (type(endpoint_config) is not dict or endpoint_config.get('schema') != 'clip-search-endpoints@1'
            or type(endpoint_config.get('entries')) is not list):
        raise ValueError('invalid endpoint declarations')
    groups = {(r['library_id'], r['index_version']): r for r in catalog['datasets']}
    covered = {key: set() for key in groups}
    bindings = set()
    for endpoint in endpoint_config['entries']:
        binding = endpoint['binding']
        key = binding['library_id'], binding['index_version']
        paths = endpoint['shard_paths']
        if (key not in groups or binding['binding_id'] in bindings
                or type(paths) is not list or not 1 <= len(paths) <= 4
                or any(type(p) is not str for p in paths) or len(set(paths)) != len(paths)):
            raise ValueError('unknown dataset, duplicate binding or invalid shard group')
        bindings.add(binding['binding_id'])
        known = {s['file_name']: s for s in groups[key]['shards']}
        if any(p not in known or known[p]['status'] != 'ready' for p in paths):
            raise ValueError('binding references unknown or unavailable shard')
        covered[key].update(known[p]['shard_id'] for p in paths)
    datasets = []
    for key, row in groups.items():
        all_ids = {s['shard_id'] for s in row['shards']}
        unavailable = [s['shard_id'] for s in row['shards'] if s['status'] != 'ready']
        missing = sorted(all_ids - covered[key])
        ready = [s['shard_id'] for s in row['shards'] if s['status'] == 'ready']
        datasets.append(dict(library_id=key[0], index_version=key[1], total=len(all_ids),
            configured=len(covered[key]), missing=missing, unavailable=unavailable,
            complete=not missing,
            planned_groups=[ready[i:i+4] for i in range(0, len(ready), 4)]))
    return dict(schema='shard-coverage-report@1', catalog_digest=digest(catalog),
                complete=all(r['complete'] for r in datasets), datasets=datasets,
                evidence='configuration_only_not_execution')
