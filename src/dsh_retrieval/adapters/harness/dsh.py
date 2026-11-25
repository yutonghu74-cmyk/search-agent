"""Explicit versioned DSH driver. Old DshModelRuntime/binding remain untouched."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from dataclasses import asdict
from ...harness.contracts import PROTOCOL, ProbeResult
from .dsh_releases import DshSdkRelease
from .process import ProcessHarnessAdapter

class DshAdapter(ProcessHarnessAdapter):
    def __init__(self, *, python, home, model_environment, release: DshSdkRelease, adapter_id, dsh_bin=None, node=None, npm_lock=None):
        self.release = release
        self.dsh_bin = Path(dsh_bin).absolute() if dsh_bin else None
        self.node = Path(node).absolute() if node else None
        self.npm_lock = Path(npm_lock).absolute() if npm_lock else None
        if release.runtime_mode == 'npm' and not all((self.dsh_bin, self.node, self.npm_lock)):
            raise ValueError('npm release requires explicit CLI, Node and lockfile')
        if release.runtime_mode == 'wheel' and any((self.dsh_bin, self.node, self.npm_lock)):
            raise ValueError('wheel release cannot silently override its runtime')
        self.python = str(Path(python).absolute())
        self.home = Path(home).absolute()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.model_environment = model_environment
        self.source = str(Path(__file__).resolve().parents[3])
        from ...query_spec import digest
        files = [Path(__file__), Path(__file__).with_name('dsh_worker.py'), Path(__file__).with_name('model_gateway.py'), Path(__file__).with_name('dsh_releases.py'),
                 Path(__file__).with_name('process.py'), Path(__file__).with_name('local.py'),
                 Path(__file__).parents[1] / 'dsh' / 'retrieval.patch.yml']
        if release.runtime_mode == 'npm':
            files += [self.dsh_bin, self.node, self.npm_lock]
        artifact = digest({'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                           'sdk_release': asdict(release)})
        super().__init__(adapter_id, (self.python, '-m', 'dsh_retrieval.adapters.harness.dsh_worker'),
                         version=release.native_version or release.sdk_version, artifact_digest=artifact)

    def describe(self):
        from dataclasses import replace
        result = super().describe()
        return replace(result, capabilities=result.capabilities | {'model_call_admission'})

    def _environment(self):
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TMPDIR') if k in os.environ} | {
            'PYTHONPATH': self.source, 'HARNESS_DSH_SDK_VERSION': self.release.sdk_version,
            'HARNESS_DSH_RUNTIME_VERSION': self.release.runtime_version, 'HARNESS_DSH_API': self.release.sdk_api}
        if self.release.runtime_mode == 'npm':
            env['HARNESS_DSH_BIN'] = str(self.dsh_bin)
            env['PATH'] = str(self.node.parent) + os.pathsep + env.get('PATH', '')
        return env

    def probe(self):
        try:
            result = subprocess.run((*self.command, '--probe'), capture_output=True, timeout=10,
                                    env=self._environment())
            value = json.loads(result.stdout)
            if result.returncode or value != {'sdk': self.release.sdk_version, 'runtime': self.release.runtime_version}:
                raise ValueError()
            if self.release.runtime_mode == 'npm':
                package = json.loads((self.dsh_bin.parents[1] / 'package.json').read_text())
                if package.get('name') != '@deepseek-ai/dsh' or package.get('version') != self.release.native_version:
                    raise ValueError('native package version mismatch')
                with tempfile.TemporaryDirectory(prefix='probe-', dir=self.home) as root:
                    cli = subprocess.run((str(self.dsh_bin), '--version'), capture_output=True, timeout=15,
                        env=self._environment() | {'DSH_HOME': root}, cwd=root)
                if cli.returncode or cli.stdout.decode().strip() != self.release.native_version:
                    raise ValueError('native CLI version mismatch')
            # This is a planning transport, not proof of production isolation or hard model budgets.
            from dataclasses import replace
            return replace(self.describe(), version=self.release.native_version or self.release.sdk_version)
        except Exception:
            return ProbeResult(self.adapter_id, PROTOCOL, self.release.native_version or self.release.sdk_version, frozenset(), False,
                               'DSH_RELEASE_UNAVAILABLE')

    def _drive(self, invocation, tools, stopped):
        # Each attempt gets an isolated home. Provider credentials are not in Invocation or logs.
        model = self.model_environment(invocation.model_profile_ref)
        allowed = {'RETRIEVAL_MODEL_KEY', 'RETRIEVAL_MODEL_URL', 'RETRIEVAL_MODEL_ID',
                   'RETRIEVAL_MAX_TOKENS', 'RETRIEVAL_REASONING_EFFORT'}
        if type(model) is not dict or set(model) - allowed or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in model.items()):
            raise ValueError('invalid backend model environment')
        with tempfile.TemporaryDirectory(prefix='attempt-', dir=self.home) as root:
            env = self._environment() | model | {'DSH_HOME': str(Path(root) / 'home'),
                                                'HARNESS_ATTEMPT_DIR': root}
            # Per-attempt driver avoids shared mutable credentials/environment on concurrent runs.
            driver = ProcessHarnessAdapter(self.adapter_id, self.command, version=self.version, artifact_digest=self.artifact_digest,
                                           environment=lambda: env, cwd=root)
            driver._drive(invocation, tools, stopped)
