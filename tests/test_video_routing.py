"""Synthetic trusted task snapshots exercise preparation, never bypass user gates."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

import test_physical_query_preparation as physical
from test_video_query import video_body
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import Library, QueryVector
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.query_routing import QueryRoutingService, QueryRouteChoice
from dsh_retrieval.retrieval_contracts import RetrievalScope


class VideoRoutingTest(unittest.TestCase):
    def setUp(self):
        f = self.fixture = physical.PhysicalPreparationTest()
        f.setUp()
        # Backend-only synthetic snapshot; production TaskService still refuses confirmation.
        f.repo._tasks[f.task_id].confirmed_proposal_json = json.dumps(normalize_proposal(video_body()))
        f.task = f.repo.get(f.actor, f.task_id)
        f.binding = replace(f.binding, encoder_ref='video-space@1')
        f.vector = QueryVector('video-space@1', (0.1, 0.2))
        f.libraries = (Library('library', 'synthetic video library', 'video', (f.binding,)),)
        f.capability = replace(f.capability, binding=f.binding, media_type='video')
        f.preparer = f.make_preparer((f.capability,))
        preview = compile_query_branches(json.loads(f.task.confirmed_proposal_json)['query_spec'])
        f.args.update(expected_scope=RetrievalScope.from_task(f.task), preview_digest=preview['preview_digest'],
                      branch_id=preview['branches'][0]['branch_id'])

    def test_video_discovery_and_text_encoding_prepare_without_mutation(self):
        f = self.fixture
        before = deepcopy(f.repo.get(f.actor, f.task_id))
        def forbidden(*args):
            self.fail('text-to-video must not invoke reference-image encoding')
        f.encode_reference = forbidden
        router = QueryRoutingService(f.preparer)
        preview = router.discover(f.actor, f.task_id, expected_scope=f.args['expected_scope'],
                                   authorization_version='auth-v1')
        self.assertEqual(preview.routes[0].options, (f.capability,))
        plan = router.prepare(f.actor, f.task_id, preview=preview,
            choices=(QueryRouteChoice(preview.routes[0].branch_id, 'binding', 10),), epoch=1)
        self.assertEqual(plan.members[0].plan.request.vector, f.vector)
        self.assertIn('encode', f.calls)
        self.assertEqual(f.repo.get(f.actor, f.task_id), before)

    def test_wrong_encoder_and_revoked_access_fail(self):
        f = self.fixture
        f.vector = QueryVector('wrong-space@1', (0.1, 0.2))
        f.assert_error('ENCODER_MISMATCH')
        f.vector = QueryVector('video-space@1', (0.1, 0.2))
        f.denied = True
        f.assert_error('ACCESS_DENIED')

    def test_legacy_clip_snapshot_rejected_before_catalog_or_encoding(self):
        f = self.fixture
        query = json.loads(f.task.confirmed_proposal_json)
        query['query_spec']['count_unit'] = 'clip'
        f.repo._tasks[f.task_id].confirmed_proposal_json = json.dumps(query)
        task = f.repo.get(f.actor, f.task_id)
        f.args['expected_scope'] = RetrievalScope.from_task(task)
        f.calls.clear()
        with self.assertRaises(ServiceError) as raised:
            f.prepare()
        self.assertEqual(raised.exception.code, 'FEATURE_NOT_SUPPORTED')
        self.assertEqual(f.calls, [])
