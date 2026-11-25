"""Phased external Agent example: planning workers never approve owner actions.

Run with PYTHONPATH=src. Token values come from the environment and are never
printed. Each phase can be a tool in a team's existing multi-Agent framework.
"""
import argparse
import json
import os
from pathlib import Path

from dsh_retrieval.shared_sdk import AgentClient, SharedTransport, SharedApiError


def retrieval_agent(client, task_id, key):
    preview=client.retrieval_preview(task_id)
    # Deterministic example policy, not an LLM or relevance-quality evaluation.
    choices=[{'branch_id':route['branch_id'],'binding_id':route['options'][0]['binding_id'],
              'batch_size':min(2,route['options'][0]['max_batch_size'])}
             for route in preview['routes'] if route['options']]
    return client.prepare_retrieval(task_id,routing_digest=preview['routing_digest'],choices=choices,key=key)


def cleaning_agent(client, task_id, manifest_ref, key):
    return client.prepare_cleaning(task_id,manifest_ref,key=key)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('bind','prepare-retrieval','retrieval-status','review-manifest',
        'prepare-cleaning','execute-cleaning','cleaning-status','results','receipt','cancel'))
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--task-file',required=True,help='Owner-confirmed task JSON; no credentials')
    parser.add_argument('--external-task',required=True)
    parser.add_argument('--key',default='example-operation')
    parser.add_argument('--manifest-ref')
    parser.add_argument('--operation-id')
    parser.add_argument('--operation',choices=('retrieval.prepare','cleaning.prepare','cleaning.execute'))
    parser.add_argument('--allow-local-http',action='store_true',help='Only isolated localhost fixtures')
    args=parser.parse_args()
    task=json.loads(Path(args.task_file).read_text());task_id=task['task_id']
    transport=SharedTransport(args.base_url,project_id=args.project,
        credential=lambda:os.environ['SHARED_AGENT_TOKEN'],allow_local_http=args.allow_local_http)
    client=AgentClient(transport,external_task_ref=args.external_task)
    if args.phase=='bind':
        result=client.bind(task_id,revision=task['confirmed_revision'],generation=task['run_generation'],
            task_version=task['task_version'],key=args.key)
    elif args.phase=='prepare-retrieval':result=retrieval_agent(client,task_id,args.key)
    elif args.phase=='retrieval-status':result=client.retrieval_status(task_id)
    elif args.phase=='review-manifest':result=client.candidate_manifest(task_id)
    elif args.phase=='prepare-cleaning':
        if not args.manifest_ref:parser.error('--manifest-ref required')
        result=cleaning_agent(client,task_id,args.manifest_ref,args.key)
    elif args.phase in ('execute-cleaning','cleaning-status'):
        if not args.operation_id:parser.error('--operation-id required')
        result=(client.execute_cleaning(task_id,args.operation_id,key=args.key) if args.phase=='execute-cleaning'
                else client.cleaning_status(task_id,args.operation_id))
    elif args.phase=='results':result=client.results(task_id)
    elif args.phase=='receipt':
        if not args.operation:parser.error('--operation required')
        result=client.receipt(task_id,operation=args.operation,key=args.key)
    else:
        result=client.cancel(task_id,task_version=task['task_version'],key=args.key)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except SharedApiError as error:raise SystemExit(error.code) from None
