"""One isolated DSH process tree per reasoning turn; SDK types stay here."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys

VERSION = '0.2.1rc2'
SKILL_PATH = Path(__file__).with_name('skills') / 'retrieval-expansion' / 'SKILL.md'


class TurnFailure(Exception):
    """Only an allowlisted provider error code, never the upstream message."""


SKILL = SKILL_PATH.read_text(encoding='utf-8')


def submission_tool(mode):
    return {'request_draft': 'mcp__retrieval__update_request_draft',
            'expansion_draft': 'mcp__retrieval__save_expansion_draft',
            'proposal': 'mcp__retrieval__submit_proposal'}[mode]


def run(request):
    for package in ('deepseek-harness-sdk', 'deepseek-harness-runtime-bin'):
        if importlib.metadata.version(package) != VERSION:
            raise ValueError('Runtime version mismatch')
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
    root = Path(request['turn_dir'])
    (root / 'context.json').write_text(json.dumps(request['context'], ensure_ascii=False))
    # The worker runs from the source tree on every turn; a service started before
    # per-model options existed omits them, so keep its former fixed values.
    max_tokens = request.get('max_tokens', 8000)
    config = DeepSeekHarnessConfig(
        provider='retrieval-gateway', model=request['model'], max_tokens=max_tokens,
        profile='sdk-minimal', dsh_home=str(root / 'home'), cwd=str(root),
        patches=(str(Path(__file__).with_name('retrieval.patch.yml')),),
        initialize_timeout_seconds=25, request_timeout_seconds=request.get('request_timeout_seconds', 90),
        env={'RETRIEVAL_MODEL_KEY': request['api_key'],
             'RETRIEVAL_MODEL_URL': request['base_url'],
             'RETRIEVAL_MODEL_ID': request['model'],
             'RETRIEVAL_MAX_TOKENS': str(max_tokens),
             'RETRIEVAL_PYTHON': sys.executable,
             'RETRIEVAL_TURN_DIR': str(root),
             'DSH_SYSTEM_PROMPT': SKILL + '\nTask-specific proposal schema:\n' + request['system']},
    )
    with DeepSeekHarness(config) as harness:
        result = harness.run('Read the authorized context and submit a proposal using the tools.',
                             session_id=request['run_id'])
    proposal = root / 'proposal.json'
    if result.finish_reason == 'error':
        ends = [event for event in result.events if event.get('type') == 'turn/end']
        code = ends[-1].get('data', {}).get('reason', {}).get('error', {}).get('code') if ends else None
        if code in {'AUTH', 'RATE_LIMIT', 'QUOTA', 'TIMEOUT', 'TRANSPORT', 'INVALID_REQUEST'}:
            raise TurnFailure(code)
    calls = {event['data']['callId']: event['data']['name'] for event in result.events
             if event.get('type') == 'tool/call'}
    completed = {calls.get(event['data']['message'].get('toolCallId')) for event in result.events
                 if event.get('type') == 'tool/result'
                 and not event['data'].get('error') and not event['data']['message'].get('isError')}
    required = {'mcp__retrieval__get_request_context',
                submission_tool(request['context'].get('tool_mode', 'proposal'))}
    if (result.finish_reason != 'completed' or not proposal.is_file()
            or proposal.stat().st_size > 100_000
            or not required <= completed):
        raise ValueError('Missing tool proposal or incomplete turn')
    value = json.loads(proposal.read_text())
    # Only normalized event types leave the SDK; raw events contain user text.
    return {'proposal': value, 'session_id': result.session_id,
            'finish_reason': result.finish_reason,
            'event_types': sorted({str(event.get('type', 'unknown')) for event in result.events}),
            'runtime_version': VERSION}


def main():
    os.umask(0o077)
    try:
        if sys.argv[1:] == ['--check']:
            for package in ('deepseek-harness-sdk', 'deepseek-harness-runtime-bin'):
                if importlib.metadata.version(package) != VERSION:
                    raise ValueError('Runtime version mismatch')
            print(json.dumps({'version': VERSION}))
            return
        raw = sys.stdin.buffer.read(500_001)
        if len(raw) > 500_000:
            raise ValueError('Input limit')
        result = run(json.loads(raw))
        print(json.dumps(result, ensure_ascii=False))
    except TurnFailure as error:
        print(json.dumps({'error': str(error)}))
        raise SystemExit(1) from None
    except Exception:
        # Never print SDK exceptions: they can contain prompts or provider bodies.
        print(json.dumps({'error': 'DSH_TURN_FAILED'}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
