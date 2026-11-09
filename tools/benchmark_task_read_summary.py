"""Measure synthetic task-list reads in disposable SQLite ledgers.

Run with PYTHONPATH=src:tests. This is a service/storage microbenchmark, not an
HTTP, browser, media, or production acceptance test. No existing ledger is read.
"""

import argparse
from contextlib import closing
from dataclasses import replace
import json
import platform
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump
from dsh_retrieval.models import Actor
from draft_helpers import confirmation, make_service


def measure(operation, samples):
    operation()
    timings = []
    for _ in range(samples):
        start = perf_counter()
        operation()
        timings.append((perf_counter() - start) * 1000)
    ordered = sorted(timings)
    return {'samples': samples, 'p50_ms': round(statistics.median(timings), 3),
            'p95_ms': round(ordered[int((samples - 1) * .95)], 3)}


def benchmark(size, samples):
    with TemporaryDirectory(prefix='task-read-benchmark-') as directory:
        path = Path(directory) / 'synthetic.sqlite'
        repo = SQLiteTaskRepository(path)
        service = make_service(repo)
        actor = Actor('tenant-a', 'user-a')
        task_id = service.create_task(actor, 'confirm', confirmation(service, actor))[0]['task_id']
        with closing(repo._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = repo._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            template = memory._tasks[task_id]
            memory._tasks = {f'rt_benchmark_{index:06d}': replace(template,
                task_id=f'rt_benchmark_{index:06d}') for index in range(size)}
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))
            repo._sync_task_read_summaries(db, memory)
        read = lambda: service.list_tasks_page(actor, 'demo', limit=20)
        indexed = measure(read, samples)
        with patch.object(repo, 'list_task_read_page', return_value=None):
            legacy = measure(read, samples)
        binding = measure(lambda: service.current_read_binding(
            actor, 'rt_benchmark_000000'), samples)
        return {'tasks': size, 'page_size': 20, 'sqlite_bytes': path.stat().st_size,
                'indexed_list': indexed, 'legacy_list': legacy,
                'current_binding': binding}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=100)
    args = parser.parse_args()
    if args.samples < 30:
        parser.error('at least 30 samples are required')
    print(json.dumps({'kind': 'synthetic-service-microbenchmark',
        'python': platform.python_version(), 'system': platform.system(),
        'machine': platform.machine(), 'cache': 'warm OS cache; fresh connection per call',
        'runs': [benchmark(size, args.samples) for size in (100, 1000)]}, indent=2))


if __name__ == '__main__':
    main()
