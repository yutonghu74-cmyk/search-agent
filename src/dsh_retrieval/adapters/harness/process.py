"""Language-neutral, bounded JSON-lines worker transport (harness-worker@1).

Commands are installed backend registrations, never model/config module paths.
No worker output or SDK text is treated as an approved business result.
"""
import json
import os
import selectors
import signal
import shutil
import subprocess
import time

from ...errors import ServiceError
from .local import LocalHarnessAdapter

WORKER_PROTOCOL = 'harness-worker@1'
MAX_LINE = 500000


class ProcessHarnessAdapter(LocalHarnessAdapter):
    def __init__(self, adapter_id, command, *, version, artifact_digest, environment=None, cwd=None):
        self.command, self.environment, self.cwd = tuple(command), environment, cwd
        super().__init__(adapter_id, self._drive, version=version, artifact_digest=artifact_digest)

    def probe(self):
        from dataclasses import replace
        if not self.command or shutil.which(self.command[0]) is None:
            return replace(self.describe(), available=False, reason_code='HARNESS_EXECUTABLE_UNAVAILABLE')
        return self.describe()

    def _drive(self, invocation, tools, stopped):
        # Environment provider is backend-owned and resolved at launch; never logged.
        env = dict(self.environment() if self.environment is not None else {})
        initial = {'protocol': WORKER_PROTOCOL, 'invocation': {
            'run_id': invocation.run_id, 'attempt_id': invocation.attempt_id,
            'role': invocation.role.role, 'system_prompt': invocation.role.system_prompt,
            'task_kind': invocation.task_kind, 'context': json.loads(invocation.context_json),
            'model_profile_ref': invocation.model_profile_ref,
            'deadline': invocation.deadline, 'tools': tools.list_tools(),
            'model_budget_required': getattr(tools, 'model_budget_enabled', False)}}
        output = bytearray((json.dumps(initial, ensure_ascii=False, allow_nan=False) + '\n').encode())
        if len(output) > MAX_LINE:
            raise ServiceError('HARNESS_INPUT_LIMIT', 'Worker input too large', 422)
        proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, cwd=self.cwd, start_new_session=True)
        selector = selectors.DefaultSelector()
        os.set_blocking(proc.stdout.fileno(), False)
        os.set_blocking(proc.stdin.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        incoming = bytearray()
        writer, done = False, False
        try:
            while True:
                if stopped.is_set() or time.time() >= invocation.deadline:
                    raise ServiceError('HARNESS_RUN_STOPPED', 'Worker stopped or deadline reached', 409)
                if output and not writer:
                    selector.register(proc.stdin, selectors.EVENT_WRITE); writer = True
                if not output and writer:
                    selector.unregister(proc.stdin); writer = False
                for key, mask in selector.select(.05):
                    if key.fileobj is proc.stdin:
                        try: count = os.write(proc.stdin.fileno(), output[:65536])
                        except BlockingIOError: continue
                        del output[:count]
                    else:
                        try: data = os.read(proc.stdout.fileno(), 65536)
                        except BlockingIOError: continue
                        if not data:
                            if proc.poll() is None: continue
                            if incoming or proc.returncode != 0 or not done:
                                raise ServiceError('HARNESS_WORKER_FAILED', 'Worker did not finish protocol', 502)
                            return
                        incoming.extend(data)
                        if len(incoming) > MAX_LINE:
                            raise ServiceError('HARNESS_OUTPUT_LIMIT', 'Worker frame too large', 502)
                        while b'\n' in incoming:
                            raw, _, rest = incoming.partition(b'\n'); incoming = bytearray(rest)
                            message = json.loads(raw)
                            if type(message) is not dict or message.get('protocol') != WORKER_PROTOCOL:
                                raise ValueError('worker protocol mismatch')
                            kind = message.get('type')
                            if kind == 'model_admit' and not done:
                                if set(message) != {'protocol', 'type', 'id', 'sequence'} or type(message['id']) is not int:
                                    raise ValueError('model admission fields')
                                try: result = {'value': tools.admit_model_call(message['sequence'])}
                                except ServiceError as error: result = {'error_code': error.code}
                                output.extend((json.dumps({'protocol': WORKER_PROTOCOL, 'type': 'tool_result',
                                    'id': message['id'], **result}) + '\n').encode())
                            elif kind == 'tool' and not done:
                                if set(message) != {'protocol', 'type', 'id', 'name', 'arguments'}:
                                    raise ValueError('worker tool fields')
                                if type(message['id']) is not int or message['id'] < 0:
                                    raise ValueError('worker call id')
                                try:
                                    result = {'value': tools.call(message['name'], message['arguments'])}
                                except ServiceError as error:
                                    result = {'error_code': error.code}
                                output.extend((json.dumps({'protocol': WORKER_PROTOCOL,
                                    'type': 'tool_result', 'id': message['id'], **result},
                                    ensure_ascii=False, allow_nan=False) + '\n').encode())
                                if len(output) > MAX_LINE: raise ValueError('worker backpressure limit')
                            elif kind == 'done' and set(message) == {'protocol', 'type'} and not done:
                                done = True
                            else:
                                raise ValueError('unknown worker frame')
        finally:
            selector.close()
            self._stop(proc)
            proc.stdin.close(); proc.stdout.close()

    @staticmethod
    def _stop(proc):
        # Fence tools separately; termination acknowledgement is not inferred from this signal.
        try: os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: proc.wait(timeout=.3)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait(timeout=2)
