"""Compatibility registration for the existing pinned SDK; old runtime untouched."""
from .dsh import DshAdapter
from .dsh_releases import LEGACY_DSH

LEGACY_VERSION = LEGACY_DSH.sdk_version


class LegacyDshAdapter(DshAdapter):
    def __init__(self, *, python, home, model_environment, adapter_id='dsh-legacy'):
        super().__init__(python=python, home=home, model_environment=model_environment,
                         release=LEGACY_DSH, adapter_id=adapter_id)
