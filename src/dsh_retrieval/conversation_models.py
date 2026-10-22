"""Independent draft conversation records, never business execution events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MESSAGE_KINDS = frozenset({"request", "feedback"})


def _require_id(value: str, field: str) -> None:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")


def _require_time(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str
    conversation_id: str
    tenant_id: str
    owner_user_id: str
    project_id: str
    draft_id: str
    sequence: int
    kind: str
    text: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field in ("message_id", "conversation_id", "tenant_id", "owner_user_id",
                      "project_id", "draft_id"):
            _require_id(getattr(self, field), field)
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if self.kind not in _MESSAGE_KINDS:
            raise ValueError("kind must be request or feedback")
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > 4000:
            raise ValueError("text must contain 1 to 4000 characters")
        _require_time(self.created_at, "created_at")

    def to_view(self) -> dict:
        """Return user-visible content without storage ownership identifiers."""
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "draft_id": self.draft_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ConversationConfirmation:
    confirmation_id: str
    conversation_id: str
    draft_id: str
    task_id: str
    revision: int
    confirmed_at: datetime

    def __post_init__(self) -> None:
        for field in ("confirmation_id", "conversation_id", "draft_id", "task_id"):
            _require_id(getattr(self, field), field)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        _require_time(self.confirmed_at, "confirmed_at")

    def to_view(self) -> dict:
        return {
            "confirmation_id": self.confirmation_id,
            "conversation_id": self.conversation_id,
            "draft_id": self.draft_id,
            "task_id": self.task_id,
            "revision": self.revision,
            "confirmed_at": self.confirmed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    conversation_id: str
    project_id: str
    messages: tuple[ConversationMessage, ...]
    confirmations: tuple[ConversationConfirmation, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.conversation_id, "conversation_id")
        _require_id(self.project_id, "project_id")
        if not isinstance(self.messages, tuple) or not isinstance(self.confirmations, tuple):
            raise ValueError("conversation entries must be tuples")
        sequences: list[int] = []
        for message in self.messages:
            if (not isinstance(message, ConversationMessage)
                    or message.conversation_id != self.conversation_id
                    or message.project_id != self.project_id):
                raise ValueError("message is not bound to this conversation")
            sequences.append(message.sequence)
        if sequences != sorted(set(sequences)):
            raise ValueError("message sequences must be unique and increasing")
        for confirmation in self.confirmations:
            if (not isinstance(confirmation, ConversationConfirmation)
                    or confirmation.conversation_id != self.conversation_id):
                raise ValueError("confirmation is not bound to this conversation")

    def to_view(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "messages": [message.to_view() for message in self.messages],
            "confirmations": [item.to_view() for item in self.confirmations],
        }

    def to_summary(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "message_count": len(self.messages),
            "confirmation_count": len(self.confirmations),
            "last_message_at": (self.messages[-1].created_at.isoformat()
                                if self.messages else None),
        }
