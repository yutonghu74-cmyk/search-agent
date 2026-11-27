from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from dsh_retrieval.recall_strategy import initial_plan_ids, rank_refill_positions
from dsh_retrieval.refill_planning import build_refill_position, RefillReservation


class RecallStrategyTest(unittest.TestCase):
    def position(self, library, query='dog', cursor='next'):
        return build_refill_position(library_id=library, index_version='v1',
                                     query=query, parameters={}, cursor=cursor)

    def reservation(self, position, iteration):
        return RefillReservation('r'+str(iteration), 'task', 1, 1, 1, iteration,
            position.library_id, position.fingerprint, 'policy', datetime.now(timezone.utc),
            position.binding_json)

    def round(self, iteration, gain, count=10, outcome='result'):
        return dict(iteration=iteration, outcome=outcome,
                    new_unique_accepted=gain, candidate_count=count)

    def test_initial_subset_retains_every_query_without_fanning_out(self):
        def member(branch, plan):
            return SimpleNamespace(branch_id=branch, plan=SimpleNamespace(
                request=SimpleNamespace(plan_id=plan)))
        approved = SimpleNamespace(members=(member('original', 'best'),
            member('original', 'fallback'), member('alias', 'alias-best')))
        self.assertEqual(initial_plan_ids(approved), frozenset({'best', 'alias-best'}))

    def test_positive_unique_yield_continues_even_if_alternative_is_shallower(self):
        a, b = self.position('a'), self.position('b')
        ranked, reasons = rank_refill_positions((b, a), (self.reservation(a, 1),),
                                                [self.round(1, 2)])
        self.assertEqual(ranked, (a, b))
        self.assertEqual(reasons[a.fingerprint]['effective_yield'], .2)
        self.assertEqual(reasons[a.fingerprint]['reason'], 'continue_productive_branch')

    def test_zero_gain_switches_to_approved_alternative_not_historical_average(self):
        a, b = self.position('a'), self.position('b')
        reservations = (self.reservation(a, 1), self.reservation(a, 2))
        ranked, reasons = rank_refill_positions((a, b), reservations,
            [self.round(1, 8), self.round(2, 0)])
        self.assertEqual(ranked, (b, a))
        self.assertEqual(reasons[b.fingerprint]['reason'], 'probe_approved_alternative')

    def test_empty_round_switches_query_and_pending_is_not_failure(self):
        a, b = self.position('a'), self.position('a', 'puppy')
        reservations = (self.reservation(a, 1), self.reservation(b, 2))
        ranked, _ = rank_refill_positions((a, b), reservations,
            [self.round(1, 0, 0, 'empty'), self.round(2, None, 0, 'pending')])
        self.assertEqual(ranked, (b, a))

    def test_missing_legacy_binding_does_not_invent_evidence(self):
        a, b = self.position('a'), self.position('b')
        reservation = replace(self.reservation(a, 1), position_binding_json=None)
        self.assertEqual(rank_refill_positions((b, a), (reservation,),
            [self.round(1, 8)])[0], (b, a))

    def test_reordering_uses_only_supplied_positions_and_is_restart_deterministic(self):
        a, b, other = self.position('a'), self.position('b'), self.position('not-approved')
        reservations = (self.reservation(a, 1), self.reservation(other, 2))
        rounds = [self.round(1, 1), self.round(2, 10)]
        before = rank_refill_positions((a, b), reservations, rounds)
        after = rank_refill_positions((a, b), tuple(reservations), list(rounds))
        self.assertEqual(before, after)
        self.assertEqual(set(before[0]), {a, b})

    def test_other_branch_completion_does_not_erase_zero_yield_state(self):
        a, b, c = self.position('a'), self.position('b'), self.position('c')
        reservations = tuple(self.reservation(p, i) for i, p in enumerate((a, a, b), 1))
        rounds = [self.round(1, 9), self.round(2, 0), self.round(3, 1)]
        ranked, reasons = rank_refill_positions((a, b, c), reservations, rounds)
        self.assertEqual(ranked, (b, c, a))
        self.assertEqual(reasons[a.fingerprint]['reason'], 'retry_low_yield_branch')
        self.assertEqual(reasons[a.fingerprint]['last_new_unique_accepted'], 0)
        # A later measured recovery makes the branch productive again.
        ranked, reasons = rank_refill_positions((b, a, c),
            reservations + (self.reservation(a, 4),), rounds + [self.round(4, 8)])
        self.assertEqual(ranked[0], a)
        self.assertEqual(reasons[a.fingerprint]['reason'], 'continue_productive_branch')


class CleaningFeedbackStrategyTest(unittest.TestCase):
    position = RecallStrategyTest.position
    reservation = RecallStrategyTest.reservation
    round = RecallStrategyTest.round
    def report(self, entries):
        return {'complete': True, 'mapping_complete': True, 'cleaning': {'status': 'observed'},
            'branches': [{'query_text': 'dog', 'sources': [
                {'library_id': library, 'binding_id': None, 'index_version': 'v1', 'plan_id': None,
                 'committed_pages': pages, 'cleaning': dict(effective_unique=gain, candidate_count=10,
                     unknown=unknown, unassessed=missing, uncertain_coverage=bool(unknown or missing))}
                for library, gain, pages, unknown, missing in entries]}]}

    def test_first_cleaning_yield_prioritizes_productive_approved_source(self):
        a, b = self.position('a'), self.position('b')
        report = self.report([('a', 3, 1, 0, 0), ('b', 0, 0, 0, 0)])
        ranked, reasons = rank_refill_positions((b, a), (), [], recall=report)
        self.assertEqual(ranked, (a, b))
        self.assertEqual(reasons[a.fingerprint]['evidence_source'], 'published_cleaning_attribution')
        self.assertEqual(reasons[a.fingerprint]['completed_rounds'], 0)

    def test_known_failure_probes_alternative_but_unknown_never_counts_as_zero(self):
        a, b = self.position('a'), self.position('b')
        report = self.report([('a', 0, 1, 0, 0), ('b', 0, 0, 0, 0)])
        self.assertEqual(rank_refill_positions((a, b), (), [], recall=report)[0], (b, a))
        report = self.report([('a', 0, 1, 1, 0), ('b', 0, 0, 0, 0)])
        ranked, reasons = rank_refill_positions((a, b), (self.reservation(a, 1),),
            [self.round(1, 0)], recall=report)
        self.assertEqual(ranked, (a, b))
        self.assertIsNone(reasons[a.fingerprint]['last_new_unique_accepted'])

    def test_new_round_yield_replaces_initial_attribution_without_double_counting(self):
        a, b = self.position('a'), self.position('b')
        report = self.report([('a', 8, 1, 0, 0), ('b', 0, 0, 0, 0)])
        ranked, reasons = rank_refill_positions((a, b), (self.reservation(a, 1),),
            [self.round(1, 0)], recall=report)
        self.assertEqual(ranked, (b, a))
        self.assertEqual(reasons[a.fingerprint]['new_unique_accepted'], 0)
        self.assertEqual(reasons[a.fingerprint]['completed_rounds'], 1)
