"""Task-bound, generic cleaning execution; no detectors are registered by default."""

from .runner import CandidateVerdict, GenericCleaningExecutor, JobVerdict, RuleVerdict
from .approved_input import ApprovedManifestAccess

__all__ = ("ApprovedManifestAccess", "CandidateVerdict", "GenericCleaningExecutor",
           "JobVerdict", "RuleVerdict")
