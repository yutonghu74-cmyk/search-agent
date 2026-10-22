"""Task-bound, generic cleaning execution; no detectors are registered by default."""

from .runner import CandidateVerdict, GenericCleaningExecutor, JobVerdict, RuleVerdict
from .approved_input import ApprovedManifestAccess
from ...multi_rule_contracts import (CleaningCandidateVerdict, CleaningJobResult,
                                     CleaningRuleVerdict)

__all__ = ("ApprovedManifestAccess", "CandidateVerdict", "GenericCleaningExecutor",
           "JobVerdict", "RuleVerdict", "CleaningCandidateVerdict", "CleaningJobResult",
           "CleaningRuleVerdict")
