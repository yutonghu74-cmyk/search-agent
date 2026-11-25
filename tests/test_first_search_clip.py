import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.retrieval.clip_search import CLIPSearchEndpoint
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.first_search_clip import CLIPFirstSearch
from dsh_retrieval.integration_models import AccessContext, IndexBinding, Library
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_index_registry import PhysicalIndex, PhysicalIndexRegistry
from dsh_retrieval.service import TaskService
from dsh_retrieval.text_catalog_config import CatalogConfigScope
from draft_helpers import BODY, confirmation


class Response(io.BytesIO):
    status = 200


class Transport:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        payload = {
            "success": True, "query_type": "text", "model": "chinese-clip-vit-huge-patch14",
            "datasets": [{"key": "dataset-key", "version": "dataset-v1"}],
            "current_page": 1, "group_size": 10, "has_next": False,
            "matches": [{"id": 1, "similarity_score": 0.42, "filename": "one.jpg",
                "full_path": "/mock/one.jpg", "web_path": "/local/one.jpg", "folder": "all",
                "app_name": "mock", "dataset_name": "mock-images",
                "dataset_key": "dataset-key", "shard": "shard_0.db"}],
        }
        return Response(json.dumps(payload).encode())


class CatalogRuntime:
    def suggest_catalog(self, text, llm_id, candidates):
        return [{"library_id": "clip-images", "description": "客厅图片",
                 "aliases": ["客厅", "图片"], "binding_ids": ["clip-v1"]}]


class FirstSearchCLIPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.actor = Actor("tenant-a", "user-a")
        projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
        self.tasks = TaskService(SQLiteTaskRepository(root / "tasks.sqlite3"), projects)
        created, _ = self.tasks.create_task(self.actor, "task",
            confirmation(self.tasks, self.actor, body=BODY))
        self.task_id = created["task_id"]
        self.binding = IndexBinding("clip-v1", "clip-images", "index-v1", "clip@1", 4, "IP")
        source = {"schema": "local-catalog@1", "libraries": [{"library_id": "clip-images",
            "description": "找客厅图片", "media_type": "image", "bindings": [{
                "binding_id": "clip-v1", "library_id": "clip-images", "index_version": "index-v1",
                "encoder_ref": "clip@1", "vector_dimension": 4, "metric": "IP"}]}],
            "grants": [{"tenant_id": "tenant-a", "owner_id": "user-a", "project_id": "demo",
                        "library_ids": ["clip-images"], "can_configure": True}]}
        self.source = root / "catalog.json"
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        capabilities = {"schema": "text-query-capabilities@1", "items": [{
            "binding": source["libraries"][0]["bindings"][0], "media_type": "image",
            "filter_template_ref": "all", "max_batch_size": 10}]}
        self.capabilities = root / "capabilities.json"
        self.capabilities.write_text(json.dumps(capabilities))
        mappings = {"schema": "clip-media-mappings@1", "entries": [{
            "source": {"binding_id": "clip-v1", "index_version": "index-v1",
                "dataset_key": "dataset-key", "dataset_version": "dataset-v1",
                "shard": "shard_0.db", "full_path": "/mock/one.jpg"},
            "media": {"media_unit_id": "unit-1", "asset_version": "asset-v1",
                "source_namespace": "mock-assets", "source_asset_id": "asset-1",
                "authorized_media_ref": "media:asset-1@asset-v1",
                "content_digest": "sha256:" + "1" * 64}}]}
        self.mappings = root / "mappings.json"
        self.mappings.write_text(json.dumps(mappings))
        self.configs = build_local_catalog(self.source, root / "catalog.sqlite3", CatalogRuntime())
        scope = CatalogConfigScope("tenant-a", "user-a", "demo")
        self.configs.save(scope, **self.configs.preview(scope, "配置客厅图片库", "text"))
        target = PhysicalIndex(self.binding, "clip-service", "none", "mock-images", "path", "vector",
            "chinese-clip-vit-huge-patch14", "unknown", "clip-text-77@1", "all",
            "offset", "live", 10, 100)
        registry = PhysicalIndexRegistry((target,))
        endpoint = CLIPSearchEndpoint(self.binding, "chinese-clip-vit-huge-patch14", "mock-project",
            "/mock/dataset", ("shard_0.db",), (("all", ()),), "all", 100, 0.1, 40,
            "http://clip.invalid:4444")
        self.transport = Transport()
        self.clip = CLIPFirstSearch(self.tasks, self.configs, source_path=self.source,
            capabilities_path=self.capabilities, registry=registry, endpoints=(endpoint,),
            jobs_path=root / "jobs.sqlite3", cursor_secret=b"c" * 32, opener=self.transport,
            media_mappings_path=self.mappings)
        self.addCleanup(self.clip.close)

    def wait_complete(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            progress = self.clip.progress(self.actor, self.task_id)
            if progress["pagination_stopped"]:
                return progress
            time.sleep(.01)
        self.fail("combined CLIP search did not finish")

    def test_candidate_preview_is_stable_read_only_and_requires_mapping(self):
        self.clip.auto_start(self.actor, self.task_id, key='candidate-input')
        self.wait_complete()
        before = self.tasks.repository.get(self.actor, self.task_id)
        view = self.clip.preview_manifest(self.actor, self.task_id)
        self.assertEqual(view['candidate_count'], 1)
        self.assertFalse(view['confirmation_supported'])
        self.assertFalse(view['confirmed'])
        self.assertIsNone(view['accepted_count'])
        self.assertEqual(self.clip.preview_manifest(self.actor, self.task_id), view)
        self.assertEqual(self.tasks.repository.get(self.actor, self.task_id).task_version, before.task_version)
        from copy import deepcopy
        from dsh_retrieval.clip_candidate_input import build_clip_input
        from dsh_retrieval.retrieval_contracts import RetrievalScope
        job_id = self.clip.jobs.current_for_browse(RetrievalScope.from_task(before))
        job = self.clip.jobs.job(job_id)
        rows = deepcopy(self.clip.jobs.view(job_id)['items'])
        rows[0]['plan_id'] = 'unapproved-plan'
        with self.assertRaises(ServiceError) as invalid:
            build_clip_input(before, job, rows, self.clip.media_mappings)
        self.assertEqual(invalid.exception.code, 'INPUT_SNAPSHOT_CHANGED')
        self.clip.media_mappings = None
        with self.assertRaises(ServiceError) as caught:
            self.clip.preview_manifest(self.actor, self.task_id)
        self.assertEqual(caught.exception.code, 'MEDIA_MAPPING_REQUIRED')

    def test_combined_candidate_confirmation_persists_and_reaches_cleaning_reader(self):
        from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        class MediaAuthority:
            def require_access(self, context, resource_ref, action):
                if resource_ref != 'media:asset-1@asset-v1' or action != 'read':
                    raise ServiceError('ACCESS_DENIED', 'not authorized', 403)
        repository = SQLiteMultiRuleApprovalRepository(self.tasks.repository.path,
            projects=self.tasks.projects, authorization=MediaAuthority())
        from copy import deepcopy
        body = deepcopy(BODY)
        body['query_spec']['rules'] = [dict(rule_id='no-text', predicate_ref='text.present@1',
            operator='eq', value=False, hardness='hard', origin='user_explicit')]
        created, _ = self.tasks.create_task(self.actor, 'cleanable-combined',
            confirmation(self.tasks, self.actor, key='cleanable-draft', body=body))
        self.task_id = created['task_id']
        self.clip.configure_cleaning_reader(repository)
        self.clip.auto_start(self.actor, self.task_id, key='persistent-input')
        self.wait_complete()
        preview = self.clip.preview_manifest(self.actor, self.task_id)
        self.assertTrue(preview['confirmation_supported'])
        args = dict(proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'], key='confirm-input', action='confirm')
        first, replayed = self.clip.confirm_manifest(self.actor, self.task_id, **args)
        self.assertFalse(replayed)
        repeated, replayed = self.clip.confirm_manifest(self.actor, self.task_id, **args)
        self.assertTrue(replayed); self.assertEqual(first, repeated)
        manifest = repository.get_manifest(self.actor, self.task_id, first['manifest_ref'])
        self.assertEqual(len(manifest.proposal.items), 1)
        from dsh_retrieval.multi_rule_contracts import CleaningRecipe, CleaningRule, DetectionStep
        from dsh_retrieval.query_spec import digest
        from dsh_retrieval.recipe_bridge import confirmed_rules_digest
        task = self.tasks.repository.get(self.actor, self.task_id)
        recipe = CleaningRecipe('image', confirmed_rules_digest(task), digest('test-registry'),
            (DetectionStep('features', 'test.detector', 'test.validator', 'test.evidence'),),
            (CleaningRule('no-text', 'features', 'text.present@1', 'eq', False),))
        cleaning = repository.prepare(self.actor, self.task_id, first['manifest_ref'], recipe,
            detector_release_digest=digest('test-release'))
        approval, replayed = repository.confirm(self.actor, self.task_id, cleaning.proposal_digest,
            key='cleaning-approval', expected_task_version=cleaning.expected_task_version, action='confirm')
        self.assertFalse(replayed)
        paused = self.clip.tick(self.actor, self.task_id, max_requests=1)
        self.assertEqual(paused['pause_reason'], 'CLEANING_IN_PROGRESS')
        self.assertEqual(paused['network_attempts'], 0)
        again, replayed = self.clip.confirm_manifest(self.actor, self.task_id, **args)
        self.assertTrue(replayed); self.assertEqual(again, first)
        self.assertEqual(repository.get_manifest(self.actor, self.task_id, first['manifest_ref']), manifest)
        # Reopen business ledger and reattach trusted source validator after restart.
        reopened = SQLiteMultiRuleApprovalRepository(self.tasks.repository.path,
            projects=self.tasks.projects, authorization=MediaAuthority())
        self.clip.configure_cleaning_reader(reopened)
        self.assertEqual(reopened.get_manifest(self.actor, self.task_id, first['manifest_ref']), manifest)
        class Denied:
            def require_access(self, *args):
                raise ServiceError('ACCESS_DENIED', 'revoked', 403)
        reopened.manifests.authorization = Denied()
        with self.assertRaises(ServiceError):
            reopened.get_manifest(self.actor, self.task_id, first['manifest_ref'])

    def test_dispatch_uses_effective_counts_and_rejects_stale_generation(self):
        from types import SimpleNamespace
        task = self.tasks.repository.get(self.actor, self.task_id)
        value = dict(generation=task.run_generation, target_count=task.target_count,
                     accepted_count=task.target_count)
        self.clip.cleaning_reader = SimpleNamespace(get_current_result_set=lambda *args: value)
        self.assertEqual(self.clip._dispatch_pause(task, 'initial'), 'TARGET_MET')
        self.assertEqual(self.clip._dispatch_pause(task, 'refill'), 'TARGET_MET')
        value['accepted_count'] = 1
        value['raw_hit_count'] = 50000
        self.assertEqual(self.clip._dispatch_pause(task, 'initial'), 'AWAIT_REFILL')
        self.assertIsNone(self.clip._dispatch_pause(task, 'refill'))
        value['generation'] += 1
        with self.assertRaises(ServiceError) as caught:
            self.clip._dispatch_pause(task, 'initial')
        self.assertEqual(caught.exception.code, 'RESULT_SCOPE_CHANGED')

    def test_window_pauses_before_page_two_and_refill_input_excludes_confirmed(self):
        from copy import deepcopy
        from dataclasses import replace
        from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        from dsh_retrieval.clip_media_mapping import CLIPMediaMappings
        from dsh_retrieval.retrieval_contracts import RetrievalScope
        class Authority:
            def require_access(self, context, ref, action):
                if action != 'read' or ref not in ('media:asset-1@asset-v1', 'media:asset-2@asset-v1'):
                    raise ServiceError('ACCESS_DENIED', 'denied', 403)
        class Pages(Transport):
            def open(this, request, timeout):
                value = json.loads(super(Pages, this).open(request, timeout).read())
                page = len(this.requests)
                value.update(current_page=page, has_next=page == 1)
                if page == 2:
                    value['matches'][0].update(id=2, filename='two.jpg', full_path='/mock/two.jpg')
                return Response(json.dumps(value).encode())
        body = deepcopy(BODY)
        body['query_spec']['refill_policy'] = dict(mode='maintain_target', target_count=10,
            approved_library_ids=['clip-images'], progression='auto')
        body['node_modes']['refill'] = 'auto'
        created, _ = self.tasks.create_task(self.actor, 'window-task',
            confirmation(self.tasks, self.actor, key='window-draft', body=body))
        self.task_id = created['task_id']
        repo = SQLiteMultiRuleApprovalRepository(self.tasks.repository.path,
            projects=self.tasks.projects, authorization=Authority())
        self.clip.configure_cleaning_reader(repo)
        transport = Pages(); self.clip.client.opener = transport
        entries = tuple(self.clip.media_mappings._entries.items())
        old_key, old_media = entries[0]
        self.clip.media_mappings = CLIPMediaMappings(self.clip.client.registry, entries + ((
            (*old_key[:-1], '/mock/two.jpg'), replace(old_media, media_unit_id='unit-2',
                source_asset_id='asset-2', authorized_media_ref='media:asset-2@asset-v1',
                content_digest='sha256:'+'2'*64)),))
        queued = self.clip.auto_start(self.actor, self.task_id, key='window-start')
        deadline = time.monotonic()+3
        while not self.clip.progress(self.actor, self.task_id)['initial_window_complete']:
            self.assertLess(time.monotonic(), deadline); time.sleep(.01)
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(self.clip.progress(self.actor, self.task_id)['pagination_stopped'])
        self.assertEqual(self.clip.tick(self.actor, self.task_id, max_requests=2)['pause_reason'],
                         'CANDIDATE_WINDOW_READY')
        preview = self.clip.preview_manifest(self.actor, self.task_id)
        first, _ = self.clip.confirm_manifest(self.actor, self.task_id,
            proposal_digest=preview['proposal_digest'], expected_task_version=preview['expected_task_version'],
            key='window-input', action='confirm')
        job = self.clip.jobs.job(queued['job_id'])
        self.assertTrue(all(m.windowed for m in job.members))
        pending = self.clip.jobs.pending_requests(job.job_id)[0][1]
        self.clip.executor.tick_refill(job.job_id, plan_id=pending.plan_id, expected_cursor=pending.cursor)
        second = self.clip.preview_manifest(self.actor, self.task_id)
        self.assertEqual(second['candidate_count'], 1)
        self.assertNotEqual(second['candidates'][0]['candidate_id'], first['candidates'][0]['candidate_id'])
        self.assertEqual(repo.get_manifest(self.actor, self.task_id, first['manifest_ref']).proposal.items[0].media,
                         old_media)
        del self.clip.cleaning_reader
        with self.assertRaises(ServiceError) as caught:
            self.clip.tick(self.actor, self.task_id, max_requests=1)
        self.assertEqual(caught.exception.code, 'SEARCH_GATE_NOT_CONFIGURED')
        self.assertEqual(len(transport.requests), 2)

    def test_refill_authority_only_allows_library_search(self):
        authority = self.clip._authority()
        context = AccessContext(self.actor, self.task_id, "refill", "current")
        authority.require_access(context, "library:clip-images", "search")
        with self.assertRaises(ServiceError) as error:
            authority.require_access(context, "library:clip-images", "discover")
        self.assertEqual(error.exception.code, "ACCESS_DENIED")

    def test_confirmed_terms_queue_background_job_and_expose_candidates(self):
        preview = self.clip.preview(self.actor, self.task_id)
        self.assertEqual(preview["mode"], "CLIP_COMBINED")
        self.assertEqual(preview["routes"][0]["options"][0]["binding_id"], "clip-v1")
        queued = self.clip.auto_start(self.actor, self.task_id, key="terms-confirmed")
        self.assertEqual(queued["status"], "queued")
        progress = self.wait_complete()
        self.assertEqual((progress["status"], progress["job_status"]), ("approved", "complete"))
        candidates = self.clip.candidates(self.actor, self.task_id)
        self.assertEqual(candidates["raw_hit_count"], 1)
        self.assertEqual(candidates["items"][0]["filename"], "one.jpg")
        self.assertEqual(candidates["items"][0]["query_text"], "找 10 张客厅图片")
        self.assertEqual(candidates["items"][0]["status"], "resolved")
        self.assertTrue(candidates["items"][0]["candidate_id"].startswith("candidate_"))
        self.assertEqual(candidates["items"][0]["content_digest"], "sha256:" + "1" * 64)
        self.assertEqual(candidates["resolved_count"], 1)
        self.assertEqual(candidates["verified_count"], 1)
        self.assertNotIn("authorized_media_ref", candidates["items"][0])
        self.assertNotIn("source_asset_id", candidates["items"][0])
        replay = self.clip.auto_start(self.actor, self.task_id, key="terms-confirmed")
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.transport.requests), 1)


if __name__ == "__main__":
    unittest.main()
