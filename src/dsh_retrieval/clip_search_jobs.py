"""Persistent bounded execution for combined CLIP text-search requests.

This ledger is intentionally separate from the existing vector-page ledger:
combined requests carry query text instead of a fabricated QueryVector. Jobs are
immutable per task scope; superseded jobs remain audit records and never become
the current task's results.
"""

from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .adapters.retrieval.clip_search import (
    CLIPMatch, CLIPSearchBatch, CLIPTextSearchRequest,
)
from .errors import ServiceError
from .integration_models import AccessContext, IndexBinding, SearchHit
from .models import Actor
from .query_spec import digest
from .retrieval_contracts import RetrievalScope
from .candidate_browsing import create_projection, append_hit


MAX_PAYLOAD = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CLIPJobMember:
    branch_id: str
    request: CLIPTextSearchRequest
    shard_group: tuple[str, ...] = ()
    declared_shards: tuple[str, ...] = ()
    windowed: bool = False


@dataclass(frozen=True, slots=True)
class CLIPSearchJob:
    job_id: str
    scope: RetrievalScope
    authorization_version: str
    members: tuple[CLIPJobMember, ...]
    status: str
    created_at: str
    updated_at: str


def _request_to_dict(request):
    return asdict(request)


def _request_from_dict(value):
    data = dict(value)
    data["binding"] = IndexBinding(**data["binding"])
    return CLIPTextSearchRequest(**data)


def _match_from_dict(value):
    data = dict(value)
    hit = dict(data.pop("hit"))
    hit["binding"] = IndexBinding(**hit["binding"])
    return CLIPMatch(SearchHit(**hit), **data)


class SQLiteCLIPSearchJobs:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS clip_search_jobs(
                    job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, generation INTEGER NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL)""")
                db.execute("CREATE INDEX IF NOT EXISTS clip_search_task ON clip_search_jobs(task_id, updated_at)")
                create_projection(db)
                for (raw,) in db.execute('SELECT payload FROM clip_search_jobs').fetchall():
                    self._project_hits(db, self._load(raw))
        except sqlite3.Error as error:
            raise ServiceError("STORAGE_UNAVAILABLE", "CLIP search job store unavailable", 503) from error

    @staticmethod
    def _validate(scope, authorization_version, members):
        if (type(scope) is not RetrievalScope or not isinstance(authorization_version, str)
                or not authorization_version or type(members) is not tuple
                or not 1 <= len(members) <= 64):
            raise ServiceError("INVALID_RETRIEVAL_PLAN", "Invalid combined CLIP job", 422)
        plans = set()
        for member in members:
            request = member.request if type(member) is CLIPJobMember else None
            if (request is None or not isinstance(member.branch_id, str) or not member.branch_id
                    or request.plan_id in plans
                    or request.cursor is not None or request.plan_version != scope.generation
                    or request.binding.library_id == "" or not request.query_text.strip()):
                raise ServiceError("INVALID_RETRIEVAL_PLAN", "Invalid combined CLIP member", 422)
            if (bool(member.shard_group) != bool(member.declared_shards)
                    or type(member.shard_group) is not tuple or type(member.declared_shards) is not tuple
                    or len(member.shard_group) > 4
                    or any(type(x) is not str or not x for x in member.declared_shards)
                    or not set(member.shard_group) <= set(member.declared_shards)):
                raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Invalid shard coverage snapshot', 422)
            if type(member.windowed) is not bool:
                raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Invalid window policy', 422)
            plans.add(request.plan_id)

    @staticmethod
    def _initial(scope, authorization_version, members, now):
        frozen = {"scope": asdict(scope), "authorization_version": authorization_version,
                  "members": [{"branch_id": member.branch_id,
                               "request": _request_to_dict(member.request),
                               **({"shard_group": member.shard_group, "declared_shards": member.declared_shards}
                                  if member.shard_group else {}),
                               **({'windowed': True} if member.windowed else {})} for member in members]}
        job_id = "clipjob_" + digest(frozen)[7:]
        return job_id, {
            "schema": "clip-search-job@1", "job_id": job_id, **frozen,
            "status": "queued", "created_at": now, "updated_at": now,
            "state": [{"plan_id": member.request.plan_id, "cursor": None, "pages": 0,
                       "attempts": 0, "done": False, "last_error": None, "matches": []}
                      for member in members],
        }

    @staticmethod
    def _dump(payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > MAX_PAYLOAD:
            raise ServiceError("STORAGE_LIMIT", "CLIP search job exceeds local ledger limit", 507)
        return raw

    @staticmethod
    def _load(raw):
        try:
            if not isinstance(raw, str) or len(raw.encode()) > MAX_PAYLOAD:
                raise ValueError("payload size")
            value = json.loads(raw)
            if (type(value) is not dict or value.get("schema") != "clip-search-job@1"
                    or type(value.get("members")) is not list or type(value.get("state")) is not list
                    or len(value["members"]) != len(value["state"])):
                raise ValueError("payload shape")
            return value
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            raise ServiceError("STORAGE_CORRUPT", "CLIP search job is corrupt", 500) from None

    def create(self, scope, authorization_version, members):
        self._validate(scope, authorization_version, members)
        now = datetime.now(timezone.utc).isoformat()
        job_id, payload = self._initial(scope, authorization_version, members, now)
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                old = db.execute("SELECT payload FROM clip_search_jobs WHERE job_id=?", (job_id,)).fetchone()
                if old is not None:
                    if self._load(old[0]) != payload:
                        # Timestamps differ on replay, compare the immutable plan only.
                        existing = self._load(old[0])
                        for key in ("status", "created_at", "updated_at", "state"):
                            existing.pop(key, None)
                            payload.pop(key, None)
                        if existing != payload:
                            raise ServiceError("IDEMPOTENCY_CONFLICT", "CLIP job identity changed", 409)
                    return job_id, True
                db.execute("INSERT INTO clip_search_jobs VALUES(?,?,?,?,?,?,?)",
                    (job_id, scope.task_id, scope.revision, scope.generation,
                     self._dump(payload), now, now))
            return job_id, False
        except ServiceError:
            raise
        except sqlite3.Error as error:
            raise ServiceError("STORAGE_UNAVAILABLE", "CLIP search job store unavailable", 503) from error

    @staticmethod
    def _project_hits(db, payload, before=None):
        for member, state in zip(payload['members'], payload['state']):
            for page_index, page in enumerate(state['matches']):
                if before and page_index < before.get(state['plan_id'], 0):
                    continue
                for index, match in enumerate(page):
                    append_hit(db, payload['job_id'], f"{state['plan_id']}:{page_index}:{index}",
                               state['plan_id'], member['branch_id'], json.dumps(match))

    def _transaction(self, job_id, write, callback):
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT payload FROM clip_search_jobs WHERE job_id=?", (job_id,)).fetchone()
                if row is None:
                    raise ServiceError("NOT_FOUND", "CLIP search job not found", 404)
                payload = self._load(row[0])
                before = {state['plan_id']: len(state['matches']) for state in payload['state']}
                result = callback(payload)
                if write:
                    now = datetime.now(timezone.utc).isoformat()
                    payload["updated_at"] = now
                    db.execute("UPDATE clip_search_jobs SET payload=?,updated_at=? WHERE job_id=?",
                               (self._dump(payload), now, job_id))
                    self._project_hits(db, payload, before)
                return result
        except ServiceError:
            raise
        except sqlite3.Error as error:
            raise ServiceError("STORAGE_UNAVAILABLE", "CLIP search job transaction failed", 503) from error

    def job(self, job_id):
        def read(payload):
            scope = RetrievalScope(**payload["scope"])
            members = tuple(CLIPJobMember(row["branch_id"], _request_from_dict(row["request"]),
                tuple(row.get("shard_group", ())), tuple(row.get("declared_shards", ())),
                row.get("windowed", False))
                            for row in payload["members"])
            return CLIPSearchJob(payload["job_id"], scope, payload["authorization_version"], members,
                                 payload["status"], payload["created_at"], payload["updated_at"])
        return self._transaction(job_id, False, read)

    def recoverable(self):
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                rows = db.execute("SELECT job_id,payload FROM clip_search_jobs ORDER BY created_at").fetchall()
            return tuple(job_id for job_id, raw in rows
                         if self._load(raw).get("status") in ("queued", "running"))
        except sqlite3.Error as error:
            raise ServiceError("STORAGE_UNAVAILABLE", "CLIP search recovery unavailable", 503) from error

    def current_for_scope(self, scope):
        if type(scope) is not RetrievalScope:
            raise ServiceError("INVALID_RETRIEVAL_PLAN", "Task scope required", 422)
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                rows = db.execute("SELECT job_id,payload FROM clip_search_jobs WHERE task_id=? "
                                  "AND revision=? AND generation=? ORDER BY created_at DESC",
                                  (scope.task_id, scope.revision, scope.generation)).fetchall()
            matches = [(job_id, self._load(raw)) for job_id, raw in rows
                       if self._load(raw).get("scope") == asdict(scope)]
            if len(matches) > 1:
                raise ServiceError("STORAGE_CORRUPT", "Multiple current CLIP jobs", 500)
            return matches[0][0] if matches else None
        except ServiceError:
            raise
        except sqlite3.Error as error:
            raise ServiceError("STORAGE_UNAVAILABLE", "CLIP search lookup unavailable", 503) from error

    def current_for_browse(self, scope):
        """Read same-generation candidates after downstream steps advance task_version."""
        try:
            with closing(sqlite3.connect(self.path)) as db:
                rows = db.execute("SELECT job_id,payload FROM clip_search_jobs WHERE task_id=? "
                                  "AND revision=? AND generation=? ORDER BY created_at DESC",
                                  (scope.task_id, scope.revision, scope.generation)).fetchall()
            matches = [(job_id, self._load(raw)) for job_id, raw in rows
                       if all(self._load(raw)['scope'].get(name) == getattr(scope, name)
                              for name in ('tenant_id', 'owner_user_id', 'project_id', 'task_id', 'revision',
                                           'generation', 'requirement_digest'))]
            if len(matches) > 1:
                raise ServiceError("STORAGE_CORRUPT", "Multiple current CLIP jobs", 500)
            return matches[0][0] if matches else None
        except sqlite3.Error as error:
            raise ServiceError("STORAGE_UNAVAILABLE", "CLIP search lookup unavailable", 503) from error

    def next_request(self, job_id, *, plan_ids=None):
        def reserve(payload):
            if payload["status"] in ("complete", "failed", "superseded"):
                return None
            candidates = [(state["pages"], index, state) for index, state in enumerate(payload["state"])
                          if not state["done"] and state["attempts"] < 3
                          and (plan_ids is None or (payload["members"][index]["request"]["plan_id"] in plan_ids
                               and state["pages"] == 0))]
            if not candidates:
                payload["status"] = "failed"
                return None
            _, index, state = min(candidates)
            state["attempts"] += 1
            payload["status"] = "running"
            member = payload["members"][index]
            request = replace(_request_from_dict(member["request"]), cursor=state["cursor"])
            return member["branch_id"], request, state["cursor"]
        return self._transaction(job_id, True, reserve)

    def pending_requests(self, job_id):
        """Read current unissued positions without incrementing attempt counters."""
        def read(payload):
            if payload["status"] in ("complete", "failed", "superseded"):
                return ()
            result = []
            for member, state in zip(payload["members"], payload["state"]):
                if not state["done"] and state["attempts"] < 3:
                    result.append((member["branch_id"],
                                   replace(_request_from_dict(member["request"]),
                                           cursor=state["cursor"]),
                                   state["pages"]))
            return tuple(result)
        return self._transaction(job_id, False, read)

    def reserve_request(self, job_id, plan_id, expected_cursor):
        """Reserve one exact approved position selected by a refill iteration."""
        def reserve(payload):
            if payload["status"] in ("failed", "superseded"):
                raise ServiceError("PLAN_NOT_CURRENT", "CLIP refill job is not writable", 409)
            index = next((i for i, state in enumerate(payload["state"])
                          if state["plan_id"] == plan_id), None)
            if index is None:
                raise ServiceError("PLAN_REQUEST_MISMATCH", "Unknown CLIP refill member", 409)
            state = payload["state"][index]
            if state["done"]:
                return None
            if state["cursor"] != expected_cursor:
                raise ServiceError("REFILL_POSITION_CHANGED", "CLIP refill cursor changed", 409)
            if state["attempts"] >= 3:
                raise ServiceError("EXECUTION_LIMIT_REACHED", "CLIP retry budget reached", 409)
            state["attempts"] += 1
            payload["status"] = "running"
            member = payload["members"][index]
            request = replace(_request_from_dict(member["request"]), cursor=state["cursor"])
            return member["branch_id"], request, state["cursor"]
        return self._transaction(job_id, True, reserve)

    def commit(self, job_id, plan_id, expected_cursor, batch):
        if type(batch) is not CLIPSearchBatch:
            raise ServiceError("INVALID_SEARCH_PAGE", "Combined CLIP batch required", 502)
        def save(payload):
            index = next((i for i, row in enumerate(payload["state"]) if row["plan_id"] == plan_id), None)
            if index is None:
                raise ServiceError("PLAN_REQUEST_MISMATCH", "Unknown combined CLIP member", 409)
            state = payload["state"][index]
            if state["cursor"] != expected_cursor or state["done"]:
                raise ServiceError("PLAN_NOT_CURRENT", "Combined CLIP page changed", 409)
            if len(batch.matches) != len(batch.page.hits):
                raise ServiceError("INVALID_SEARCH_PAGE", "CLIP match projection changed", 502)
            state["matches"].append([asdict(match) for match in batch.matches])
            state["pages"] += 1
            state["cursor"] = batch.page.next_cursor
            state["attempts"] = 0
            state["last_error"] = None
            state["done"] = batch.page.next_cursor is None
            state["terminal_reason"] = batch.page.stop_reason
            payload["status"] = ("complete" if all(row["done"] for row in payload["state"])
                                 else "running")
            return state["pages"]
        return self._transaction(job_id, True, save)

    def record_error(self, job_id, plan_id, code, *, retryable):
        def save(payload):
            state = next((row for row in payload["state"] if row["plan_id"] == plan_id), None)
            if state is None:
                raise ServiceError("PLAN_REQUEST_MISMATCH", "Unknown combined CLIP member", 409)
            state["last_error"] = code
            if not retryable or state["attempts"] >= 3:
                payload["status"] = "failed"
            return payload["status"]
        return self._transaction(job_id, True, save)

    def supersede(self, job_id):
        return self._transaction(job_id, True,
            lambda payload: payload.update(status="superseded") or "superseded")

    def view(self, job_id):
        def read(payload):
            items = []
            for member, state in zip(payload["members"], payload["state"]):
                for page in state["matches"]:
                    items.extend({"branch_id": member["branch_id"], "plan_id": state["plan_id"],
                                  **asdict(_match_from_dict(match))}
                                 for match in page)
            return {"job_id": job_id, "task_id": payload["scope"]["task_id"],
                    "revision": payload["scope"]["revision"],
                    "generation": payload["scope"]["generation"],
                    "status": payload["status"], "raw_hit_count": len(items),
                    "members": [{"branch_id": member["branch_id"], "plan_id": state["plan_id"],
                                 "pages": state["pages"], "done": state["done"],
                                 "last_error": state["last_error"]}
                                for member, state in zip(payload["members"], payload["state"])],
                    "coverage": execution_coverage(payload["members"], payload["state"]),
                    "items": items}
        return self._transaction(job_id, False, read)

    def matches(self, job_id):
        """Return typed immutable source matches for trusted material mapping."""
        def read(payload):
            result = []
            for member, state in zip(payload["members"], payload["state"]):
                for page in state["matches"]:
                    result.extend((member["branch_id"], state["plan_id"],
                                   _match_from_dict(match)) for match in page)
            return tuple(result)
        return self._transaction(job_id, False, read)


class CLIPSearchExecutor:
    def __init__(self, tasks, jobs, client, *, budget=None, continuation=None, initial_selector=None):
        self.tasks, self.jobs, self.client, self.budget = tasks, jobs, client, budget
        self.continuation = continuation
        self.initial_selector = initial_selector

    def _pause(self, task, job, phase):
        if any(m.windowed for m in job.members) and (self.continuation is None
                or phase == 'initial' and self.initial_selector is None):
            raise ServiceError('SEARCH_GATE_NOT_CONFIGURED', 'Persisted window policy requires dispatch gate', 503)
        if self.continuation is None or not self._refill_current(task, job):
            return None
        reason = self.continuation(task, phase)
        if reason is None:
            return None
        if reason not in {'CLEANING_IN_PROGRESS', 'TARGET_MET', 'AWAIT_REFILL', 'CANDIDATE_WINDOW_READY'}:
            raise ServiceError('INVALID_SEARCH_GATE', 'Invalid business continuation decision', 503)
        return {**self.jobs.view(job.job_id), 'status': 'paused', 'pause_reason': reason,
                'network_attempts': 0, 'outcomes': []}

    @staticmethod
    def _current(task, job):
        try:
            return RetrievalScope.from_task(task) == job.scope
        except ServiceError:
            return False

    @staticmethod
    def _refill_current(task, job):
        try:
            scope = RetrievalScope.from_task(task)
        except ServiceError:
            return False
        return (scope.tenant_id, scope.owner_user_id, scope.project_id, scope.task_id,
                scope.revision, scope.generation, scope.requirement_digest) == (
                job.scope.tenant_id, job.scope.owner_user_id, job.scope.project_id,
                job.scope.task_id, job.scope.revision, job.scope.generation,
                job.scope.requirement_digest) and scope.task_version >= job.scope.task_version

    def _search(self, job, context, request):
        member = next(m for m in job.members if m.request.plan_id == request.plan_id)
        if member.shard_group:
            endpoint = self.client.endpoints.get(request.binding.binding_id)
            if (endpoint is None or endpoint.shard_paths != member.shard_group
                    or endpoint.declared_shards != member.declared_shards):
                raise ServiceError('SHARD_CATALOG_CHANGED', 'Frozen shard configuration changed', 409)
        if self.budget is not None:
            self.budget._charge(job.scope, request.batch_size)
        return self.client.search_text(context, request)

    def tick(self, job_id, *, max_requests=1):
        from contextlib import nullcontext
        job = self.jobs.job(job_id)
        guard = self.budget.execution(job.scope) if self.budget is not None else nullcontext()
        with guard:
            return self._tick(job_id, max_requests=max_requests)

    def _tick(self, job_id, *, max_requests=1):
        if type(max_requests) is not int or not 1 <= max_requests <= 64:
            raise ServiceError("INPUT_LIMIT", "Bounded CLIP search tick required", 422)
        job = self.jobs.job(job_id)
        actor = Actor(job.scope.tenant_id, job.scope.owner_user_id)
        attempts, outcomes = 0, []
        for _ in range(max_requests):
            task = self.tasks.get(actor, job.scope.task_id)
            paused = self._pause(task, job, 'initial')
            if paused is not None:
                return {**paused, 'network_attempts': attempts, 'outcomes': outcomes}
            if not self._current(task, job):
                self.jobs.supersede(job_id)
                break
            selected = self.initial_selector(task, job) if self.initial_selector else None
            reserved = self.jobs.next_request(job_id, plan_ids=selected)
            if reserved is None:
                break
            branch_id, request, expected_cursor = reserved
            attempts += 1
            context = AccessContext(actor, job.scope.task_id, "retrieval", job.authorization_version)
            try:
                batch = self._search(job, context, request)
                current = self.tasks.get(actor, job.scope.task_id)
                if not self._current(current, job):
                    self.jobs.supersede(job_id)
                    outcomes.append({"plan_id": request.plan_id, "status": "superseded"})
                    break
                pages = self.jobs.commit(job_id, request.plan_id, expected_cursor, batch)
                outcomes.append({"plan_id": request.plan_id, "branch_id": branch_id,
                                 "status": "committed", "pages": pages})
            except ServiceError as error:
                retryable = error.retryable and error.status in (429, 502, 503, 504)
                status = self.jobs.record_error(job_id, request.plan_id, error.code,
                                                retryable=retryable)
                outcomes.append({"plan_id": request.plan_id,
                                 "status": "retry_pending" if status != "failed" else "failed",
                                 "code": error.code})
                if not retryable:
                    raise
        return {"job_id": job_id, "network_attempts": attempts,
                "outcomes": outcomes, **self.jobs.view(job_id)}

    def tick_refill(self, job_id, *, plan_id, expected_cursor):
        from contextlib import nullcontext
        job = self.jobs.job(job_id)
        guard = self.budget.execution(job.scope) if self.budget is not None else nullcontext()
        with guard:
            return self._tick_refill(job_id, plan_id=plan_id, expected_cursor=expected_cursor)

    def _tick_refill(self, job_id, *, plan_id, expected_cursor):
        """Execute one exact pending position after downstream task-version advances."""
        job = self.jobs.job(job_id)
        actor = Actor(job.scope.tenant_id, job.scope.owner_user_id)
        task = self.tasks.get(actor, job.scope.task_id)
        paused = self._pause(task, job, 'refill')
        if paused is not None:
            return paused
        if not self._refill_current(task, job):
            self.jobs.supersede(job_id)
            return {"job_id": job_id, "network_attempts": 0,
                    "outcomes": [{"plan_id": plan_id, "status": "superseded"}],
                    **self.jobs.view(job_id)}
        reserved = self.jobs.reserve_request(job_id, plan_id, expected_cursor)
        if reserved is None:
            return {"job_id": job_id, "network_attempts": 0, "outcomes": [],
                    **self.jobs.view(job_id)}
        branch_id, request, cursor = reserved
        context = AccessContext(actor, job.scope.task_id, "retrieval", job.authorization_version)
        try:
            batch = self._search(job, context, request)
            if not self._refill_current(self.tasks.get(actor, job.scope.task_id), job):
                self.jobs.supersede(job_id)
                return {"job_id": job_id, "network_attempts": 1,
                        "outcomes": [{"plan_id": plan_id, "status": "superseded"}],
                        **self.jobs.view(job_id)}
            pages = self.jobs.commit(job_id, plan_id, cursor, batch)
            outcome = {"plan_id": plan_id, "branch_id": branch_id,
                       "status": "committed", "pages": pages}
        except ServiceError as error:
            retryable = error.retryable and error.status in (429, 502, 503, 504)
            status = self.jobs.record_error(job_id, plan_id, error.code, retryable=retryable)
            outcome = {"plan_id": plan_id,
                       "status": "retry_pending" if status != "failed" else "failed",
                       "code": error.code}
            if not retryable:
                raise
        return {"job_id": job_id, "network_attempts": 1, "outcomes": [outcome],
                **self.jobs.view(job_id)}


def execution_coverage(members, states):
    """Per-query physical coverage; a page observed is not exhaustive search."""
    branches = {}
    for member, state in zip(members, states):
        if not member.get('declared_shards'):
            continue
        key = (member['branch_id'], member['request']['binding']['library_id'],
               member['request']['binding']['index_version'])
        row = branches.setdefault(key, dict(total=set(), visited=set(), exhausted=set(), failed=set()))
        row['total'].update(member['declared_shards'])
        group = set(member['shard_group'])
        if state['pages'] > 0: row['visited'].update(group)
        if state['done'] and state.get('terminal_reason') == 'exhausted' and not state['last_error']:
            row['exhausted'].update(group)
        if state['last_error']: row['failed'].update(group)
    return {'known': bool(branches) and all(m.get('declared_shards') for m in members), 'queries': [dict(branch_id=k[0], library_id=k[1],
        index_version=k[2], total=len(r['total']), visited=len(r['visited']), exhausted=len(r['exhausted']),
        failed=len(r['failed']), pending=len(r['total'] - r['visited']),
        complete=r['total'] == r['exhausted'] and not r['failed'])
        for k, r in branches.items()]}
