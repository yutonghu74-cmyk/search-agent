"""R14 through the real ledger: confirm -> search -> manifest -> recipe -> publish -> restart."""
import json
import unittest

from draft_helpers import BODY, confirmation
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
from dsh_retrieval.vlm_instruction_plan import confirmed_instruction_plan
from dsh_retrieval.vlm_registry import ConfirmedTaskVLMRegistry
from test_first_search_local import FirstSearchLocalTest


SEMANTIC = {'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
            'operator': 'eq', 'value': True, 'hardness': 'hard', 'origin': 'user_confirmed'}


class ExplicitTargetParsingTests(unittest.TestCase):
    LABELS = ['狗', '猫']

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


class QuotaLedgerFlowTests(unittest.TestCase):
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
        steps = repo.step_results(f.actor, self.task_id)['runs'][0]['steps']
        self.assertEqual([step['counts']['input'] for step in steps], [2, 2])
        self.assertEqual([len(step['items']) for step in steps], [2, 2])


if __name__ == '__main__':
    unittest.main()
