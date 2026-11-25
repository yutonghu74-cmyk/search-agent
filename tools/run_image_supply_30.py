#!/usr/bin/env python3
"""Run the explicit 30-case image supply regression against isolated test fixtures."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='JSON result file')
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
    cases = json.loads((ROOT / 'tests/image_supply_30.json').read_text())
    if len(cases) != 30 or len({c['test'] for c in cases}) != 30:
        raise ValueError('Expected exactly 30 distinct test methods')
    started = datetime.now(ZoneInfo('America/New_York')).isoformat()
    results = []
    for case in cases:
        suite = unittest.defaultTestLoader.loadTestsFromName(case['test'])
        before = time.monotonic()
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        status = ('ERROR' if result.errors else 'FAIL' if result.failures or result.unexpectedSuccesses
                  else 'SKIP' if result.skipped else 'XFAIL' if result.expectedFailures else 'PASS')
        results.append({**case, 'status': status, 'tests_run': result.testsRun,
                        'seconds': round(time.monotonic() - before, 3),
                        'details': [detail for _, detail in result.errors + result.failures],
                        'skipped': [reason for _, reason in result.skipped]})
        print(f"{case['id']} {status}: {case['scenario']}", flush=True)
    report = {'started_at': started, 'scope': 'local fixtures; targeted regression; no live services',
              'results': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return 0 if all(r['status'] == 'PASS' and r['tests_run'] == 1 for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
