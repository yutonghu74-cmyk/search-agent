"""Explicit owner approval after reviewing the saved proposal JSON.

Keep this command outside Agent tool lists. Server-side verified principal
checks also reject integration credentials, even if they invoke this script.
"""
import argparse
import json
import os
from pathlib import Path

from dsh_retrieval.shared_sdk import OwnerClient, SharedTransport, SharedApiError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--task',required=True)
    parser.add_argument('--stage',choices=('retrieval','manifest','cleaning'),required=True)
    parser.add_argument('--review-file',required=True)
    parser.add_argument('--key',required=True)
    parser.add_argument('--allow-local-http',action='store_true')
    args=parser.parse_args()
    client=OwnerClient(SharedTransport(args.base_url,project_id=args.project,
        credential=lambda:os.environ['SHARED_OWNER_TOKEN'],allow_local_http=args.allow_local_http))
    result=client.confirm(args.task,json.loads(Path(args.review_file).read_text()),stage=args.stage,key=args.key)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except SharedApiError as error:raise SystemExit(error.code) from None
