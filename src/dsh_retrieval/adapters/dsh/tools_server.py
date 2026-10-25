"""Private stdio MCP proposal tools. No task mutation, filesystem or network tools.

The launch path is supplied by the backend, never by model arguments. Each process
can read exactly its turn context and deposit one provisional response. Business
validation and confirmation happen in the caller, not here.
"""
import json
from pathlib import Path
import sys

TOOLS = [
    {'name': 'get_context', 'description': 'Read the current authorized conversation context. No task has been executed by this tool.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'submit_proposal', 'description': 'Submit the JSON response described in the system instructions for backend validation. This NEVER approves or executes a task. Call once after reading context.',
     'inputSchema': {'type': 'object', 'properties': {'proposal': {'type': 'object'}},
                     'required': ['proposal'], 'additionalProperties': False}},
]


class ProposalTools:
    def __init__(self, turn_dir):
        self.root = Path(turn_dir)
        self.context_read = False

    def call(self, name, arguments):
        if name == 'get_context' and arguments == {}:
            self.context_read = True
            return json.loads((self.root / 'context.json').read_text())
        if (name != 'submit_proposal' or not self.context_read
                or not isinstance(arguments, dict) or set(arguments) != {'proposal'}
                or not isinstance(arguments['proposal'], dict)):
            raise ValueError('Unknown tool, invalid arguments, or context not read')
        encoded = json.dumps(arguments['proposal'], ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 100_000:
            raise ValueError('Proposal too large')
        path = self.root / 'proposal.json'
        # One immutable proposal per isolated turn. Repeated tool calls cannot
        # override an earlier submission or write outside this directory.
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(encoded)
        except FileExistsError:
            if path.read_text() != encoded:
                raise ValueError('Proposal already submitted') from None
        return {'status': 'pending_backend_validation', 'executed': False}


def serve(root, source, sink):
    tools = ProposalTools(root)
    for line in source:
        if len(line.encode()) > 200_000:
            break
        try:
            request = json.loads(line)
            if 'id' not in request:
                continue
            method = request.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'retrieval-proposals', 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                params = request.get('params', {})
                try:
                    value = tools.call(params.get('name'), params.get('arguments', {}))
                    result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
                except (ValueError, OSError):
                    result = {'isError': True, 'content': [{'type': 'text', 'text': 'Proposal tool rejected the request'}]}
            else:
                sink.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                                       'error': {'code': -32601, 'message': 'Method unavailable'}}) + '\n')
                sink.flush()
                continue
            sink.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}, ensure_ascii=False) + '\n')
            sink.flush()
        except (ValueError, TypeError, KeyError):
            break


if __name__ == '__main__':
    serve(sys.argv[1], sys.stdin, sys.stdout)
