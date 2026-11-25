"""Owner-facing planning boundary for one approved refill iteration."""

import json

from .errors import ServiceError
from .expansion_models import delivery_targets
from .models import Actor
from .query_branches import compile_query_branches
from .refill_planning import decide_refill, parse_refill_position
from .refill_planning import normalize_refill_limits
from .service import require_key


class ConfirmedRefillService:
    """Preview and confirm the next server-owned position without redoing terms."""

    def __init__(self, tasks, results, runtime):
        if not callable(getattr(tasks, "get", None)):
            raise TypeError("task repository required")
        if not callable(getattr(results, "get_current_result_set", None)):
            raise TypeError("current result reader required")
        if runtime is None or not hasattr(runtime, "adapter"):
            raise TypeError("refill runtime required")
        self.tasks = tasks
        self.results = results
        self.runtime = runtime

    @staticmethod
    def _policy(task):
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            policy = proposal["query_spec"]["refill_policy"]
            node_mode = proposal["node_modes"]["refill"]
        except (TypeError, ValueError, KeyError):
            raise ServiceError("TASK_CORRUPT", "Confirmed refill policy is unavailable", 500) from None
        return policy, node_mode

    @staticmethod
    def _limits(task):
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            return normalize_refill_limits(proposal["query_spec"].get("refill_limits"))
        except (TypeError, ValueError, KeyError):
            raise ServiceError("TASK_CORRUPT", "Confirmed refill limits are unavailable", 500) from None

    @staticmethod
    def _quota_focus(task, result, positions):
        """Keep only positions that can still close a short group, neediest first.

        A full group's queries can only add reserve images, which spend VLM
        calls without moving the gap. Positions are matched to groups through
        the confirmed branch query text, the same text every adapter searches,
        so this adds no state and leaves position fingerprints unchanged.
        Returns (positions, per-group view, groups per fingerprint); a task
        without quotas gets its positions back untouched.
        """
        spec = json.loads(task.confirmed_proposal_json)["query_spec"]
        grouped = spec.get("grouped_expansion")
        if not delivery_targets(grouped):
            return positions, [], {}
        labels = {group["group_id"]: group["primary_label"] for group in grouped["groups"]}
        rows = result.get("group_quotas") or ()
        gaps = {row.group_id: row.gap for row in rows}
        if set(gaps) != set(labels):
            raise ServiceError("REFILL_PLAN_INVALID", "Current group quotas are unavailable", 409)
        by_query = {}
        for branch in compile_query_branches(spec)["branches"]:
            by_query[branch["query_text"]] = {origin["group_id"] for origin in branch["origins"]
                                              if origin["group_id"] is not None}
        position_groups, ranked = {}, []
        for index, position in enumerate(positions):
            groups = by_query.get(parse_refill_position(position)["query"], set())
            position_groups[position.fingerprint] = sorted(groups)
            need = max((gaps[group] for group in groups), default=0)
            if need:
                ranked.append((-need, index, position))
        view = [{"group_id": group_id, "label": labels[group_id], "gap": gaps[group_id]}
                for group_id in labels]
        return tuple(position for _, _, position in sorted(ranked, key=lambda row: row[:2])), \
            view, {key: [labels[group] for group in value] for key, value in position_groups.items()}

    def preview(self, actor, task_id):
        task = self.tasks.get(actor, task_id)
        result = self.results.get_current_result_set(actor, task_id)
        policy, node_mode = self._policy(task)
        limits = self._limits(task)
        evidence = self.runtime.reservations.execution_evidence(actor, task_id)
        # Expose measured effective output, not raw file counts or an estimated
        # recall percentage. Call budgets are an execution cost proxy.
        completed = [row for row in evidence['rounds'] if row['outcome'] in {'empty', 'result'}]
        seen = sum(row.get('candidate_count') or 0 for row in completed)
        gained = sum(row.get('new_unique_accepted') or 0 for row in completed)
        evidence = {**evidence, 'effective_yield': gained / seen if seen else None,
                    'new_unique_accepted': gained,
                    'remaining_vlm_calls': (None if limits['max_vlm_calls'] is None else
                        max(0, limits['max_vlm_calls'] - evidence['vlm_calls'])),
                    'remaining_rounds': (None if limits['max_rounds'] is None else
                        max(0, limits['max_rounds'] - evidence['issued_rounds']))}
        positions = self.runtime.adapter.list_refill_positions(
            actor, task_id, revision=task.confirmed_revision,
            generation=task.run_generation)
        reservations = self.runtime.reservations.list_current(actor, task_id)
        issued = frozenset(item.position_fingerprint for item in reservations)
        # Once a refill page commits, its cursor is no longer among the next
        # search positions, but its manifest/cleaning intake may still be
        # running. Do not announce a budget stop while that issued iteration is
        # unfinished.
        pending_iterations = {row['iteration'] for row in evidence['rounds']
                              if row['outcome'] == 'pending'}
        active = next((item for item in reservations
                       if item.iteration in pending_iterations), None)
        if active is None:
            active = next((item for item in reservations
                           if item.position_fingerprint in {
                               p.fingerprint for p in positions}), None)
        if active is not None and evidence["stop_reason"] is None:
            return {
                "task_id": task_id, "revision": task.confirmed_revision,
                "generation": task.run_generation, "task_version": task.task_version,
                "accepted_count": result["accepted_count"], "target_count": task.target_count,
                "gap": max(0, task.target_count - result["accepted_count"]),
                "action": "wait", "stop_reason": None, "next_position": None,
                "active_reservation_id": active.reservation_id,
                "iteration": active.iteration,
                "execution": {**evidence, **limits},
            }
        if evidence["stop_reason"] is not None:
            return {
                "task_id": task_id, "revision": task.confirmed_revision,
                "generation": task.run_generation, "task_version": task.task_version,
                "accepted_count": result["accepted_count"], "target_count": task.target_count,
                "gap": max(0, task.target_count - result["accepted_count"]),
                "action": "partial_delivery", "stop_reason": evidence["stop_reason"],
                "next_position": None, "active_reservation_id": None, "iteration": None,
                "execution": {**evidence, **limits},
            }
        remaining_batches = (None if limits["max_rounds"] is None else
                             max(0, limits["max_rounds"] - evidence["issued_rounds"]))
        focused, group_gaps, position_groups = self._quota_focus(task, result, positions)
        from .recall_strategy import rank_refill_positions
        focused, strategy_reasons = rank_refill_positions(focused, reservations, evidence['rounds'])
        if group_gaps:
            needs = {r['label']: r['gap'] for r in group_gaps}
            focused = tuple(sorted(focused, key=lambda p: -max(
                (needs[label] for label in position_groups.get(p.fingerprint, ())), default=0)))
        try:
            decision = decide_refill(
                target_count=task.target_count,
                accepted_count=result["accepted_count"], policy=policy,
                node_mode=node_mode, positions=focused,
                issued_fingerprints=issued,
                remaining_batches=remaining_batches,
                stagnant_rounds=evidence["stagnant_rounds"],
                no_progress_limit=limits["no_progress_rounds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError("REFILL_PLAN_INVALID",
                               "Current refill evidence is invalid", 409) from exc
        next_view = None
        if decision.next_position is not None:
            binding = parse_refill_position(decision.next_position)
            next_view = {
                'selection': strategy_reasons[decision.next_position.fingerprint],
                "library_id": decision.next_position.library_id,
                "position_fingerprint": decision.next_position.fingerprint,
                "query": binding["query"],
                "index_version": binding["index_version"],
                "position": binding["position"],
                "candidate_window_cap": binding['parameters'].get('batch_size'),
                "progression": "approved_query_pagination",
            }
            spec = json.loads(task.confirmed_proposal_json)['query_spec']
            next_view['query_origins'] = [origin
                for branch in compile_query_branches(spec)['branches']
                if branch['query_text'] == binding['query'] for origin in branch['origins']]
            if group_gaps:
                next_view["group_labels"] = position_groups.get(
                    decision.next_position.fingerprint, [])
        view = {
            "task_id": task_id, "revision": task.confirmed_revision,
            "generation": task.run_generation, "task_version": task.task_version,
            "accepted_count": result["accepted_count"], "target_count": task.target_count,
            "gap": decision.gap, "action": decision.action,
            "stop_reason": decision.stop_reason, "next_position": next_view,
            "active_reservation_id": None, "iteration": None,
            "execution": {**evidence, **limits},
        }
        if group_gaps:
            view["group_gaps"] = group_gaps
        return view

    def confirm_next(self, actor, task_id, *, expected_revision, expected_generation,
                     expected_task_version, position_fingerprint, action, key):
        require_key(key)
        if action != "confirm":
            raise ServiceError("CONFIRMATION_REQUIRED",
                               "Owner confirmation is required for this refill iteration", 422)
        task = self.tasks.get(actor, task_id)
        if (expected_revision, expected_generation, expected_task_version) != (
                task.confirmed_revision, task.run_generation, task.task_version):
            raise ServiceError("REVISION_CONFLICT", "Refill preview changed", 409)
        existing = next((item for item in self.runtime.reservations.list_current(
            actor, task_id) if item.position_fingerprint == position_fingerprint), None)
        if existing is not None:
            dispatched = self.runtime.dispatch_reservation(existing.reservation_id)
            return {
                "status": dispatched["status"], "replayed": True,
                "reservation_id": existing.reservation_id,
                "iteration": existing.iteration, "revision": existing.revision,
                "generation": existing.generation,
                "position_fingerprint": existing.position_fingerprint,
            }
        view = self.preview(actor, task_id)
        next_view = view["next_position"]
        if (view["action"] not in {"await_owner", "search_more"}
                or next_view is None
                or next_view["position_fingerprint"] != position_fingerprint):
            raise ServiceError("REFILL_POSITION_NOT_APPROVED",
                               "Refill position changed; reload the preview", 409)
        position = next((item for item in self.runtime.adapter.list_refill_positions(
            actor, task_id, revision=expected_revision, generation=expected_generation)
            if item.fingerprint == position_fingerprint), None)
        if position is None:
            raise ServiceError("REFILL_POSITION_NOT_APPROVED",
                               "Refill position changed; reload the preview", 409)
        reservation, replayed = self.runtime.reservations.reserve(
            actor, task_id, position, expected_revision=expected_revision,
            expected_generation=expected_generation,
            expected_task_version=expected_task_version)
        dispatched = self.runtime.dispatch_reservation(reservation.reservation_id)
        return {
            "status": dispatched["status"], "replayed": replayed,
            "reservation_id": reservation.reservation_id,
            "iteration": reservation.iteration, "revision": reservation.revision,
            "generation": reservation.generation,
            "position_fingerprint": reservation.position_fingerprint,
        }

    def finalize_stop(self, actor, task_id, reason):
        self.runtime.reservations.record_execution_stop(actor, task_id, reason)
        finalize = getattr(self.results, "finalize_refill_stop", None)
        if callable(finalize):
            finalize(actor, task_id, reason)

    def finalize_pending_stops(self):
        pending = getattr(self.runtime.reservations, "pending_stop_finalizations", None)
        if not callable(pending):
            return 0
        count = 0
        for item in pending():
            actor = Actor(item["tenant_id"], item["owner_user_id"])
            self.finalize_stop(actor, item["task_id"], item["stop_reason"])
            count += 1
        return count

    def advance_auto_event(self, actor, event):
        """Consume one durable result-publication event within confirmed auto scope."""
        required = {"task_id", "revision", "generation", "task_version", "result_set_id"}
        if not isinstance(event, dict) or not required <= set(event):
            raise ValueError("invalid auto refill event")
        task = self.tasks.get(actor, event["task_id"])
        if (task.confirmed_revision, task.run_generation, task.task_version) != (
                event["revision"], event["generation"], event["task_version"]):
            return {"status": "superseded", "task_id": event["task_id"]}
        policy, node_mode = self._policy(task)
        if (policy.get("mode") != "maintain_target"
                or policy.get("progression") != "auto" or node_mode != "auto"):
            return {"status": "superseded", "task_id": event["task_id"]}
        current = self.results.get_current_result_set(actor, event["task_id"])
        if (current.get("result_set_id") != event["result_set_id"]
                or current.get("as_of_task_version") != event["task_version"]):
            return {"status": "superseded", "task_id": event["task_id"]}
        if current.get("operation_id"):
            self.runtime.reservations.complete_result_iteration(
                actor, current["operation_id"], current["accepted_count"])
        view = self.preview(actor, event["task_id"])
        if view["action"] == "wait" and view["active_reservation_id"]:
            dispatched = self.runtime.dispatch_reservation(view["active_reservation_id"])
            return {"status": dispatched["status"], "replayed": True,
                    "reservation_id": view["active_reservation_id"],
                    "iteration": view["iteration"]}
        if view["action"] != "search_more" or view["next_position"] is None:
            if view["stop_reason"] in {"EXECUTION_LIMIT_REACHED", "NO_PROGRESS",
                                       "SEARCH_STRATEGIES_EXHAUSTED"}:
                self.finalize_stop(actor, event["task_id"], view["stop_reason"])
            return {"status": "stopped", "action": view["action"],
                    "stop_reason": view["stop_reason"], "gap": view["gap"]}
        position_fingerprint = view["next_position"]["position_fingerprint"]
        position = next((item for item in self.runtime.adapter.list_refill_positions(
            actor, event["task_id"], revision=event["revision"],
            generation=event["generation"])
            if item.fingerprint == position_fingerprint), None)
        if position is None:
            raise ServiceError("REFILL_POSITION_NOT_APPROVED",
                               "Automatic refill position changed", 409)
        reservation, replayed = self.runtime.reservations.reserve(
            actor, event["task_id"], position,
            expected_revision=event["revision"],
            expected_generation=event["generation"],
            expected_task_version=event["task_version"])
        self.runtime.reservations.record_iteration_baseline(
            actor, reservation.reservation_id, current["accepted_count"])
        dispatched = self.runtime.dispatch_reservation(reservation.reservation_id)
        return {"status": dispatched["status"], "replayed": replayed,
                "reservation_id": reservation.reservation_id,
                "iteration": reservation.iteration,
                "position_fingerprint": reservation.position_fingerprint}

    def advance_empty_intake(self, actor, event):
        """Record an empty completed iteration and continue only within limits."""
        required = {"reservation_id", "task_id", "revision", "generation", "task_version"}
        if not isinstance(event, dict) or not required <= set(event):
            raise ValueError("invalid empty refill intake event")
        current = self.results.get_current_result_set(actor, event["task_id"])
        self.runtime.reservations.complete_empty_iteration(
            actor, event["reservation_id"], current["accepted_count"])
        task = self.tasks.get(actor, event["task_id"])
        synthetic = {"task_id": task.task_id, "revision": task.confirmed_revision,
                     "generation": task.run_generation, "task_version": task.task_version,
                     "result_set_id": current["result_set_id"]}
        return self.advance_auto_event(actor, synthetic)
