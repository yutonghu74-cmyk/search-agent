"""R14 through the real ledger: confirm -> search -> manifest -> recipe -> publish -> restart."""
import json
import unittest

from draft_helpers import BODY, confirmation
from dsh_retrieval.dialogue import explicit_category_targets
from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from dsh_retrieval.candidate_contracts import CandidateManifestProposal, ManifestItem
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import (explicit_group_targets, preview_edited_groups,
                                            seal_explicit_targets, with_delivery_targets)
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.multi_rule_contracts import (CleaningCandidateVerdict, CleaningJobResult,
                                                CleaningRuleVerdict, aggregate_rule_verdicts)
from dsh_retrieval.progress_models import build_task_progress
from dsh_retrieval.recipe_bridge import compile_confirmed_recipe, require_confirmed_rules
from dsh_retrieval.result_views import build_result_page
from dsh_retrieval.vlm_instruction_plan import (confirmed_instruction_plan, group_instruction_plan,
                                                plan_for_instructions)
from dsh_retrieval.vlm_registry import ConfirmedTaskVLMRegistry
from test_first_search_local import FirstSearchLocalTest


SEMANTIC = {'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
            'operator': 'eq', 'value': True, 'hardness': 'hard', 'origin': 'user_confirmed'}


class ExplicitTargetParsingTests(unittest.TestCase):
    LABELS = ['狗', '猫']

    def test_dialogue_recognizes_label_first_allocation(self):
        # Found by a real browser run: this phrasing was asked "需要多少张图片？".
        expected = [{'label': '狗', 'count': 2}, {'label': '猫', 'count': 2}]
        for text in ('狗2张、猫2张的照片', '狗 2 张、猫 2 张', '找狗2张，猫2张', '狗的图片2张，猫图片2张'):
            self.assertEqual(explicit_category_targets(text), expected, text)
        self.assertEqual(explicit_category_targets('2张狗和2张猫'), expected)
        for text in ('狗2张', '找一些户外的狗', '猫2张的照片'):
            self.assertEqual(explicit_category_targets(text), [], text)

    def test_added_cleaning_conditions_create_nodes_for_each_quota_group(self):
        groups = with_delivery_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地']},
            {'primary_label': '猫', 'auxiliary_terms': ['猫在草地']}])['plan'], {'狗': 2, '猫': 2})
        spec = {'raw_request': '狗2张，猫2张', 'grouped_expansion': groups}
        self.assertEqual(len(confirmed_instruction_plan(spec)), 2)
        spec['raw_request'] += '\n追加检测条件：不要水印'
        nodes = confirmed_instruction_plan(spec)
        self.assertEqual(len(nodes), 4)
        for group in groups['groups']:
            checks = [node for node in nodes if node['group_id'] == group['group_id']]
            self.assertEqual(len(checks), 2)
            self.assertIn('不要水印', checks[1]['source'])

    def test_single_subject_total_is_not_sealed_as_a_group_allocation(self):
        result = preview_edited_groups(None, [{'primary_label': '毛驴',
            'auxiliary_terms': [f'毛驴场景{i}' for i in range(8)]}])
        for text in ('一只毛驴 10张', '找10张毛驴图片', '毛驴图片需要10张'):
            self.assertIsNone(explicit_group_targets(text, ['毛驴'], 10))
            sealed = seal_explicit_targets(result, text, 10)
            self.assertEqual(len(sealed['plan']['groups'][0]['auxiliary_terms']), 8)
            self.assertIsNone(sealed['plan']['groups'][0]['delivery_target'])

    def test_label_first_phrasing_from_the_acceptance_example(self):
        self.assertEqual(explicit_group_targets('狗 20 张、猫 30 张', self.LABELS, 50),
                         {'狗': 20, '猫': 30})

    def test_count_first_phrasing_is_not_misread_across_labels(self):
        self.assertEqual(explicit_group_targets('20张狗30张猫', self.LABELS, 50),
                         {'狗': 20, '猫': 30})

    def test_noun_between_label_and_count(self):
        self.assertEqual(explicit_group_targets('狗的图片20张，猫图片要30张', self.LABELS, 50),
                         {'狗': 20, '猫': 30})

    def test_unallocated_or_non_summing_requests_set_no_quota(self):
        for text, total in (('找狗和猫共50张', 50), ('狗20张', 50),
                            ('狗20张、猫30张', 60), ('狗20张狗25张猫5张', 50)):
            self.assertIsNone(explicit_group_targets(text, self.LABELS, total), text)

    def test_seal_leaves_unallocated_preview_untouched(self):
        result = preview_edited_groups(None, [{'primary_label': '狗', 'auxiliary_terms': []},
                                              {'primary_label': '猫', 'auxiliary_terms': []}])
        self.assertIs(seal_explicit_targets(result, '找狗和猫', 50), result)
        sealed = seal_explicit_targets(result, '狗20张、猫30张', 50)
        self.assertEqual([g['delivery_target'] for g in sealed['plan']['groups']], [20, 30])

    def test_each_group_check_keeps_shared_conditions_without_and_ing_groups(self):
        plan = with_delivery_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': []},
            {'primary_label': '猫', 'auxiliary_terms': []}])['plan'], {'狗': 20, '猫': 30})
        raw = '狗20张，猫30张，都要户外'
        nodes = confirmed_instruction_plan({'raw_request': raw, 'grouped_expansion': plan})
        self.assertEqual(len(nodes), 2)
        self.assertEqual([node['group_id'] for node in nodes],
                         [group['group_id'] for group in plan['groups']])
        self.assertIn('类别「狗」', nodes[0]['instruction'])
        self.assertIn('都要户外', nodes[1]['instruction'])

    def test_group_checks_do_not_ask_one_image_for_the_quota(self):
        plan = with_delivery_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': []},
            {'primary_label': '猫', 'auxiliary_terms': []}])['plan'], {'狗': 2, '猫': 2})
        nodes = confirmed_instruction_plan({'raw_request': '狗2张、猫2张的户外照片',
                                            'grouped_expansion': plan})
        for node in nodes:
            self.assertNotRegex(node['source'], r'[0-9]\s*张')
            self.assertIn('户外照片', node['source'])

    def test_release_frozen_before_quantity_stripping_still_reads(self):
        # A real run froze the full-text check; upgrading must not orphan its result.
        plan = with_delivery_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': []},
            {'primary_label': '猫', 'auxiliary_terms': []}])['plan'], {'狗': 2, '猫': 2})
        spec = {'raw_request': '狗2张、猫2张的照片', 'grouped_expansion': plan}
        legacy = group_instruction_plan(spec['raw_request'], plan, strip_quantities=False)
        frozen = tuple('整理：' + node['source'] for node in legacy)
        self.assertEqual([n['source'] for n in plan_for_instructions(spec, frozen)],
                         [n['source'] for n in legacy])
        current = tuple('整理：' + node['source'] for node in confirmed_instruction_plan(spec))
        self.assertEqual(len(plan_for_instructions(spec, current)), 2)
        with self.assertRaises(ServiceError):
            plan_for_instructions(spec, ('无关', '无关'))


class QuotaLedgerFlowTests(unittest.TestCase):
    POLICY = None

    def setUp(self):
        self.fixture = FirstSearchLocalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = ModelRuntime((
            ModelProfile('text', 'llm', ModelConfig('fixture', 'text-v1',
                                                    'https://fixture.invalid/v1', 'secret')),
            ModelProfile('vision', 'vlm', ModelConfig('fixture', 'vision-v1',
                                                      'https://fixture.invalid/v1', 'secret'))))
        f = self.fixture
        # The fixture holds two dogs and two cats, so 3 + 1 can reach the total
        # of 4 only by miscounting a surplus cat as covering the dog shortfall.
        raw = '3张狗和1张猫'
        result = seal_explicit_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': []},
            {'primary_label': '猫', 'auxiliary_terms': []}]), raw, 4)
        plan = self.runtime.bind(result, self.runtime.binding('text', 'vision'))['plan']
        self.groups = {group['primary_label']: group['group_id'] for group in plan['groups']}
        body = json.loads(json.dumps(BODY))
        body['query_spec'].update(raw_request=raw, target_count=4, rules=[SEMANTIC],
                                  grouped_expansion=plan)
        body['query_spec']['refill_policy']['target_count'] = 4
        if self.POLICY:
            body['query_spec']['refill_policy'] = {**self.POLICY, 'target_count': 4}
        created, _ = f.tasks.create_task(f.actor, 'quota-task',
                                         confirmation(f.tasks, f.actor, key='quota-draft', body=body))
        self.task_id = created['task_id']
        f.local.auto_start(f.actor, self.task_id, key='quota-search')
        f.wait_stopped(f.local, self.task_id)

    def repo(self):
        f = self.fixture
        return SQLiteMultiRuleApprovalRepository(f.ledger, projects=f.projects,
                                                 authorization=f.local._authority())

    def task(self):
        return self.fixture.tasks.repository.get(self.fixture.actor, self.task_id)

    def fixed_manifest(self):
        f = self.fixture
        preview = f.local.preview_manifest(f.actor, self.task_id)
        manifest, _ = f.local.confirm_manifest(
            f.actor, self.task_id, proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'],
            key='quota-manifest', action='confirm')
        return preview, manifest

    def publish_all_passing(self):
        f = self.fixture
        _, manifest = self.fixed_manifest()
        registry = ConfirmedTaskVLMRegistry(self.runtime).build(f.actor, self.task())
        recipe = compile_confirmed_recipe(self.task(), registry)
        repo = self.repo()
        proposal = repo.prepare(f.actor, self.task_id, manifest['manifest_ref'], recipe,
                                detector_release_digest=registry.release_digest)
        approval, _ = repo.confirm(f.actor, self.task_id, proposal.proposal_digest,
                                   key='quota-cleaning',
                                   expected_task_version=proposal.expected_task_version,
                                   action='confirm')
        verdicts = []
        for candidate in approval.job.candidates:
            rules = tuple(
                CleaningRuleVerdict(rule.rule_id, 'pass', 'MATCH', 'evidence', group_id=rule.group_id)
                if rule.group_id in candidate.group_ids else
                CleaningRuleVerdict(rule.rule_id, 'not_applicable', 'GROUP_NOT_APPLICABLE', None,
                                    group_id=rule.group_id)
                for rule in approval.job.recipe.rules)
            verdicts.append(CleaningCandidateVerdict(candidate.candidate_id, candidate.asset_version,
                                                     aggregate_rule_verdicts(rules), rules))
        repo.publish_result(f.actor, self.task_id, approval.operation_id,
                            CleaningJobResult(approval.job.binding, tuple(verdicts)))
        return repo, approval, repo.publish_preview(f.actor, self.task_id, approval.operation_id)[0]

    def test_manifest_freezes_origin_groups_and_excludes_unattributable_hits(self):
        preview, manifest = self.fixed_manifest()
        groups = {tuple(row['group_ids']) for row in preview['candidates']}
        self.assertEqual(groups, {(self.groups['狗'],), (self.groups['猫'],)})
        self.assertEqual(preview['candidate_count'], 4)
        self.assertEqual(manifest['candidates'], preview['candidates'])

    def test_forged_origin_group_is_rejected(self):
        f = self.fixture
        proposal = f.local._candidate_manifest_proposal(f.actor, self.task_id)
        other = {self.groups['狗']: self.groups['猫'], self.groups['猫']: self.groups['狗']}
        forged = CandidateManifestProposal(
            proposal.scope, proposal.operation_id, proposal.plan_digest,
            tuple(ManifestItem(item.media, item.hits, (other[item.group_ids[0]],))
                  for item in proposal.items))
        with self.assertRaises(ServiceError) as raised:
            f.local.repository.put_candidate_proposal(f.actor, forged)
        self.assertEqual(raised.exception.code, 'MANIFEST_INVALID')

    def test_recipe_has_one_group_scoped_check_per_group(self):
        registry = ConfirmedTaskVLMRegistry(self.runtime).build(self.fixture.actor, self.task())
        recipe = compile_confirmed_recipe(self.task(), registry)
        self.assertEqual({rule.group_id for rule in recipe.rules}, set(self.groups.values()))
        self.assertEqual(len(recipe.steps), 2)
        require_confirmed_rules(self.task(), recipe)

    def test_structured_cleaning_edit_reuses_confirmed_candidates_in_new_revision(self):
        f = self.fixture
        self.fixed_manifest()
        old = self.task()
        draft, _ = f.tasks.save_feedback(f.actor, self.task_id, 'structured-feedback', {
            'text': '只给猫增加眼睛睁开', 'scope': 'all', 'cleaning_feedback': True,
            'cleaning_changes': [{'op': 'add', 'condition_id': None,
                                  'source_text': '眼睛睁开', 'group_id': self.groups['猫']}],
            'expected_revision': old.confirmed_revision, 'expected_task_version': old.task_version})
        f.tasks.confirm_feedback(f.actor, self.task_id, 'structured-confirm', {
            'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'], 'expected_task_version': draft['base_task_version'],
            'action': 'confirm'})
        current = self.task()
        registry = ConfirmedTaskVLMRegistry(self.runtime).build(f.actor, current)
        recipe = compile_confirmed_recipe(current, registry)
        self.assertEqual(len(recipe.steps), 3)
        self.assertEqual(recipe.rules[-1].group_id, self.groups['猫'])
        repo = self.repo()
        from dsh_retrieval.integration_models import AccessContext
        context = AccessContext(f.actor, self.task_id, 'cleaning_input', 'current')
        for resource, action in (('library:dogs', 'search'), ('media:unavailable', 'write'),
                                 ('media:unavailable', 'read')):
            with self.assertRaises(ServiceError):
                f.local._authority().require_access(context, resource, action)
        prepared = repo.prepare_incremental_recompute(f.actor, self.task_id, recipe,
                                                     detector_release_digest=registry.release_digest)
        self.assertEqual(len(prepared.candidates), 4)
        self.assertEqual(prepared.revision, current.confirmed_revision)
        reopened = self.repo()
        self.assertEqual(json.loads(reopened.tasks.get(f.actor, self.task_id).confirmed_proposal_json)
                         ['query_spec']['cleaning_conditions'][-1]['text'], '眼睛睁开')

    def test_total_met_but_dog_group_short_is_not_reported_as_met(self):
        _, _, view = self.publish_all_passing()
        rows = {row.group_id: row for row in view['group_quotas']}
        self.assertEqual((rows[self.groups['狗']].accepted_count, rows[self.groups['狗']].gap), (2, 1))
        self.assertEqual((rows[self.groups['猫']].accepted_count, rows[self.groups['猫']].gap), (1, 0))
        self.assertEqual(view['eligible_unique_count'], 4)
        self.assertEqual((view['accepted_count'], view['reserve_count'], view['gap']), (3, 1, 1))
        self.assertNotEqual(view['stop_reason'], 'TARGET_MET')

    def test_quota_result_rebuilds_identically_after_restart(self):
        f = self.fixture
        _, _, view = self.publish_all_passing()
        restarted = self.repo().get_current_result_set(f.actor, self.task_id)
        self.assertEqual(restarted, view)

    def test_result_page_shows_each_group_and_the_shortfall(self):
        _, _, view = self.publish_all_passing()
        task = self.task()
        page = build_result_page(task.to_summary(), json.loads(task.confirmed_proposal_json), view)
        self.assertEqual([(row['label'], row['accepted_count'], row['target_count'], row['gap'])
                          for row in page['group_progress']],
                         [('狗', 2, 3, 1), ('猫', 1, 1, 0)])
        self.assertEqual(page['counts']['gap'], 1)
        repo = self.repo()
        first = repo.get_result_read_page(self.fixture.actor, self.task_id,
                                          limit=1)
        expected = build_result_page(task.to_summary(),
            json.loads(task.confirmed_proposal_json), view, limit=1)
        self.assertEqual(first, expected)
        self.assertIsNotNone(first['next_cursor'])
        second = repo.get_result_read_page(self.fixture.actor, self.task_id,
            limit=1, cursor=first['next_cursor'])
        self.assertEqual(second, build_result_page(task.to_summary(),
            json.loads(task.confirmed_proposal_json), view,
            limit=1, cursor=first['next_cursor']))

    def test_progress_names_the_short_group(self):
        _, _, view = self.publish_all_passing()
        task = self.task()
        progress = build_task_progress(task.to_summary(), json.loads(task.confirmed_proposal_json),
                                       view).to_view()
        self.assertEqual([row['gap'] for row in progress['counts']['groups']], [1, 0])
        json.dumps(progress)  # The API serves this as JSON; group rows must survive that.
        refill = next(node for node in progress['nodes'] if node['node'] == 'refill')
        self.assertIn('缺口 1（狗 1）', refill['detail'])

    def test_step_view_excludes_candidates_a_group_check_did_not_apply_to(self):
        f = self.fixture
        repo, _, _ = self.publish_all_passing()
        full_view = repo.step_results(f.actor, self.task_id)
        run = full_view['runs'][0]
        steps = run['steps']
        self.assertEqual([step['counts']['input'] for step in steps], [2, 2])
        self.assertEqual([len(step['items']) for step in steps], [2, 2])
        from dsh_retrieval.cleaning_step_outputs import build_step_output_page
        step = steps[0]
        kwargs = {'operation_id': run['operation_id'],
                  'step_execution_id': step['step_execution_id'],
                  'output_version': run['output_version'], 'limit': 1}
        first = repo.step_output_read_page(f.actor, self.task_id, **kwargs)
        self.assertEqual(first, build_step_output_page(full_view, **kwargs))
        self.assertIsNotNone(first['next_cursor'])
        kwargs['cursor'] = first['next_cursor']
        self.assertEqual(repo.step_output_read_page(f.actor, self.task_id, **kwargs),
                         build_step_output_page(full_view, **kwargs))


if __name__ == '__main__':
    unittest.main()


PNG = b'\x89PNG\r\n\x1a\n'


class GroupedDeliveryTests(QuotaLedgerFlowTests):
    """R18: a short quota task packages each group in its own folder as a partial delivery."""
    POLICY = {'mode': 'maintain_target', 'approved_library_ids': ['dogs', 'cats'],
              'progression': 'manual'}

    def setUp(self):
        # Delivery accepts only real image bytes, so give each fixture media PNG
        # content whose digest the ledger records before anything is searched.
        from hashlib import sha256
        probe = FirstSearchLocalTest(); probe.setUp(); self.addCleanup(probe.doCleanups)
        records = json.loads(probe.fixture_path.read_text())['records']
        self.bytes_by_ref = {}
        for record in records:
            for media in record['media']:
                content = PNG + media['media_unit_id'].encode()
                self.bytes_by_ref[media['authorized_media_ref']] = content
        original = FirstSearchLocalTest.setUp
        bytes_by_ref = self.bytes_by_ref

        def png_setup(fixture):
            original(fixture)
            data = json.loads(fixture.fixture_path.read_text())
            for record in data['records']:
                for media in record['media']:
                    media['content_digest'] = 'sha256:' + sha256(
                        bytes_by_ref[media['authorized_media_ref']]).hexdigest()
            fixture.fixture_path.write_text(json.dumps(data, ensure_ascii=False))
            fixture.local = fixture.make_local()
        from unittest import mock
        with mock.patch.object(FirstSearchLocalTest, 'setUp', png_setup):
            super().setUp()

    def gateway(self):
        from contextlib import contextmanager
        from io import BytesIO
        contents = self.bytes_by_ref

        class Gateway:
            @contextmanager
            def open_read(inner, context, ref, *, max_bytes):
                if context.purpose != 'delivery_export':
                    raise ServiceError('ACCESS_DENIED', 'delivery reads only', 403)
                yield BytesIO(contents[ref])
        return Gateway()

    def stop_and_package(self):
        import sqlite3
        from contextlib import closing
        from dsh_retrieval.delivery import ImageDeliveryService
        f = self.fixture
        repo, _, view = self.publish_all_passing()
        task = self.task()
        with closing(sqlite3.connect(f.ledger)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS refill_execution_stops (
                task_id TEXT NOT NULL, revision INTEGER NOT NULL, generation INTEGER NOT NULL,
                stop_reason TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(task_id,revision,generation))''')
            db.execute('INSERT INTO refill_execution_stops VALUES (?,?,?,?,?)',
                       (self.task_id, task.confirmed_revision, task.run_generation,
                        'NO_PROGRESS', '2026-11-04T00:00:00+00:00'))
        repo.finalize_refill_stop(f.actor, self.task_id, 'NO_PROGRESS')
        delivery = ImageDeliveryService(repo, self.gateway())
        current = repo.get_current_result_set(f.actor, self.task_id)
        package, _ = delivery.prepare(f.actor, self.task_id,
                                      expected_task_version=self.task().task_version,
                                      result_set_digest=current['result_set_digest'])
        return repo, delivery, package

    def test_short_group_packages_per_group_folders_as_partial(self):
        from io import BytesIO
        from zipfile import ZipFile
        from dsh_retrieval.models import RunStatus
        f = self.fixture
        repo, delivery, package = self.stop_and_package()
        manifest = package['manifest']
        self.assertEqual((manifest['schema'], manifest['completion'], manifest['gap'],
                          manifest['stop_reason']), ('image-delivery@3', 'partial', 1, 'NO_PROGRESS'))
        self.assertEqual([(g['label'], g['directory'], g['packaged_file_count'], g['target_count'], g['gap'])
                          for g in manifest['groups']],
                         [('狗', 'images/01-狗', 2, 3, 1), ('猫', 'images/02-猫', 1, 1, 0)])
        published, _ = delivery.publish(f.actor, self.task_id, package_id=package['package_id'],
                                        manifest_digest=package['manifest_digest'],
                                        expected_task_version=package['current_version'],
                                        action='confirm')
        self.assertEqual(published['status'], 'published')
        self.assertEqual(self.task().run_status, RunStatus.PARTIAL)
        content = delivery.download(f.actor, self.task_id, package['package_id'])
        with ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
            self.assertEqual(names, ['images/01-狗/0001.png', 'images/01-狗/0002.png',
                                     'images/02-猫/0001.png', 'manifest.json'])
            groups = {g['group_id']: g['directory'] for g in manifest['groups']}
            for member in manifest['members']:
                self.assertTrue(member['file'].startswith(groups[member['group_id']] + '/'))
                self.assertEqual(archive.read(member['file']),
                                 self.bytes_by_ref[repo.get_published_candidate(
                                     f.actor, self.task_id, manifest['operation_id'],
                                     member['candidate_id'])[1]])
        # The published result and package must still read back after a restart.
        restarted = self.repo()
        self.assertEqual(restarted.get_current_result_set(f.actor, self.task_id)['kind'], 'delivery')

    def test_unallocated_multi_group_task_is_still_refused(self):
        from dsh_retrieval.delivery import ImageDeliveryService
        repo, _, _ = self.publish_all_passing()
        task = self.task()
        proposal = json.loads(task.confirmed_proposal_json)
        for group in proposal['query_spec']['grouped_expansion']['groups']:
            group['delivery_target'] = None
        delivery = ImageDeliveryService(repo, self.gateway())
        with mock_task_proposal(repo, proposal):
            with self.assertRaises(ServiceError) as raised:
                delivery.prepare(self.fixture.actor, self.task_id,
                                 expected_task_version=task.task_version,
                                 result_set_digest='sha256:' + '0' * 64)
        self.assertEqual(raised.exception.code, 'FEATURE_NOT_SUPPORTED')


def mock_task_proposal(repo, proposal):
    from dataclasses import replace
    from unittest import mock
    real_get = repo.tasks.get

    def get(actor, task_id):
        task = real_get(actor, task_id)
        return replace(task, confirmed_proposal_json=json.dumps(proposal, ensure_ascii=False))
    return mock.patch.object(repo.tasks, 'get', get)


class DeliveryCostTests(GroupedDeliveryTests):
    def test_download_rebuilds_lineage_a_bounded_number_of_times(self):
        # A real 10-image download re-ran the full rebuild per image (~150 s).
        from unittest import mock
        f = self.fixture
        repo, delivery, package = self.stop_and_package()
        delivery.publish(f.actor, self.task_id, package_id=package['package_id'],
                         manifest_digest=package['manifest_digest'],
                         expected_task_version=package['current_version'], action='confirm')
        real, calls = repo._rebuild_result_set, []

        def counted(*args, **kwargs):
            if kwargs.get('seen') is None and len(args) < 11:
                calls.append(1)  # top-level only, not the recursion into parents
            return real(*args, **kwargs)
        with mock.patch.object(repo, '_rebuild_result_set', counted):
            delivery.download(f.actor, self.task_id, package['package_id'])
        self.assertLessEqual(len(calls), 3, 'independent of the number of images')
