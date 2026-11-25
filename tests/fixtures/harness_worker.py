"""Installed-test worker for the vendor-neutral stdio transport."""
import json
import sys
import time

boot = json.loads(sys.stdin.readline())
protocol = 'harness-worker@1'
kind = boot['invocation']['task_kind']
def frame(value):
    print(json.dumps(dict(protocol=protocol, **value)), flush=True)
def call(call_id, name, args):
    frame(dict(type='tool', id=call_id, name=name, arguments=args))
    return json.loads(sys.stdin.readline())
if kind == 'hang':
    time.sleep(30)
elif kind == 'malformed':
    print('raw-provider-secret-do-not-expose', flush=True)
elif kind != 'no_output':
    args = {'proposal': {'action': 'rewrite_query', 'binding_id': 'binding', 'method': 'vector',
            'query': '客厅', 'evidence_refs': ['facts-1'], 'reason': '提高相关性'}}
    name = 'submit_retrieval_strategy'
    if boot['invocation']['role'] == 'cleaning':
        name = 'submit_processing_plan'
        args = {'proposal': {'steps': [{'requirement_id': 'watermark-tag', 'step_id': 'tag'}]}}
    call(1, 'get_stage_context', {})
    call(2, name, args)
    call(99, name, args)
frame(dict(type='done'))
