"""Legacy SDK worker and MCP socket bridge. No business validators live here."""
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import threading

PROTOCOL = 'harness-worker@1'
LIMIT = 500000


def frame(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    sys.stdout.flush()


def read(stream):
    value = stream.readline(LIMIT + 1)
    if not value or len(value.encode()) > LIMIT: raise ValueError('frame limit')
    return json.loads(value)


def versions():
    return {'sdk': importlib.metadata.version('deepseek-harness-sdk'),
            'runtime': importlib.metadata.version('deepseek-harness-runtime-bin')}


def bridge():
    # The SDK starts this MCP child; socket is inside an owner-only attempt directory.
    for raw in sys.stdin:
        if len(raw.encode()) > LIMIT: raise ValueError('MCP frame limit')
        request = json.loads(raw)
        if 'id' not in request: continue
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(os.environ['HARNESS_BROKER_SOCKET'])
            with client.makefile('rw', encoding='utf-8') as stream:
                stream.write(json.dumps(request) + '\n'); stream.flush()
                result = read(stream)
        frame(result)


def run():
    expected = {'sdk': os.environ['HARNESS_DSH_SDK_VERSION'],
                'runtime': os.environ['HARNESS_DSH_RUNTIME_VERSION']}
    if versions() != expected: raise ValueError('version mismatch')
    api = os.environ['HARNESS_DSH_API']
    if api not in ('config-v1', 'keywords-v2'): raise ValueError('unregistered SDK API')
    from deepseek_harness import DeepSeekHarness
    boot = read(sys.stdin)
    if boot.get('protocol') != PROTOCOL: raise ValueError('protocol mismatch')
    invocation = boot['invocation']
    root = Path(os.environ['HARNESS_ATTEMPT_DIR'])
    socket_path = root / 'broker.sock'
    # Unix path limits can be short on macOS; callers must choose a short isolated home.
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(socket_path)); os.chmod(socket_path, 0o600); server.listen(4)
    stopped = threading.Event()
    channel_lock = threading.Lock()
    channel_counter = 0
    def parent(kind, **parameters):
        nonlocal channel_counter
        with channel_lock:
            channel_counter += 1
            frame({'protocol': PROTOCOL, 'type': kind, 'id': channel_counter, **parameters})
            response = read(sys.stdin)
            if response.get('type') != 'tool_result' or response.get('id') != channel_counter:
                raise ValueError('broker response mismatch')
            return response
    def serve():
        counter = 0
        while not stopped.is_set():
            try: client, _ = server.accept()
            except OSError: return
            with client, client.makefile('rw', encoding='utf-8') as stream:
                request = {}
                try:
                    request = read(stream); method = request.get('method')
                    if method == 'initialize':
                        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                                  'serverInfo': {'name': 'business', 'version': '1'}}
                    elif method == 'ping': result = {}
                    elif method == 'tools/list': result = {'tools': invocation['tools']}
                    elif method == 'tools/call':
                        params = request.get('params', {})
                        response = parent('tool', name=params.get('name'), arguments=params.get('arguments', {}))
                        result = {'content': [{'type': 'text', 'text': json.dumps(
                            response.get('value', {'error_code': response.get('error_code')}), ensure_ascii=False)}]}
                        if 'error_code' in response: result['isError'] = True
                    else: raise ValueError('method unavailable')
                    value = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
                except Exception:
                    value = {'jsonrpc': '2.0', 'id': request.get('id'),
                             'error': {'code': -32603, 'message': 'Business tool bridge rejected request'}}
                stream.write(json.dumps(value, ensure_ascii=False) + '\n'); stream.flush()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    original = Path(__file__).parents[1] / 'dsh' / 'retrieval.patch.yml'
    patch = original.read_text()
    old = "          - dsh_retrieval.adapters.dsh.tools_server\n          - !!js process.env.RETRIEVAL_TURN_DIR"
    if old not in patch: raise ValueError('legacy patch changed')
    patch = patch.replace(old, "          - 'dsh_retrieval.adapters.harness.dsh_worker'\n          - '--mcp'")
    patch = patch.replace('serverName: retrieval', 'serverName: business')
    patch = patch.replace('          PYTHONPATH: !!js process.env.PYTHONPATH',
                          '          PYTHONPATH: !!js process.env.PYTHONPATH\n          HARNESS_BROKER_SOCKET: !!js process.env.HARNESS_BROKER_SOCKET')
    patch_path = root / 'tools.patch.yml'; patch_path.write_text(patch)
    env = {k: os.environ[k] for k in ('RETRIEVAL_MODEL_KEY', 'RETRIEVAL_MODEL_URL', 'RETRIEVAL_MODEL_ID')}
    env.update(RETRIEVAL_MAX_TOKENS=os.environ.get('RETRIEVAL_MAX_TOKENS', '8000'),
               RETRIEVAL_REASONING_EFFORT=os.environ.get('RETRIEVAL_REASONING_EFFORT', ''),
               RETRIEVAL_PYTHON=sys.executable, HARNESS_BROKER_SOCKET=str(socket_path),
               PYTHONPATH=os.environ['PYTHONPATH'], DSH_SYSTEM_PROMPT=invocation['system_prompt'])
    model_gateway = None
    if invocation.get('model_budget_required'):
        from .model_gateway import ModelGateway
        def admit(sequence):
            response = parent('model_admit', sequence=sequence)
            if response.get('value') != {'admitted': True}:
                raise ValueError('model admission rejected')
        model_gateway = ModelGateway(env['RETRIEVAL_MODEL_URL'], invocation['deadline'], admit)
        model_gateway.start()
        env['RETRIEVAL_MODEL_URL'] = model_gateway.url
    options = dict(provider='retrieval-gateway', model=env['RETRIEVAL_MODEL_ID'],
        max_tokens=int(env['RETRIEVAL_MAX_TOKENS']), profile='sdk-minimal',
        cwd=str(root), dsh_home=str(root / 'home'), patches=(str(patch_path),), env=env,
        initialize_timeout_seconds=25, request_timeout_seconds=90)
    if os.environ.get('HARNESS_DSH_BIN'):
        options['dsh_bin'] = os.environ['HARNESS_DSH_BIN']
    if api == 'config-v1':
        from deepseek_harness import DeepSeekHarnessConfig
        harness_client = DeepSeekHarness(DeepSeekHarnessConfig(**options))
    else:
        harness_client = DeepSeekHarness(**options)
    class OutputReady(Exception): pass
    calls = {}
    def on_notification(notification):
        if notification.method != 'session.event': return
        event = notification.payload.get('event', {})
        data = event.get('data', {})
        if event.get('type') == 'tool/call': calls[data.get('callId')] = data.get('name')
        elif event.get('type') == 'tool/result':
            message = data.get('message', {})
            name = calls.get(message.get('toolCallId'), '')
            if (not data.get('error') and not message.get('isError')
                    and name in ('mcp__business__submit_retrieval_strategy',
                                 'mcp__business__submit_processing_plan', 'mcp__business__submit_stage_report',
                                 'mcp__business__submit_coordination_plan')):
                raise OutputReady()
    try:
        with harness_client as harness:
            options = {'on_notification': on_notification} if invocation.get('model_budget_required') else {}
            try:
                result = harness.run(json.dumps({'task_kind': invocation['task_kind'],
                                    'context': invocation['context']}, ensure_ascii=False),
                                    session_id=invocation['attempt_id'], **options)
                if result.finish_reason != 'completed': raise ValueError('SDK incomplete')
            except OutputReady:
                pass  # Parent's durable tool receipt is authoritative, not final SDK prose.
        frame({'protocol': PROTOCOL, 'type': 'done'})
    finally:
        stopped.set(); server.close()
        if model_gateway is not None: model_gateway.close()


def main():
    os.umask(0o077)
    if sys.argv[1:] == ['--mcp']: bridge()
    elif sys.argv[1:] == ['--probe']: frame(versions())
    else: run()


if __name__ == '__main__':
    try: main()
    except Exception:
        # Never write raw SDK exceptions or provider bodies to the protocol/log.
        raise SystemExit(1) from None
