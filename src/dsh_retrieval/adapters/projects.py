"""Explicit local grants only; not a substitute for W3/project authorization."""
from ..errors import ServiceError
from ..models import Actor


class LocalProjectAccess:
    def __init__(self, grants=()):
        # (tenant, user, project, dimension); empty configuration denies everything.
        self.grants = set(grants)

    def require_access(self, actor: Actor, project_id: str, dimension_id: str, action: str) -> None:
        if (actor.tenant_id, actor.user_id, project_id, dimension_id) not in self.grants:
            raise ServiceError("PROJECT_ACCESS_DENIED", "project operation is not authorized", 403)
