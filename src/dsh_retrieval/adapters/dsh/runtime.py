"""DSH-backed reasoning; domain validation remains in ModelRuntime/business code."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import uuid

from ...errors import ServiceError
from ...intent_model import IntentModelClient, SYSTEM_PROMPT
from ...model_runtime import ModelRuntime
from ...query_spec import digest
from .runs import RunIndex

_scope = ContextVar('dsh_request_scope', default=None)


class DshModelRuntime(ModelRuntime):
    def __init__(self, profiles=(), *, python, home, timeout=120, opener=None):
        super().__init__(profiles, opener=opener)
        self.python = str(Path(python).absolute())
        self.home = Path(home).absolute()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not 1 <= timeout <= 300:
            raise ValueError('DSH timeout must be 1–300 seconds')
        self.timeout = timeout
        self.runs = RunIndex(self.home / 'runs.sqlite3')

    def recover(self):
        return self.runs.recover(self.home)

    def agent_runs(self, tenant_id, task_id):
        return self.runs.for_task(tenant_id, task_id)

    def status(self):
        return {'engine': 'dsh', 'version': '0.2.1rc2', 'profile': 'sdk-minimal',
                'tools': ['mcp__retrieval__get_context', 'mcp__retrieval__submit_proposal'],
                'approval_authority': 'business_service', 'vlm': 'direct_operator'}

    def binding(self, llm_id, vlm_id):
        from .worker import SKILL, VERSION
        value = super().binding(llm_id, vlm_id)
        value['config_digest'] = digest({'models': value['config_digest'], 'dsh': VERSION,
            'skill': hashlib.sha256(SKILL.encode()).hexdigest(),
            'patch': hashlib.sha256(Path(__file__).with_name('retrieval.patch.yml').read_bytes()).hexdigest()})
        return value

    @contextmanager
    def business_scope(self, **binding):
        token = _scope.set((_scope.get() or {'tenant_id': 'internal', 'user_id': 'internal'}) | binding)
        try:
            yield
        finally:
            _scope.reset(token)

    @contextmanager
    def request_scope(self, tenant_id, user_id, request_id, route):
        # Identity comes from the handler, not model-produced tool arguments.
        token = _scope.set({'tenant_id': tenant_id, 'user_id': user_id,
                            'request_id': request_id, 'route': route})
        try:
            yield
        finally:
            _scope.reset(token)

    def _environment(self):
        # SDK inherits its parent's environment. The dedicated worker receives
        # neither unrelated credentials nor the developer's DSH home/config.
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[3])
        return env

    def check_installation(self):
        try:
            result = subprocess.run([self.python, '-m', 'dsh_retrieval.adapters.dsh.worker', '--check'],
                                    env=self._environment(), capture_output=True, timeout=10)
            if result.returncode or json.loads(result.stdout) != {'version': '0.2.1rc2'}:
                raise ValueError()
        except Exception:
            raise ServiceError('DSH_NOT_AVAILABLE', 'Pinned DSH SDK/runtime 0.2.1rc2 required', 503) from None

    @staticmethod
    def _terminate(proc):
        # Kill the entire worker/runtime/MCP tree, not just the Python parent.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    def _request(self, profile, system, content, *, conversation=None):
        if profile.kind == 'vlm':
            # Image scoring is an operator invocation, not an Agent loop.
            return super()._request(profile, system, content, conversation=conversation)
        run_id = 'retrieval-' + uuid.uuid4().hex
        scope = _scope.get() or {'tenant_id': 'internal', 'user_id': 'internal'}
        principal = hashlib.sha256(json.dumps([scope['tenant_id'], scope['user_id']]).encode()).hexdigest()
        root = self.home / principal / run_id
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root.parent, 0o700)
        context = {'messages': (conversation or []) + [{'role': 'user', 'content': content}]}
        request_timeout = profile.config.request_timeout_seconds or 90
        request = {'run_id': run_id, 'turn_dir': str(root), 'model': profile.config.model,
                   'base_url': profile.config.base_url, 'api_key': profile.config.api_key,
                   'max_tokens': profile.config.max_tokens or 8000,
                   'request_timeout_seconds': request_timeout,
                   'system': system, 'context': context}
        metadata = {'run_id': run_id, 'principal_digest': principal, 'engine': 'dsh',
                    'runtime_version': '0.2.1rc2', 'status': 'running',
                    'request_id': scope.get('request_id'), 'route': scope.get('route')}
        metadata['business_binding'] = {key: scope[key] for key in
            ('project_id', 'dialogue_session_id', 'expected_version', 'task_id', 'task_version',
             'revision', 'generation') if key in scope}
        audit = root / 'audit.json'
        audit.write_text(json.dumps(metadata))
        self.runs.start(run_id, principal=principal, tenant=scope['tenant_id'], scope=scope,
                        model_profile=profile.profile_id)
        proc = None
        try:
            payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
            if len(payload) > 500_000:
                raise ServiceError('MODEL_INPUT_LIMIT', 'Agent context exceeds limit', 422)
            env = self._environment() | {'DSH_HOME': str(root / 'home')}
            proc = subprocess.Popen([self.python, '-m', 'dsh_retrieval.adapters.dsh.worker'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=root, env=env, start_new_session=True)
            self.runs.attach_process(run_id, proc.pid)
            # A model configured with a slower request budget extends, never shortens, the turn budget.
            budget = self.timeout
            if profile.config.request_timeout_seconds:
                budget = max(budget, min(300, request_timeout + 30))
            stdout, _ = proc.communicate(payload, timeout=budget)
            if len(stdout) > 200_000:
                raise ValueError()
            result = json.loads(stdout)
            errors = {
                'RATE_LIMIT': ('MODEL_RATE_LIMIT', '模型服务限流，请稍后重试；原需求未改变。', 429, True),
                'QUOTA': ('MODEL_QUOTA', '模型服务额度不足，请检查服务端配置。', 503, False),
                'AUTH': ('MODEL_AUTH_FAILED', '模型凭据不可用，请检查服务端配置。', 503, False),
                'TIMEOUT': ('DSH_TIMEOUT', '模型响应超时，原需求未改变，请重试。', 504, True),
                'TRANSPORT': ('MODEL_UNAVAILABLE', '模型服务暂时不可用，请稍后重试。', 503, True),
                'INVALID_REQUEST': ('MODEL_REQUEST_FAILED', '模型服务拒绝此请求，请检查协议配置。', 502, False),
            }
            if result.get('error') in errors:
                raise ServiceError(*errors[result['error']])
            if proc.returncode:
                raise ValueError()
            if (result.get('session_id') != run_id or result.get('runtime_version') != '0.2.1rc2'
                    or not isinstance(result.get('proposal'), dict)):
                raise ValueError()
            metadata.update(status='proposal_received', session_id=result['session_id'],
                            finish_reason=result['finish_reason'], event_types=result['event_types'])
            return result['proposal']
        except subprocess.TimeoutExpired:
            metadata['status'] = 'timeout'
            raise ServiceError('DSH_TIMEOUT', 'Agent timed out; no proposal accepted, retry permitted', 504, True) from None
        except ServiceError as error:
            metadata['status'] = 'rejected'
            metadata['error_code'] = error.code
            raise
        except Exception:
            metadata['status'] = 'failed'
            raise ServiceError('DSH_TURN_FAILED', 'DSH reasoning failed; no direct-model fallback or task execution', 502) from None
        finally:
            if proc is not None:
                self._terminate(proc)
            audit.write_text(json.dumps(metadata))
            self.runs.finish(run_id, metadata['status'], metadata.get('error_code'))

    def analyze_request(self, raw_request, llm_id):
        profile = self.select(llm_id, 'llm')
        client = IntentModelClient(profile.config)
        client.reviewer.review(raw_request)
        parsed = self._request(profile, SYSTEM_PROMPT,
                               json.dumps({'raw_request': raw_request}, ensure_ascii=False))
        try:
            proposal = client._ground(raw_request, parsed)
        except ServiceError as error:
            if error.code not in ('MODEL_GROUNDING_FAILED', 'MODEL_MEDIA_INVALID'):
                raise
            return client._safe_clarification(raw_request)
        report = client.reviewer.review(raw_request, proposal)
        return {'mode': 'LIVE_MODEL_INTERPRETATION', 'provider': profile.config.provider,
                'model': profile.config.model, 'interpretation': proposal, 'review': report}
