"""Explicit SDK publication manifests. GitHub tags and Python versions differ."""
from dataclasses import dataclass
import re

from ...errors import invalid


@dataclass(frozen=True)
class DshSdkRelease:
    release_id: str
    sdk_version: str
    runtime_version: str
    sdk_api: str
    upstream_tag: str | None = None
    runtime_mode: str = 'wheel'
    native_version: str | None = None

    def __post_init__(self):
        if (self.sdk_api not in ('config-v1', 'keywords-v2')
                or not all(isinstance(v, str) and re.fullmatch(r'\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?', v)
                           for v in (self.sdk_version, self.runtime_version))
                or self.sdk_version != self.runtime_version
                or self.runtime_mode not in ('wheel', 'npm')
                or self.runtime_mode == 'npm' and (not isinstance(self.native_version, str)
                    or re.fullmatch(r'\d+\.\d+\.\d+-rc\.\d+', self.native_version) is None)):
            raise invalid('explicit matching DSH SDK/runtime versions required')


LEGACY_DSH = DshSdkRelease('dsh-sdk-021rc2', '0.2.1rc2', '0.2.1rc2', 'config-v1')
# Verified against PyPI metadata, not inferred from the newer GitHub tag.
WHEEL_DSH_022RC2 = DshSdkRelease('dsh-sdk-022rc2', '0.2.2rc2', '0.2.2rc2', 'keywords-v2')
# Python controller version is separate from the selected, explicitly launched native runtime.
CANDIDATE_DSH = DshSdkRelease('dsh-runtime-022rc5', '0.2.2rc2', '0.2.2rc2', 'keywords-v2',
    upstream_tag='dsh-v0.2.2-rc.5', runtime_mode='npm', native_version='0.2.2-rc.5')
