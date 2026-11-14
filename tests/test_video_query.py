"""Video draft boundary regressions: no image fallback or accidental execution."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

from draft_helpers import BODY, make_service
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.query_branches import compile_query_branches


def video_body(unit='distinct_video'):
    body = deepcopy(BODY)
    spec = body['query_spec']
    spec.update(schema_version='query-spec@3', raw_request='找10个喝水的原视频',
                media_type='video', count_unit=unit,
                query_input={'mode': 'text', 'text': '有人喝水'},
                video_conditions={'clauses': [
                    {'id': 'visual_1', 'channel': 'visual', 'predicate': '有人喝水'},
                    {'id': 'visual_2', 'channel': 'visual', 'predicate': '画面包含杯子'}],
                    'relation': {'operator': 'all', 'scope': 'same_video'}})
    if unit == 'clip':
        spec['video_options'] = {'clip_duration_ms': {'min': 3000, 'max': 15000},
                                 'allow_multiple_clips_per_video': False}
    return body


class VideoDraftTest(unittest.TestCase):
    actor = Actor('tenant-a', 'user-a')

    def test_text_video_draft_persists_without_image_upload_or_business_records(self):
        repo = InMemoryTaskRepository()
        service = make_service(repo)
        first, replay = service.save_draft(self.actor, 'demo', 'video', video_body())
        second, replay = service.save_draft(self.actor, 'demo', 'video', video_body())
        self.assertTrue(replay)
        self.assertEqual(first, second)
        self.assertEqual(repo._tasks, {})
        self.assertEqual(repo._batches, {})
        summary = first['video_summary']
        self.assertEqual(summary['count_unit'], 'distinct_video')
        self.assertFalse(summary['execution']['available'])
        self.assertEqual(summary['video_conditions']['clauses'][1]['channel'], 'visual')
        self.assertEqual(summary['qualification_scope'], 'video_content_match')

    def test_video_confirmation_fails_before_creating_task(self):
        repo = InMemoryTaskRepository()
        service = make_service(repo)
        draft, _ = service.save_draft(self.actor, 'demo', 'video', video_body())
        confirmation = {'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
                        'proposal_digest': draft['proposal_digest'], 'action': 'confirm'}
        for _ in range(2):
            with self.assertRaises(ServiceError) as raised:
                service.create_task(self.actor, 'confirm-video', confirmation)
            self.assertEqual(raised.exception.code, 'VIDEO_EXECUTION_UNAVAILABLE')
        self.assertEqual(repo._tasks, {})
        self.assertEqual(repo._batches, {})
        self.assertEqual(service.get_draft(self.actor, 'demo', draft['draft_id'])['status'], 'draft')
        service.projects.grants.add(('tenant-a', 'other', 'demo', 'default'))
        with self.assertRaises(ServiceError) as raised:
            service.create_task(Actor('tenant-a', 'other'), 'foreign', confirmation)
        self.assertEqual(raised.exception.status, 404)

    def test_sqlite_restart_keeps_video_semantics_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'video.db')
            service = make_service(SQLiteTaskRepository(path))
            draft, _ = service.save_draft(self.actor, 'demo', 'video', video_body('distinct_video'))
            restored = make_service(SQLiteTaskRepository(path))
            view = restored.get_draft(self.actor, 'demo', draft['draft_id'])
            replay, reused = restored.save_draft(self.actor, 'demo', 'video', video_body('distinct_video'))
            self.assertTrue(reused)
            self.assertEqual(view, draft)
            self.assertEqual(replay, draft)
            self.assertIsNone(view['video_summary']['video_options'])

    def test_semantic_edits_invalidate_digest_and_logical_fingerprint(self):
        original = normalize_proposal(video_body())
        preview = compile_query_branches(original['query_spec'])
        self.assertEqual(preview['branches'][0]['query_role'], 'original')
        self.assertEqual(preview['branches'][0]['query_text'], '有人喝水')
        self.assertIn('video_condition:visual_2', preview['branches'][0]['constraint_refs'])
        self.assertIn('video_conditions', preview['constraints'])
        self.assertNotIn('参考图', preview['note'])
        for field, value in [('predicate', '画面有桌子'), ('predicate', '不要人物')]:
            edited = deepcopy(original)
            edited['query_spec']['video_conditions']['clauses'][1][field] = value
            changed = compile_query_branches(edited['query_spec'])
            self.assertNotEqual(changed['preview_digest'], preview['preview_digest'])
            self.assertNotEqual(changed['branches'][0]['query_fingerprint'],
                                preview['branches'][0]['query_fingerprint'])
        repo = InMemoryTaskRepository()
        service = make_service(repo)
        draft, _ = service.save_draft(self.actor, 'demo', 'first', video_body())
        body = video_body()
        body['query_spec']['video_conditions']['relation']['operator'] = 'any'
        body['expected_draft_version'] = 1
        updated, _ = service.save_draft(self.actor, 'demo', 'edit', body, draft['draft_id'])
        self.assertNotEqual(updated['proposal_digest'], draft['proposal_digest'])
        self.assertEqual(updated['draft_version'], 2)
        with self.assertRaises(ServiceError) as raised:
            service.create_task(self.actor, 'stale', {'draft_id': draft['draft_id'],
                'expected_draft_version': 1, 'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
        self.assertEqual(raised.exception.code, 'DRAFT_VERSION_CONFLICT')

    def test_unsupported_targets_inputs_relations_and_missing_units_are_not_downgraded(self):
        cases = []
        for media in ('image', 'text'):
            body = video_body(); body['query_spec']['media_type'] = media; cases.append(body)
        body = video_body(); body['query_spec'].pop('count_unit'); cases.append(body)
        body = video_body(); body['query_spec']['count_unit'] = 'clip'; cases.append(body)
        body = video_body(); body['query_spec']['query_input'] = {'mode': 'image', 'text': '早餐'}; cases.append(body)
        body = video_body(); body['query_spec']['video_conditions']['relation']['scope'] = 'same_clip'; cases.append(body)
        body = video_body(); body['query_spec']['video_conditions']['relation']['offset_ms'] = 1000; cases.append(body)
        body = video_body(); body['query_spec']['schema_version'] = 'query-spec@2'; cases.append(body)
        body = video_body(); body['query_spec']['rules'] = [{'predicate_ref': 'image.semantic-match@1'}]; cases.append(body)
        for body in cases:
            with self.subTest(spec=body['query_spec']), self.assertRaises(ServiceError):
                normalize_proposal(body)

    def test_malformed_conditions_and_duration_cannot_escape_schema_validation(self):
        cases = []
        for duration in ({'min': True, 'max': 5000}, {'min': 6000, 'max': 5000}, {'min': 0, 'max': 5000}):
            body = video_body(); body['query_spec']['video_options'] = {'clip_duration_ms': duration}; cases.append(body)
        for clauses in ([], [None], [{'id': 'x', 'channel': [], 'predicate': 'hello'}],
                        [{'id': 'x', 'channel': 'visual', 'predicate': '   '}],
                        [{'id': 'x', 'channel': 'visual', 'predicate': 'hello'}] * 2):
            body = video_body(); body['query_spec']['video_conditions']['clauses'] = clauses; cases.append(body)
        for body in cases:
            with self.subTest(spec=body['query_spec']), self.assertRaises(ServiceError):
                normalize_proposal(body)

    def test_auxiliary_channels_and_full_duration_claims_are_rejected(self):
        for channel in ('subtitle', 'asr', 'ocr'):
            body = video_body()
            body['query_spec']['video_conditions']['clauses'][0]['channel'] = channel
            with self.subTest(channel=channel), self.assertRaises(ServiceError):
                normalize_proposal(body)
        for scope in ('clip', 'whole_video', 'same_instant'):
            body = video_body()
            body['query_spec']['video_conditions']['clauses'][0]['verification_scope'] = scope
            with self.subTest(scope=scope), self.assertRaises(ServiceError):
                normalize_proposal(body)

    def test_legacy_video_draft_is_readable_without_rewriting_old_constraints(self):
        repo = InMemoryTaskRepository()
        service = make_service(repo)
        draft, _ = service.save_draft(self.actor, 'demo', 'old', video_body())
        old = repo.get_draft(self.actor, draft['draft_id'])
        proposal = json.loads(old.proposal_json)
        proposal['query_spec']['count_unit'] = 'clip'
        proposal['query_spec']['video_conditions']['relation']['scope'] = 'same_clip'
        repo._drafts[old.draft_id] = replace(old, proposal_json=json.dumps(proposal))
        original_json = repo.get_draft(self.actor, old.draft_id).proposal_json
        view = service.get_draft(self.actor, 'demo', old.draft_id)
        self.assertTrue(view['video_summary']['requires_scope_update'])
        self.assertEqual(view['video_summary']['qualification_scope'], 'legacy_video_scope')
        self.assertEqual(view['proposal']['query_spec']['count_unit'], 'clip')
        self.assertEqual(repo.get_draft(self.actor, old.draft_id).proposal_json, original_json)

    def test_v2_image_contract_and_summary_unchanged(self):
        repo = InMemoryTaskRepository()
        service = make_service(repo)
        draft, _ = service.save_draft(self.actor, 'demo', 'image', deepcopy(BODY))
        self.assertNotIn('video_summary', draft)
        proposal = draft['proposal']
        self.assertEqual(proposal['query_spec']['schema_version'], 'query-spec@2')
        self.assertNotIn('query_input', proposal['query_spec'])
        task, _ = service.create_task(self.actor, 'confirm', {'draft_id': draft['draft_id'],
            'expected_draft_version': 1, 'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
        self.assertEqual(task['run_status'], 'PLANNING')


if __name__ == '__main__':
    unittest.main()
