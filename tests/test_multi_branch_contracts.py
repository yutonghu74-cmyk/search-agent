from copy import deepcopy
from dataclasses import replace
import json
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.integration_models import IndexBinding, QueryVector, SearchRequest
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_branch_contracts import (
    RetrievalPlanSet, validate_plan_set, validate_set_confirmation, validate_member_execution,
)
from dsh_retrieval.physical_query_preparation import PreparedQuery
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.retrieval_contracts import RetrievalScope, RetrievalPlan
from dsh_retrieval.service import InMemoryTaskRepository
from draft_helpers import BODY, make_service, confirmation


class MultiBranchContractTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor('tenant', 'owner')
        service = make_service(InMemoryTaskRepository())
        body = deepcopy(BODY)
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']}])['plan']
        summary, _ = service.create_task(self.actor, 'task', confirmation(service, self.actor, body=body))
        self.task = service.repository.get(self.actor, summary['task_id'])
        scope = RetrievalScope.from_task(self.task)
        preview = compile_query_branches(json.loads(self.task.confirmed_proposal_json)['query_spec'])
        binding = IndexBinding('binding', 'library', 'v1', 'encoder-v1', 2, 'COSINE')
        self.members = tuple(PreparedQuery(
            RetrievalPlan(scope, 1, SearchRequest('plan-' + str(i), 1, binding,
                          QueryVector('encoder-v1', (float(i), 0.1)), 'filter-v1', 10)),
            branch['branch_id'], branch['query_fingerprint'], preview['preview_digest'])
            for i, branch in enumerate(preview['branches']))
        self.plan_set = RetrievalPlanSet(scope, 1, self.members)

    def assert_code(self, code, fn):
        with self.assertRaises(ServiceError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_valid_set_and_confirmation_are_side_effect_free(self):
        before = deepcopy(self.task)
        validate_set_confirmation(self.actor, self.task, self.plan_set, action='confirm',
                                  proposal_digest=self.plan_set.proposal_digest, expected_task_version=1)
        self.assertEqual(self.task, before)
        self.assertEqual(self.plan_set.proposal_digest, deepcopy(self.plan_set).proposal_digest)

    def test_missing_original_empty_and_budget_rejected(self):
        for members, code in ((self.members[1:], 'ORIGINAL_QUERY_REQUIRED'),
                              ((), 'INVALID_PLAN_SET'), (self.members * 22, 'INVALID_PLAN_SET')):
            self.assert_code(code, lambda: validate_plan_set(
                self.actor, self.task, replace(self.plan_set, members=members)))

    def test_duplicate_and_mixed_member_rejected(self):
        self.assert_code('DUPLICATE_QUERY', lambda: validate_plan_set(self.actor, self.task,
            replace(self.plan_set, members=self.members + (self.members[0],))))
        for changed in (replace(self.members[0], logical_preview_digest='changed'),
                        replace(self.members[0], query_fingerprint='changed'),
                        replace(self.members[0], plan=replace(self.members[0].plan, epoch=2))):
            self.assert_code('PLAN_SET_BINDING_MISMATCH', lambda: validate_plan_set(
                self.actor, self.task, replace(self.plan_set, members=(changed,) + self.members[1:])))

    def test_owner_scope_and_digest_fences(self):
        self.assert_code('NOT_FOUND', lambda: validate_plan_set(Actor('tenant', 'other'), self.task, self.plan_set))
        self.assert_code('REVISION_CONFLICT', lambda: validate_plan_set(
            self.actor, replace(self.task, run_generation=2), self.plan_set))
        self.assert_code('PLAN_DIGEST_MISMATCH', lambda: validate_set_confirmation(
            self.actor, self.task, self.plan_set, action='confirm', proposal_digest='changed', expected_task_version=1))
        self.assert_code('CONFIRMATION_REQUIRED', lambda: validate_set_confirmation(
            self.actor, self.task, self.plan_set, action='auto',
            proposal_digest=self.plan_set.proposal_digest, expected_task_version=1))

    def test_membership_and_current_set_execution_fences(self):
        request = self.members[0].plan.request
        def check(current, req=request, plan_id=request.plan_id):
            validate_member_execution(self.actor, self.task, self.plan_set, current,
                                      plan_id=plan_id, request=req)
        check(self.plan_set)
        self.assert_code('PLAN_NOT_CURRENT', lambda: check(None))
        self.assert_code('PLAN_NOT_CURRENT', lambda: check(replace(self.plan_set, epoch=2)))
        self.assert_code('PLAN_REQUEST_MISMATCH', lambda: check(self.plan_set, plan_id='unknown'))
        self.assert_code('PLAN_REQUEST_MISMATCH', lambda: check(self.plan_set, replace(request, batch_size=11)))
        self.assert_code('CURSOR_INVALID', lambda: check(self.plan_set, replace(request, cursor='')))

    def test_member_order_and_physical_changes_change_confirmation_digest(self):
        self.assertNotEqual(self.plan_set.proposal_digest,
                            replace(self.plan_set, members=tuple(reversed(self.members))).proposal_digest)
        changed = replace(self.members[0], plan=replace(self.members[0].plan,
                          request=replace(self.members[0].plan.request, batch_size=11)))
        self.assertNotEqual(self.plan_set.proposal_digest,
                            replace(self.plan_set, members=(changed,) + self.members[1:]).proposal_digest)


if __name__ == '__main__':
    unittest.main()
