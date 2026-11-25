"""Compile durable strategies and execute explicitly approved single-plan pages.

No Harness SDK, automatic confirmation or accepted-count writes. The handoff
port repeats receipt/input/authority checks in the plan-write transaction.
Network work stays outside transactions, behind the approved plan and budget.
"""
from dataclasses import asdict, dataclass, replace
from typing import Protocol

from .agent_contracts import AgentStageTask, RetrievalStrategyProposal, _hash
from .errors import ServiceError
from .integration_models import AccessContext, QueryVector, SearchRequest
from .physical_query_preparation import TextQueryCapability
from .query_spec import digest
from .retrieval_contracts import RetrievalPlan, validate_plan


@dataclass(frozen=True, slots=True)
class RetrievalHandoff:
    stage: AgentStageTask
    strategy: RetrievalStrategyProposal
    receipt_digest: str
    max_rounds: int


class AgentRetrievalHandoffStore(Protocol):
    def load(self, actor, handle) -> RetrievalHandoff: ...
    def save(self, actor, handle, snapshot, plan) -> RetrievalPlan: ...


def agent_plan_id(snapshot, epoch, batch_size, filter_template_ref):
    return 'agentplan_' + digest({'handoff': asdict(snapshot), 'epoch': epoch,
        'batch_size': batch_size, 'filter': filter_template_ref})[7:]


class AgentRetrievalService:
    def __init__(self, *, handoffs, embeddings, capabilities):
        self.handoffs, self.embeddings = handoffs, embeddings
        self.capabilities = {}
        for item in capabilities:
            if (type(item) is not TextQueryCapability or item.binding in self.capabilities
                    or item.media_type != 'image' or type(item.max_batch_size) is not int
                    or not 1 <= item.max_batch_size <= 100
                    or not isinstance(item.filter_template_ref, str) or not item.filter_template_ref.strip()):
                raise ValueError('unique released image text-query capabilities required')
            self.capabilities[item.binding] = item

    def prepare(self, actor, handle, *, epoch, batch_size):
        if (type(epoch) is not int or not 1 <= epoch <= 9223372036854775807
                or type(batch_size) is not int or not 1 <= batch_size <= 100):
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Bounded epoch and batch required', 422)
        snapshot = self.handoffs.load(actor, handle)
        if epoch > snapshot.max_rounds:
            raise ServiceError('AGENT_BUDGET_EXCEEDED', 'Retrieval round limit exceeded', 409)
        strategy = snapshot.strategy
        capability = self.capabilities.get(strategy.binding)
        if capability is None:
            raise ServiceError('QUERY_UNSUPPORTED', 'No released text encoder capability', 422)
        if batch_size > capability.max_batch_size:
            raise ServiceError('INPUT_LIMIT', 'Batch exceeds binding limit', 422)
        context = AccessContext(actor, snapshot.stage.scope.task_id, 'search', 'current')
        vector = self.embeddings.encode_query(context, strategy.query, strategy.binding)
        if type(vector) is not QueryVector:
            raise ServiceError('ENCODER_MISMATCH', 'Invalid encoder response', 422)
        # Stable logical ID: an encoder changing its answer on retry causes an
        # immutable-plan conflict, rather than silently minting another proposal.
        plan_id = agent_plan_id(snapshot, epoch, batch_size, capability.filter_template_ref)
        plan = RetrievalPlan(snapshot.stage.scope, epoch, SearchRequest(plan_id, epoch,
            strategy.binding, vector, capability.filter_template_ref, batch_size))
        validate_plan(plan)
        return self.handoffs.save(actor, handle, snapshot, plan)


class AgentRetrievalExecutor:
    """One bounded page per explicit backend command; no automatic approval.

    Atomic reserve_page replay is the dispatch fence: only its first writer can
    send the request. An uncommitted reservation is pending/unknown, including
    crashes before sending. It is NEVER automatically resent by this executor.
    A connector-specific reconciliation path may later commit the original page.
    """
    def __init__(self, *, retrieval, gateway, budget):
        if not callable(getattr(gateway, 'search', None)) or not callable(getattr(budget, 'attempt', None)):
            raise ValueError('released search gateway and bounded execution budget required')
        self.retrieval, self.gateway, self.budget = retrieval, gateway, budget

    @staticmethod
    def _view(operation, *, network_attempts=0, replayed=False):
        return {'status': 'committed' if operation.page is not None else 'pending_or_unknown',
                'operation': operation, 'network_attempts': network_attempts, 'replayed': replayed}

    def read(self, actor, task_id, operation_id):
        operation = self.retrieval.get_operation(actor, task_id, operation_id)
        if not operation.plan.request.plan_id.startswith('agentplan_'):
            raise ServiceError('AGENT_RETRIEVAL_UNSUPPORTED', 'Agent-origin plan required', 409)
        return self._view(operation)

    def execute_page(self, actor, task_id, *, plan_id, proposal_digest, cursor=None):
        if (not isinstance(plan_id, str) or not plan_id.startswith('agentplan_') or len(plan_id) > 128
                or not _hash(proposal_digest)
                or cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 8192)):
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Frozen Agent plan identity and bounded cursor required', 422)
        approved = self.retrieval.get_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit business plan confirmation required', 409)
        if approved.request.plan_id != plan_id or approved.proposal_digest != proposal_digest:
            raise ServiceError('PLAN_NOT_CURRENT', 'Approved Agent plan changed', 409)
        request = replace(approved.request, cursor=cursor)
        key = 'agentpage_' + digest({'plan': approved.proposal_digest, 'request': asdict(request)})[7:]
        operation_id, replayed = self.retrieval.reserve_page(actor, approved, request, key)
        operation = self.retrieval.get_operation(actor, task_id, operation_id)
        if replayed or operation.page is not None:
            return self._view(operation, replayed=True)
        # Limits are backend-owned and shared across queries/epochs of this scope.
        # A failed admission retains the reservation, never masquerading as zero hits.
        with self.budget.attempt(approved.scope, request.batch_size):
            operation = self.retrieval.get_operation(actor, task_id, operation_id)
            if operation.page is not None:
                return self._view(operation, replayed=True)
            context = AccessContext(actor, task_id, 'retrieval', 'current')
            page = self.gateway.search(context, request)
            self.retrieval.commit_page(actor, operation_id, approved, request, page)
        return self._view(self.retrieval.get_operation(actor, task_id, operation_id), network_attempts=1)
