"""Capture/read frozen evidence and compare results without running a model or task."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from dsh_retrieval.models import Actor
from dsh_retrieval.recall_capture import capture_run
from dsh_retrieval.recall_evaluation import summarize_run, compare_runs, prepare_review, attach_judgments, review_worksheet


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='mode', required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('--ledger', type=Path, required=True)
    capture.add_argument('--tenant', required=True)
    capture.add_argument('--user', required=True)
    capture.add_argument('--task-id', required=True)
    capture.add_argument('--corpus', type=Path)
    capture.add_argument('--output', type=Path, required=True)
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('--input', type=Path, required=True)
    evaluate.add_argument('--baseline', type=Path)
    evaluate.add_argument('--baseline-judgments', type=Path)
    evaluate.add_argument('--judgments', type=Path)
    evaluate.add_argument('--output', type=Path, required=True)
    review = sub.add_parser('prepare-review')
    review.add_argument('--input', type=Path, required=True)
    review.add_argument('--output', type=Path, required=True)
    review.add_argument('--worksheet', type=Path)
    args = p.parse_args()
    if args.mode == 'prepare-review' and args.worksheet and (
            args.worksheet.exists() or args.worksheet.resolve() == args.output.resolve()):
        p.error('worksheet must be a new, separate file')
    if args.mode == 'capture':
        corpus = ('sha256:' + hashlib.sha256(args.corpus.read_bytes()).hexdigest()
                  if args.corpus else None)
        result = capture_run(args.ledger, Actor(args.tenant, args.user), args.task_id,
                             corpus_digest=corpus)
    elif args.mode == 'prepare-review':
        result = prepare_review(json.loads(args.input.read_text()))
    else:
        run = json.loads(args.input.read_text())
        if args.judgments:
            run = attach_judgments(run, json.loads(args.judgments.read_text()))
        if args.baseline_judgments and not args.baseline:
            p.error('--baseline-judgments requires --baseline')
        if args.baseline:
            baseline = json.loads(args.baseline.read_text())
            if args.baseline_judgments:
                baseline = attach_judgments(baseline, json.loads(args.baseline_judgments.read_text()))
            result = compare_runs(baseline, run)
        else:
            result = summarize_run(run)
    # Actual task text/identities and annotations stay outside the repository.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    if args.mode == 'prepare-review' and args.worksheet:
        fd = os.open(args.worksheet, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(review_worksheet(result))
    print(json.dumps({'schema': result['schema'], 'output_written': True}))


if __name__ == '__main__':
    main()
