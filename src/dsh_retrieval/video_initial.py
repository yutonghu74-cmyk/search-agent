"""Recover initial auto-video intake from the existing durable search outbox.

The outbox receipt stores only orchestration checkpoints. Actual approval,
version fences and results remain in the existing business repositories.
"""
from contextlib import closing
import json
import threading
import logging

from .errors import ServiceError
from .models import Actor
from .refill_intake import AutoRefillIntakeService


def initial_video_calls(db, scope):
    rows = db.execute('SELECT result_json FROM search_dispatch_outbox WHERE task_id=? AND revision=? AND generation=?', scope).fetchall()
    values = [json.loads(row[0] or '{}').get('video_intake', {}).get('estimated_calls', 0) for row in rows]
    if any(type(value) is not int or value < 0 or value > 800 for value in values):
        raise ServiceError('STORAGE_CORRUPT', 'Invalid initial video budget receipt', 500)
    return max(values, default=0)


class InitialVideoIntake:
    def __init__(self, service, first, cleaning, dispatch, *, start=True):
        self.service, self.first, self.cleaning, self.dispatch = service, first, cleaning, dispatch
        self.closed = threading.Event(); self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._work, daemon=True, name='video-initial-intake') if start else None
        if self.thread: self.thread.start()

    def _save(self, event_id, stage):
        with closing(self.service.repository._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT result_json FROM search_dispatch_outbox WHERE event_id=?', (event_id,)).fetchone()
            if row is None: raise ServiceError('NOT_FOUND', 'Initial search receipt missing', 404)
            result = json.loads(row[0] or '{}'); result['video_intake'] = stage
            db.execute('UPDATE search_dispatch_outbox SET result_json=? WHERE event_id=?',
                       (json.dumps(result, sort_keys=True), event_id))

    def drain(self):
        if not self.lock.acquire(blocking=False): return
        try:
            with closing(self.service.repository._connect()) as db:
                rows = db.execute("SELECT event_id,tenant_id,owner_user_id,task_id,revision,generation,result_json FROM search_dispatch_outbox WHERE status='delivered' AND json_extract(result_json,'$.target_media_type')='video' AND COALESCE(json_extract(result_json,'$.video_intake.status'),'pending') NOT IN ('delivered','superseded','blocked','manual') ORDER BY created_at LIMIT 1000").fetchall()
            for event_id, tenant, owner, tid, revision, generation, payload in rows:
                stage = json.loads(payload or '{}').get('video_intake', {})
                if stage.get('status') in ('delivered', 'superseded', 'blocked', 'manual'): continue
                actor = Actor(tenant, owner)
                try:
                    task = self.service.repository.get(actor, tid)
                    if task.media_type != 'video': continue
                    self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'approve')
                    if (task.confirmed_revision, task.run_generation) != (revision, generation):
                        self._save(event_id, {'status': 'superseded'}); continue
                    if not AutoRefillIntakeService._automatic(task):
                        self._save(event_id, {'status': 'manual'}); continue
                    if task.active_cleaning_operation_id or task.stage in ('refill', 'FINALIZING'):
                        self._save(event_id, {**stage, 'status':'delivered'}); continue
                    if stage.get('status') != 'prepared':
                        progress = self.first.progress(actor, tid)
                        if not progress.get('initial_window_complete'): continue
                        manifest = self.first.preview_manifest(actor, tid)
                        if not manifest['candidate_count']:
                            self._save(event_id, {'status':'blocked','reason_code':'VIDEO_ZERO_CANDIDATES'}); continue
                        if not manifest.get('confirmed'):
                            manifest, _ = self.first.confirm_manifest(actor, tid, proposal_digest=manifest['proposal_digest'],
                                expected_task_version=manifest['expected_task_version'], action='confirm', key='video-initial-manifest:'+event_id)
                        proposal = self.cleaning.prepare(actor, tid, manifest['manifest_ref'])
                        calls = proposal.get('estimated_vlm_calls') or 0
                        limit = json.loads(task.confirmed_proposal_json)['query_spec'].get('refill_limits', {}).get('max_vlm_calls')
                        if limit is not None and calls > limit:
                            self._save(event_id, {'status':'blocked','reason_code':'VLM_CALL_LIMIT_REACHED'}); continue
                        stage = {'status':'prepared','proposal_digest':proposal['proposal_digest'],
                                 'expected_task_version':proposal['expected_task_version'],'estimated_calls':calls}
                        self._save(event_id, stage)
                    self.cleaning.confirm(actor, tid, proposal_digest=stage['proposal_digest'],
                        expected_task_version=stage['expected_task_version'], action='confirm', key='video-initial-cleaning:'+event_id)
                    self._save(event_id, {**stage, 'status':'delivered'})
                    self.dispatch.notify()
                except ServiceError as error:
                    self._save(event_id, {**stage, 'last_error': error.code})
        finally: self.lock.release()

    def _work(self):
        while not self.closed.is_set():
            try: self.drain()
            except Exception: logging.getLogger(__name__).warning("Video initial intake is unavailable")
            self.closed.wait(1)

    def close(self):
        self.closed.set()
        if self.thread: self.thread.join(timeout=3)
