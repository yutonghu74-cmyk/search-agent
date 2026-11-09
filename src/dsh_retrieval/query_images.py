"""Immutable, project-scoped reference images for query planning.

Uploading a reference image only creates a trusted input artifact.  It does not
create a draft, task, revision, or retrieval run.
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
import threading
import uuid
import warnings
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import ServiceError


MAX_QUERY_IMAGE_BYTES = 20 * 1024 * 1024
MAX_QUERY_IMAGE_PIXELS = 40_000_000
_KEY = re.compile(r"[A-Za-z0-9._:-]{1,200}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def inspect_query_image(content: bytes, supplied_content_type: str) -> dict:
    """Decode the complete static JPEG/PNG and return trusted metadata."""
    if not content or len(content) > MAX_QUERY_IMAGE_BYTES:
        raise ServiceError("QUERY_IMAGE_TOO_LARGE", "参考图不得超过 20 MB", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                actual_format = (image.format or "").upper()
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
                if actual_format not in {"JPEG", "PNG"}:
                    raise ServiceError("QUERY_IMAGE_TYPE_UNSUPPORTED", "参考图仅支持静态 JPEG 或 PNG", 415)
                if frames != 1 or getattr(image, "is_animated", False):
                    raise ServiceError("QUERY_IMAGE_ANIMATED", "参考图必须是静态 JPEG 或 PNG", 422)
                if width < 1 or height < 1 or width * height > MAX_QUERY_IMAGE_PIXELS:
                    raise ServiceError("QUERY_IMAGE_PIXELS_EXCEEDED", "参考图像素不得超过 4000 万", 413)
                image.verify()
            # verify() checks the container; load() forces safe full decoding.
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
    except ServiceError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ServiceError("QUERY_IMAGE_PIXELS_EXCEEDED", "参考图像素超出安全限制", 413) from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ServiceError("QUERY_IMAGE_INVALID", "参考图已损坏或不是有效图片", 422) from None

    mime_type = "image/jpeg" if actual_format == "JPEG" else "image/png"
    declared = supplied_content_type.split(";", 1)[0].strip().lower()
    aliases = {"image/jpg": "image/jpeg"}
    declared = aliases.get(declared, declared)
    if declared not in {"image/jpeg", "image/png"}:
        raise ServiceError("QUERY_IMAGE_TYPE_UNSUPPORTED", "上传时必须声明 image/jpeg 或 image/png", 415)
    if declared != mime_type:
        raise ServiceError("QUERY_IMAGE_TYPE_MISMATCH", "声明的图片类型与文件内容不一致", 422)
    return {"format": actual_format, "mime_type": mime_type,
            "width": width, "height": height}


def _view(row) -> dict:
    return {"query_image_id": row[0], "status": "READY", "digest": row[5],
            "byte_count": row[6], "width": row[7], "height": row[8],
            "format": row[9], "mime_type": row[10], "created_at": row[11],
            "preview_url": f"/v1/retrieval/projects/{row[3]}/query-images/{row[0]}/preview"}


class QueryImageService:
    def __init__(self, projects, path: Path | None = None):
        self.projects = projects
        self.path = None if path is None else Path(path).absolute()
        self._lock = threading.RLock()
        self._memory: dict[str, tuple] = {}
        self._keys: dict[tuple[str, str, str, str], str] = {}
        if self.path is not None:
            self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _initialize(self):
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS query_images (
                query_image_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                digest TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                format TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                content BLOB NOT NULL,
                UNIQUE(tenant_id, owner_user_id, project_id, idempotency_key))""")

    @staticmethod
    def _validate_key(key: str):
        if not _KEY.fullmatch(key or ""):
            raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "上传参考图需要有效的 Idempotency-Key", 400)

    def create(self, actor, project_id: str, key: str, content: bytes,
               supplied_content_type: str) -> tuple[dict, bool]:
        self.projects.require_access(actor, project_id, "default", "create")
        self._validate_key(key)
        metadata = inspect_query_image(content, supplied_content_type)
        content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
        scope = (actor.tenant_id, actor.user_id, project_id, key)
        with self._lock:
            if self.path is None:
                existing_id = self._keys.get(scope)
                if existing_id:
                    existing = self._memory[existing_id]
                    if existing[5] != content_digest:
                        raise ServiceError("IDEMPOTENCY_CONFLICT", "该幂等键已用于另一张参考图", 409)
                    return _view(existing), True
                identifier = "qimg_" + uuid.uuid4().hex
                row = (identifier, actor.tenant_id, actor.user_id, project_id, key,
                       content_digest, len(content), metadata["width"], metadata["height"],
                       metadata["format"], metadata["mime_type"], _now(), bytes(content))
                self._memory[identifier] = row
                self._keys[scope] = identifier
                return _view(row), False
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("""SELECT query_image_id,tenant_id,owner_user_id,project_id,
                    idempotency_key,digest,byte_count,width,height,format,mime_type,created_at,content
                    FROM query_images WHERE tenant_id=? AND owner_user_id=? AND project_id=?
                    AND idempotency_key=?""", scope).fetchone()
                if existing:
                    if existing[5] != content_digest:
                        raise ServiceError("IDEMPOTENCY_CONFLICT", "该幂等键已用于另一张参考图", 409)
                    return _view(existing), True
                identifier = "qimg_" + uuid.uuid4().hex
                row = (identifier, actor.tenant_id, actor.user_id, project_id, key,
                       content_digest, len(content), metadata["width"], metadata["height"],
                       metadata["format"], metadata["mime_type"], _now(), sqlite3.Binary(content))
                db.execute("INSERT INTO query_images VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                return _view(row), False

    def _get(self, actor, project_id: str, query_image_id: str):
        self.projects.require_access(actor, project_id, "default", "read")
        if self.path is None:
            row = self._memory.get(query_image_id)
        else:
            with closing(self._connect()) as db:
                row = db.execute("""SELECT query_image_id,tenant_id,owner_user_id,project_id,
                    idempotency_key,digest,byte_count,width,height,format,mime_type,created_at,content
                    FROM query_images WHERE query_image_id=?""", (query_image_id,)).fetchone()
        if not row or row[1] != actor.tenant_id or row[2] != actor.user_id or row[3] != project_id:
            raise ServiceError("NOT_FOUND", "参考图不存在", 404)
        return row

    def get(self, actor, project_id: str, query_image_id: str) -> dict:
        return _view(self._get(actor, project_id, query_image_id))

    def require_input(self, actor, project_id: str, query_input: dict) -> dict:
        row = self._get(actor, project_id, query_input["query_image_id"])
        if row[5] != query_input["digest"]:
            raise ServiceError("QUERY_IMAGE_CHANGED", "参考图摘要与不可变记录不一致", 409)
        return _view(row)

    def content(self, actor, project_id: str, query_image_id: str,
                expected_digest: str) -> bytes:
        row = self._get(actor, project_id, query_image_id)
        if row[5] != expected_digest:
            raise ServiceError("QUERY_IMAGE_CHANGED", "参考图摘要与不可变记录不一致", 409)
        return bytes(row[12])

    def preview(self, actor, project_id: str, query_image_id: str) -> tuple[str, bytes]:
        row = self._get(actor, project_id, query_image_id)
        return row[10], bytes(row[12])
