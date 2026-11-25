"""SDK test double: exercise real MCP subprocess/socket wiring, no model/network."""
from types import SimpleNamespace
import json
import os
import subprocess
import sys

class DeepSeekHarnessConfig:
    def __init__(self, **kwargs): self.options = kwargs

class DeepSeekHarness:
    def __init__(self, config=None, **kwargs):
        if os.environ['HARNESS_DSH_API'] == 'keywords-v2':
            assert config is None and kwargs
            self.config = kwargs
        else:
            assert config is not None and not kwargs
            self.config = config.options
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def run(self, prompt, session_id):
        patch = open(self.config['patches'][0]).read()
        assert 'serverName: business' in patch and 'dsh_retrieval.adapters.dsh.tools_server' not in patch
        env = dict(os.environ); env.update(self.config['env'])
        child = subprocess.Popen([sys.executable, '-m', 'dsh_retrieval.adapters.harness.dsh_worker', '--mcp'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        def rpc(call_id, method, params=None):
            child.stdin.write(json.dumps({'id': call_id, 'method': method, 'params': params or {}}) + '\n')
            child.stdin.flush()
            return json.loads(child.stdout.readline())
        try:
            rpc(1, 'initialize')
            tools = rpc(2, 'tools/list')['result']['tools']
            assert {t['name'] for t in tools} == {'get_stage_context', 'submit_retrieval_strategy', 'submit_stage_report'}
            rpc(3, 'tools/call', {'name': 'get_stage_context', 'arguments': {}})
            result = rpc(4, 'tools/call', {'name': 'submit_retrieval_strategy', 'arguments': {'proposal': {
                'action': 'rewrite_query', 'binding_id': 'binding', 'method': 'vector',
                'query': '客厅', 'evidence_refs': ['facts-1'], 'reason': '提高相关性'}}})
            assert not result['result'].get('isError'), result
            return SimpleNamespace(finish_reason='completed')
        finally:
            child.stdin.close(); child.wait(timeout=3); child.stdout.close()
