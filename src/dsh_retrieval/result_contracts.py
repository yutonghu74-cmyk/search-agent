"""Pure image result previews and cumulative exact-content merge primitives."""

from dataclasses import dataclass, replace

from .multi_rule_contracts import (ApprovedCleaningJob, CleaningJobResult,
                                   require_complete_cleaning_result)
from .query_spec import digest
from .refill_planning import decide_refill
from .historical_dedup import merge_rows
from .near_duplicates import (NearDuplicateDecision, NearDuplicatePair,
                              PerceptualFingerprint,
                              build_near_duplicate_clusters)


@dataclass(frozen=True, slots=True)
class FusedSourceEvidence:
    candidate_id: str
    binding_id: str
    library_id: str
    index_version: str
    vector_id: str | int
    score: float


@dataclass(frozen=True, slots=True)
class ExactRepresentative:
    candidate_id: str
    asset_version: str
    content_digest: str
    candidate_ids: tuple[str, ...] = ()
    sources: tuple[FusedSourceEvidence, ...] = ()
    fingerprint: PerceptualFingerprint | None = None
    # Groups whose check this content passed, and the one group it counts for.
    group_ids: tuple[str, ...] = ()
    assigned_group_id: str | None = None


@dataclass(frozen=True, slots=True)
class GroupQuotaProgress:
    group_id: str
    target_count: int
    accepted_count: int
    # May overlap across groups: a surplus image can qualify for several.
    eligible_reserve_count: int
    gap: int


@dataclass(frozen=True, slots=True)
class ResultExclusionDecision:
    """Owner rejection applied to one result version, never to shared media."""

    scope: str
    source_candidate_id: str
    candidate_ids: tuple[str, ...]
    reason: str

    def __post_init__(self):
        if (self.scope not in {"candidate", "near_duplicate_cluster"}
                or not isinstance(self.source_candidate_id, str)
                or not self.source_candidate_id
                or not isinstance(self.candidate_ids, tuple)
                or not self.candidate_ids
                or tuple(sorted(set(self.candidate_ids))) != self.candidate_ids
                or self.source_candidate_id not in self.candidate_ids
                or (self.scope == "candidate"
                    and self.candidate_ids != (self.source_candidate_id,))
                or not isinstance(self.reason, str)
                or not 1 <= len(self.reason.strip()) <= 500
                or self.reason != self.reason.strip()):
            raise ValueError("invalid result exclusion decision")


@dataclass(frozen=True, slots=True)
class ManualReviewDecision:
    """Owner verdict for one model-unknown candidate in one result lineage."""

    source_operation_id: str
    candidate_id: str
    asset_version: str
    action: str
    reason: str
    group_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if (not all(isinstance(value, str) and value for value in (
                self.source_operation_id, self.candidate_id, self.asset_version))
                or self.action not in {"accept", "reject"}
                or not isinstance(self.reason, str)
                or not 1 <= len(self.reason.strip()) <= 500
                or self.reason != self.reason.strip()
                or not isinstance(self.group_ids, tuple)
                or tuple(sorted(set(self.group_ids))) != self.group_ids
                or any(not isinstance(value, str) or not value
                       for value in self.group_ids)
                or self.action == "reject" and self.group_ids):
            raise ValueError("invalid manual review decision")


@dataclass(frozen=True, slots=True)
class ExactResultSetPreview:
    operation_id: str
    generation: int
    task_version: int
    cleaning_result_digest: str
    target_count: int
    accepted: tuple[ExactRepresentative, ...]
    reserve: tuple[ExactRepresentative, ...]
    duplicate_count: int
    fail_count: int
    unknown_count: int
    gap: int
    stop_reason: str
    parent_result_set_id: str | None = None
    parent_result_set_digest: str | None = None
    source_operation_ids: tuple[str, ...] = ()
    near_duplicate_pairs: tuple[NearDuplicatePair, ...] = ()
    near_duplicate_decisions: tuple[NearDuplicateDecision, ...] = ()
    group_quotas: tuple[GroupQuotaProgress, ...] = ()
    exclusion_decisions: tuple[ResultExclusionDecision, ...] = ()
    manual_review_decisions: tuple[ManualReviewDecision, ...] = ()
    # None preserves the legacy rule where near-duplicate decision count was
    # the publication mutation count. New exclusion-aware lineages record it
    # explicitly so inherited decisions do not inflate a later batch version.
    publication_mutation_count: int | None = None
    historical_reference_digest: str | None = None
    historical_exclusions: tuple = ()

    def __post_init__(self):
        import re
        if self.historical_reference_digest is None:
            if self.historical_exclusions:
                raise ValueError('historical exclusions require a reference binding')
            return
        if (not isinstance(self.historical_reference_digest, str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', self.historical_reference_digest)
                or type(self.historical_exclusions) is not tuple):
            raise ValueError('invalid historical reference binding')
        for content, ids, refs in self.historical_exclusions:
            if (not re.fullmatch(r'sha256:[0-9a-f]{64}', content)
                    or type(ids) is not tuple or not ids or type(refs) is not tuple or not refs
                    or any(not isinstance(v, str) or not v for v in ids + refs)
                    or ids != tuple(sorted(set(ids))) or refs != tuple(sorted(set(refs)))):
                raise ValueError('invalid historical exclusion evidence')
        if self.historical_exclusions != merge_rows(self.historical_exclusions):
            raise ValueError('noncanonical historical exclusions')

    @property
    def accepted_count(self):
        return len(self.accepted)

    @property
    def user_excluded_count(self):
        return sum(len(item.candidate_ids) for item in self.exclusion_decisions)

    @property
    def result_set_digest(self):
        if self.historical_reference_digest is not None:
            return digest({'schema_version': 'exact-result-preview@9',
                'base_digest': replace(self, historical_reference_digest=None,
                                       historical_exclusions=()).result_set_digest,
                'historical_reference_digest': self.historical_reference_digest,
                'historical_exclusions': self.historical_exclusions})
        if self.manual_review_decisions:
            return digest({
                "schema_version": "exact-result-preview@8",
                "base": _preview_payload(self),
                "exclusion_decisions": [_exclusion_payload(item)
                                        for item in self.exclusion_decisions],
                "manual_review_decisions": [_manual_review_payload(item)
                                            for item in self.manual_review_decisions],
            })
        if self.exclusion_decisions:
            return digest({
                "schema_version": "exact-result-preview@7",
                "base": _preview_payload(self),
                "exclusion_decisions": [_exclusion_payload(item)
                                        for item in self.exclusion_decisions],
            })
        if self.group_quotas:
            return digest({
                "schema_version": "exact-result-preview@6",
                "operation_id": self.operation_id, "generation": self.generation,
                "task_version": self.task_version,
                "cleaning_result_digest": self.cleaning_result_digest,
                "target_count": self.target_count,
                "accepted": [_quota_ref_payload(item) for item in self.accepted],
                "reserve": [_quota_ref_payload(item) for item in self.reserve],
                "duplicate_count": self.duplicate_count, "fail_count": self.fail_count,
                "unknown_count": self.unknown_count, "gap": self.gap,
                "stop_reason": self.stop_reason,
                "parent_result_set_id": self.parent_result_set_id,
                "parent_result_set_digest": self.parent_result_set_digest,
                "source_operation_ids": list(self.source_operation_ids),
                "group_quotas": [{"group_id": row.group_id,
                                  "target_count": row.target_count,
                                  "accepted_count": row.accepted_count,
                                  "eligible_reserve_count": row.eligible_reserve_count,
                                  "gap": row.gap} for row in self.group_quotas]})
        review_enriched = bool(self.near_duplicate_decisions) or any(
            item.fingerprint is not None and (
                item.fingerprint.width is not None
                or item.fingerprint.sharpness is not None)
            for item in self.accepted + self.reserve)
        near_enriched = bool(self.near_duplicate_pairs) or any(
            item.fingerprint is not None for item in self.accepted + self.reserve)
        enriched = near_enriched or any(item.candidate_ids or item.sources
                                        for item in self.accepted + self.reserve)
        schema = ("exact-result-preview@5" if review_enriched else
                  "exact-result-preview@4" if near_enriched else
                  "exact-result-preview@3" if enriched else
                  "exact-result-preview@2" if self.source_operation_ids
                  or self.parent_result_set_id is not None else "exact-result-preview@1")
        payload = {"schema_version": schema,
                       "operation_id": self.operation_id,
                       "generation": self.generation,
                       "task_version": self.task_version,
                       "cleaning_result_digest": self.cleaning_result_digest,
                       "target_count": self.target_count,
                       "accepted": [_ref_payload(item, enriched=enriched,
                                                  fingerprinted=near_enriched,
                                                  quality_enriched=review_enriched)
                                    for item in self.accepted],
                       "reserve": [_ref_payload(item, enriched=enriched,
                                                 fingerprinted=near_enriched,
                                                 quality_enriched=review_enriched)
                                   for item in self.reserve],
                       "duplicate_count": self.duplicate_count,
                       "fail_count": self.fail_count,
                       "unknown_count": self.unknown_count,
                       "gap": self.gap,
                       "stop_reason": self.stop_reason}
        if schema in {"exact-result-preview@2", "exact-result-preview@4",
                      "exact-result-preview@5"}:
            payload.update(parent_result_set_id=self.parent_result_set_id,
                           parent_result_set_digest=self.parent_result_set_digest,
                           source_operation_ids=list(self.source_operation_ids))
        if schema in {"exact-result-preview@4", "exact-result-preview@5"}:
            payload["near_duplicate_pairs"] = [
                {"candidate_id": pair.candidate_id,
                 "other_candidate_id": pair.other_candidate_id,
                 "hamming_distance": pair.hamming_distance,
                 "review_threshold": pair.review_threshold,
                 "policy_ref": pair.policy_ref}
                for pair in self.near_duplicate_pairs]
        if schema == "exact-result-preview@5":
            payload["near_duplicate_decisions"] = [
                {"cluster_id": decision.cluster_id,
                 "candidate_ids": list(decision.candidate_ids),
                 "action": decision.action,
                 "representative_candidate_id": decision.representative_candidate_id}
                for decision in self.near_duplicate_decisions]
        return digest(payload)


def _exclusion_payload(item):
    return {"scope": item.scope,
            "source_candidate_id": item.source_candidate_id,
            "candidate_ids": list(item.candidate_ids),
            "reason": item.reason}


def _manual_review_payload(item):
    return {"source_operation_id": item.source_operation_id,
            "candidate_id": item.candidate_id,
            "asset_version": item.asset_version,
            "action": item.action,
            "reason": item.reason,
            "group_ids": list(item.group_ids)}


def _preview_payload(preview):
    """Stable full payload used only by the exclusion-aware schema."""
    return {
        "operation_id": preview.operation_id,
        "generation": preview.generation,
        "task_version": preview.task_version,
        "cleaning_result_digest": preview.cleaning_result_digest,
        "target_count": preview.target_count,
        "accepted": [_quota_ref_payload(item) for item in preview.accepted],
        "reserve": [_quota_ref_payload(item) for item in preview.reserve],
        "duplicate_count": preview.duplicate_count,
        "fail_count": preview.fail_count,
        "unknown_count": preview.unknown_count,
        "gap": preview.gap,
        "stop_reason": preview.stop_reason,
        "parent_result_set_id": preview.parent_result_set_id,
        "parent_result_set_digest": preview.parent_result_set_digest,
        "source_operation_ids": list(preview.source_operation_ids),
        "near_duplicate_pairs": [{
            "candidate_id": pair.candidate_id,
            "other_candidate_id": pair.other_candidate_id,
            "hamming_distance": pair.hamming_distance,
            "review_threshold": pair.review_threshold,
            "policy_ref": pair.policy_ref,
        } for pair in preview.near_duplicate_pairs],
        "near_duplicate_decisions": [{
            "cluster_id": item.cluster_id,
            "candidate_ids": list(item.candidate_ids),
            "action": item.action,
            "representative_candidate_id": item.representative_candidate_id,
        } for item in preview.near_duplicate_decisions],
        "group_quotas": [{
            "group_id": row.group_id,
            "target_count": row.target_count,
            "accepted_count": row.accepted_count,
            "eligible_reserve_count": row.eligible_reserve_count,
            "gap": row.gap,
        } for row in preview.group_quotas],
        "publication_mutation_count": preview.publication_mutation_count,
    }


def _ref_payload(item, *, enriched=False, fingerprinted=False, quality_enriched=False):
    payload = {"candidate_id": item.candidate_id, "asset_version": item.asset_version,
               "content_digest": item.content_digest}
    if enriched:
        payload.update(candidate_ids=list(_candidate_ids(item)),
                       sources=[{"candidate_id": source.candidate_id,
                                 "binding_id": source.binding_id,
                                 "library_id": source.library_id,
                                 "index_version": source.index_version,
                                 "vector_id": source.vector_id,
                                 "score": source.score}
                                for source in item.sources])
    if fingerprinted:
        payload["perceptual_fingerprint"] = (None if item.fingerprint is None else {
            "candidate_id": item.fingerprint.candidate_id,
            "algorithm": item.fingerprint.algorithm,
            "bit_length": item.fingerprint.bit_length,
            "value": item.fingerprint.value})
        if quality_enriched and item.fingerprint is not None:
            payload["perceptual_fingerprint"].update(
                width=item.fingerprint.width, height=item.fingerprint.height,
                sharpness=item.fingerprint.sharpness)
    return payload


def _quota_ref_payload(item):
    return {**_ref_payload(item, enriched=True), "group_ids": list(item.group_ids),
            "assigned_group_id": item.assigned_group_id}


def _representative_key(ref):
    return (ref.candidate_id, ref.asset_version, ref.content_digest)


def _normalize_group_targets(group_targets, target_count):
    """Quotas are one exclusive dimension whose targets sum to the total (§8.6)."""
    if (not isinstance(group_targets, tuple) or not group_targets
            or any(not isinstance(row, tuple) or len(row) != 2
                   or not isinstance(row[0], str) or not row[0]
                   or type(row[1]) is not int or row[1] < 1 for row in group_targets)
            or len({row[0] for row in group_targets}) != len(group_targets)
            or sum(row[1] for row in group_targets) != target_count):
        raise ValueError("group targets must be unique and sum to the total target")
    return group_targets


def allocate_group_quotas(representatives, group_targets):
    """Assign each unique content to at most one group, filling as many as possible.

    Maximum bipartite matching rather than first-come assignment: an image
    qualifying for both groups must not take the only slot a dog-only image
    could fill while the cat group goes short. Input order fixes the result, so
    restart and rebuild reproduce it. Returns (accepted, reserve, progress).
    """
    ordered = tuple(sorted(representatives, key=_representative_key))
    capacity = dict(group_targets)
    members = {group_id: [] for group_id in capacity}

    def place(index, visited):
        for group_id in capacity:
            if group_id not in ordered[index].group_ids or group_id in visited:
                continue
            visited.add(group_id)
            if len(members[group_id]) < capacity[group_id]:
                members[group_id].append(index)
                return True
            for position, other in enumerate(members[group_id]):
                if place(other, visited):
                    members[group_id][position] = index
                    return True
        return False

    for index in range(len(ordered)):
        place(index, set())
    assigned = {index: group_id for group_id, indexes in members.items() for index in indexes}
    accepted = tuple(replace(ref, assigned_group_id=assigned[index])
                     for index, ref in enumerate(ordered) if index in assigned)
    reserve = tuple(replace(ref, assigned_group_id=None)
                    for index, ref in enumerate(ordered) if index not in assigned)
    progress = tuple(GroupQuotaProgress(
        group_id, target, len(members[group_id]),
        sum(group_id in ref.group_ids for ref in reserve),
        target - len(members[group_id])) for group_id, target in group_targets)
    return accepted, reserve, progress


def _candidate_ids(item):
    return item.candidate_ids or (item.candidate_id,)


def _source_key(source):
    return (source.candidate_id, source.library_id, source.binding_id,
            source.index_version, type(source.vector_id).__name__,
            source.vector_id, source.score)


def _merge_representatives(left, right):
    chosen = min((left, right), key=lambda ref: (
        ref.candidate_id, ref.asset_version, ref.content_digest))
    enriched = bool(left.candidate_ids or right.candidate_ids or left.sources or right.sources)
    candidate_ids = (tuple(sorted(set(_candidate_ids(left)) | set(_candidate_ids(right))))
                     if enriched else ())
    sources = tuple(sorted(set(left.sources + right.sources), key=_source_key))
    if (left.fingerprint is not None and right.fingerprint is not None
            and (left.fingerprint.algorithm, left.fingerprint.bit_length,
                 left.fingerprint.value) != (right.fingerprint.algorithm,
                                              right.fingerprint.bit_length,
                                              right.fingerprint.value)):
        raise ValueError("exact-content fingerprints differ")
    source_fingerprint = left.fingerprint or right.fingerprint
    fingerprint = (None if source_fingerprint is None else PerceptualFingerprint(
        chosen.candidate_id, source_fingerprint.algorithm,
        source_fingerprint.bit_length, source_fingerprint.value,
        source_fingerprint.width, source_fingerprint.height,
        source_fingerprint.sharpness))
    return ExactRepresentative(chosen.candidate_id, chosen.asset_version,
                               chosen.content_digest, candidate_ids, sources, fingerprint,
                               tuple(sorted(set(left.group_ids) | set(right.group_ids))))


def build_exact_result_set(job: ApprovedCleaningJob, result: CleaningJobResult,
                           target_count: int, *, legacy_input_only: bool = False,
                           refill_policy=None, node_mode: str = "manual",
                           legacy_lineage: bool = False,
                           source_evidence_by_candidate=None,
                           group_targets=None, whole_video_media_by_candidate=None) -> ExactResultSetPreview:
    """Count only fully passing, exact-content-unique image or whole-video candidates.

    This is a deterministic preview for one fixed batch. It does not substitute
    for current authorization, near-duplicate policy, ranking, cumulative merge,
    or a published business result set.
    """

    require_complete_cleaning_result(job, result)
    if job.recipe.media_type == "video":
        from .whole_video import require_video_candidate_mapping
        require_video_candidate_mapping(job.candidates, whole_video_media_by_candidate)
        if group_targets is not None or any(c.group_ids for c in job.candidates):
            raise ValueError("video quotas are not enabled")
        if result.fingerprints:
            raise ValueError("image fingerprints cannot validate whole-video duplicates")
    elif job.recipe.media_type != "image" or whole_video_media_by_candidate is not None:
        raise ValueError("unsupported result media mapping")
    if type(target_count) is not int or target_count < 1:
        raise ValueError("target_count must be a positive integer")
    source_evidence_by_candidate = ({} if source_evidence_by_candidate is None
                                    else source_evidence_by_candidate)
    if (not isinstance(source_evidence_by_candidate, dict)
            or any(candidate_id not in {item.candidate_id for item in job.candidates}
                   or not isinstance(sources, tuple)
                   or any(type(source) is not FusedSourceEvidence
                          or source.candidate_id != candidate_id for source in sources)
                   for candidate_id, sources in source_evidence_by_candidate.items())):
        raise ValueError("invalid fused source evidence")
    if group_targets is not None:
        group_targets = _normalize_group_targets(group_targets, target_count)

    representatives = {}
    duplicate_count = fail_count = unknown_count = 0
    for candidate, verdict in zip(job.candidates, result.candidates):
        if verdict.verdict == "fail":
            fail_count += 1
        elif verdict.verdict == "unknown":
            unknown_count += 1
        else:
            ref = ExactRepresentative(candidate.candidate_id, candidate.asset_version,
                                      candidate.content_digest,
                                      ((candidate.candidate_id,) if source_evidence_by_candidate else ()),
                                      source_evidence_by_candidate.get(candidate.candidate_id, ()),
                                      group_ids=verdict.passed_group_ids)
            if group_targets is not None and not ref.group_ids:
                # A task-wide pass says nothing about which group it belongs to.
                raise ValueError("quota result has no group verdict")
            old = representatives.get(candidate.content_digest)
            if old is None:
                representatives[candidate.content_digest] = ref
            else:
                duplicate_count += 1
                representatives[candidate.content_digest] = _merge_representatives(old, ref)

    ordered = tuple(sorted(representatives.values(),
                           key=lambda ref: (ref.candidate_id, ref.asset_version, ref.content_digest)))
    if group_targets is None:
        accepted, reserve = ordered[:target_count], ordered[target_count:]
        group_quotas = ()
    else:
        accepted, reserve, group_quotas = allocate_group_quotas(ordered, group_targets)
    # With quotas accepted counts only allocated images, so a surplus in one
    # group cannot hide another group's shortfall in gap or stop reason.
    gap = max(0, target_count - len(accepted))
    stop_reason = _stop_reason(target_count, len(accepted), refill_policy, node_mode)
    # Existing persisted previews include this old reason in their digest.
    # Rebuild them unchanged on read; only newly published previews use the
    # precise fixed-input reason.
    if legacy_input_only and gap:
        stop_reason = "SEARCH_STRATEGIES_EXHAUSTED"
    return ExactResultSetPreview(job.binding.operation_id, job.binding.generation,
                                 job.binding.task_version, result.result_digest,
                                 target_count, accepted, reserve, duplicate_count,
                                 fail_count, unknown_count, gap,
                                 stop_reason, source_operation_ids=(
                                     () if legacy_lineage else (job.binding.operation_id,)),
                                 group_quotas=group_quotas)


def merge_exact_result_sets(parent_result_set_id: str,
                            parent: ExactResultSetPreview,
                            current: ExactResultSetPreview, *, refill_policy=None,
                            node_mode: str = "manual") -> ExactResultSetPreview:
    """Merge one completely verified batch into a verified prior result set.

    Selection remains deterministic across restart and retry. Exact content
    duplicates only count once, while fail/unknown totals remain auditable.
    """
    if (not isinstance(parent_result_set_id, str) or not parent_result_set_id
            or type(parent) is not ExactResultSetPreview
            or type(current) is not ExactResultSetPreview
            or parent.generation != current.generation
            or parent.target_count != current.target_count
            or parent.task_version >= current.task_version
            or current.parent_result_set_id is not None
            or _group_targets(parent) != _group_targets(current)
            or parent.historical_reference_digest != current.historical_reference_digest):
        raise ValueError("incompatible exact result lineage")
    parent_sources = parent.source_operation_ids or (parent.operation_id,)
    current_sources = current.source_operation_ids or (current.operation_id,)
    if (len(set(parent_sources)) != len(parent_sources)
            or len(set(current_sources)) != len(current_sources)
            or set(parent_sources) & set(current_sources)):
        raise ValueError("result source operations must be unique")

    # Old snapshots may contain deferred perceptual-review metadata. New
    # publications intentionally carry only SHA-256 exact-content identity.
    representatives = {
        item.content_digest: ExactRepresentative(
            item.candidate_id, item.asset_version, item.content_digest,
            item.candidate_ids, item.sources, group_ids=item.group_ids)
        for item in parent.accepted + parent.reserve}
    cross_batch_duplicates = 0
    for ref in current.accepted + current.reserve:
        old = representatives.get(ref.content_digest)
        if old is None:
            representatives[ref.content_digest] = ref
            continue
        cross_batch_duplicates += 1
        representatives[ref.content_digest] = _merge_representatives(old, ref)
    excluded_ids = {candidate_id for decision in parent.exclusion_decisions
                    for candidate_id in decision.candidate_ids}
    ordered = tuple(sorted((item for item in representatives.values()
                            if item.candidate_id not in excluded_ids),
                           key=lambda ref: (ref.candidate_id, ref.asset_version,
                                            ref.content_digest)))
    group_targets = _group_targets(current)
    if group_targets:
        # Reallocate from scratch: a new batch can move an earlier image to a
        # different group when that fills more quota overall.
        accepted, reserve, group_quotas = allocate_group_quotas(ordered, group_targets)
    else:
        accepted = ordered[:current.target_count]
        reserve = ordered[current.target_count:]
        group_quotas = ()
    gap = max(0, current.target_count - len(accepted))
    stop_reason = _stop_reason(current.target_count, len(accepted),
                               refill_policy, node_mode)
    return ExactResultSetPreview(
        current.operation_id, current.generation, current.task_version,
        current.cleaning_result_digest, current.target_count, accepted, reserve,
        parent.duplicate_count + current.duplicate_count + cross_batch_duplicates,
        parent.fail_count + current.fail_count,
        parent.unknown_count + current.unknown_count, gap, stop_reason,
        parent_result_set_id=parent_result_set_id,
        parent_result_set_digest=parent.result_set_digest,
        source_operation_ids=parent_sources + current_sources,
        group_quotas=group_quotas,
        exclusion_decisions=parent.exclusion_decisions,
        manual_review_decisions=parent.manual_review_decisions,
        historical_reference_digest=current.historical_reference_digest,
        historical_exclusions=merge_rows(parent.historical_exclusions, current.historical_exclusions),
        publication_mutation_count=0)


def _group_targets(preview):
    return tuple((row.group_id, row.target_count) for row in preview.group_quotas)


def near_duplicate_clusters(preview: ExactResultSetPreview):
    if type(preview) is not ExactResultSetPreview:
        raise TypeError("exact result preview required")
    return build_near_duplicate_clusters(
        preview.near_duplicate_pairs,
        tuple(item.fingerprint for item in preview.accepted + preview.reserve
              if item.fingerprint is not None))


def apply_near_duplicate_decision(preview: ExactResultSetPreview,
                                  decision: NearDuplicateDecision, *,
                                  refill_policy=None, node_mode="manual"):
    """Resolve one whole pending cluster and deterministically recount results."""
    if type(preview) is not ExactResultSetPreview or type(decision) is not NearDuplicateDecision:
        raise TypeError("preview and near-duplicate decision required")
    if preview.group_quotas:
        # Manual near-duplicate review is deferred and has no quota semantics.
        raise ValueError("near-duplicate review does not support group quotas")
    cluster = next((item for item in near_duplicate_clusters(preview)
                    if item.cluster_id == decision.cluster_id), None)
    if cluster is None or cluster.candidate_ids != decision.candidate_ids:
        raise ValueError("near-duplicate cluster is no longer pending")
    all_representatives = preview.accepted + preview.reserve
    by_id = {item.candidate_id: item for item in all_representatives}
    if any(candidate_id not in by_id for candidate_id in decision.candidate_ids):
        raise ValueError("near-duplicate decision member is unavailable")
    removed = set()
    duplicate_count = preview.duplicate_count
    if decision.action == "merge":
        removed = set(decision.candidate_ids) - {decision.representative_candidate_id}
        duplicate_count += len(removed)
    ordered = tuple(sorted((item for item in all_representatives
                            if item.candidate_id not in removed),
                           key=lambda ref: (ref.candidate_id, ref.asset_version,
                                            ref.content_digest)))
    accepted = ordered[:preview.target_count]
    reserve = ordered[preview.target_count:]
    gap = max(0, preview.target_count - len(accepted))
    members = set(decision.candidate_ids)
    remaining_pairs = tuple(pair for pair in preview.near_duplicate_pairs
                            if not (pair.candidate_id in members
                                    and pair.other_candidate_id in members))
    return ExactResultSetPreview(
        preview.operation_id, preview.generation, preview.task_version,
        preview.cleaning_result_digest, preview.target_count, accepted, reserve,
        duplicate_count, preview.fail_count, preview.unknown_count, gap,
        _stop_reason(preview.target_count, len(accepted), refill_policy, node_mode),
        historical_reference_digest=preview.historical_reference_digest,
        historical_exclusions=preview.historical_exclusions,
        parent_result_set_id=preview.parent_result_set_id,
        parent_result_set_digest=preview.parent_result_set_digest,
        source_operation_ids=preview.source_operation_ids,
        near_duplicate_pairs=remaining_pairs,
        near_duplicate_decisions=preview.near_duplicate_decisions + (decision,),
        exclusion_decisions=preview.exclusion_decisions,
        manual_review_decisions=preview.manual_review_decisions,
        publication_mutation_count=(
            (preview.publication_mutation_count
             if preview.publication_mutation_count is not None
             else len(preview.near_duplicate_decisions)) + 1
            if preview.exclusion_decisions else None))


def apply_result_exclusion(preview: ExactResultSetPreview,
                           decision: ResultExclusionDecision, *,
                           refill_policy=None, node_mode="manual"):
    """Exclude an owner-selected item or a whole pending near-duplicate cluster."""
    if (type(preview) is not ExactResultSetPreview
            or type(decision) is not ResultExclusionDecision):
        raise TypeError("preview and result exclusion decision required")
    available = preview.accepted + preview.reserve
    available_ids = {item.candidate_id for item in available}
    if any(candidate_id not in available_ids for candidate_id in decision.candidate_ids):
        raise ValueError("result exclusion member is unavailable")
    if decision.scope == "near_duplicate_cluster":
        cluster = next((item for item in near_duplicate_clusters(preview)
                        if item.candidate_ids == decision.candidate_ids), None)
        if cluster is None:
            raise ValueError("near-duplicate cluster is no longer pending")
    removed = set(decision.candidate_ids)
    ordered = tuple(sorted((item for item in available
                            if item.candidate_id not in removed),
                           key=_representative_key))
    if preview.group_quotas:
        group_targets = tuple((row.group_id, row.target_count)
                              for row in preview.group_quotas)
        accepted, reserve, group_quotas = allocate_group_quotas(
            ordered, group_targets)
    else:
        accepted = ordered[:preview.target_count]
        reserve = ordered[preview.target_count:]
        group_quotas = ()
    gap = max(0, preview.target_count - len(accepted))
    remaining_pairs = tuple(
        pair for pair in preview.near_duplicate_pairs
        if pair.candidate_id not in removed and pair.other_candidate_id not in removed)
    return ExactResultSetPreview(
        preview.operation_id, preview.generation, preview.task_version,
        preview.cleaning_result_digest, preview.target_count, accepted, reserve,
        preview.duplicate_count, preview.fail_count, preview.unknown_count, gap,
        _stop_reason(preview.target_count, len(accepted), refill_policy, node_mode),
        historical_reference_digest=preview.historical_reference_digest,
        historical_exclusions=preview.historical_exclusions,
        parent_result_set_id=preview.parent_result_set_id,
        parent_result_set_digest=preview.parent_result_set_digest,
        source_operation_ids=preview.source_operation_ids,
        near_duplicate_pairs=remaining_pairs,
        near_duplicate_decisions=preview.near_duplicate_decisions,
        group_quotas=group_quotas,
        exclusion_decisions=preview.exclusion_decisions + (decision,),
        manual_review_decisions=preview.manual_review_decisions,
        publication_mutation_count=(
            (preview.publication_mutation_count
             if preview.publication_mutation_count is not None
             else len(preview.near_duplicate_decisions)) + 1))


def apply_manual_review_decision(preview: ExactResultSetPreview,
                                 decision: ManualReviewDecision,
                                 candidate, *, sources=(),
                                 refill_policy=None, node_mode="manual"):
    """Resolve one model-unknown candidate and deterministically recount."""
    if (type(preview) is not ExactResultSetPreview
            or type(decision) is not ManualReviewDecision):
        raise TypeError("preview and manual review decision required")
    if (candidate.candidate_id != decision.candidate_id
            or candidate.asset_version != decision.asset_version
            or preview.unknown_count < 1
            or decision.candidate_id in {
                item.candidate_id for item in preview.accepted + preview.reserve}
            or decision.candidate_id in {
                item.candidate_id for item in preview.manual_review_decisions}):
        raise ValueError("manual review candidate is no longer pending")
    if preview.group_quotas and decision.action == "accept" and not decision.group_ids:
        raise ValueError("manual acceptance requires a passed quota group")
    representatives = {item.content_digest: item
                       for item in preview.accepted + preview.reserve}
    duplicate_count = preview.duplicate_count
    fail_count = preview.fail_count
    if decision.action == "accept":
        reviewed = ExactRepresentative(
            candidate.candidate_id, candidate.asset_version,
            candidate.content_digest,
            (candidate.candidate_id,) if sources else (), tuple(sources),
            group_ids=decision.group_ids)
        existing = representatives.get(reviewed.content_digest)
        if existing is None:
            representatives[reviewed.content_digest] = reviewed
        else:
            duplicate_count += 1
            representatives[reviewed.content_digest] = _merge_representatives(
                existing, reviewed)
    else:
        fail_count += 1
    ordered = tuple(sorted(representatives.values(), key=_representative_key))
    if preview.group_quotas:
        targets = tuple((row.group_id, row.target_count)
                        for row in preview.group_quotas)
        accepted, reserve, group_quotas = allocate_group_quotas(ordered, targets)
    else:
        accepted = ordered[:preview.target_count]
        reserve = ordered[preview.target_count:]
        group_quotas = ()
    gap = max(0, preview.target_count - len(accepted))
    return ExactResultSetPreview(
        preview.operation_id, preview.generation, preview.task_version,
        preview.cleaning_result_digest, preview.target_count, accepted, reserve,
        duplicate_count, fail_count, preview.unknown_count - 1, gap,
        _stop_reason(preview.target_count, len(accepted), refill_policy, node_mode),
        historical_reference_digest=preview.historical_reference_digest,
        historical_exclusions=preview.historical_exclusions,
        parent_result_set_id=preview.parent_result_set_id,
        parent_result_set_digest=preview.parent_result_set_digest,
        source_operation_ids=preview.source_operation_ids,
        near_duplicate_pairs=preview.near_duplicate_pairs,
        near_duplicate_decisions=preview.near_duplicate_decisions,
        group_quotas=group_quotas,
        exclusion_decisions=preview.exclusion_decisions,
        manual_review_decisions=preview.manual_review_decisions + (decision,),
        publication_mutation_count=(
            (preview.publication_mutation_count
             if preview.publication_mutation_count is not None
             else len(preview.near_duplicate_decisions)) + 1))


def _stop_reason(target_count, accepted_count, refill_policy, node_mode):
    policy = (refill_policy if refill_policy is not None else
              {"mode": "input_only", "target_count": target_count,
               "approved_library_ids": [], "progression": "manual"})
    gap = max(0, target_count - accepted_count)
    if gap and policy.get("mode") == "maintain_target":
        # Result publication has no search-position authority. The durable
        # refill coordinator resolves real positions before it either asks the
        # owner or dispatches an already-approved automatic iteration.
        if policy.get("progression") == "manual" or node_mode == "manual":
            return "NEED_USER_INPUT"
        return "REFILL_SCHEDULED"
    return decide_refill(target_count=target_count, accepted_count=accepted_count,
                         policy=policy, node_mode=node_mode).stop_reason
