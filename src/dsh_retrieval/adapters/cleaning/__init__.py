"""Task-bound, generic cleaning execution; no detectors are registered by default."""

from .runner import CandidateVerdict, GenericCleaningExecutor, JobVerdict, RuleVerdict

__all__ = ("CandidateVerdict", "GenericCleaningExecutor", "JobVerdict", "RuleVerdict")
