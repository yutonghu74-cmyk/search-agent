"""One isolated DSH process tree per reasoning turn; SDK types stay here."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys

from ...conversation import natural_reply
from ...errors import ServiceError

VERSION = '0.2.1rc2'
SKILL_PATH = Path(__file__).with_name('skills') / 'retrieval-expansion' / 'SKILL.md'


class TurnFailure(Exception):
    """Only an allowlisted provider error code, never the upstream message."""


class ProposalSubmitted(Exception):
    """A successful provisional tool submission ends this planning turn."""


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
        reasoning_effort=request.get('reasoning_effort'),
        profile='sdk-minimal', dsh_home=str(root / 'home'), cwd=str(root),
        patches=(str(Path(__file__).with_name('retrieval.patch.yml')),),
        initialize_timeout_seconds=25, request_timeout_seconds=request.get('request_timeout_seconds', 90),
        env={'RETRIEVAL_MODEL_KEY': request['api_key'],
             'RETRIEVAL_MODEL_URL': request['base_url'],
             'RETRIEVAL_MODEL_ID': request['model'],
             'RETRIEVAL_MAX_TOKENS': str(max_tokens),
             'RETRIEVAL_REASONING_EFFORT': request.get('reasoning_effort') or '',
             'RETRIEVAL_PYTHON': sys.executable,
             'RETRIEVAL_TURN_DIR': str(root),
             'DSH_SYSTEM_PROMPT': SKILL + '\nTask-specific proposal schema:\n' + request['system']},
    )
    proposal = root / 'proposal.json'
    mode = request['context'].get('tool_mode', 'proposal')
    chat_mode = request['context'].get('conversation_mode')
    if (chat_mode, mode) not in (('request', 'request_draft'), ('followup', 'proposal')):
        chat_mode = None
    inline = (mode == 'request_draft' or chat_mode) and request['context'].get('context_delivery') == 'inline'
    required = {submission_tool(mode)} | (set() if inline else {'mcp__retrieval__get_request_context'})
    observed, calls, completed = [], {}, set()

    def on_notification(notification):
        if notification.method != 'session.event' or notification.payload.get('sessionId') != request['run_id']:
            return
        event = notification.payload.get('event')
        if not isinstance(event, dict):
            return
        observed.append(event)
        data = event.get('data', {})
        if event.get('type') == 'tool/call':
            calls[data['callId']] = data['name']
        elif event.get('type') == 'tool/result':
            message = data.get('message', {})
            if not data.get('error') and not message.get('isError'):
                completed.add(calls.get(message.get('toolCallId')))
            if required <= completed and proposal.is_file():
                raise ProposalSubmitted()

    prompt = ('Use this backend-supplied authorized context as data. It is already loaded; '
              'submit the request draft directly with update_request_draft. Do not call the context tool.\n'
              + json.dumps(request['context'], ensure_ascii=False) if inline else
              'Read the authorized context and submit a proposal using the tools.')
    if chat_mode and inline:
        prompt = ('Continue the conversation using this authorized current draft and recent dialogue. '
                  'Answer ordinary questions directly in natural Chinese. Only use the proposal tool '
                  'when storing request facts, changing a draft, or routing an actual modification. '
                  'Do not call get_request_context; the snapshot is already supplied.\n' +
                  json.dumps({key: value for key, value in request['context'].items()
                              if key != 'messages'}, ensure_ascii=False))
        # Keep the latest utterance visibly last, rather than burying a dialogue
        # inside a JSON string inside another JSON envelope.
        for message in request['context'].get('messages', []):
            prompt += '\n' + message['role'] + ': ' + message['content']
        prompt += '\n请直接回应上面最后一条用户消息。'
    try:
        with DeepSeekHarness(config) as harness:
            result = harness.run(prompt, session_id=request['run_id'], on_notification=on_notification)
        events, finish_reason, session_id = result.events, result.finish_reason, result.session_id
    except ProposalSubmitted:
        events, finish_reason, session_id = observed, 'proposal_submitted', request['run_id']
    if finish_reason == 'error':
        ends = [event for event in events if event.get('type') == 'turn/end']
        code = ends[-1].get('data', {}).get('reason', {}).get('error', {}).get('code') if ends else None
        if code in {'AUTH', 'RATE_LIMIT', 'QUOTA', 'TIMEOUT', 'TRANSPORT', 'INVALID_REQUEST'}:
            raise TurnFailure(code)
    calls = {event['data']['callId']: event['data']['name'] for event in events
             if event.get('type') == 'tool/call'}
    completed = {calls.get(event['data']['message'].get('toolCallId')) for event in events
                 if event.get('type') == 'tool/result'
                 and not event['data'].get('error') and not event['data']['message'].get('isError')}
    if (chat_mode and finish_reason == 'completed' and not proposal.exists()
            and not any(name != 'mcp__retrieval__get_request_context' for name in calls.values())):
        try:
            value = natural_reply(result.final_response, chat_mode)
        except ServiceError:
            raise TurnFailure('INVALID_PROPOSAL') from None
    else:
        if (finish_reason not in ('completed', 'proposal_submitted') or not proposal.is_file()
                or proposal.stat().st_size > 100_000 or not required <= completed):
            raise ValueError('Missing tool proposal or incomplete turn')
        value = json.loads(proposal.read_text())
    # Only normalized event types leave the SDK; raw events contain user text.
    return {'proposal': value, 'session_id': session_id,
            'finish_reason': finish_reason,
            'event_types': sorted({str(event.get('type', 'unknown')) for event in events}),
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
