"""Summarize numeric completed activations from a service log; ignore other lines."""
import argparse
from collections import defaultdict, deque
import json
import re


def records(lines):
    for line in lines:
        marker = line.find('pipeline_timing ')
        if marker < 0 or len(line) > 1_000_000:
            continue
        try:
            row = json.loads(line[marker + len('pipeline_timing '):])
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('schema_version') == 'pipeline-timing@1' and row.get('event') == 'completed':
            yield row


def summarize(rows):
    samples = defaultdict(list)
    failures = defaultdict(int)
    calls = defaultdict(int)
    valid_name = re.compile(r'[a-z_]{1,48}')
    for row in rows:
        operation = row.get('operation')
        if not isinstance(operation, str) or not valid_name.fullmatch(operation):
            continue
        stages = row.get('stages', {})
        if not isinstance(stages, dict):
            stages = {}
        for name, value in [('total', {'ms': row.get('total_ms'), 'calls': 1,
                                     'failures': int(row.get('outcome') == 'failed')})] + list(stages.items()):
            if not isinstance(name, str) or not valid_name.fullmatch(name) or not isinstance(value, dict):
                continue
            ms = value.get('ms')
            if type(ms) not in (int, float) or not 0 <= ms < float('inf'):
                continue
            key = operation + '.' + name
            samples[key].append(ms)
            for field, target in [('calls', calls), ('failures', failures)]:
                count = value.get(field, 0)
                if type(count) is int and count >= 0:
                    target[key] += count
    result = {}
    for key, values in sorted(samples.items()):
        values.sort()
        result[key] = {'samples': len(values), 'calls': calls[key], 'failures': failures[key],
                       'p50_ms': values[(len(values) - 1) // 2],
                       'p95_ms': values[max(0, (len(values) * 95 + 99) // 100 - 1)]}
    return {'schema_version': 'pipeline-timing-report@1', 'stages_are_inclusive': True, 'groups': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log')
    parser.add_argument('--task-id')
    parser.add_argument('--job-id')
    parser.add_argument('--trace-id')
    args = parser.parse_args()
    # Keep bounded diagnostics even when the service log contains months of data.
    with open(args.log, encoding='utf-8', errors='replace') as source:
        rows = deque((row for row in records(source) if all(
            value is None or row.get(key) == value
            for key, value in [('task_id', args.task_id), ('job_id', args.job_id), ('trace_id', args.trace_id)])), maxlen=10000)
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
