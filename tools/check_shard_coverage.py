"""Read-only audit: python tools/check_shard_coverage.py CATALOG ENDPOINTS."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dsh_retrieval.shard_coverage import coverage_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('endpoints', type=Path)
    args = parser.parse_args()
    try:
        values = []
        for path in (args.catalog, args.endpoints):
            if path.stat().st_size > 8_000_000:
                raise ValueError('input too large')
            values.append(json.loads(path.read_text()))
        report = coverage_report(*values)
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(2, 'Invalid catalog or endpoint declarations; private values omitted.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    sys.exit(main())
