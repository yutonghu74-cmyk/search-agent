"""Dependency-free Python client for shared modules and explicit owner actions.

Integrations never receive implicit confirm methods or automatic POST retries.
Credentials are resolved per request and are never placed in URLs or errors.
"""
import json
import math
from urllib.parse import urlsplit, quote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


class SharedApiError(Exception):
    def __init__(self, code, status=0, retryable=False):
        super().__init__(code)
        self.code, self.status, self.retryable = code, status, retryable


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args): return None


class SharedTransport:
    def __init__(self, base_url, *, project_id, credential, timeout=15, allow_local_http=False):
        parts=urlsplit(base_url)
        local=allow_local_http and parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1')
        if (not parts.hostname or parts.scheme!='https' and not local or parts.username or parts.password
                or parts.query or parts.fragment or any(c.isspace() for c in base_url)
                or not callable(credential) or not isinstance(project_id,str) or not project_id
                or any(c.isspace() for c in project_id)
                or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=120):
            raise ValueError('trusted HTTPS API, project and credential resolver required')
        self.base_url,self.project_id,self.credential,self.timeout=base_url.rstrip('/'),project_id,credential,timeout
        self._open=build_opener(_NoRedirect()).open

    def request(self, path, *, body=None, key=None, external_ref=None):
        if not path.startswith('/v1/') or '\\' in path or '#' in path or '..' in path:
            raise ValueError('business API path required')
        token=self.credential()
        if not isinstance(token,str) or not token or any(c.isspace() for c in token):
            raise ValueError('credential resolver must return one bearer token')
        headers={'Authorization':'Bearer '+token,'x-app-id':self.project_id,'Accept':'application/json'}
        if key is not None:
            if not isinstance(key,str) or not 1<=len(key)<=128 or any(ord(c)<33 or ord(c)>126 for c in key):
                raise ValueError('bounded idempotency key required')
            headers['Idempotency-Key']=key
        if external_ref is not None:
            from .shared_access import external_ref as valid_ref
            if not valid_ref(external_ref): raise ValueError('invalid external reference')
            headers['X-External-Task-Ref']=external_ref
        data=None
        if body is not None:
            if type(body) is not dict: raise ValueError('JSON object required')
            data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            if len(data)>100000: raise ValueError('request too large')
            headers['Content-Type']='application/json'
        request=Request(self.base_url+path,headers=headers,data=data,method='GET' if body is None else 'POST')
        try:
            with self._open(request,timeout=self.timeout) as response:
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise SharedApiError('RESPONSE_TOO_LARGE')
                value=json.loads(raw)
                if type(value) is not dict: raise SharedApiError('INVALID_RESPONSE')
                return value
        except HTTPError as error:
            status=error.code
            with error:
                try:value=json.loads(error.read(65537))
                except (ValueError,UnicodeError):value={}
            code=value.get('code','HTTP_ERROR') if type(value) is dict else 'HTTP_ERROR'
            if not isinstance(code,str) or not code.replace('_','').isalnum() or len(code)>100:code='HTTP_ERROR'
            raise SharedApiError(code,status,value.get('retryable') is True if type(value) is dict else False) from None
        except (URLError,TimeoutError,OSError):
            # The server may have accepted a write. Query before explicit retry.
            raise SharedApiError('TRANSPORT_OUTCOME_UNKNOWN',retryable=True) from None
        except (ValueError,UnicodeError):
            raise SharedApiError('INVALID_RESPONSE') from None


def _task(task_id): return '/v1/retrieval/tasks/'+quote(task_id,safe='')


class AgentClient:
    def __init__(self, transport, *, external_task_ref):
        self.transport,self.external_task_ref=transport,external_task_ref

    def capabilities(self): return self.transport.request('/v1/shared/capabilities')
    def bind(self, task_id, *, revision, generation, task_version, key):
        return self.transport.request('/v1/shared/tasks/'+quote(task_id,safe='')+'/binding',key=key,body={
            'external_task_ref':self.external_task_ref,'expected_revision':revision,
            'expected_generation':generation,'expected_task_version':task_version})
    def binding(self, task_id):
        return self.transport.request('/v1/shared/tasks/'+quote(task_id,safe='')+'/binding',external_ref=self.external_task_ref)
    def receipt(self, task_id, *, operation, key):
        return self.transport.request('/v1/shared/tasks/'+quote(task_id,safe='')+'/receipt?'+urlencode({
            'operation':operation,'key':key}),external_ref=self.external_task_ref)
    def _get(self, task_id, suffix):
        return self.transport.request(_task(task_id)+suffix,external_ref=self.external_task_ref)
    def _post(self, task_id, suffix, body, key):
        return self.transport.request(_task(task_id)+suffix,body=body,key=key,external_ref=self.external_task_ref)
    def task(self, task_id): return self._get(task_id,'')
    def retrieval_preview(self, task_id): return self._get(task_id,'/first-search')
    def prepare_retrieval(self, task_id, *, routing_digest, choices, key):
        return self._post(task_id,'/first-search/prepare',{'routing_digest':routing_digest,'choices':choices},key)
    def retrieval_status(self, task_id): return self._get(task_id,'/first-search/progress')
    def candidates(self, task_id, *, limit=100, cursor=None):
        values={'limit':limit}
        if cursor is not None:values['cursor']=cursor
        return self._get(task_id,'/first-search/candidates?'+urlencode(values))
    def candidate_manifest(self, task_id): return self._get(task_id,'/first-search/manifest')
    def prepare_terms(self, task_id, body, *, key):
        """Generate a provisional term draft for the bound Task; never confirm it."""
        return self._post(task_id, '/retrieval-agent/terms', body, key)
    def recall(self, task_id):
        return self._get(task_id, '/retrieval-agent/recall')
    def terms(self, task_id, job_id=None):
        suffix = '/retrieval-agent/terms' + ('/' + quote(job_id, safe='') if job_id else '')
        return self._get(task_id, suffix)
    def retry_terms(self, task_id, job_id, *, key):
        return self._post(task_id, '/retrieval-agent/terms/' + quote(job_id, safe='') + '/retry', {}, key)
    def prepare_cleaning(self, task_id, manifest_ref, *, key):
        return self._post(task_id,'/cleaning/prepare',{'manifest_ref':manifest_ref},key)
    def execute_cleaning(self, task_id, operation_id, *, key):
        return self._post(task_id,'/cleaning/execute',{'operation_id':operation_id},key)
    def cleaning_status(self, task_id, operation_id):
        return self._get(task_id,'/cleaning/status?'+urlencode({'operation_id':operation_id}))
    def results(self, task_id, *, limit=20, cursor=None):
        values={'limit':limit}
        if cursor is not None:values['cursor']=cursor
        return self._get(task_id,'/results?'+urlencode(values))
    def cancel(self, task_id, *, task_version, key):
        return self.transport.request('/v1/shared/tasks/'+quote(task_id,safe='')+'/cancel',
            body={'action':'cancel','expected_task_version':task_version},key=key,external_ref=self.external_task_ref)


class OwnerClient:
    """Use only in the owner's explicit approval flow, not an Agent tool list."""
    def __init__(self, transport): self.transport=transport
    def save_draft(self, proposal, *, key):
        return self.transport.request('/v1/retrieval/projects/'+quote(self.transport.project_id,safe='')+'/drafts',body=proposal,key=key)
    def confirm_task(self, review, *, key):
        return self.transport.request('/v1/retrieval/tasks',body={'draft_id':review['draft_id'],
            'expected_draft_version':review['draft_version'],'proposal_digest':review['proposal_digest'],'action':'confirm'},key=key)
    def confirm(self, task_id, review, *, stage, key):
        paths={'retrieval':'/first-search/confirm','manifest':'/first-search/manifest','cleaning':'/cleaning/confirm'}
        if stage not in paths:raise ValueError('explicit approval stage required')
        return self.transport.request(_task(task_id)+paths[stage],body={'proposal_digest':review['proposal_digest'],
            'expected_task_version':review['expected_task_version'],'action':'confirm'},key=key)

    def confirm_job(self, task_id, job_ref, confirmation, *, key):
        return self.transport.request(_task(task_id)+'/agent-jobs/'+quote(job_ref,safe='')+'/confirm', key=key,
            body={'action':'confirm','checkpoint':confirmation['checkpoint'],
                  'proposal_digest':confirmation['proposal_digest'],
                  'expected_task_version':confirmation['expected_task_version']})


class AgentJobClient:
    """Unified retrieval jobs for delegated tools; owner confirmations stay separate."""
    def __init__(self, transport, *, external_task_ref):
        self.transport, self.external_task_ref = transport, external_task_ref

    def _path(self, task_id, job_ref=None, suffix=''):
        return _task(task_id)+'/agent-jobs'+('/'+quote(job_ref,safe='') if job_ref else '')+suffix

    def prepare(self, task_id, planning, *, key, workflow_ref=None):
        body = {'capability':'retrieval','planning':planning}
        if workflow_ref is not None: body['workflow_ref'] = workflow_ref
        return self.transport.request(self._path(task_id, 'prepare'), key=key, external_ref=self.external_task_ref,
            body=body)

    def list(self, task_id):
        return self.transport.request(self._path(task_id), external_ref=self.external_task_ref)

    def get(self, task_id, job_ref):
        return self.transport.request(self._path(task_id, job_ref), external_ref=self.external_task_ref)

    def artifacts(self, task_id, job_ref):
        return self.transport.request(self._path(task_id, job_ref, '/artifacts'), external_ref=self.external_task_ref)

    def start(self, task_id, job_ref, planning_review, *, key):
        return self.transport.request(self._path(task_id, job_ref, '/start'), key=key, external_ref=self.external_task_ref,
            body={'action':'start','proposal_digest':planning_review['proposal_digest'],
                  'expected_task_version':planning_review['expected_task_version']})

    def cancel(self, task_id, job_ref, *, task_version, key):
        return self.transport.request(self._path(task_id, job_ref, '/cancel'), key=key, external_ref=self.external_task_ref,
            body={'action':'cancel','expected_task_version':task_version})
