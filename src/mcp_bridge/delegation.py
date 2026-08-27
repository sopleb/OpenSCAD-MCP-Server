"""Deferred generation: hand a prompt back to the caller and resume later.

MCP sampling would let a server request a completion from its client, but the
2026-07-28 spec deprecated it and Claude Code does not implement it. Instead a
tool that needs generated content returns a `needs_model_input` envelope. The
calling model answers and calls `submit_model_input`, which resumes the job.

Nothing about this is provider-specific, so it works on any client.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

STATUS_NEEDS_INPUT = "needs_model_input"
RESUME_TOOL = "submit_model_input"


class PendingNotFound(KeyError):
    """No pending request carries this id."""


class PendingExpired(KeyError):
    """The pending request outlived its TTL and was discarded."""


@dataclass
class PendingRequest:
    """One outstanding question for the calling model."""

    request_id: str
    prompt: str
    response_schema: dict[str, Any]
    resume: Callable[[Any], Any] | None
    context: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)

    def envelope(self) -> dict[str, Any]:
        """The payload a tool returns instead of calling a model itself."""
        return {
            "status": STATUS_NEEDS_INPUT,
            "request_id": self.request_id,
            "prompt": self.prompt,
            "response_schema": self.response_schema,
            "resume_with": RESUME_TOOL,
            "context": self.context,
            "instructions": (
                f"Produce a response matching response_schema, then call "
                f"{RESUME_TOOL} with request_id={self.request_id!r} and your response."
            ),
        }


class DelegationStore:
    """Holds pending requests until the caller answers or they expire."""

    def __init__(self, ttl_seconds: int = 900):
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingRequest] = {}

    def request(
        self,
        prompt: str,
        response_schema: dict[str, Any],
        resume: Callable[[Any], Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a question for the caller and return the envelope to send."""
        self._evict_expired()
        pending = PendingRequest(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            prompt=prompt,
            response_schema=response_schema,
            resume=resume,
            context=context or {},
        )
        self._pending[pending.request_id] = pending
        logger.info("Delegated %s to the calling model", pending.request_id)
        return pending.envelope()

    def resolve(self, request_id: str, response: Any) -> Any:
        """Apply the caller's answer and run the resume callback."""
        self._evict_expired()
        pending = self._pending.pop(request_id, None)
        if pending is None:
            raise PendingNotFound(request_id)
        if pending.resume is None:
            return response
        return pending.resume(response)

    def peek(self, request_id: str) -> PendingRequest:
        self._evict_expired()
        pending = self._pending.get(request_id)
        if pending is None:
            raise PendingNotFound(request_id)
        return pending

    def pending_ids(self) -> list[str]:
        self._evict_expired()
        return sorted(self._pending)

    def _evict_expired(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        stale = [rid for rid, p in self._pending.items() if p.created_at < cutoff]
        for rid in stale:
            del self._pending[rid]
            logger.info("Discarded expired delegation %s", rid)
