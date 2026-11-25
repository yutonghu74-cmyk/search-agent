"""Independent-process fixture; not an actual production/training Agent."""
import json
import sys

boot = json.loads(sys.stdin.readline())
invocation = boot['invocation']
protocol = 'harness-worker@1'
print(json.dumps({'protocol': protocol, 'type': 'tool', 'id': 1,
    'name': invocation['tools'][0]['name'],
    'arguments': {'task_ref': invocation['context']['task_ref']}}), flush=True)
reply = json.loads(sys.stdin.readline())
if 'error_code' in reply:
    raise SystemExit(1)
print(json.dumps({'protocol': protocol, 'type': 'done'}), flush=True)
