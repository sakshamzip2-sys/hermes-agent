"""
OpenAI-compatible API server platform adapter.

Exposes an HTTP server with endpoints:
- POST /v1/chat/completions        — OpenAI Chat Completions format (stateless; opt-in session continuity via X-OpenComputer-Session-Id header; opt-in long-term memory scoping via X-OpenComputer-Session-Key header)
- POST /v1/responses               — OpenAI Responses API format (stateful via previous_response_id; X-OpenComputer-Session-Key supported)
- GET  /v1/responses/{response_id} — Retrieve a stored response
- DELETE /v1/responses/{response_id} — Delete a stored response
- GET  /v1/models                  — lists hermes-agent as an available model
- GET  /v1/capabilities            — machine-readable API capabilities for external UIs
- GET  /api/sessions               — list client-visible OpenComputer sessions
- POST /api/sessions               — create an empty OpenComputer session
- GET/PATCH/DELETE /api/sessions/{session_id} — read/update/delete a session
- GET  /api/sessions/{session_id}/messages — read session message history
- POST /api/sessions/{session_id}/fork — branch a session using SessionDB lineage
- POST /api/sessions/{session_id}/chat[/stream] — chat with a persisted session
- POST /v1/runs                    — start a run, returns run_id immediately (202)
- GET  /v1/runs/{run_id}           — retrieve current run status
- GET  /v1/runs/{run_id}/events    — SSE stream of structured lifecycle events
- POST /v1/runs/{run_id}/approval — resolve a pending run approval
- POST /v1/runs/{run_id}/stop       — interrupt a running agent
- GET  /health                     — health check
- GET  /health/detailed            — rich status for cross-container dashboard probing

Any OpenAI-compatible frontend (Open WebUI, LobeChat, LibreChat,
AnythingLLM, NextChat, ChatBox, etc.) can connect to hermes-agent
through this adapter by pointing at http://localhost:8642/v1 and
authenticating with API_SERVER_KEY.

Requires:
- aiohttp (already available in the gateway)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import socket as _socket
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    is_network_accessible,
)

logger = logging.getLogger(__name__)


def _hermes_version() -> str:
    """Return the hermes-agent version string, or "dev" if it can't be resolved.

    Tries the installed package metadata first (authoritative for a pip/uv
    install), then the in-tree ``hermes_cli.__version__`` (covers editable /
    source checkouts where metadata may be stale or absent). Never raises —
    a version probe must not be able to break the health endpoint.
    """
    try:
        from importlib.metadata import version

        return version("hermes-agent")
    except Exception:
        pass
    try:
        from hermes_cli import __version__

        return __version__
    except Exception:
        return "dev"


# Default settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642
MAX_STORED_RESPONSES = 100
MAX_REQUEST_BYTES = 10_000_000  # 10 MB — accommodates long agent conversations with tool calls
CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS = 30.0
MAX_NORMALIZED_TEXT_LENGTH = 65_536  # 64 KB cap for normalized content parts
MAX_CONTENT_LIST_SIZE = 1_000  # Max items when content is an array


def _coerce_port(value: Any, default: int = DEFAULT_PORT) -> int:
    """Parse a listen port without letting malformed env/config values crash startup."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_TRUE_REQUEST_BOOL_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_REQUEST_BOOL_STRINGS = frozenset({"0", "false", "no", "off"})


def _coerce_request_bool(value: Any, default: bool = False) -> bool:
    """Normalize boolean-like API payload values.

    External clients should send real JSON booleans, but some OpenAI-compatible
    frontends and middleware serialize flags like ``stream`` as strings.  Using
    Python truthiness on those values misroutes requests because ``"false"`` is
    still truthy.  Treat only explicit bool-ish scalars as booleans; everything
    else falls back to the caller's default.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_REQUEST_BOOL_STRINGS:
            return True
        if normalized in _FALSE_REQUEST_BOOL_STRINGS:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _normalize_chat_content(
    content: Any, *, _max_depth: int = 10, _depth: int = 0,
) -> str:
    """Normalize OpenAI chat message content into a plain text string.

    Some clients (Open WebUI, LobeChat, etc.) send content as an array of
    typed parts instead of a plain string::

        [{"type": "text", "text": "hello"}, {"type": "input_text", "text": "..."}]

    This function flattens those into a single string so the agent pipeline
    (which expects strings) doesn't choke.

    Defensive limits prevent abuse: recursion depth, list size, and output
    length are all bounded.
    """
    if _depth > _max_depth:
        return ""
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:MAX_NORMALIZED_TEXT_LENGTH] if len(content) > MAX_NORMALIZED_TEXT_LENGTH else content

    if isinstance(content, list):
        parts: List[str] = []
        total_len = 0
        items = content[:MAX_CONTENT_LIST_SIZE] if len(content) > MAX_CONTENT_LIST_SIZE else content
        for item in items:
            if isinstance(item, str):
                if item:
                    part = item[:MAX_NORMALIZED_TEXT_LENGTH]
                    parts.append(part)
                    total_len += len(part)
            elif isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if item_type in {"text", "input_text", "output_text"}:
                    text = item.get("text", "")
                    if text:
                        try:
                            part = str(text)[:MAX_NORMALIZED_TEXT_LENGTH]
                            parts.append(part)
                            total_len += len(part)
                        except Exception:
                            pass
                # Silently skip image_url / other non-text parts
            elif isinstance(item, list):
                nested = _normalize_chat_content(item, _max_depth=_max_depth, _depth=_depth + 1)
                if nested:
                    parts.append(nested)
                    total_len += len(nested)
            # Check accumulated size
            if total_len >= MAX_NORMALIZED_TEXT_LENGTH:
                break
        result = "\n".join(parts)
        return result[:MAX_NORMALIZED_TEXT_LENGTH] if len(result) > MAX_NORMALIZED_TEXT_LENGTH else result

    # Fallback for unexpected types (int, float, bool, etc.)
    try:
        result = str(content)
        return result[:MAX_NORMALIZED_TEXT_LENGTH] if len(result) > MAX_NORMALIZED_TEXT_LENGTH else result
    except Exception:
        return ""


# Content part type aliases used by the OpenAI Chat Completions and Responses
# APIs.  We accept both spellings on input and emit a single canonical internal
# shape (``{"type": "text", ...}`` / ``{"type": "image_url", ...}``) that the
# rest of the agent pipeline already understands.
_TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
_IMAGE_PART_TYPES = frozenset({"image_url", "input_image"})
_FILE_PART_TYPES = frozenset({"file", "input_file"})


def _normalize_multimodal_content(content: Any) -> Any:
    """Validate and normalize multimodal content for the API server.

    Returns a plain string when the content is text-only, or a list of
    ``{"type": "text"|"image_url", ...}`` parts when images are present.
    The output shape is the native OpenAI Chat Completions vision format,
    which the agent pipeline accepts verbatim (OpenAI-wire providers) or
    converts (``_preprocess_anthropic_content`` for Anthropic).

    Raises ``ValueError`` with an OpenAI-style code on invalid input:
      * ``unsupported_content_type`` — file/input_file/file_id parts, or
        non-image ``data:`` URLs.
      * ``invalid_image_url`` — missing URL or unsupported scheme.
      * ``invalid_content_part`` — malformed text/image objects.

    Callers translate the ValueError into a 400 response.
    """
    # Scalar passthrough mirrors ``_normalize_chat_content``.
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:MAX_NORMALIZED_TEXT_LENGTH] if len(content) > MAX_NORMALIZED_TEXT_LENGTH else content
    if not isinstance(content, list):
        # Mirror the legacy text-normalizer's fallback so callers that
        # pre-existed image support still get a string back.
        return _normalize_chat_content(content)

    items = content[:MAX_CONTENT_LIST_SIZE] if len(content) > MAX_CONTENT_LIST_SIZE else content
    normalized_parts: List[Dict[str, Any]] = []
    text_accum_len = 0

    for part in items:
        if isinstance(part, str):
            if part:
                trimmed = part[:MAX_NORMALIZED_TEXT_LENGTH]
                normalized_parts.append({"type": "text", "text": trimmed})
                text_accum_len += len(trimmed)
            continue

        if not isinstance(part, dict):
            # Ignore unknown scalars for forward compatibility with future
            # Responses API additions (e.g. ``refusal``).  The same policy
            # the text normalizer applies.
            continue

        raw_type = part.get("type")
        part_type = str(raw_type or "").strip().lower()

        if part_type in _TEXT_PART_TYPES:
            text = part.get("text")
            if text is None:
                continue
            if not isinstance(text, str):
                text = str(text)
            if text:
                trimmed = text[:MAX_NORMALIZED_TEXT_LENGTH]
                normalized_parts.append({"type": "text", "text": trimmed})
                text_accum_len += len(trimmed)
            continue

        if part_type in _IMAGE_PART_TYPES:
            detail = part.get("detail")
            image_ref = part.get("image_url")
            # OpenAI Responses sends ``input_image`` with a top-level
            # ``image_url`` string; Chat Completions sends ``image_url`` as
            # ``{"url": "...", "detail": "..."}``.  Support both.
            if isinstance(image_ref, dict):
                url_value = image_ref.get("url")
                detail = image_ref.get("detail", detail)
            else:
                url_value = image_ref
            if not isinstance(url_value, str) or not url_value.strip():
                raise ValueError("invalid_image_url:Image parts must include a non-empty image URL.")
            url_value = url_value.strip()
            lowered = url_value.lower()
            if lowered.startswith("data:"):
                if not lowered.startswith("data:image/") or "," not in url_value:
                    raise ValueError(
                        "unsupported_content_type:Only image data URLs are supported. "
                        "Non-image data payloads are not supported."
                    )
            elif not (lowered.startswith("http://") or lowered.startswith("https://")):
                raise ValueError(
                    "invalid_image_url:Image inputs must use http(s) URLs or data:image/... URLs."
                )
            image_part: Dict[str, Any] = {"type": "image_url", "image_url": {"url": url_value}}
            if detail is not None:
                if not isinstance(detail, str) or not detail.strip():
                    raise ValueError("invalid_content_part:Image detail must be a non-empty string when provided.")
                image_part["image_url"]["detail"] = detail.strip()
            normalized_parts.append(image_part)
            continue

        if part_type in _FILE_PART_TYPES:
            raise ValueError(
                "unsupported_content_type:Inline image inputs are supported, "
                "but uploaded files and document inputs are not supported on this endpoint."
            )

        # Unknown part type — reject explicitly so clients get a clear error
        # instead of a silently dropped turn.
        raise ValueError(
            f"unsupported_content_type:Unsupported content part type {raw_type!r}. "
            "Only text and image_url/input_image parts are supported."
        )

    if not normalized_parts:
        return ""

    # Text-only: collapse to a plain string so downstream logging/trajectory
    # code sees the native shape and prompt caching on text-only turns is
    # unaffected.
    if all(p.get("type") == "text" for p in normalized_parts):
        return "\n".join(p["text"] for p in normalized_parts if p.get("text"))

    return normalized_parts


def _content_has_visible_payload(content: Any) -> bool:
    """True when content has any text or image attachment.  Used to reject empty turns."""
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in _TEXT_PART_TYPES and str(part.get("text") or "").strip():
                    return True
                if ptype in _IMAGE_PART_TYPES:
                    return True
    return False


def _multimodal_validation_error(exc: ValueError, *, param: str) -> "web.Response":
    """Translate a ``_normalize_multimodal_content`` ValueError into a 400 response."""
    raw = str(exc)
    code, _, message = raw.partition(":")
    if not message:
        code, message = "invalid_content_part", raw
    return web.json_response(
        _openai_error(message, code=code, param=param),
        status=400,
    )


def _session_chat_user_message(body: Dict[str, Any], *, param: str = "message") -> tuple[Any, Optional["web.Response"]]:
    """Parse and normalize session chat ``message`` / ``input`` like chat completions."""
    user_message = body.get("message") or body.get("input")
    if not _content_has_visible_payload(user_message):
        return None, web.json_response(
            _openai_error("Missing 'message' field", code="missing_message"),
            status=400,
        )
    try:
        return _normalize_multimodal_content(user_message), None
    except ValueError as exc:
        return None, _multimodal_validation_error(exc, param=param)


def check_api_server_requirements() -> bool:
    """Check if API server dependencies are available."""
    return AIOHTTP_AVAILABLE


class ResponseStore:
    """
    SQLite-backed LRU store for Responses API state.

    Each stored response includes the full internal conversation history
    (with tool calls and results) so it can be reconstructed on subsequent
    requests via previous_response_id.

    Persists across gateway restarts.  Falls back to in-memory SQLite
    if the on-disk path is unavailable.
    """

    def __init__(self, max_size: int = MAX_STORED_RESPONSES, db_path: str = None):
        self._max_size = max_size
        if db_path is None:
            try:
                from hermes_cli.config import get_hermes_home
                db_path = str(get_hermes_home() / "response_store.db")
            except Exception:
                db_path = ":memory:"
        self._db_path: Optional[str] = db_path if db_path != ":memory:" else None
        try:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        except Exception:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._db_path = None
        # Use shared WAL-fallback helper so response_store.db degrades
        # gracefully on NFS/SMB/FUSE-mounted HERMES_HOME (same filesystem
        # issue addressed for state.db/kanban.db — see
        # hermes_state._WAL_INCOMPAT_MARKERS).
        from hermes_state import apply_wal_with_fallback
        apply_wal_with_fallback(self._conn, db_label="response_store.db")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                accessed_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                name TEXT PRIMARY KEY,
                response_id TEXT NOT NULL
            )"""
        )
        self._conn.commit()
        # response_store.db contains conversation history (tool payloads,
        # prompts, results). Tighten to owner-only after creation so other
        # local users on a shared box can't read it. Run once at __init__
        # rather than after every commit — chmod-on-every-write is wasted
        # syscalls on a hot path.
        self._tighten_file_permissions()

    def _tighten_file_permissions(self) -> None:
        """Force owner-only permissions on the DB and SQLite sidecars."""
        if not self._db_path:
            return
        for candidate in (
            Path(self._db_path),
            Path(f"{self._db_path}-wal"),
            Path(f"{self._db_path}-shm"),
        ):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                logger.debug(
                    "Failed to restrict response store permissions for %s",
                    candidate,
                    exc_info=True,
                )

    def get(self, response_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a stored response by ID (updates access time for LRU)."""
        row = self._conn.execute(
            "SELECT data FROM responses WHERE response_id = ?", (response_id,)
        ).fetchone()
        if row is None:
            return None
        self._conn.execute(
            "UPDATE responses SET accessed_at = ? WHERE response_id = ?",
            (time.time(), response_id),
        )
        self._conn.commit()
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Corrupted JSON in response store for id=%s, evicting entry",
                response_id,
            )
            self._conn.execute(
                "DELETE FROM responses WHERE response_id = ?",
                (response_id,),
            )
            self._conn.commit()
            return None

    def put(self, response_id: str, data: Dict[str, Any]) -> None:
        """Store a response, evicting the oldest if at capacity."""
        self._conn.execute(
            "INSERT OR REPLACE INTO responses (response_id, data, accessed_at) VALUES (?, ?, ?)",
            (response_id, json.dumps(data, default=str), time.time()),
        )
        # Evict oldest entries beyond max_size
        count = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        if count > self._max_size:
            # Collect IDs that will be evicted
            evict_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT response_id FROM responses ORDER BY accessed_at ASC LIMIT ?",
                    (count - self._max_size,),
                ).fetchall()
            ]
            if evict_ids:
                placeholders = ",".join("?" for _ in evict_ids)
                # Clear conversation mappings pointing to evicted responses
                self._conn.execute(
                    f"DELETE FROM conversations WHERE response_id IN ({placeholders})",
                    evict_ids,
                )
                # Delete evicted responses
                self._conn.execute(
                    f"DELETE FROM responses WHERE response_id IN ({placeholders})",
                    evict_ids,
                )
        self._conn.commit()

    def delete(self, response_id: str) -> bool:
        """Remove a response from the store. Returns True if found and deleted."""
        # Clear conversation mappings pointing to this response
        self._conn.execute(
            "DELETE FROM conversations WHERE response_id = ?", (response_id,)
        )
        cursor = self._conn.execute(
            "DELETE FROM responses WHERE response_id = ?", (response_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_conversation(self, name: str) -> Optional[str]:
        """Get the latest response_id for a conversation name."""
        row = self._conn.execute(
            "SELECT response_id FROM conversations WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def set_conversation(self, name: str, response_id: str) -> None:
        """Map a conversation name to its latest response_id."""
        self._conn.execute(
            "INSERT OR REPLACE INTO conversations (name, response_id) VALUES (?, ?)",
            (name, response_id),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM responses").fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key",
}


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def cors_middleware(request, handler):
        """Add CORS headers for explicitly allowed origins; handle OPTIONS preflight."""
        adapter = request.app.get("api_server_adapter")
        origin = request.headers.get("Origin", "")
        cors_headers = None
        if adapter is not None:
            if not adapter._origin_allowed(origin):
                return web.Response(status=403)
            cors_headers = adapter._cors_headers_for_origin(origin)

        if request.method == "OPTIONS":
            if cors_headers is None:
                return web.Response(status=403)
            return web.Response(status=200, headers=cors_headers)

        response = await handler(request)
        if cors_headers is not None:
            response.headers.update(cors_headers)
        return response
else:
    cors_middleware = None  # type: ignore[assignment]


def _openai_error(message: str, err_type: str = "invalid_request_error", param: str = None, code: str = None) -> Dict[str, Any]:
    """OpenAI-style error envelope."""
    return {
        "error": {
            "message": message,
            "type": err_type,
            "param": param,
            "code": code,
        }
    }


_TOOL_RESULT_STREAM_LIMIT = 100_000


def _tool_result_to_text(result: Any, *, limit: int = _TOOL_RESULT_STREAM_LIMIT) -> str:
    """Normalize a tool's return value into a string for the SSE timeline.

    The structured ``tool_complete`` callback hands us whatever the tool
    returned (str, dict, list, None). The chat-completions SSE only carries
    text, so collapse it to a string and cap the size so a huge result
    (e.g. a large file read or a long ``delegate_task`` report) can't flood
    the stream. Returns ``""`` for ``None``/empty so the frontend renders
    no Response box rather than the literal string ``"None"``.
    """
    if result is None:
        return ""
    try:
        text = result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception:
        text = str(result)
    # Cap by encoded byte length (not code points) so the SSE payload stays
    # bounded even for multibyte (e.g. CJK) output. errors="ignore" trims any
    # multibyte sequence split at the boundary.
    encoded = text.encode("utf-8")
    if len(encoded) > limit:
        text = encoded[:limit].decode("utf-8", errors="ignore") + "\n…[truncated]"
    return text


def _humanize_agent_error(raw: str) -> str:
    """Turn a raw provider/agent error string into a short, user-facing line.

    Used by the streaming SSE writers so a failed model call surfaces a visible
    message instead of an empty "stop" (the old silent failure). Maps the common
    router/provider failures to friendly guidance and otherwise returns a
    trimmed version of the raw message. Never echoes more than ~240 chars so a
    verbose provider payload can't flood the chat.
    """
    low = (raw or "").lower()
    if "does not support the effort parameter" in low:
        return ("This model doesn't support the selected reasoning effort. "
                "Turn thinking off or pick another model (e.g. Opus or Sonnet).")
    if "more credits" in low or "requires more credits" in low or "code': 402" in low or "http 402" in low:
        return ("The model provider is out of credits, so the request couldn't "
                "be completed. Add credits or switch providers.")
    if "no available accounts" in low or "http 503" in low:
        return ("No available account for this model right now. Try again in a "
                "moment or pick another model.")
    if "invalid api key" in low or "http 401" in low or "unauthorized" in low:
        return ("The model provider rejected the credentials for this model. "
                "Check the provider login or API key.")
    if "rate limit" in low or "http 429" in low:
        return "The model provider is rate-limiting requests. Try again shortly."
    # Model not served by this provider/router (e.g. the OC router 404s a GPT id
    # or Fable 5 with {"message":"model: <id>","type":"server_error"}, or returns
    # "<model> is not available"). Map it to actionable guidance instead of
    # leaking a raw "server_error" string.
    if (
        "http 404" in low
        or "model not found" in low
        or "no such model" in low
        or "is not available" in low
        or ("server_error" in low and "model:" in low)
    ):
        return ("That model isn't available on this provider right now. "
                "Pick another model from the picker.")
    msg = " ".join((raw or "The model request failed.").split())
    return msg[:240] + ("…" if len(msg) > 240 else "")


def _is_openai_family_model(model: str) -> bool:
    """True for OpenAI-family model ids (gpt-*/o-series/codex/chatgpt).

    The OC Router binds ONE platform per API key (a key's group is anthropic OR
    openai), so a combined picker must select the KEY by the model's provider,
    not just swap the model string. OpenAI-family ids need an OpenAI-group key;
    claude-* keep the default (Anthropic) key. See _create_agent.
    """
    m = (model or "").strip().lower()
    return (
        m.startswith("gpt-")
        or m.startswith("gpt5")
        or m.startswith("chatgpt")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
        or "codex" in m
    )


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def body_limit_middleware(request, handler):
        """Reject overly large request bodies early based on Content-Length."""
        if request.method in {"POST", "PUT", "PATCH"}:
            cl = request.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > MAX_REQUEST_BYTES:
                        return web.json_response(_openai_error("Request body too large.", code="body_too_large"), status=413)
                except ValueError:
                    return web.json_response(_openai_error("Invalid Content-Length header.", code="invalid_content_length"), status=400)
        return await handler(request)
else:
    body_limit_middleware = None  # type: ignore[assignment]

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Referrer-Policy": "no-referrer",
}


if AIOHTTP_AVAILABLE:
    @web.middleware
    async def security_headers_middleware(request, handler):
        """Add security headers to all responses (including errors)."""
        response = await handler(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response
else:
    security_headers_middleware = None  # type: ignore[assignment]


class _IdempotencyCache:
    """In-memory idempotency cache with TTL and basic LRU semantics."""
    def __init__(self, max_items: int = 1000, ttl_seconds: int = 300):
        from collections import OrderedDict
        self._store = OrderedDict()
        self._inflight: Dict[tuple[str, str], "asyncio.Task[Any]"] = {}
        self._ttl = ttl_seconds
        self._max = max_items

    def _purge(self):
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v["ts"] > self._ttl]
        for k in expired:
            self._store.pop(k, None)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    async def get_or_set(self, key: str, fingerprint: str, compute_coro):
        self._purge()
        item = self._store.get(key)
        if item and item["fp"] == fingerprint:
            return item["resp"]

        inflight_key = (key, fingerprint)
        task = self._inflight.get(inflight_key)
        if task is None:
            async def _compute_and_store():
                resp = await compute_coro()
                import time as _t
                self._store[key] = {"resp": resp, "fp": fingerprint, "ts": _t.time()}
                self._purge()
                return resp

            task = asyncio.create_task(_compute_and_store())
            self._inflight[inflight_key] = task

            def _clear_inflight(done_task: "asyncio.Task[Any]") -> None:
                if self._inflight.get(inflight_key) is done_task:
                    self._inflight.pop(inflight_key, None)

            task.add_done_callback(_clear_inflight)

        return await asyncio.shield(task)


_idem_cache = _IdempotencyCache()


def _make_request_fingerprint(body: Dict[str, Any], keys: List[str]) -> str:
    from hashlib import sha256
    subset = {k: body.get(k) for k in keys}
    return sha256(repr(subset).encode("utf-8")).hexdigest()


def _derive_chat_session_id(
    system_prompt: Optional[str],
    first_user_message: str,
) -> str:
    """Derive a stable session ID from the conversation's first user message.

    OpenAI-compatible frontends (Open WebUI, LibreChat, etc.) send the full
    conversation history with every request.  The system prompt and first user
    message are constant across all turns of the same conversation, so hashing
    them produces a deterministic session ID that lets the API server reuse
    the same OpenComputer session (and therefore the same Docker container sandbox
    directory) across turns.
    """
    seed = f"{system_prompt or ''}\n{first_user_message}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"api-{digest}"


_CRON_AVAILABLE = False
try:
    from cron.jobs import (
        list_jobs as _cron_list,
        get_job as _cron_get,
        create_job as _cron_create,
        update_job as _cron_update,
        remove_job as _cron_remove,
        pause_job as _cron_pause,
        resume_job as _cron_resume,
        trigger_job as _cron_trigger,
    )
    _CRON_AVAILABLE = True
except ImportError:
    _cron_list = None
    _cron_get = None
    _cron_create = None
    _cron_update = None
    _cron_remove = None
    _cron_pause = None
    _cron_resume = None
    _cron_trigger = None

# Defense-in-depth: mirror the agent-facing cronjob tool, which scans the
# user-supplied prompt for exfiltration/injection payloads at create/update
# time (tools/cronjob_tools.py).  The REST cron endpoints are authenticated
# (every handler runs _check_auth, and connect() refuses to start without
# API_SERVER_KEY), so this is not the trust boundary — it's parity with the
# tool path so a malicious prompt is rejected the same way regardless of
# which surface created the job.  Imported defensively: a missing scanner
# must not disable the cron REST API.
try:
    from tools.cronjob_tools import _scan_cron_prompt as _scan_cron_prompt
except Exception:  # pragma: no cover - scanner is optional hardening
    _scan_cron_prompt = None


class APIServerAdapter(BasePlatformAdapter):
    """
    OpenAI-compatible HTTP API server adapter.

    Runs an aiohttp web server that accepts OpenAI-format requests
    and routes them through hermes-agent's AIAgent.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.API_SERVER)
        extra = config.extra or {}
        self._host: str = extra.get("host", os.getenv("API_SERVER_HOST", DEFAULT_HOST))
        raw_port = extra.get("port")
        if raw_port is None:
            raw_port = os.getenv("API_SERVER_PORT", str(DEFAULT_PORT))
        self._port: int = _coerce_port(raw_port, DEFAULT_PORT)
        self._api_key: str = extra.get("key", os.getenv("API_SERVER_KEY", ""))
        self._cors_origins: tuple[str, ...] = self._parse_cors_origins(
            extra.get("cors_origins", os.getenv("API_SERVER_CORS_ORIGINS", "")),
        )
        self._model_name: str = self._resolve_model_name(
            extra.get("model_name", os.getenv("API_SERVER_MODEL_NAME", "")),
        )
        self._app: Optional["web.Application"] = None
        self._runner: Optional["web.AppRunner"] = None
        self._site: Optional["web.TCPSite"] = None
        self._response_store = ResponseStore()
        # Active run streams: run_id -> asyncio.Queue of SSE event dicts
        self._run_streams: Dict[str, "asyncio.Queue[Optional[Dict]]"] = {}
        # Creation timestamps for orphaned-run TTL sweep
        self._run_streams_created: Dict[str, float] = {}
        # Active run agent/task references for stop support
        self._active_run_agents: Dict[str, Any] = {}
        self._active_run_tasks: Dict[str, "asyncio.Task"] = {}
        # Pollable run status for dashboards and external control-plane UIs.
        self._run_statuses: Dict[str, Dict[str, Any]] = {}
        # Active approval session key for each run_id.  The approval core
        # resolves requests by session key, while API clients address the
        # in-flight run by run_id.
        self._run_approval_sessions: Dict[str, str] = {}
        self._session_db: Optional[Any] = None  # Lazy-init SessionDB for session continuity
        # Per-agent profile SessionDBs (frontend "agents" each get their own
        # state.db at agent-profiles/{slug}/state.db). Cached per slug.
        self._agent_profile_dbs: Dict[str, Any] = {}
        # Guards _agent_profile_dbs: reachable from the event loop thread (read
        # handlers) and executor worker threads (_run_agent) at once, so the
        # check-then-open-then-cache must be atomic or two threads would open
        # (and leak) two SQLite connections for the same slug.
        self._agent_profile_dbs_lock = threading.Lock()
        # Artifact registry (in-memory, per running gateway). write_file/patch
        # completions register a descriptor here so the web UI can (a) receive a
        # live ``artifact.created`` SSE event and (b) download the file by id via
        # ``/api/v1/sessions/{sid}/artifacts/{aid}/download``. artifact_id is an
        # unguessable capability token; downloads only ever serve a path we
        # recorded ourselves (no client-supplied path → no traversal surface).
        self._session_artifacts: Dict[str, list] = {}      # session_id -> [descriptor]
        self._artifacts_by_id: Dict[str, Dict[str, Any]] = {}  # artifact_id -> rec(+path,+session)

    @staticmethod
    def _parse_cors_origins(value: Any) -> tuple[str, ...]:
        """Normalize configured CORS origins into a stable tuple."""
        if not value:
            return ()

        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [str(value)]

        return tuple(str(item).strip() for item in items if str(item).strip())

    @staticmethod
    def _resolve_model_name(explicit: str) -> str:
        """Derive the advertised model name for /v1/models.

        Priority:
        1. Explicit override (config extra or API_SERVER_MODEL_NAME env var)
        2. Active profile name (so each profile advertises a distinct model)
        3. Fallback: "open-computer"
        """
        if explicit and explicit.strip():
            return explicit.strip()
        try:
            from hermes_cli.profiles import get_active_profile_name
            profile = get_active_profile_name()
            if profile and profile not in {"default", "custom"}:
                return profile
        except Exception:
            pass
        return "open-computer"

    def _cors_headers_for_origin(self, origin: str) -> Optional[Dict[str, str]]:
        """Return CORS headers for an allowed browser origin."""
        if not origin or not self._cors_origins:
            return None

        if "*" in self._cors_origins:
            headers = dict(_CORS_HEADERS)
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Max-Age"] = "600"
            return headers

        if origin not in self._cors_origins:
            return None

        headers = dict(_CORS_HEADERS)
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
        headers["Access-Control-Max-Age"] = "600"
        return headers

    def _origin_allowed(self, origin: str) -> bool:
        """Allow non-browser clients and explicitly configured browser origins."""
        if not origin:
            return True

        if not self._cors_origins:
            return False

        return "*" in self._cors_origins or origin in self._cors_origins

    @staticmethod
    def _clean_log_value(value: Any, *, max_len: int = 200) -> str:
        """Sanitize request metadata before it reaches security logs."""
        if value is None:
            return ""
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        return text[:max_len]

    def _request_audit_context(self, request: "web.Request") -> Dict[str, str]:
        """Return non-secret source metadata for security/audit warnings."""
        peer_ip = ""
        try:
            peer = request.transport.get_extra_info("peername") if request.transport else None
            if isinstance(peer, (tuple, list)) and peer:
                peer_ip = str(peer[0])
        except Exception:
            peer_ip = ""

        return {
            "remote": self._clean_log_value(getattr(request, "remote", "") or peer_ip),
            "peer_ip": self._clean_log_value(peer_ip),
            "forwarded_for": self._clean_log_value(request.headers.get("X-Forwarded-For", "")),
            "real_ip": self._clean_log_value(request.headers.get("X-Real-IP", "")),
            "method": self._clean_log_value(request.method, max_len=16),
            "path": self._clean_log_value(request.path_qs, max_len=500),
            "user_agent": self._clean_log_value(request.headers.get("User-Agent", ""), max_len=300),
        }

    def _request_audit_log_suffix(self, request: "web.Request") -> str:
        ctx = self._request_audit_context(request)
        fields = [f"{key}={value!r}" for key, value in ctx.items() if value]
        return " ".join(fields) if fields else "source='unknown'"

    def _cron_origin_from_request(self, request: "web.Request") -> Dict[str, str]:
        """Persist safe API source metadata on cron jobs created over HTTP."""
        ctx = self._request_audit_context(request)
        origin = {
            "platform": "api_server",
            "chat_id": "api",
        }
        if ctx.get("remote"):
            origin["source_ip"] = ctx["remote"]
        if ctx.get("peer_ip"):
            origin["peer_ip"] = ctx["peer_ip"]
        if ctx.get("forwarded_for"):
            origin["forwarded_for"] = ctx["forwarded_for"]
        if ctx.get("real_ip"):
            origin["real_ip"] = ctx["real_ip"]
        if ctx.get("user_agent"):
            origin["user_agent"] = ctx["user_agent"]
        return origin

    # ------------------------------------------------------------------
    # Auth helper
    # ------------------------------------------------------------------

    def _check_auth(self, request: "web.Request") -> Optional["web.Response"]:
        """
        Validate Bearer token from Authorization header.

        Returns None if auth is OK, or a 401 web.Response on failure.
        connect() refuses to start the API server without API_SERVER_KEY, so
        the no-key branch only exists for tests or unsupported manual wiring.
        """
        if not self._api_key:
            return None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if hmac.compare_digest(token, self._api_key):
                return None  # Auth OK

        logger.warning(
            "API server rejected invalid API key: %s",
            self._request_audit_log_suffix(request),
        )
        return web.json_response(
            {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            status=401,
        )

    # ------------------------------------------------------------------
    # Session header helpers
    # ------------------------------------------------------------------

    # Soft length cap for session identifiers.  Headers are bounded in
    # aggregate by aiohttp (``client_max_size`` / default 8 KiB per
    # header), but we impose a tighter limit on the session headers so a
    # caller can't burn memory by passing a multi-kilobyte "session key".
    # 256 chars is well above any realistic stable channel identifier
    # (e.g. ``agent:main:webui:dm:user-42``) while staying small enough
    # that the sanitized form is safe to pass into Honcho / state.db.
    _MAX_SESSION_HEADER_LEN = 256

    def _parse_session_key_header(
        self, request: "web.Request"
    ) -> tuple[Optional[str], Optional["web.Response"]]:
        """Extract and validate the ``X-OpenComputer-Session-Key`` header.

        The session key is a stable per-channel identifier that scopes
        long-term memory (e.g. Honcho sessions) across transcripts.  It
        is independent of ``X-OpenComputer-Session-Id``: callers may send
        either, both, or neither.

        Returns ``(session_key, None)`` on success (with an empty/absent
        header yielding ``None`` for the key), or ``(None, error_response)``
        on validation failure.

        Security: like session continuation, accepting a caller-supplied
        memory scope requires API-key authentication so that an
        unauthenticated client on a local-only server can't inject itself
        into another user's long-term memory scope by guessing a key.
        """
        raw = request.headers.get("X-OpenComputer-Session-Key", "").strip()
        if not raw:
            return None, None

        if not self._api_key:
            logger.warning(
                "X-OpenComputer-Session-Key rejected: no API key configured. "
                "Set API_SERVER_KEY to enable long-term memory scoping."
            )
            return None, web.json_response(
                _openai_error(
                    "X-OpenComputer-Session-Key requires API key authentication. "
                    "Configure API_SERVER_KEY to enable this feature."
                ),
                status=403,
            )

        # Reject control characters that could enable header injection on
        # the echo path.
        if re.search(r'[\r\n\x00]', raw):
            return None, web.json_response(
                {"error": {"message": "Invalid session key", "type": "invalid_request_error"}},
                status=400,
            )

        if len(raw) > self._MAX_SESSION_HEADER_LEN:
            return None, web.json_response(
                {"error": {"message": "Session key too long", "type": "invalid_request_error"}},
                status=400,
            )

        return raw, None

    # ------------------------------------------------------------------
    # Session DB helper
    # ------------------------------------------------------------------

    def _ensure_session_db(self):
        """Lazily initialise and return the shared SessionDB instance.

        Sessions are persisted to ``state.db`` so that ``oc sessions list``
        shows API-server conversations alongside CLI and gateway ones.
        """
        if self._session_db is None:
            try:
                from hermes_state import SessionDB
                self._session_db = SessionDB()
            except Exception as e:
                logger.debug("SessionDB unavailable for API server: %s", e)
        return self._session_db

    # ------------------------------------------------------------------
    # Per-agent profiles: each frontend "agent" gets its own backend profile
    # = its own state.db (sessions/history/FTS5) + its own memory dir, isolated
    # from the default/main chat agent (which keeps Honcho + GBrain). Activated
    # when a request carries ``oc_agent_id``.
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_oc_agent_id(body: Dict[str, Any]) -> Optional[str]:
        """Validate the optional ``oc_agent_id`` profile slug from a request.

        Returns a safe slug (``[a-z0-9][a-z0-9-]*``, <=64 chars, no path
        traversal) or None. None means "use the default/main agent".
        """
        raw = body.get("oc_agent_id")
        if not isinstance(raw, str):
            return None
        slug = raw.strip().lower()
        if not slug or len(slug) > 64:
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            return None
        return slug

    def _get_agent_profile_db(self, slug: Optional[str]):
        """Return a cached SessionDB for an agent profile's own state.db.

        Path: ``{hermes_home}/agent-profiles/{slug}/state.db`` — its own
        sessions, message history, and FTS5 search, fully isolated per agent.
        Returns None for the default agent (no slug) or on failure.
        """
        if not slug:
            return None
        cached = self._agent_profile_dbs.get(slug)
        if cached is not None:
            return cached
        # Double-checked locking: hold the lock across the open so only one
        # SessionDB (one sqlite connection) is ever created per slug, even when
        # the event loop and an executor thread race on first use.
        with self._agent_profile_dbs_lock:
            cached = self._agent_profile_dbs.get(slug)
            if cached is not None:
                return cached
            try:
                from hermes_constants import get_hermes_home
                from hermes_state import SessionDB
                profile_dir = get_hermes_home() / "agent-profiles" / slug
                profile_dir.mkdir(parents=True, exist_ok=True)
                db = SessionDB(db_path=profile_dir / "state.db")
                self._agent_profile_dbs[slug] = db
                return db
            except Exception as e:
                logger.warning("Failed to open agent-profile DB for %s: %s", slug, e)
                return None

    def _agent_db_for_request(self, request: "web.Request"):
        """Return the profile SessionDB for the request's agent, or None.

        Reads the ``X-OpenComputer-Agent-Id`` header so session reads/deletes can
        be routed to the same per-agent profile db that the chat turns persist
        to (fixes "resume an agent chat shows no messages"). None => shared db.
        """
        raw = request.headers.get("X-OpenComputer-Agent-Id", "")
        slug = self._parse_oc_agent_id({"oc_agent_id": raw})
        return self._get_agent_profile_db(slug) if slug else None

    # ------------------------------------------------------------------
    # Agent creation helper
    # ------------------------------------------------------------------

    def _create_agent(
        self,
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        reasoning_callback=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        status_callback=None,
        gateway_session_key: Optional[str] = None,
        model_override: Optional[str] = None,
        reasoning_config_override: Optional[Dict[str, Any]] = None,
        session_db_override: Optional[Any] = None,
        is_agent_profile: bool = False,
        replace_system_prompt: bool = False,
    ) -> Any:
        """
        Create an AIAgent instance using the gateway's runtime config.

        Uses _resolve_runtime_agent_kwargs() to pick up model, api_key,
        base_url, etc. from config.yaml / env vars.  Toolsets are resolved
        from config.yaml platform_toolsets.api_server (same as all other
        gateway platforms), falling back to the hermes-api-server default.

        ``gateway_session_key`` is a stable per-channel identifier supplied
        by the client (via ``X-OpenComputer-Session-Key``).  Unlike ``session_id``
        which scopes the short-term transcript and rotates on /new, this
        key is meant to persist across transcripts so long-term memory
        providers (e.g. Honcho) can scope their per-chat state correctly
        — matching the semantics of the native gateway's ``session_key``.
        """
        from run_agent import AIAgent
        from gateway.run import _resolve_runtime_agent_kwargs, _resolve_gateway_model, _load_gateway_config, GatewayRunner
        from hermes_cli.tools_config import _get_platform_tools

        runtime_kwargs = _resolve_runtime_agent_kwargs()
        reasoning_config = GatewayRunner._load_reasoning_config()
        model = _resolve_gateway_model()

        # Per-request overrides (from the prompt-bar model picker).  When the
        # caller pins a model / reasoning effort / thinking toggle for this turn,
        # honor it instead of the config defaults.  model_override is trusted to
        # be a provider-routable id (validated/whitelisted by the handler).
        if model_override and model_override.strip():
            model = model_override.strip()
        if reasoning_config_override is not None:
            reasoning_config = reasoning_config_override

        user_config = _load_gateway_config()
        enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))

        # Per-MODEL credential routing (the OC Router binds one platform per API
        # key). When the selected model is OpenAI-family (gpt-*/o-series/codex)
        # and the user has configured a `providers.openai` entry in config.yaml
        # (the OpenAI-group router key + base_url), use ITS credentials for this
        # turn so GPT models actually route to the OpenAI group instead of 404-ing
        # against the default Anthropic-group key. claude-* turns are untouched.
        if _is_openai_family_model(model):
            _providers = user_config.get("providers")
            _oai = _providers.get("openai") if isinstance(_providers, dict) else None
            if isinstance(_oai, dict) and _oai.get("api_key"):
                runtime_kwargs = dict(runtime_kwargs)
                runtime_kwargs["api_key"] = _oai["api_key"]
                if _oai.get("base_url"):
                    runtime_kwargs["base_url"] = _oai["base_url"]
                runtime_kwargs["provider"] = _oai.get("provider", "custom")
                runtime_kwargs["api_mode"] = _oai.get("api_mode", "chat_completions")
                logger.info(
                    "[%s] OpenAI-family model %s → providers.openai credentials",
                    self.name, model,
                )

        # Per-agent profiles are local-only: they get their own
        # SQLite/FTS5/markdown memory but NOT the shared external memory plane
        # (Honcho provider + GBrain MCP), which stays exclusive to the main
        # agent. Drop the gbrain toolset here; the Honcho provider is skipped
        # via disable_memory_provider below. The local `memory` toolset stays.
        if is_agent_profile:
            enabled_toolsets = [t for t in enabled_toolsets if t != "gbrain"]

        max_iterations = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))

        # Load fallback provider chain so the API server platform has the
        # same fallback behaviour as Telegram/Discord/Slack (fixes #4954).
        fallback_model = GatewayRunner._load_fallback_model()

        agent = AIAgent(
            model=model,
            **runtime_kwargs,
            max_iterations=max_iterations,
            quiet_mode=True,
            verbose_logging=False,
            ephemeral_system_prompt=ephemeral_system_prompt or None,
            enabled_toolsets=enabled_toolsets,
            session_id=session_id,
            platform="api_server",
            stream_delta_callback=stream_delta_callback,
            reasoning_callback=reasoning_callback,
            tool_progress_callback=tool_progress_callback,
            tool_start_callback=tool_start_callback,
            tool_complete_callback=tool_complete_callback,
            status_callback=status_callback,
            session_db=session_db_override or self._ensure_session_db(),
            fallback_model=fallback_model,
            reasoning_config=reasoning_config,
            gateway_session_key=gateway_session_key,
            # Profile agents keep their local memory store but skip the external
            # Honcho provider (GBrain toolset already filtered above).
            disable_memory_provider=is_agent_profile,
            # When the agent overwrites the base prompt, the ephemeral system
            # prompt replaces (not extends) the base for this turn.
            ephemeral_system_replaces_base=replace_system_prompt,
        )
        return agent

    def _parse_oc_overrides(self, body: Dict[str, Any]) -> tuple:
        """Parse the prompt-bar model-picker fields from a request body.

        Returns ``(model_override, reasoning_config_override)``.  Sent under
        dedicated ``oc_*`` body fields so they never collide with the OpenAI
        ``model`` field arbitrary OpenAI-compat clients populate.  Shared by
        ``/v1/chat/completions`` and ``/v1/responses`` so both honor the picker
        (and so neither path can reference these names undefined).
        """
        oc_model = body.get("oc_model")
        model_override = (
            oc_model.strip()
            if isinstance(oc_model, str)
            and oc_model.strip()
            and not re.search(r'[\r\n\x00]', oc_model)
            and len(oc_model.strip()) <= self._MAX_SESSION_HEADER_LEN
            else None
        )
        reasoning_config_override: Optional[Dict[str, Any]] = None
        oc_thinking = body.get("oc_thinking")
        oc_effort = body.get("oc_reasoning_effort")
        if oc_thinking is False:
            reasoning_config_override = {"enabled": False}
        elif isinstance(oc_effort, str) and oc_effort.strip():
            from hermes_constants import parse_reasoning_effort
            reasoning_config_override = parse_reasoning_effort(oc_effort.strip())
        return model_override, reasoning_config_override

    # ------------------------------------------------------------------
    # HTTP Handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """GET /health — simple health check."""
        return web.json_response(
            {"status": "ok", "platform": "open-computer", "version": _hermes_version()}
        )

    async def _handle_health_detailed(self, request: "web.Request") -> "web.Response":
        """GET /health/detailed — rich status for cross-container dashboard probing.

        Returns gateway state, connected platforms, PID, and uptime so the
        dashboard can display full status without needing a shared PID file or
        /proc access.  No authentication required.
        """
        from gateway.status import read_runtime_status

        try:
            from hermes_cli.profiles import get_active_profile_name
            _profile = get_active_profile_name() or "default"
        except Exception:
            _profile = "default"

        runtime = read_runtime_status() or {}
        # Active execution sandbox so the UI can show "Docker sandbox" vs "Host".
        # Read the (already-resolved) TERMINAL_ENV without probing — sandbox_resolver
        # writes the concrete backend back here after first use; "auto" means it
        # resolves on the first agent terminal call.
        _term_backend = (os.getenv("TERMINAL_ENV") or "auto").strip().lower()
        try:
            from tools.sandbox_resolver import ISOLATED_BACKENDS as _ISO
            _sandboxed = _term_backend in _ISO
        except Exception:
            _sandboxed = False
        return web.json_response({
            "status": "ok",
            "platform": "open-computer",
            "profile": _profile,
            "version": _hermes_version(),
            "gateway_state": runtime.get("gateway_state"),
            "platforms": runtime.get("platforms", {}),
            "active_agents": runtime.get("active_agents", 0),
            "exit_reason": runtime.get("exit_reason"),
            "updated_at": runtime.get("updated_at"),
            "pid": os.getpid(),
            "terminal_backend": _term_backend,
            "sandboxed": _sandboxed,
        })

    async def _handle_models(self, request: "web.Request") -> "web.Response":
        """GET /v1/models — return hermes-agent as an available model."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        return web.json_response({
            "object": "list",
            "data": [
                {
                    "id": self._model_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "opencomputer",
                    "permission": [],
                    "root": self._model_name,
                    "parent": None,
                }
            ],
        })

    async def _handle_oc_model_availability(self, request: "web.Request") -> "web.Response":
        """GET /v1/oc/model_availability?models=a,b,c — cheap live availability.

        Probes each model with a minimal ``max_tokens=1`` completion against the
        configured provider (no agent loop, no tools, no system prompt), so the
        web prompt-bar picker can grey out models that currently fail (e.g. the
        router's credit-gating) AND auto-enable them the moment they work —
        without anyone editing the hardcoded list. Results are cached in-process
        (TTL), so this is one cheap probe-set per cache window, never continuous
        polling. Model-agnostic: uses the resolved provider's base_url/api_key;
        only the chat_completions api_mode is probed — others report
        ``available: null`` and the client falls back to its static defaults.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        raw = request.query.get("models", "")
        models = [m.strip() for m in raw.split(",") if m.strip()][:16]
        if not models:
            models = list(_DEFAULT_AVAIL_MODELS)
        result = await _probe_model_availability(models)
        return web.json_response(result)

    async def _handle_capabilities(self, request: "web.Request") -> "web.Response":
        """GET /v1/capabilities — advertise the stable API surface.

        External UIs and orchestrators use this endpoint to discover the API
        server's plugin-safe contract without scraping docs or assuming that
        every OpenComputer version exposes the same endpoints.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        return web.json_response({
            "object": "hermes.api_server.capabilities",
            "platform": "open-computer",
            "model": self._model_name,
            "auth": {
                "type": "bearer",
                "required": bool(self._api_key),
            },
            "runtime": {
                "mode": "server_agent",
                "tool_execution": "server",
                "split_runtime": False,
                "description": (
                    "The API server creates a server-side OpenComputer AIAgent; "
                    "tools execute on the API-server host unless a future "
                    "explicit split-runtime mode is enabled."
                ),
            },
            "features": {
                "chat_completions": True,
                "chat_completions_streaming": True,
                "responses_api": True,
                "responses_streaming": True,
                "run_submission": True,
                "run_status": True,
                "run_events_sse": True,
                "run_stop": True,
                "run_approval_response": True,
                "tool_progress_events": True,
                "approval_events": True,
                "session_resources": True,
                "session_chat": True,
                "session_chat_streaming": True,
                "session_fork": True,
                "admin_config_rw": False,
                "jobs_admin": False,
                "memory_write_api": False,
                "skills_api": True,
                "audio_api": False,
                "realtime_voice": False,
                "session_continuity_header": "X-OpenComputer-Session-Id",
                "session_key_header": "X-OpenComputer-Session-Key",
                "cors": bool(self._cors_origins),
            },
            "endpoints": {
                "health": {"method": "GET", "path": "/health"},
                "health_detailed": {"method": "GET", "path": "/health/detailed"},
                "models": {"method": "GET", "path": "/v1/models"},
                "chat_completions": {"method": "POST", "path": "/v1/chat/completions"},
                "responses": {"method": "POST", "path": "/v1/responses"},
                "runs": {"method": "POST", "path": "/v1/runs"},
                "run_status": {"method": "GET", "path": "/v1/runs/{run_id}"},
                "run_events": {"method": "GET", "path": "/v1/runs/{run_id}/events"},
                "run_approval": {"method": "POST", "path": "/v1/runs/{run_id}/approval"},
                "run_stop": {"method": "POST", "path": "/v1/runs/{run_id}/stop"},
                "skills": {"method": "GET", "path": "/v1/skills"},
                "toolsets": {"method": "GET", "path": "/v1/toolsets"},
                "sessions": {"method": "GET", "path": "/api/sessions"},
                "session_create": {"method": "POST", "path": "/api/sessions"},
                "session": {"method": "GET", "path": "/api/sessions/{session_id}"},
                "session_update": {"method": "PATCH", "path": "/api/sessions/{session_id}"},
                "session_delete": {"method": "DELETE", "path": "/api/sessions/{session_id}"},
                "session_messages": {"method": "GET", "path": "/api/sessions/{session_id}/messages"},
                "session_fork": {"method": "POST", "path": "/api/sessions/{session_id}/fork"},
                "session_chat": {"method": "POST", "path": "/api/sessions/{session_id}/chat"},
                "session_chat_stream": {"method": "POST", "path": "/api/sessions/{session_id}/chat/stream"},
            },
        })

    async def _handle_skills(self, request: "web.Request") -> "web.Response":
        """GET /v1/skills — list installed skills visible to the API-server agent.

        Read-only listing intended for external clients that need to know
        which skills are available without sending a chat message and asking
        the model. Mirrors what the gateway/CLI surfaces through
        ``/skills list``, but as a deterministic JSON payload.

        Returns the same skill metadata (name, description, category) the
        skills hub uses internally. Disabled skills are excluded so the
        listing matches what the agent actually loads.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        try:
            from tools.skills_tool import _find_all_skills, _sort_skills
            skills = _sort_skills(_find_all_skills(skip_disabled=False))
        except Exception:
            logger.exception("GET /v1/skills failed")
            return web.json_response(
                _openai_error("Failed to enumerate skills", err_type="server_error"),
                status=500,
            )

        return web.json_response({
            "object": "list",
            "data": skills,
        })

    async def _handle_toolsets(self, request: "web.Request") -> "web.Response":
        """GET /v1/toolsets — list toolsets and their resolved tools.

        Returns the toolset surface the api_server platform actually exposes
        to its agent: each toolset's enabled/configured state plus the
        concrete tool names it expands to. This is the deterministic
        equivalent of what a client would otherwise have to recover by
        asking the model what tools it can call.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        try:
            from hermes_cli.config import load_config
            from hermes_cli.tools_config import (
                _get_effective_configurable_toolsets,
                _get_platform_tools,
                _toolset_has_keys,
            )
            from toolsets import resolve_toolset

            config = load_config()
            enabled_toolsets = _get_platform_tools(
                config,
                "api_server",
                include_default_mcp_servers=False,
            )
            data: List[Dict[str, Any]] = []
            for name, label, desc in _get_effective_configurable_toolsets():
                try:
                    tools = sorted(set(resolve_toolset(name)))
                except Exception:
                    tools = []
                is_enabled = name in enabled_toolsets
                data.append({
                    "name": name,
                    "label": label,
                    "description": desc,
                    "enabled": is_enabled,
                    "configured": _toolset_has_keys(name, config),
                    "tools": tools,
                })
        except Exception:
            logger.exception("GET /v1/toolsets failed")
            return web.json_response(
                _openai_error("Failed to enumerate toolsets", err_type="server_error"),
                status=500,
            )

        return web.json_response({
            "object": "list",
            "platform": "api_server",
            "data": data,
        })

    # ------------------------------------------------------------------
    # /api/sessions — thin client/session resource API
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_nonnegative_int(value: Any, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed < 0:
            return default
        return min(parsed, maximum)

    @staticmethod
    def _session_response(session: Dict[str, Any]) -> Dict[str, Any]:
        """Return a stable, client-safe session representation."""
        safe_keys = (
            "id", "source", "user_id", "model", "title", "started_at", "ended_at",
            "end_reason", "message_count", "tool_call_count", "input_tokens",
            "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd",
            "api_call_count", "parent_session_id", "last_active", "preview",
            "_lineage_root_id",
        )
        payload = {key: session.get(key) for key in safe_keys if key in session}
        # Avoid exposing full system prompts/model_config through the client API;
        # callers only need to know whether those snapshots exist.
        payload["has_system_prompt"] = bool(session.get("system_prompt"))
        payload["has_model_config"] = bool(session.get("model_config"))
        return payload

    @staticmethod
    def _message_response(message: Dict[str, Any]) -> Dict[str, Any]:
        safe_keys = (
            "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
            "tool_name", "timestamp", "token_count", "finish_reason", "reasoning",
            "reasoning_content",
        )
        return {key: message.get(key) for key in safe_keys if key in message}

    async def _read_json_body(self, request: "web.Request") -> tuple[Dict[str, Any], Optional["web.Response"]]:
        try:
            body = await request.json()
        except Exception:
            return {}, web.json_response(_openai_error("Invalid JSON in request body"), status=400)
        if not isinstance(body, dict):
            return {}, web.json_response(_openai_error("Request body must be a JSON object"), status=400)
        return body, None

    def _get_existing_session_or_404(self, session_id: str, db: Optional[Any] = None) -> tuple[Optional[Dict[str, Any]], Optional["web.Response"]]:
        db = db if db is not None else self._ensure_session_db()
        if db is None:
            return None, web.json_response(_openai_error("Session database unavailable", code="session_db_unavailable"), status=503)
        session = db.get_session(session_id)
        if not session:
            return None, web.json_response(_openai_error(f"Session not found: {session_id}", code="session_not_found"), status=404)
        return session, None

    def _conversation_history_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        db = self._ensure_session_db()
        if db is None:
            return []
        try:
            return db.get_messages_as_conversation(session_id)
        except Exception as exc:
            logger.warning("Failed to load session history for %s: %s", session_id, exc)
            return []

    async def _handle_list_sessions(self, request: "web.Request") -> "web.Response":
        """GET /api/sessions — list persisted OpenComputer sessions."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        db = self._ensure_session_db()
        if db is None:
            return web.json_response(_openai_error("Session database unavailable", code="session_db_unavailable"), status=503)

        limit = self._parse_nonnegative_int(request.query.get("limit"), default=50, maximum=200)
        offset = self._parse_nonnegative_int(request.query.get("offset"), default=0, maximum=1_000_000)
        # Default to api_server-only so the WebUI never lists CLI/Telegram/etc.
        # sessions (keeps WebUI and CLI chat histories isolated). A client can
        # still pass ?source=<other> explicitly, or ?source=all for everything.
        _src_param = request.query.get("source")
        if _src_param is None or _src_param == "":
            source = "api_server"
        elif _src_param == "all":
            source = None
        else:
            source = _src_param
        include_children = _coerce_request_bool(request.query.get("include_children"), default=False)
        sessions = db.list_sessions_rich(
            source=source,
            limit=limit,
            offset=offset,
            include_children=include_children,
            order_by_last_active=True,
        )
        return web.json_response({
            "object": "list",
            "data": [self._session_response(s) for s in sessions],
            "limit": limit,
            "offset": offset,
            "has_more": len(sessions) == limit,
        })

    async def _handle_get_memory(self, request: "web.Request") -> "web.Response":
        """GET /api/memory — unified read-only view of the three memory planes
        (local Markdown, Honcho, GBrain) that powers the frontend Memory tab."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            from gateway.platforms.memory_aggregator import build_memory_payload
            from hermes_constants import get_hermes_home

            payload = await build_memory_payload(get_hermes_home())
            return web.json_response(payload)
        except Exception as e:  # never 500 the dashboard
            logger.warning("memory aggregation failed: %s", e)
            return web.json_response(
                {
                    "local": {"enabled": False, "error": str(e)},
                    "honcho": {"enabled": False, "error": str(e)},
                    "gbrain": {"enabled": False, "error": str(e)},
                },
                status=200,
            )

    async def _handle_create_session(self, request: "web.Request") -> "web.Response":
        """POST /api/sessions — create an empty OpenComputer session row."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err

        db = self._ensure_session_db()
        if db is None:
            return web.json_response(_openai_error("Session database unavailable", code="session_db_unavailable"), status=503)

        raw_id = body.get("id") or body.get("session_id")
        session_id = str(raw_id).strip() if raw_id else f"api_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        if not session_id or re.search(r'[\r\n\x00]', session_id):
            return web.json_response(_openai_error("Invalid session ID", code="invalid_session_id"), status=400)
        if len(session_id) > self._MAX_SESSION_HEADER_LEN:
            return web.json_response(_openai_error("Session ID too long", code="invalid_session_id"), status=400)
        if db.get_session(session_id):
            return web.json_response(_openai_error(f"Session already exists: {session_id}", code="session_exists"), status=409)

        model = body.get("model") or self._model_name
        system_prompt = body.get("system_prompt")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return web.json_response(_openai_error("system_prompt must be a string", code="invalid_system_prompt"), status=400)
        db.create_session(session_id, "api_server", model=str(model) if model else None, system_prompt=system_prompt)
        title = body.get("title")
        if title is not None:
            try:
                db.set_session_title(session_id, str(title))
            except ValueError as exc:
                db.delete_session(session_id)
                return web.json_response(_openai_error(str(exc), code="invalid_title"), status=400)
        session = db.get_session(session_id) or {"id": session_id, "source": "api_server", "model": model, "title": title}
        return web.json_response({"object": "hermes.session", "session": self._session_response(session)}, status=201)

    async def _handle_get_session(self, request: "web.Request") -> "web.Response":
        """GET /api/sessions/{session_id}."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session, err = self._get_existing_session_or_404(request.match_info["session_id"])
        if err:
            return err
        return web.json_response({"object": "hermes.session", "session": self._session_response(session)})

    async def _handle_patch_session(self, request: "web.Request") -> "web.Response":
        """PATCH /api/sessions/{session_id} — update client-safe session metadata."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        session, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        allowed = {"title", "end_reason"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return web.json_response(_openai_error(f"Unsupported session fields: {', '.join(unknown)}", code="unsupported_session_field"), status=400)

        db = self._ensure_session_db()
        if "title" in body:
            try:
                db.set_session_title(session_id, "" if body["title"] is None else str(body["title"]))
            except ValueError as exc:
                return web.json_response(_openai_error(str(exc), code="invalid_title"), status=400)
        if body.get("end_reason"):
            db.end_session(session_id, str(body["end_reason"]))
        session = db.get_session(session_id) or session
        return web.json_response({"object": "hermes.session", "session": self._session_response(session)})

    async def _handle_delete_session(self, request: "web.Request") -> "web.Response":
        """DELETE /api/sessions/{session_id}.

        An agent-scoped chat has its metadata row in the shared db (created
        there for the global session list) and its turns persisted in the
        agent's own profile db. Delete from BOTH so no orphaned messages are
        left behind in the profile db when the chat is removed.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]

        shared_db = self._ensure_session_db()
        agent_db = self._agent_db_for_request(request)
        # Distinct db objects to act on (agent_db may be None or, defensively,
        # the same object as shared_db — dedupe by identity).
        targets = [shared_db]
        if agent_db is not None and agent_db is not shared_db:
            targets.append(agent_db)

        # 404 only if the session exists in NONE of the candidate dbs.
        existed = False
        for db in targets:
            try:
                if db is not None and db.get_session(session_id) is not None:
                    existed = True
                    break
            except Exception:
                continue
        if not existed:
            return web.json_response(_openai_error(f"Session not found: {session_id}", code="session_not_found"), status=404)

        deleted = False
        for db in targets:
            if db is None:
                continue
            try:
                if db.delete_session(session_id):
                    deleted = True
            except Exception:
                # Best-effort cleanup across dbs; one failing db must not block
                # the others or leave the caller with a 500 on a real delete.
                continue
        return web.json_response({"object": "hermes.session.deleted", "id": session_id, "deleted": bool(deleted)})

    async def _handle_session_messages(self, request: "web.Request") -> "web.Response":
        """GET /api/sessions/{session_id}/messages."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        # Route to the agent's profile db when the request is agent-scoped, so a
        # resumed agent chat returns the messages persisted by its turns.
        db = self._agent_db_for_request(request) or self._ensure_session_db()
        _, err = self._get_existing_session_or_404(session_id, db)
        if err:
            return err
        resolved_id = db.resolve_resume_session_id(session_id)
        messages = db.get_messages(resolved_id)
        return web.json_response({
            "object": "list",
            "session_id": resolved_id,
            "data": [self._message_response(m) for m in messages],
        })

    # ---- Artifacts -------------------------------------------------------
    # write_file / patch completions register the file they produced so the web
    # UI can present it (claude.ai-style card → panel) and download it by id.

    @staticmethod
    def _artifact_kind(suffix: str, mime: str) -> str:
        """Coarse category the web UI uses to pick a preview renderer."""
        s = (suffix or "").lower().lstrip(".")
        if s == "svg":
            return "svg"
        if mime.startswith("image/"):
            return "image"
        if s in ("md", "markdown"):
            return "markdown"
        if s == "pdf":
            return "pdf"
        if s == "csv":
            return "csv"
        if s in ("xlsx", "xls"):
            return "xlsx"
        if s in ("docx", "doc"):
            return "docx"
        if s in (
            "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "c", "cc",
            "cpp", "h", "hpp", "rb", "sh", "bash", "zsh", "json", "yaml",
            "yml", "toml", "html", "css", "scss", "sql", "xml", "kt", "swift",
        ):
            return "code"
        return "file"

    @staticmethod
    def _extract_written_paths(function_result: Any) -> list:
        """Absolute file paths a write_file/patch result reports (best-effort)."""
        data = function_result
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return []
        if not isinstance(data, dict):
            return []
        paths: list = []
        fm = data.get("files_modified")
        if isinstance(fm, list):
            paths.extend(str(x) for x in fm if x)
        rp = data.get("resolved_path")
        if rp and str(rp) not in paths:
            paths.append(str(rp))
        return paths

    @staticmethod
    def _artifact_id_for(session_id: Optional[str], path: str) -> str:
        """Deterministic artifact id for a (session, path).

        Stable across reloads AND gateway restarts, so a card minted live
        during a turn and one re-hydrated from message history later resolve to
        the SAME id (and the same download URL). This is what makes artifacts
        durable — "create a file, leave, come back" still shows + downloads it.
        """
        import hashlib
        h = hashlib.sha1(f"{session_id or ''}:{path}".encode("utf-8")).hexdigest()
        return f"art-{h[:20]}"

    def _build_artifact_descriptor(self, session_id: Optional[str], path: str) -> Optional[Dict[str, Any]]:
        """Build a wire descriptor for a produced file (deterministic id) and
        cache it in-memory for the live turn. Returns None for non-files."""
        import mimetypes
        import pathlib
        p = pathlib.Path(path)
        # Only present real files (skip dirs / phantom paths from diff output).
        if not p.is_file():
            return None
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        artifact_id = self._artifact_id_for(session_id, str(p))
        descriptor = {
            "artifact_id": artifact_id,
            "title": p.name,
            "kind": self._artifact_kind(p.suffix, mime),
            "path": str(p),
            "mime_type": mime,
            "size_bytes": size,
        }
        rec = dict(descriptor)
        rec["session_id"] = session_id or ""
        self._artifacts_by_id[artifact_id] = rec
        key = session_id or "_none"
        lst = self._session_artifacts.setdefault(key, [])
        if not any(r.get("artifact_id") == artifact_id for r in lst):
            lst.append(rec)
            if len(lst) > 200:  # bound memory: keep the most recent
                del lst[:-200]
        return descriptor

    # Streaming emit calls this on write_file/patch completion.
    def _register_artifact(self, session_id: Optional[str], path: str) -> Optional[Dict[str, Any]]:
        return self._build_artifact_descriptor(session_id, path)

    def _artifacts_from_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Durable artifact list derived from persisted write_file/patch tool
        results in the session's message history. Survives restarts/reloads
        because state.db is the source of truth, not the in-memory cache."""
        out: List[Dict[str, Any]] = []
        seen: set = set()
        try:
            db = self._ensure_session_db()
            if db is None:
                return out
            resolved = db.resolve_resume_session_id(session_id)
            messages = db.get_messages(resolved)
        except Exception:
            return out
        for m in messages:
            if m.get("role") != "tool" or m.get("tool_name") not in ("write_file", "patch"):
                continue
            for path in self._extract_written_paths(m.get("content")):
                if path in seen:
                    continue
                seen.add(path)
                desc = self._build_artifact_descriptor(session_id, path)
                if desc:
                    out.append(desc)
        return out

    def _collect_session_artifacts(self, session_id: str) -> List[Dict[str, Any]]:
        """Merge durable (history) + live (in-memory, current turn) artifacts,
        deduped by deterministic id."""
        merged: Dict[str, Dict[str, Any]] = {}
        for rec in self._artifacts_from_history(session_id):
            merged[rec["artifact_id"]] = dict(rec, session_id=session_id)
        for rec in self._session_artifacts.get(session_id, []):
            merged[rec["artifact_id"]] = rec
        return list(merged.values())

    async def _handle_artifact_list(self, request: "web.Request") -> "web.Response":
        """GET /api/(v1/)sessions/{session_id}/artifacts — descriptors for the browser grid."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        items = self._collect_session_artifacts(session_id)
        data = [
            {k: v for k, v in rec.items() if k not in ("path", "session_id")}
            for rec in items
        ]
        return web.json_response({"object": "list", "session_id": session_id, "data": data})

    async def _handle_artifact_download(self, request: "web.Request") -> "web.Response":
        """GET /api/(v1/)sessions/{session_id}/artifacts/{artifact_id}/download."""
        import pathlib
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info.get("session_id") or ""
        artifact_id = request.match_info["artifact_id"]
        # Resolve id -> path among THIS session's artifacts (durable history +
        # live cache). This both survives restarts and acts as the auth guard:
        # we only ever serve a file the session actually produced.
        rec = None
        for cand in self._collect_session_artifacts(session_id):
            if cand.get("artifact_id") == artifact_id:
                rec = cand
                break
        if not rec:
            return web.json_response(
                _openai_error("Artifact not found", code="artifact_not_found"), status=404
            )
        p = pathlib.Path(rec["path"])
        if not p.is_file():
            return web.json_response(
                _openai_error("Artifact file no longer available", code="artifact_gone"), status=410
            )
        try:
            body = p.read_bytes()
        except OSError:
            return web.json_response(
                _openai_error("Artifact unreadable", code="artifact_unreadable"), status=500
            )
        # Quote the filename defensively (strip CR/LF) for the header.
        safe_name = re.sub(r"[\r\n\"]", "_", p.name)
        return web.Response(
            body=body,
            content_type=rec.get("mime_type") or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )

    async def _handle_oc_image_file(self, request: "web.Request") -> "web.Response":
        """GET /api/files/{file_id} — serve a generated image from the images cache.

        The web image-generation flow saves images under
        ``$HERMES_HOME/cache/images/<filename>`` (see
        agent.image_gen_provider.save_b64_image); the frontend references each by
        that bare filename. We serve ONLY files inside that directory, resolved
        by basename, so this route can never read an arbitrary path (the choke
        point that previously left generated images unservable — the BFF proxies
        ``/api/chat/file/{id}`` here, and nothing answered).
        """
        import pathlib
        import mimetypes
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        file_id = request.match_info.get("file_id") or ""
        # Basename-only guard: reject anything that could escape the cache dir.
        if (
            not file_id
            or "/" in file_id
            or "\\" in file_id
            or ".." in file_id
            or "\x00" in file_id
        ):
            return web.json_response(
                _openai_error("Invalid file id", code="invalid_file_id"), status=400
            )
        try:
            from agent.image_gen_provider import _images_cache_dir
            base = _images_cache_dir().resolve()
        except Exception:
            return web.json_response(
                _openai_error("Image cache unavailable", code="image_cache_unavailable"),
                status=500,
            )
        p = (base / file_id).resolve()
        # Confine strictly to the images cache directory.
        if p.parent != base or not p.is_file():
            return web.json_response(
                _openai_error("File not found", code="file_not_found"), status=404
            )
        try:
            body = p.read_bytes()
        except OSError:
            return web.json_response(
                _openai_error("File unreadable", code="file_unreadable"), status=500
            )
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        safe_name = re.sub(r'[\r\n"]', "_", p.name)
        # `inline` (not attachment) so the browser <img> renders it.
        return web.Response(
            body=body,
            content_type=ctype,
            headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
        )

    async def _handle_fork_session(self, request: "web.Request") -> "web.Response":
        """POST /api/sessions/{session_id}/fork — branch via current SessionDB primitives."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        source_id = request.match_info["session_id"]
        source, err = self._get_existing_session_or_404(source_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        db = self._ensure_session_db()
        fork_id = str(body.get("id") or body.get("session_id") or f"api_{int(time.time())}_{uuid.uuid4().hex[:8]}").strip()
        if not fork_id or re.search(r'[\r\n\x00]', fork_id):
            return web.json_response(_openai_error("Invalid session ID", code="invalid_session_id"), status=400)
        if db.get_session(fork_id):
            return web.json_response(_openai_error(f"Session already exists: {fork_id}", code="session_exists"), status=409)

        # Match the CLI /branch semantics: mark the original as branched, then
        # create a child session that carries the transcript forward. This uses
        # SessionDB's native parent_session_id/end_reason visibility model rather
        # than inventing a parallel fork store.
        db.end_session(source_id, "branched")
        db.create_session(
            fork_id,
            "api_server",
            model=source.get("model"),
            system_prompt=source.get("system_prompt"),
            parent_session_id=source_id,
        )
        messages = db.get_messages(source_id)
        db.replace_messages(fork_id, messages)
        title = body.get("title")
        if title is None:
            base = source.get("title") or "fork"
            try:
                title = db.get_next_title_in_lineage(base)
            except Exception:
                title = f"{base} fork"
        try:
            db.set_session_title(fork_id, str(title))
        except ValueError as exc:
            return web.json_response(_openai_error(str(exc), code="invalid_title"), status=400)
        fork = db.get_session(fork_id) or {"id": fork_id, "parent_session_id": source_id}
        return web.json_response({"object": "hermes.session", "session": self._session_response(fork)}, status=201)

    async def _handle_session_chat(self, request: "web.Request") -> "web.Response":
        """POST /api/sessions/{session_id}/chat — one synchronous agent turn."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err
        session_id = request.match_info["session_id"]
        _, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        user_message, err = _session_chat_user_message(body)
        if err is not None:
            return err
        system_prompt = body.get("system_message") or body.get("instructions")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return web.json_response(_openai_error("system_message must be a string", code="invalid_system_message"), status=400)
        history = self._conversation_history_for_session(session_id)
        result, usage = await self._run_agent(
            user_message=user_message,
            conversation_history=history,
            ephemeral_system_prompt=system_prompt,
            session_id=session_id,
            gateway_session_key=gateway_session_key,
        )
        effective_session_id = result.get("session_id") if isinstance(result, dict) else session_id
        final_response = result.get("final_response", "") if isinstance(result, dict) else ""
        headers = {"X-OpenComputer-Session-Id": effective_session_id or session_id}
        if gateway_session_key:
            headers["X-OpenComputer-Session-Key"] = gateway_session_key
        return web.json_response(
            {
                "object": "hermes.session.chat.completion",
                "session_id": effective_session_id or session_id,
                "message": {"role": "assistant", "content": final_response},
                "usage": usage,
            },
            headers=headers,
        )

    async def _handle_session_chat_stream(self, request: "web.Request") -> "web.StreamResponse":
        """POST /api/sessions/{session_id}/chat/stream — SSE wrapper over _run_agent."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err
        session_id = request.match_info["session_id"]
        _, err = self._get_existing_session_or_404(session_id)
        if err:
            return err
        body, err = await self._read_json_body(request)
        if err:
            return err
        user_message, err = _session_chat_user_message(body)
        if err is not None:
            return err
        system_prompt = body.get("system_message") or body.get("instructions")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return web.json_response(_openai_error("system_message must be a string", code="invalid_system_message"), status=400)

        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[Optional[tuple[str, Dict[str, Any]]]]" = asyncio.Queue()
        message_id = f"msg_{uuid.uuid4().hex}"
        run_id = f"run_{uuid.uuid4().hex}"
        seq = 0

        def _event_payload(name: str, payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
            nonlocal seq
            seq += 1
            payload.setdefault("session_id", session_id)
            payload.setdefault("run_id", run_id)
            payload.setdefault("seq", seq)
            payload.setdefault("ts", time.time())
            return name, payload

        def _enqueue(name: str, payload: Dict[str, Any]) -> None:
            event = _event_payload(name, payload)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            try:
                if running_loop is loop:
                    queue.put_nowait(event)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass

        def _delta(delta: str) -> None:
            if delta:
                _enqueue("assistant.delta", {"message_id": message_id, "delta": delta})

        def _tool_progress(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs) -> None:
            if event_type == "reasoning.available":
                _enqueue("tool.progress", {"message_id": message_id, "tool_name": tool_name or "_thinking", "delta": preview or ""})
            elif event_type in {"tool.started", "tool.completed", "tool.failed"}:
                event_name = event_type.replace("tool.", "tool.")
                _enqueue(event_name, {"message_id": message_id, "tool_name": tool_name, "preview": preview, "args": args})

        approval_skey = gateway_session_key or session_id

        def _approval_notify(approval_data: Dict[str, Any]) -> None:
            # Fired (from the agent worker thread) when a command / execute_code
            # ESCALATES under smart mode and needs a human decision. Register the
            # run→session mapping + run status so the existing
            # POST /v1/runs/{run_id}/approval endpoint can resolve it, then emit
            # an approval.request SSE event the WebUI renders as an Approve card.
            event = dict(approval_data or {})
            self._run_approval_sessions[run_id] = approval_skey
            self._set_run_status(run_id, "waiting_for_approval",
                                 session_id=session_id, last_event="approval.request")
            _enqueue("approval.request", {
                "message_id": message_id,
                "command": event.get("command"),
                "description": event.get("description"),
                "pattern_key": event.get("pattern_key"),
                "choices": ["once", "session", "always", "deny"],
            })

        async def _run_and_signal() -> None:
            try:
                await queue.put(_event_payload("run.started", {"user_message": {"role": "user", "content": user_message}}))
                await queue.put(_event_payload("message.started", {"message": {"id": message_id, "role": "assistant"}}))
                history = self._conversation_history_for_session(session_id)
                result, usage = await self._run_agent(
                    user_message=user_message,
                    conversation_history=history,
                    ephemeral_system_prompt=system_prompt,
                    session_id=session_id,
                    stream_delta_callback=_delta,
                    tool_progress_callback=_tool_progress,
                    gateway_session_key=gateway_session_key,
                    approval_notify_callback=_approval_notify,
                )
                final_response = result.get("final_response", "") if isinstance(result, dict) else ""
                effective_session_id = result.get("session_id", session_id) if isinstance(result, dict) else session_id
                turn_messages = self._turn_transcript_messages(history, user_message, result) if isinstance(result, dict) else []
                await queue.put(_event_payload("assistant.completed", {
                    "session_id": effective_session_id,
                    "message_id": message_id,
                    "content": final_response,
                    "completed": True,
                    "partial": False,
                    "interrupted": False,
                }))
                await queue.put(_event_payload("run.completed", {
                    "session_id": effective_session_id,
                    "message_id": message_id,
                    "completed": True,
                    "messages": turn_messages,
                    "usage": usage,
                }))
            except Exception as exc:
                logger.exception("[api_server] session chat stream failed")
                await queue.put(_event_payload("error", {"message": str(exc)}))
            finally:
                self._run_approval_sessions.pop(run_id, None)
                await queue.put(_event_payload("done", {}))
                await queue.put(None)

        task = asyncio.create_task(_run_and_signal())
        try:
            self._background_tasks.add(task)
        except TypeError:
            pass
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._background_tasks.discard)

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-OpenComputer-Session-Id": session_id,
        }
        if gateway_session_key:
            headers["X-OpenComputer-Session-Key"] = gateway_session_key
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        last_write = time.monotonic()
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
                    last_write = time.monotonic()
                    continue
                if item is None:
                    break
                name, payload = item
                data = json.dumps(payload, ensure_ascii=False)
                await response.write(f"event: {name}\ndata: {data}\n\n".encode("utf-8"))
                last_write = time.monotonic()
        except (asyncio.CancelledError, ConnectionResetError):
            task.cancel()
            raise
        except Exception as exc:
            logger.debug("[api_server] session SSE stream error: %s", exc)
        return response

    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
        """POST /v1/chat/completions — OpenAI Chat Completions format."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # Parse request body
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(_openai_error("Invalid JSON in request body"), status=400)

        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return web.json_response(
                {"error": {"message": "Missing or invalid 'messages' field", "type": "invalid_request_error"}},
                status=400,
            )

        stream = _coerce_request_bool(body.get("stream"), default=False)

        # Extract system message (becomes ephemeral system prompt layered ON TOP of core)
        system_prompt = None
        conversation_messages: List[Dict[str, str]] = []

        for idx, msg in enumerate(messages):
            role = msg.get("role", "")
            raw_content = msg.get("content", "")
            if role == "system":
                # System messages don't support images (Anthropic rejects, OpenAI
                # text-model systems don't render them).  Flatten to text.
                content = _normalize_chat_content(raw_content)
                if system_prompt is None:
                    system_prompt = content
                else:
                    system_prompt = system_prompt + "\n" + content
            elif role in {"user", "assistant"}:
                try:
                    content = _normalize_multimodal_content(raw_content)
                except ValueError as exc:
                    return _multimodal_validation_error(exc, param=f"messages[{idx}].content")
                conversation_messages.append({"role": role, "content": content})

        # Extract the last user message as the primary input
        user_message: Any = ""
        history = []
        if conversation_messages:
            user_message = conversation_messages[-1].get("content", "")
            history = conversation_messages[:-1]

        if not _content_has_visible_payload(user_message):
            return web.json_response(
                {"error": {"message": "No user message found in messages", "type": "invalid_request_error"}},
                status=400,
            )

        # Allow caller to scope long-term memory (e.g. Honcho) with a
        # stable per-channel identifier via X-OpenComputer-Session-Key.  This
        # is independent of X-OpenComputer-Session-Id: the key persists across
        # transcripts while the id rotates when the caller starts a new
        # transcript (i.e. /new semantics).  See _parse_session_key_header.
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        # Allow caller to continue an existing session.  The canonical carrier is
        # the X-OpenComputer-Session-Id header, but OpenAI-compat clients commonly
        # send only the latest message and carry the conversation id in the request
        # body instead.  Accept the body field "oc_session_id" as a fallback so
        # those clients get the same server-side history rehydration rather than
        # silently starting a fresh session every turn — the bug behind "the web
        # UI agent has no memory of earlier turns".  Both sources go through the
        # identical auth gate + validation below.  (Only a real string is honored;
        # a non-string oc_session_id is ignored so malformed input can't coerce a
        # garbage id.)  When provided, history is loaded from state.db instead of
        # from the request body.
        #
        # Security: session continuation exposes conversation history, so it is
        # only allowed when the API key is configured and the request is
        # authenticated.  Without this gate, any unauthenticated client could
        # read arbitrary session history by guessing/enumerating session IDs.
        _body_sid = body.get("oc_session_id")
        provided_session_id = (
            request.headers.get("X-OpenComputer-Session-Id", "").strip()
            or (_body_sid.strip() if isinstance(_body_sid, str) else "")
        )
        if provided_session_id:
            if not self._api_key:
                logger.warning(
                    "Session continuation rejected: no API key configured.  "
                    "Set API_SERVER_KEY to enable session continuity."
                )
                return web.json_response(
                    _openai_error(
                        "Session continuation requires API key authentication. "
                        "Configure API_SERVER_KEY to enable this feature."
                    ),
                    status=403,
                )
            # Validate identically to the session-creation path: reject control
            # characters (header injection) and over-long ids (store abuse).
            if re.search(r'[\r\n\x00]', provided_session_id):
                return web.json_response(
                    {"error": {"message": "Invalid session ID", "type": "invalid_request_error"}},
                    status=400,
                )
            if len(provided_session_id) > self._MAX_SESSION_HEADER_LEN:
                return web.json_response(
                    {"error": {"message": "Session ID too long", "type": "invalid_request_error"}},
                    status=400,
                )
            session_id = provided_session_id
            try:
                _agent_slug = self._parse_oc_agent_id(body)
                db = (
                    self._get_agent_profile_db(_agent_slug)
                    if _agent_slug
                    else self._ensure_session_db()
                )
                if db is not None:
                    history = db.get_messages_as_conversation(session_id)
            except Exception as e:
                logger.warning("Failed to load session history for %s: %s", session_id, e)
                history = []
        else:
            # Derive a stable session ID from the conversation fingerprint so
            # that consecutive messages from the same Open WebUI (or similar)
            # conversation map to the same OpenComputer session.  The first user
            # message + system prompt are constant across all turns.
            first_user = ""
            for cm in conversation_messages:
                if cm.get("role") == "user":
                    first_user = cm.get("content", "")
                    break
            session_id = _derive_chat_session_id(system_prompt, first_user)
            # history already set from request body above

        # Per-request model / reasoning overrides from the prompt-bar model
        # picker (shared parser — see _parse_oc_overrides; /v1/responses uses it too).
        model_override, reasoning_config_override = self._parse_oc_overrides(body)
        # Per-agent profile: a frontend "agent" runs against its own state.db +
        # memory dir (agent-profiles/{slug}). None => default/main chat agent.
        agent_id_slug = self._parse_oc_agent_id(body)
        # Agent opted to overwrite (not extend) the base system prompt: use the
        # ephemeral system prompt as the entire system prompt for this turn.
        replace_system_prompt = bool(body.get("oc_replace_system_prompt"))

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        model_name = (model_override or body.get("model") or self._model_name)
        created = int(time.time())

        if stream:
            import queue as _q
            _stream_q: _q.Queue = _q.Queue()

            def _on_delta(delta):
                # Filter out None — the agent fires stream_delta_callback(None)
                # to signal the CLI display to close its response box before
                # tool execution, but the SSE writer uses None as end-of-stream
                # sentinel.  Forwarding it would prematurely close the HTTP
                # response, causing Open WebUI (and similar frontends) to miss
                # the final answer after tool calls.  The SSE loop detects
                # completion via agent_task.done() instead.
                if delta is not None:
                    _stream_q.put(delta)

            def _on_reasoning(text):
                # Forward extended-thinking deltas as a tagged queue item; the
                # SSE writer turns them into ``delta.reasoning_content`` chunks
                # which the frontend renders as a thinking block. Reasoning
                # arrives before content (Anthropic thinking), preserving order.
                if text:
                    _stream_q.put(("__reasoning__", text))

            def _on_status(kind, message):
                # Surface agent status/lifecycle activity (context compaction,
                # provider fallbacks, warnings) to the frontend as a
                # ``hermes.agent.status`` SSE event — this is the model-agnostic
                # seam that was previously CLI-only ("Gateway status_callback is
                # not yet wired"). ``kind`` is e.g. "lifecycle" | "warn".
                if message:
                    _stream_q.put(
                        ("__agent_status__", {"kind": str(kind or "info"), "message": str(message)})
                    )

            # Track which tool_call_ids we've emitted a "running" lifecycle
            # event for, so a "completed" event without a matching "running"
            # (e.g. internal/filtered tools) is silently dropped instead of
            # producing an orphaned event clients can't correlate.
            _started_tool_call_ids: set[str] = set()

            def _on_tool_start(tool_call_id, function_name, function_args):
                """Emit ``hermes.tool.progress`` with ``status: running``.

                Replaces the old ``tool_progress_callback("tool.started",
                ...)`` emit so SSE consumers receive a single event per
                tool start, carrying both the legacy ``tool``/``emoji``/
                ``label`` payload (for #6972 frontends) and the new
                ``toolCallId``/``status`` correlation fields (#16588).

                Skips tools whose names start with ``_`` so internal
                events (``_thinking``, …) stay off the wire — matching
                the prior ``_on_tool_progress`` filter exactly.
                """
                if not tool_call_id or function_name.startswith("_"):
                    return
                _started_tool_call_ids.add(tool_call_id)
                from agent.display import build_tool_preview, get_tool_emoji
                label = build_tool_preview(function_name, function_args) or function_name
                _stream_q.put(("__tool_progress__", {
                    "tool": function_name,
                    "emoji": get_tool_emoji(function_name),
                    "label": label,
                    "toolCallId": tool_call_id,
                    "status": "running",
                }))

            def _on_tool_complete(tool_call_id, function_name, function_args, function_result):
                """Emit the matching ``status: completed`` event.

                Dropped if the start was filtered (internal tool, missing
                id, or never seen) so clients never get an orphaned
                ``completed`` they can't correlate to a prior ``running``.
                """
                if not tool_call_id or tool_call_id not in _started_tool_call_ids:
                    return
                _started_tool_call_ids.discard(tool_call_id)
                _stream_q.put(("__tool_progress__", {
                    "tool": function_name,
                    "toolCallId": tool_call_id,
                    "status": "completed",
                    # Carry the tool's output so the frontend timeline can render
                    # it (delegate_task report, skill_view body, etc.). Previously
                    # dropped → every "Response" box rendered empty.
                    "result": _tool_result_to_text(function_result),
                }))
                # Artifact presentation: when a file-producing tool completes,
                # register the file and emit a named ``artifact.created`` event
                # the web UI renders as a claude.ai-style card → panel. Purely
                # observational (no model tool / system-prompt change → prompt
                # cache prefix untouched).
                if function_name in ("write_file", "patch"):
                    try:
                        for _apath in self._extract_written_paths(function_result):
                            _desc = self._register_artifact(session_id, _apath)
                            if _desc:
                                _stream_q.put(("__artifact__", {
                                    "tool_name": function_name,
                                    "artifact": _desc,
                                }))
                    except Exception:
                        logger.debug("artifact emit failed", exc_info=True)

            # Surface an escalated approval (smart mode) as a named
            # ``approval.requested`` SSE event the WebUI renders as an Approve
            # card. ``completion_id`` doubles as the run_id the frontend POSTs
            # back to /v1/runs/{id}/approval, so register the run→session map +
            # status (mirrors _handle_runs). Fires only on escalation — no
            # effect on normal chat.
            _approval_skey = gateway_session_key or session_id

            def _on_approval(approval_data):
                event = dict(approval_data or {})
                if _approval_skey:
                    self._run_approval_sessions[completion_id] = _approval_skey
                self._set_run_status(
                    completion_id, "waiting_for_approval",
                    session_id=session_id, last_event="approval.requested",
                )
                _stream_q.put(("__approval__", {
                    "approval_id": completion_id,
                    "tool_call_id": completion_id,
                    "tool_name": event.get("pattern_key") or "command",
                    "command": event.get("command"),
                    "description": event.get("description"),
                    "args": {"command": event.get("command")},
                    "choices": ["once", "session", "always", "deny"],
                }))

            # Start agent in background.  agent_ref is a mutable container
            # so the SSE writer can interrupt the agent on client disconnect.
            #
            # ``tool_progress_callback`` is intentionally not wired here:
            # it would duplicate every emit because ``run_agent`` fires it
            # side-by-side with ``tool_start_callback``/``tool_complete_callback``.
            # The structured callbacks are strictly richer (they carry the
            # tool_call id), so they own the chat-completions SSE channel.
            agent_ref = [None]
            agent_task = asyncio.ensure_future(self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                reasoning_callback=_on_reasoning,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                status_callback=_on_status,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
                model_override=model_override,
                reasoning_config_override=reasoning_config_override,
                approval_notify_callback=_on_approval,
                agent_id_slug=agent_id_slug,
                replace_system_prompt=replace_system_prompt,
            ))
            # Ensure SSE drain loops can terminate without relying on polling
            # agent_task.done(), which can race with queue timeout checks. Also
            # drop any approval run→session mapping registered by _on_approval
            # (matches the cleanup on the session-chat-stream and /v1/runs paths).
            def _on_agent_done(_fut):
                _stream_q.put(None)
                self._run_approval_sessions.pop(completion_id, None)
            agent_task.add_done_callback(_on_agent_done)

            return await self._write_sse_chat_completion(
                request, completion_id, model_name, created, _stream_q,
                agent_task, agent_ref, session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

        # Non-streaming: run the agent (with optional Idempotency-Key)
        async def _compute_completion():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
                model_override=model_override,
                reasoning_config_override=reasoning_config_override,
                agent_id_slug=agent_id_slug,
                replace_system_prompt=replace_system_prompt,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(body, keys=["model", "messages", "tools", "tool_choice", "stream"])
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_completion)
            except Exception as e:
                logger.error("Error running agent for chat completions: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_completion()
            except Exception as e:
                logger.error("Error running agent for chat completions: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )

        final_response = result.get("final_response") or ""
        is_partial = bool(result.get("partial"))
        is_failed = bool(result.get("failed"))
        completed = bool(result.get("completed", True))
        err_msg = result.get("error")

        # Decide finish_reason. OpenAI uses "length" for truncation, "stop"
        # for normal completion, and downstream SDKs accept "error" / custom
        # codes. See issue #22496.
        if is_partial and err_msg and "truncat" in err_msg.lower():
            finish_reason = "length"
        elif is_failed or (not completed and err_msg):
            finish_reason = "error"
        else:
            finish_reason = "stop"

        response_headers = {
            "X-OpenComputer-Session-Id": result.get("session_id", session_id),
        }
        if gateway_session_key:
            response_headers["X-OpenComputer-Session-Key"] = gateway_session_key

        # Hard-fail path: no usable assistant text AND a real failure → 5xx
        # with OpenAI-style error envelope so SDK clients raise instead of
        # silently rendering the internal failure string as message.content.
        if not final_response and (is_failed or is_partial):
            err_body = _openai_error(
                err_msg or "Agent run did not produce a response.",
                err_type="server_error",
                code="agent_incomplete",
            )
            err_body["error"]["hermes"] = {
                "completed": completed,
                "partial": is_partial,
                "failed": is_failed,
            }
            response_headers["X-OpenComputer-Completed"] = "false"
            response_headers["X-OpenComputer-Partial"] = "true" if is_partial else "false"
            return web.json_response(err_body, status=502, headers=response_headers)

        # Soft-partial path: we have *some* text but the run did not complete
        # (e.g. truncation with partial buffered output). Still 200 but signal
        # truncation via finish_reason="length" + OpenComputer-specific extras.
        response_data = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": final_response,
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        if is_partial or is_failed or not completed:
            response_data["hermes"] = {
                "completed": completed,
                "partial": is_partial,
                "failed": is_failed,
                "error": err_msg,
                "error_code": "output_truncated" if finish_reason == "length" else "agent_error",
            }
            response_headers["X-OpenComputer-Completed"] = "false"
            response_headers["X-OpenComputer-Partial"] = "true" if is_partial else "false"
            if err_msg:
                response_headers["X-OpenComputer-Error"] = err_msg[:200]

        return web.json_response(response_data, headers=response_headers)

    async def _write_sse_chat_completion(
        self, request: "web.Request", completion_id: str, model: str,
        created: int, stream_q, agent_task, agent_ref=None, session_id: str = None,
        gateway_session_key: str = None,
    ) -> "web.StreamResponse":
        """Write real streaming SSE from agent's stream_delta_callback queue.

        If the client disconnects mid-stream (network drop, browser tab close),
        the agent is interrupted via ``agent.interrupt()`` so it stops making
        LLM API calls, and the asyncio task wrapper is cancelled.
        """
        import queue as _q

        sse_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        # CORS middleware can't inject headers into StreamResponse after
        # prepare() flushes them, so resolve CORS headers up front.
        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        if cors:
            sse_headers.update(cors)
        if session_id:
            sse_headers["X-OpenComputer-Session-Id"] = session_id
        if gateway_session_key:
            sse_headers["X-OpenComputer-Session-Key"] = gateway_session_key
        response = web.StreamResponse(status=200, headers=sse_headers)
        await response.prepare(request)

        try:
            last_activity = time.monotonic()

            # Role chunk
            role_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            await response.write(f"data: {json.dumps(role_chunk)}\n\n".encode())
            last_activity = time.monotonic()

            # Helper — route a queue item to the correct SSE event.
            async def _emit(item):
                """Write a single queue item to the SSE stream.

                Plain strings are sent as normal ``delta.content`` chunks.
                Tagged tuples ``("__tool_progress__", payload)`` are sent
                as a custom ``event: hermes.tool.progress`` SSE event so
                frontends can display them without storing the markers in
                conversation history.  See #6972 for the original event,
                #16588 for the ``toolCallId``/``status`` lifecycle fields.
                """
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "__tool_progress__":
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: hermes.tool.progress\ndata: {event_data}\n\n".encode()
                    )
                elif isinstance(item, tuple) and len(item) == 2 and item[0] == "__agent_status__":
                    # Agent status/lifecycle activity (compaction, fallback,
                    # warnings) → custom ``event: hermes.agent.status`` event so
                    # the frontend can show what the agent is doing behind the
                    # scenes without storing it in conversation history.
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: hermes.agent.status\ndata: {event_data}\n\n".encode()
                    )
                elif isinstance(item, tuple) and len(item) == 2 and item[0] == "__approval__":
                    # Escalated approval (smart mode) → ``event: approval.requested``
                    # which the WebUI (oc-openai-stream) parses into an
                    # awaiting_approval tool chunk + Approve card. The decision is
                    # POSTed back to /v1/runs/{approval_id}/approval.
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: approval.requested\ndata: {event_data}\n\n".encode()
                    )
                elif isinstance(item, tuple) and len(item) == 2 and item[0] == "__artifact__":
                    # A file-producing tool completed → ``event: artifact.created``,
                    # which the WebUI (oc-openai-stream → oc-translator) turns into
                    # an artifact_created packet rendered as a card + side panel.
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: artifact.created\ndata: {event_data}\n\n".encode()
                    )
                elif isinstance(item, tuple) and len(item) == 2 and item[0] == "__reasoning__":
                    # Extended-thinking delta → ``delta.reasoning_content`` chunk.
                    # The frontend (oc-openai-stream) reads delta.reasoning /
                    # reasoning_content and renders it as a thinking block.
                    reasoning_chunk = {
                        "id": completion_id, "object": "chat.completion.chunk",
                        "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {"reasoning_content": item[1]}, "finish_reason": None}],
                    }
                    await response.write(f"data: {json.dumps(reasoning_chunk)}\n\n".encode())
                else:
                    content_chunk = {
                        "id": completion_id, "object": "chat.completion.chunk",
                        "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {"content": item}, "finish_reason": None}],
                    }
                    await response.write(f"data: {json.dumps(content_chunk)}\n\n".encode())
                return time.monotonic()

            # Stream content chunks as they arrive from the agent
            loop = asyncio.get_running_loop()
            while True:
                try:
                    delta = await loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5))
                except _q.Empty:
                    if agent_task.done():
                        # Drain any remaining items
                        while True:
                            try:
                                delta = stream_q.get_nowait()
                                if delta is None:
                                    break
                                last_activity = await _emit(delta)
                            except _q.Empty:
                                break
                        break
                    if time.monotonic() - last_activity >= CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS:
                        await response.write(b": keepalive\n\n")
                        last_activity = time.monotonic()
                    continue

                if delta is None:  # End of stream sentinel
                    break

                last_activity = await _emit(delta)

            # Get usage + result from the completed agent. There are TWO failure
            # shapes that must both reach the client instead of a silent "stop":
            #   • the agent RAISES (rare: unexpected/infra errors) → caught here;
            #   • the agent RETURNS ``result["failed"]=True`` with an ``error``
            #     string — the COMMON path for provider HTTP 4xx/5xx (400 "effort
            #     not supported", 402 "out of credits", 503 "no available
            #     accounts"). This used to fall straight through to a clean
            #     ``finish_reason: "stop"`` with empty content, so the UI showed
            #     nothing at all.
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            agent_error = None
            try:
                result, agent_usage = await agent_task
                usage = agent_usage or usage
                if isinstance(result, dict) and result.get("failed"):
                    agent_error = result.get("error") or "The model request failed."
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                agent_error = str(exc) or "The model request failed."
                logger.warning("Agent task %s failed: %s", completion_id, exc)

            if agent_error:
                # Surface the failure: a visible content delta so the UI shows a
                # message, plus ``finish_reason: "error"`` and an OpenAI-style
                # ``error`` envelope for programmatic clients.
                friendly = _humanize_agent_error(agent_error)
                err_delta = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": f"⚠️ {friendly}"}, "finish_reason": None}],
                }
                await response.write(f"data: {json.dumps(err_delta)}\n\n".encode())
                error_finish = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
                    "error": {"message": friendly, "type": "upstream_error"},
                }
                await response.write(f"data: {json.dumps(error_finish)}\n\n".encode())
                await response.write(b"data: [DONE]\n\n")
                return response

            # Finish chunk
            finish_chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }
            await response.write(f"data: {json.dumps(finish_chunk)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            # Client disconnected mid-stream.  Interrupt the agent so it
            # stops making LLM API calls at the next loop iteration, then
            # cancel the asyncio task wrapper.
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE client disconnected")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.info("SSE client disconnected; interrupted agent task %s", completion_id)
        except Exception as _exc:
            # Agent crashed mid-stream.  Try to emit an error chunk
            # so the client gets a proper response instead of a
            # TransferEncodingError from incomplete chunked encoding.
            import traceback as _tb
            logger.error("Agent crashed mid-stream for %s: %s", completion_id, _tb.format_exc()[:300])
            try:
                error_chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
                }
                await response.write(f"data: {json.dumps(error_chunk)}\n\n".encode())
                await response.write(b"data: [DONE]\n\n")
            except Exception:
                pass

        return response

    async def _write_sse_responses(
        self,
        request: "web.Request",
        response_id: str,
        model: str,
        created_at: int,
        stream_q,
        agent_task,
        agent_ref,
        conversation_history: List[Dict[str, str]],
        user_message: str,
        instructions: Optional[str],
        conversation: Optional[str],
        store: bool,
        session_id: str,
        gateway_session_key: Optional[str] = None,
    ) -> "web.StreamResponse":
        """Write an SSE stream for POST /v1/responses (OpenAI Responses API).

        Emits spec-compliant event types as the agent runs:

        - ``response.created`` — initial envelope (status=in_progress)
        - ``response.output_text.delta`` / ``response.output_text.done`` —
          streamed assistant text
        - ``response.output_item.added`` / ``response.output_item.done``
          with ``item.type == "function_call"`` — when the agent invokes a
          tool (both events fire; the ``done`` event carries the finalized
          ``arguments`` string)
        - ``response.output_item.added`` with
          ``item.type == "function_call_output"`` — tool result with
          ``{call_id, output, status}``
        - ``response.completed`` — terminal event carrying the full
          response object with all output items + usage (same payload
          shape as the non-streaming path for parity)
        - ``response.failed`` — terminal event on agent error

        If the client disconnects mid-stream, ``agent.interrupt()`` is
        called so the agent stops issuing upstream LLM calls, then the
        asyncio task is cancelled.  When ``store=True`` an initial
        ``in_progress`` snapshot is persisted immediately after
        ``response.created`` and disconnects update it to an
        ``incomplete`` snapshot so GET /v1/responses/{id} and
        ``previous_response_id`` chaining still have something to
        recover from.
        """
        import queue as _q

        sse_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        if cors:
            sse_headers.update(cors)
        if session_id:
            sse_headers["X-OpenComputer-Session-Id"] = session_id
        if gateway_session_key:
            sse_headers["X-OpenComputer-Session-Key"] = gateway_session_key
        response = web.StreamResponse(status=200, headers=sse_headers)
        await response.prepare(request)

        # State accumulated during the stream
        final_text_parts: List[str] = []
        # Track open function_call items by name so we can emit a matching
        # ``done`` event when the tool completes.  Order preserved.
        pending_tool_calls: List[Dict[str, Any]] = []
        # Output items we've emitted so far (used to build the terminal
        # response.completed payload).  Kept in the order they appeared.
        emitted_items: List[Dict[str, Any]] = []
        # Monotonic counter for output_index (spec requires it).
        output_index = 0
        # Monotonic counter for call_id generation if the agent doesn't
        # provide one (it doesn't, from tool_progress_callback).
        call_counter = 0
        # Canonical Responses SSE events include a monotonically increasing
        # sequence_number. Add it server-side for every emitted event so
        # clients that validate the OpenAI event schema can parse our stream.
        sequence_number = 0
        # Track the assistant message item id + content index for text
        # delta events — the spec ties deltas to a specific item.
        message_item_id = f"msg_{uuid.uuid4().hex[:24]}"
        message_output_index: Optional[int] = None
        message_opened = False

        async def _write_event(event_type: str, data: Dict[str, Any]) -> None:
            nonlocal sequence_number
            if "sequence_number" not in data:
                data["sequence_number"] = sequence_number
            sequence_number += 1
            payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            await response.write(payload.encode())

        def _envelope(status: str) -> Dict[str, Any]:
            env: Dict[str, Any] = {
                "id": response_id,
                "object": "response",
                "status": status,
                "created_at": created_at,
                "model": model,
            }
            return env

        final_response_text = ""
        agent_error: Optional[str] = None
        usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        terminal_snapshot_persisted = False

        def _persist_response_snapshot(
            response_env: Dict[str, Any],
            *,
            conversation_history_snapshot: Optional[List[Dict[str, Any]]] = None,
        ) -> None:
            if not store:
                return
            if conversation_history_snapshot is None:
                conversation_history_snapshot = list(conversation_history)
                conversation_history_snapshot.append({"role": "user", "content": user_message})
            self._response_store.put(response_id, {
                "response": response_env,
                "conversation_history": conversation_history_snapshot,
                "instructions": instructions,
                "session_id": session_id,
            })
            if conversation:
                self._response_store.set_conversation(conversation, response_id)

        def _persist_incomplete_if_needed() -> None:
            """Persist an ``incomplete`` snapshot if no terminal one was written.

            Called from both the client-disconnect (``ConnectionResetError``)
            and server-cancellation (``asyncio.CancelledError``) paths so
            GET /v1/responses/{id} and ``previous_response_id`` chaining keep
            working after abrupt stream termination.
            """
            if not store or terminal_snapshot_persisted:
                return
            incomplete_text = "".join(final_text_parts) or final_response_text
            incomplete_items: List[Dict[str, Any]] = list(emitted_items)
            if incomplete_text:
                incomplete_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": incomplete_text}],
                })
            incomplete_env = _envelope("incomplete")
            incomplete_env["output"] = incomplete_items
            incomplete_env["usage"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
            incomplete_history = list(conversation_history)
            incomplete_history.append({"role": "user", "content": user_message})
            if incomplete_text:
                incomplete_history.append({"role": "assistant", "content": incomplete_text})
            _persist_response_snapshot(
                incomplete_env,
                conversation_history_snapshot=incomplete_history,
            )

        try:
            # response.created — initial envelope, status=in_progress
            created_env = _envelope("in_progress")
            created_env["output"] = []
            await _write_event("response.created", {
                "type": "response.created",
                "response": created_env,
            })
            _persist_response_snapshot(created_env)
            last_activity = time.monotonic()

            async def _open_message_item() -> None:
                """Emit response.output_item.added for the assistant message
                the first time any text delta arrives."""
                nonlocal message_opened, message_output_index, output_index
                if message_opened:
                    return
                message_opened = True
                message_output_index = output_index
                output_index += 1
                item = {
                    "id": message_item_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                }
                await _write_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": message_output_index,
                    "item": item,
                })

            async def _emit_text_delta(delta_text: str) -> None:
                await _open_message_item()
                final_text_parts.append(delta_text)
                await _write_event("response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "item_id": message_item_id,
                    "output_index": message_output_index,
                    "content_index": 0,
                    "delta": delta_text,
                    "logprobs": [],
                })

            async def _emit_tool_started(payload: Dict[str, Any]) -> str:
                """Emit response.output_item.added for a function_call.

                Returns the call_id so the matching completion event can
                reference it.  Prefer the real ``tool_call_id`` from the
                agent when available; fall back to a generated call id for
                safety in tests or older code paths.
                """
                nonlocal output_index, call_counter
                call_counter += 1
                call_id = payload.get("tool_call_id") or f"call_{response_id[5:]}_{call_counter}"
                args = payload.get("arguments", {})
                if isinstance(args, dict):
                    arguments_str = json.dumps(args)
                else:
                    arguments_str = str(args)
                item = {
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "type": "function_call",
                    "status": "in_progress",
                    "name": payload.get("name", ""),
                    "call_id": call_id,
                    "arguments": arguments_str,
                }
                idx = output_index
                output_index += 1
                pending_tool_calls.append({
                    "call_id": call_id,
                    "name": payload.get("name", ""),
                    "arguments": arguments_str,
                    "item_id": item["id"],
                    "output_index": idx,
                })
                emitted_items.append({
                    "type": "function_call",
                    "name": payload.get("name", ""),
                    "arguments": arguments_str,
                    "call_id": call_id,
                })
                await _write_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": idx,
                    "item": item,
                })
                return call_id

            async def _emit_tool_completed(payload: Dict[str, Any]) -> None:
                """Emit response.output_item.done (function_call) followed
                by response.output_item.added (function_call_output)."""
                nonlocal output_index
                call_id = payload.get("tool_call_id")
                result = payload.get("result", "")
                pending = None
                if call_id:
                    for i, p in enumerate(pending_tool_calls):
                        if p["call_id"] == call_id:
                            pending = pending_tool_calls.pop(i)
                            break
                if pending is None:
                    # Completion without a matching start — skip to avoid
                    # emitting orphaned done events.
                    return

                # function_call done
                done_item = {
                    "id": pending["item_id"],
                    "type": "function_call",
                    "status": "completed",
                    "name": pending["name"],
                    "call_id": pending["call_id"],
                    "arguments": pending["arguments"],
                }
                await _write_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": pending["output_index"],
                    "item": done_item,
                })

                # function_call_output added (result)
                result_str = result if isinstance(result, str) else json.dumps(result)
                output_parts = [{"type": "input_text", "text": result_str}]
                output_item = {
                    "id": f"fco_{uuid.uuid4().hex[:24]}",
                    "type": "function_call_output",
                    "call_id": pending["call_id"],
                    "output": output_parts,
                    "status": "completed",
                }
                idx = output_index
                output_index += 1
                emitted_items.append({
                    "type": "function_call_output",
                    "call_id": pending["call_id"],
                    "output": output_parts,
                })
                await _write_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": idx,
                    "item": output_item,
                })
                await _write_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": idx,
                    "item": output_item,
                })

            # Main drain loop — thread-safe queue fed by agent callbacks.
            async def _dispatch(it) -> None:
                """Route a queue item to the correct SSE emitter.

                Plain strings are text deltas — they are batched (50ms)
                to reduce Open WebUI re-render storms.  Tagged tuples
                with ``__tool_started__`` / ``__tool_completed__``
                prefixes are tool lifecycle events and flush the buffer
                before emitting.
                """
                nonlocal _batch_timer
                if isinstance(it, tuple) and len(it) == 2 and isinstance(it[0], str):
                    tag, payload = it
                    # Flush batched text before tool events
                    if _batch_buf:
                        await _flush_batch()
                    if tag == "__tool_started__":
                        await _emit_tool_started(payload)
                    elif tag == "__tool_completed__":
                        await _emit_tool_completed(payload)
                elif isinstance(it, str):
                    # Batch text deltas — append to buffer, flush on timer
                    _batch_buf.append(it)
                    if _batch_timer is None:
                        _batch_timer = asyncio.create_task(_batch_flush_after(0.05))
                # Other types are silently dropped.

            # ── Batching state ──
            _batch_buf: List[str] = []
            _batch_timer: Optional[asyncio.Task] = None
            _batch_lock = asyncio.Lock()

            async def _batch_flush_after(delay: float) -> None:
                """Wait delay seconds, then flush accumulated text deltas."""
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                # Clear timer reference BEFORE flush so new deltas
                # can start a fresh timer while we emit
                nonlocal _batch_buf, _batch_timer
                _batch_timer = None
                await _flush_batch()

            async def _flush_batch() -> None:
                """Emit a single SSE delta for all accumulated text."""
                nonlocal _batch_buf
                async with _batch_lock:
                    if _batch_buf:
                        combined = "".join(_batch_buf)
                        _batch_buf = []
                        await _emit_text_delta(combined)

            loop = asyncio.get_running_loop()
            while True:
                try:
                    item = await loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5))
                except _q.Empty:
                    if agent_task.done():
                        # Drain remaining
                        while True:
                            try:
                                item = stream_q.get_nowait()
                                if item is None:
                                    break
                                await _dispatch(item)
                                last_activity = time.monotonic()
                            except _q.Empty:
                                break
                        break
                    if time.monotonic() - last_activity >= CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS:
                        await response.write(b": keepalive\n\n")
                        last_activity = time.monotonic()
                    continue

                if item is None:  # EOS sentinel
                    # Cancel pending timer and flush remaining batched text
                    if _batch_timer and not _batch_timer.done():
                        _batch_timer.cancel()
                        _batch_timer = None
                    if _batch_buf:
                        await _flush_batch()
                    break

                await _dispatch(item)
                last_activity = time.monotonic()

            # Flush any final batched text before processing result
            if _batch_buf:
                await _flush_batch()

            # Pick up agent result + usage from the completed task
            try:
                result, agent_usage = await agent_task
                usage = agent_usage or usage
                # If the agent produced a final_response but no text
                # deltas were streamed (e.g. some providers only emit
                # the full response at the end), emit a single fallback
                # delta so Responses clients still receive a live text part.
                agent_final = result.get("final_response", "") if isinstance(result, dict) else ""
                if agent_final and not final_text_parts:
                    await _emit_text_delta(agent_final)
                if agent_final and not final_response_text:
                    final_response_text = agent_final
                if isinstance(result, dict) and result.get("error") and not final_response_text:
                    agent_error = result["error"]
            except Exception as e:  # noqa: BLE001
                logger.error("Error running agent for streaming responses: %s", e, exc_info=True)
                agent_error = str(e)

            # Close the message item if it was opened
            final_response_text = "".join(final_text_parts) or final_response_text
            if message_opened:
                await _write_event("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": message_item_id,
                    "output_index": message_output_index,
                    "content_index": 0,
                    "text": final_response_text,
                    "logprobs": [],
                })
                msg_done_item = {
                    "id": message_item_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": final_response_text}
                    ],
                }
                await _write_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "output_index": message_output_index,
                    "item": msg_done_item,
                })

            # Always append a final message item in the completed
            # response envelope so clients that only parse the terminal
            # payload still see the assistant text.  This mirrors the
            # shape produced by _extract_output_items in the batch path.
            final_items: List[Dict[str, Any]] = list(emitted_items)

            # Trim large content from tool call arguments to keep the
            # response.completed event under ~100KB.  Clients already
            # received full details via incremental events.
            for _item in final_items:
                if _item.get("type") == "function_call":
                    try:
                        _args = json.loads(_item.get("arguments", "{}")) if isinstance(_item.get("arguments"), str) else _item.get("arguments", {})
                        if isinstance(_args, dict):
                            for _k in ("content", "query", "pattern", "old_string", "new_string"):
                                if isinstance(_args.get(_k), str) and len(_args[_k]) > 500:
                                    _args[_k] = "[" + str(len(_args[_k])) + " chars — truncated for response.completed]"
                            _item["arguments"] = json.dumps(_args)
                    except Exception:
                        pass
                elif _item.get("type") == "function_call_output":
                    _output = _item.get("output", [])
                    if isinstance(_output, list) and _output:
                        _first = _output[0]
                        if isinstance(_first, dict) and _first.get("type") == "input_text":
                            _text = _first.get("text", "")
                            if len(_text) > 1000:
                                _first["text"] = _text[:500] + "...[" + str(len(_text) - 500) + " more chars]"
                                _item["output"] = [_first]

            final_items.append({
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": final_response_text or (agent_error or "")}
                ],
            })

            if agent_error:
                failed_env = _envelope("failed")
                failed_env["output"] = final_items
                failed_env["error"] = {"message": agent_error, "type": "server_error"}
                failed_env["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                _failed_history = list(conversation_history)
                _failed_history.append({"role": "user", "content": user_message})
                if final_response_text or agent_error:
                    _failed_history.append({
                        "role": "assistant",
                        "content": final_response_text or agent_error,
                    })
                _persist_response_snapshot(
                    failed_env,
                    conversation_history_snapshot=_failed_history,
                )
                terminal_snapshot_persisted = True
                await _write_event("response.failed", {
                    "type": "response.failed",
                    "response": failed_env,
                })
            else:
                completed_env = _envelope("completed")
                completed_env["output"] = final_items
                completed_env["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                full_history = self._build_response_conversation_history(
                    conversation_history,
                    user_message,
                    result,
                    final_response_text,
                )
                _persist_response_snapshot(
                    completed_env,
                    conversation_history_snapshot=full_history,
                )
                terminal_snapshot_persisted = True
                await _write_event("response.completed", {
                    "type": "response.completed",
                    "response": completed_env,
                })

        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            _persist_incomplete_if_needed()
            # Client disconnected — interrupt the agent so it stops
            # making upstream LLM calls, then cancel the task.
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE client disconnected")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.info("SSE client disconnected; interrupted agent task %s", response_id)
        except asyncio.CancelledError:
            # Server-side cancellation (e.g. shutdown, request timeout) —
            # persist an incomplete snapshot so GET /v1/responses/{id} and
            # previous_response_id chaining still work, then re-raise so the
            # runtime's cancellation semantics are respected.
            _persist_incomplete_if_needed()
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                try:
                    agent.interrupt("SSE task cancelled")
                except Exception:
                    pass
            if not agent_task.done():
                agent_task.cancel()
            logger.info("SSE task cancelled; persisted incomplete snapshot for %s", response_id)
            raise
        except Exception as _exc:
            # Agent crashed with an unhandled error (e.g. model API error like
            # BadRequestError, AuthenticationError).  Emit a response.failed
            # event and properly terminate the SSE stream so the client doesn't
            # get a TransferEncodingError from incomplete chunked encoding.
            import traceback as _tb
            _persist_incomplete_if_needed()
            agent_error = _tb.format_exc()
            try:
                failed_env = _envelope("failed")
                failed_env["output"] = list(emitted_items)
                failed_env["error"] = {"message": str(_exc)[:500], "type": "server_error"}
                failed_env["usage"] = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                await _write_event("response.failed", {
                    "type": "response.failed",
                    "response": failed_env,
                })
            except Exception:
                pass
            logger.error("Agent crashed mid-stream for %s: %s", response_id, str(agent_error)[:300])

        return response

    async def _handle_responses(self, request: "web.Request") -> "web.Response":
        """POST /v1/responses — OpenAI Responses API format."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # Long-term memory scope header (see chat_completions for details).
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        # Parse request body
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": {"message": "Invalid JSON in request body", "type": "invalid_request_error"}},
                status=400,
            )

        raw_input = body.get("input")
        if raw_input is None:
            return web.json_response(_openai_error("Missing 'input' field"), status=400)

        instructions = body.get("instructions")
        previous_response_id = body.get("previous_response_id")
        conversation = body.get("conversation")
        store = _coerce_request_bool(body.get("store"), default=True)

        # conversation and previous_response_id are mutually exclusive
        if conversation and previous_response_id:
            return web.json_response(_openai_error("Cannot use both 'conversation' and 'previous_response_id'"), status=400)

        # Resolve conversation name to latest response_id
        if conversation:
            previous_response_id = self._response_store.get_conversation(conversation)
            # No error if conversation doesn't exist yet — it's a new conversation

        # Normalize input to message list
        input_messages: List[Dict[str, Any]] = []
        if isinstance(raw_input, str):
            input_messages = [{"role": "user", "content": raw_input}]
        elif isinstance(raw_input, list):
            for idx, item in enumerate(raw_input):
                if isinstance(item, str):
                    input_messages.append({"role": "user", "content": item})
                elif isinstance(item, dict):
                    role = item.get("role", "user")
                    try:
                        content = _normalize_multimodal_content(item.get("content", ""))
                    except ValueError as exc:
                        return _multimodal_validation_error(exc, param=f"input[{idx}].content")
                    input_messages.append({"role": role, "content": content})
        else:
            return web.json_response(_openai_error("'input' must be a string or array"), status=400)

        # Accept explicit conversation_history from the request body.
        # This lets stateless clients supply their own history instead of
        # relying on server-side response chaining via previous_response_id.
        # Precedence: explicit conversation_history > previous_response_id.
        conversation_history: List[Dict[str, Any]] = []
        raw_history = body.get("conversation_history")
        if raw_history:
            if not isinstance(raw_history, list):
                return web.json_response(
                    _openai_error("'conversation_history' must be an array of message objects"),
                    status=400,
                )
            for i, entry in enumerate(raw_history):
                if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                    return web.json_response(
                        _openai_error(f"conversation_history[{i}] must have 'role' and 'content' fields"),
                        status=400,
                    )
                try:
                    entry_content = _normalize_multimodal_content(entry["content"])
                except ValueError as exc:
                    return _multimodal_validation_error(exc, param=f"conversation_history[{i}].content")
                conversation_history.append({"role": str(entry["role"]), "content": entry_content})
            if previous_response_id:
                logger.debug("Both conversation_history and previous_response_id provided; using conversation_history")

        stored_session_id = None
        if not conversation_history and previous_response_id:
            stored = self._response_store.get(previous_response_id)
            if stored is None:
                return web.json_response(_openai_error(f"Previous response not found: {previous_response_id}"), status=404)
            conversation_history = list(stored.get("conversation_history", []))
            stored_session_id = stored.get("session_id")
            # If no instructions provided, carry forward from previous
            if instructions is None:
                instructions = stored.get("instructions")

        # Append new input messages to history (all but the last become history)
        for msg in input_messages[:-1]:
            conversation_history.append(msg)

        # Last input message is the user_message
        user_message: Any = input_messages[-1].get("content", "") if input_messages else ""
        if not _content_has_visible_payload(user_message):
            return web.json_response(_openai_error("No user message found in input"), status=400)

        # Truncation support
        if body.get("truncation") == "auto" and len(conversation_history) > 100:
            conversation_history = conversation_history[-100:]

        # Reuse session from previous_response_id chain so the dashboard
        # groups the entire conversation under one session entry.
        session_id = stored_session_id or str(uuid.uuid4())

        stream = _coerce_request_bool(body.get("stream"), default=False)
        # Per-request model / reasoning overrides (prompt-bar picker) — shared
        # parser; applies to BOTH the streaming and non-streaming branches below
        # (must be defined before either references it).
        model_override, reasoning_config_override = self._parse_oc_overrides(body)
        if stream:
            # Streaming branch — emit OpenAI Responses SSE events as the
            # agent runs so frontends can render text deltas and tool
            # calls in real time.  See _write_sse_responses for details.
            import queue as _q
            _stream_q: _q.Queue = _q.Queue()

            def _on_delta(delta):
                # None from the agent is a CLI box-close signal, not EOS.
                # Forwarding would kill the SSE stream prematurely; the
                # SSE writer detects completion via agent_task.done().
                if delta is not None:
                    _stream_q.put(delta)

            def _on_tool_progress(event_type, name, preview, args, **kwargs):
                """Queue non-start tool progress events if needed in future.

                The structured Responses stream uses ``tool_start_callback``
                and ``tool_complete_callback`` for exact call-id correlation,
                so progress events are currently ignored here.
                """
                return

            def _on_tool_start(tool_call_id, function_name, function_args):
                """Queue a started tool for live function_call streaming."""
                _stream_q.put(("__tool_started__", {
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "arguments": function_args or {},
                }))

            def _on_tool_complete(tool_call_id, function_name, function_args, function_result):
                """Queue a completed tool result for live function_call_output streaming."""
                _stream_q.put(("__tool_completed__", {
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "arguments": function_args or {},
                    "result": function_result,
                }))

            agent_ref = [None]
            agent_task = asyncio.ensure_future(self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_progress_callback=_on_tool_progress,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
                model_override=model_override,
                reasoning_config_override=reasoning_config_override,
            ))
            # Ensure SSE drain loops can terminate without relying on polling
            # agent_task.done(), which can race with queue timeout checks.
            agent_task.add_done_callback(lambda _fut: _stream_q.put(None))

            response_id = f"resp_{uuid.uuid4().hex[:28]}"
            model_name = body.get("model", self._model_name)
            created_at = int(time.time())

            return await self._write_sse_responses(
                request=request,
                response_id=response_id,
                model=model_name,
                created_at=created_at,
                stream_q=_stream_q,
                agent_task=agent_task,
                agent_ref=agent_ref,
                conversation_history=conversation_history,
                user_message=user_message,
                instructions=instructions,
                conversation=conversation,
                store=store,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

        async def _compute_response():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
                model_override=model_override,
                reasoning_config_override=reasoning_config_override,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(
                body,
                keys=["input", "instructions", "previous_response_id", "conversation", "model", "tools"],
            )
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_response)
            except Exception as e:
                logger.error("Error running agent for responses: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_response()
            except Exception as e:
                logger.error("Error running agent for responses: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )

        final_response = result.get("final_response", "")
        if not final_response:
            final_response = result.get("error", "(No response generated)")

        response_id = f"resp_{uuid.uuid4().hex[:28]}"
        created_at = int(time.time())

        # Build the full conversation history for storage
        # (includes tool calls from the agent run)
        full_history = self._build_response_conversation_history(
            conversation_history,
            user_message,
            result,
            final_response,
        )

        # Build output items from the current turn only.  AIAgent returns a
        # full transcript in result["messages"], while older/mocked paths may
        # return only the current turn suffix.
        output_start_index = self._response_messages_turn_start_index(
            conversation_history,
            user_message,
            result,
        )
        output_items = self._extract_output_items(result, start_index=output_start_index)

        response_data = {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "created_at": created_at,
            "model": body.get("model", self._model_name),
            "output": output_items,
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

        # Store the complete response object for future chaining / GET retrieval
        if store:
            self._response_store.put(response_id, {
                "response": response_data,
                "conversation_history": full_history,
                "instructions": instructions,
                "session_id": session_id,
            })
            # Update conversation mapping so the next request with the same
            # conversation name automatically chains to this response
            if conversation:
                self._response_store.set_conversation(conversation, response_id)

        response_headers = {"X-OpenComputer-Session-Id": session_id}
        if gateway_session_key:
            response_headers["X-OpenComputer-Session-Key"] = gateway_session_key
        return web.json_response(response_data, headers=response_headers)

    # ------------------------------------------------------------------
    # GET / DELETE response endpoints
    # ------------------------------------------------------------------

    async def _handle_get_response(self, request: "web.Request") -> "web.Response":
        """GET /v1/responses/{response_id} — retrieve a stored response."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        response_id = request.match_info["response_id"]
        stored = self._response_store.get(response_id)
        if stored is None:
            return web.json_response(_openai_error(f"Response not found: {response_id}"), status=404)

        return web.json_response(stored["response"])

    async def _handle_delete_response(self, request: "web.Request") -> "web.Response":
        """DELETE /v1/responses/{response_id} — delete a stored response."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        response_id = request.match_info["response_id"]
        deleted = self._response_store.delete(response_id)
        if not deleted:
            return web.json_response(_openai_error(f"Response not found: {response_id}"), status=404)

        return web.json_response({
            "id": response_id,
            "object": "response",
            "deleted": True,
        })

    # ------------------------------------------------------------------
    # Cron jobs API
    # ------------------------------------------------------------------

    _JOB_ID_RE = __import__("re").compile(r"[a-f0-9]{12}")
    # Allowed fields for update — prevents clients injecting arbitrary keys
    _UPDATE_ALLOWED_FIELDS = {"name", "schedule", "prompt", "deliver", "skills", "skill", "repeat", "enabled"}
    _MAX_NAME_LENGTH = 200
    _MAX_PROMPT_LENGTH = 5000

    @staticmethod
    def _check_jobs_available() -> Optional["web.Response"]:
        """Return error response if cron module isn't available."""
        if not _CRON_AVAILABLE:
            return web.json_response(
                {"error": "Cron module not available"}, status=501,
            )
        return None

    def _check_job_id(self, request: "web.Request") -> tuple:
        """Validate and extract job_id. Returns (job_id, error_response)."""
        job_id = request.match_info["job_id"]
        if not self._JOB_ID_RE.fullmatch(job_id):
            logger.warning(
                "Cron jobs API rejected invalid job_id %r: %s",
                job_id,
                self._request_audit_log_suffix(request),
            )
            return job_id, web.json_response(
                {"error": "Invalid job ID format"}, status=400,
            )
        return job_id, None

    async def _handle_list_jobs(self, request: "web.Request") -> "web.Response":
        """GET /api/jobs — list all cron jobs."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        try:
            include_disabled = request.query.get("include_disabled", "").lower() in {"true", "1"}
            jobs = _cron_list(include_disabled=include_disabled)
            return web.json_response({"jobs": jobs})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    @staticmethod
    def build_parallel_agents_snapshot() -> dict:
        """Gather a read-only snapshot of dynamic workflows (oc_flow), background
        agent sessions (oc_agents), and agent teams (oc_teams). Each section
        degrades to an empty list (with its error recorded under ``errors``) if
        its plugin is disabled or absent, so the snapshot never hard-fails.

        Factored out of the handler so it is unit-testable without aiohttp.
        """
        flows: list = []
        agents: list = []
        teams: list = []
        flows_err = agents_err = teams_err = None

        try:
            from plugins.oc_flow import db as _flow_db

            flows = _flow_db.list_runs(limit=50)
        except Exception as exc:  # noqa: BLE001 — plugin may be disabled/absent
            flows_err = str(exc)

        try:
            from plugins.oc_agents import supervisor as _agents_sup

            # Reconcile dead PIDs to 'failed' on read (the daemonless supervisor).
            agents = _agents_sup.snapshot(include_done=True, limit=100)
        except Exception as exc:  # noqa: BLE001
            agents_err = str(exc)

        try:
            from plugins.oc_teams import db as _teams_db

            teams = []
            for t in _teams_db.list_teams():
                summary = _teams_db.team_status_summary(t["id"])
                teams.append({
                    "id": t["id"], "name": t["name"], "goal": t.get("goal"),
                    "status": t["status"],
                    "member_count": len(summary.get("members", [])),
                    "task_counts": summary.get("task_counts", {}),
                    "tasks_total": summary.get("tasks_total", 0),
                })
        except Exception as exc:  # noqa: BLE001
            teams_err = str(exc)

        return {
            "object": "hermes.parallel_agents",
            "flows": flows,
            "agents": agents,
            "teams": teams,
            "errors": {"flows": flows_err, "agents": agents_err, "teams": teams_err},
            "timestamp": int(time.time()),
        }

    async def _handle_parallel_agents(self, request: "web.Request") -> "web.Response":
        """GET /api/parallel-agents — read-only snapshot of dynamic workflows,
        background agent sessions, and agent teams for the dashboard."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        return web.json_response(self.build_parallel_agents_snapshot())

    async def _handle_parallel_agents_events(self, request: "web.Request") -> "web.StreamResponse":
        """GET /v1/parallel-agents/events — live SSE stream of the parallel-agents
        run view: a snapshot on connect, then deltas as run-state lands on the
        oc_runs spine. Last-Event-ID (or ?cursor=) resumes from the durable spine.

        Backend-agnostic: it reads the spine directly, so it works for the default
        oc-backend without reviving the dormant, hermes-gated /v1/runs/{id}/events
        channel. The streaming logic lives in plugins.oc_runs.sse_endpoint so it is
        unit-tested over real HTTP independently of this server."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        from plugins.oc_runs import sse_endpoint

        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        return await sse_endpoint.stream_events(request, extra_headers=cors)

    # ------------------------------------------------------------------
    # Parallel-agents drill-down: per-entity detail, control, and the
    # click-to-chat bridge. All read paths reuse the plugin DB accessors;
    # the static builders are factored out so they are unit-testable
    # without aiohttp (mirroring build_parallel_agents_snapshot()).
    # ------------------------------------------------------------------

    @staticmethod
    def _tail_file(
        path: str, max_bytes: int = 16384, *, allowed_dir: Optional[str] = None
    ) -> str:
        """Return the last ``max_bytes`` of a (possibly large) text file,
        dropping a leading partial line. Empty string if the file is absent.

        When ``allowed_dir`` is provided, the resolved path must live inside it
        — defense-in-depth so a tampered ``log_path`` (e.g. from a compromised
        DB row) can't read arbitrary files via the API.
        """
        p = Path(path).resolve()
        if allowed_dir is not None:
            try:
                p.relative_to(Path(allowed_dir).resolve())
            except ValueError:
                return ""
        if not p.is_file():
            return ""
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1:]
        return text

    @staticmethod
    def build_flow_detail(flow_id: str) -> Optional[dict]:
        """Full detail for one dynamic-workflow run: phases, per-agent rows,
        progress logs, and the decoded result. Returns None if no such run."""
        from plugins.oc_flow import db as _flow_db

        run = _flow_db.get_run(flow_id)
        if run is None:
            return None
        errors: dict = {}
        phases: list = []
        agents: list = []
        logs: list = []
        result = None
        try:
            phases = _flow_db.list_phases(flow_id, limit=500)
        except Exception as exc:  # noqa: BLE001
            errors["phases"] = str(exc)
        try:
            agents = _flow_db.list_agents(flow_id, limit=500)
        except Exception as exc:  # noqa: BLE001
            errors["agents"] = str(exc)
        try:
            logs = _flow_db.list_logs(flow_id, limit=500)
        except Exception as exc:  # noqa: BLE001
            errors["logs"] = str(exc)
        try:
            result = _flow_db.decode_result(run)
        except Exception as exc:  # noqa: BLE001
            errors["result"] = str(exc)
        return {
            "object": "hermes.flow_detail",
            "run": run,
            "phases": phases,
            "agents": agents,
            "logs": logs,
            "result": result,
            "errors": errors,
        }

    @staticmethod
    def build_agent_detail(session_id: str, *, log_bytes: int = 16384) -> Optional[dict]:
        """Full detail for one background agent session: the row plus a bounded
        tail of its on-disk activity log. Returns None if no such session.

        Reconciles dead PIDs first so a crashed agent reads as ``failed``
        (matching the daemonless supervisor semantics of the snapshot)."""
        from plugins.oc_agents import db as _agents_db

        try:
            from plugins.oc_agents import supervisor as _agents_sup

            _agents_sup.reconcile()
        except Exception:  # noqa: BLE001 — best-effort liveness reconcile
            pass

        session = _agents_db.get_session(session_id)
        if session is None:
            return None
        events: list = []
        events_err = None
        try:
            events = _agents_db.list_events(session_id, limit=300)
        except Exception as exc:  # noqa: BLE001 — bg_events may not exist on old DBs
            events_err = str(exc)
        pending_messages: list = []
        try:
            pending_messages = _agents_db.pending_inbox(session_id)
        except Exception:  # noqa: BLE001 — bg_inbox may not exist on old DBs
            pending_messages = []
        # Persona link (when the agent was launched from a persona).
        persona = None
        try:
            _m = json.loads(session.get("meta") or "null")
            if isinstance(_m, dict) and (_m.get("persona_name") or _m.get("persona_id") is not None):
                persona = {"id": _m.get("persona_id"), "name": _m.get("persona_name") or ""}
        except Exception:  # noqa: BLE001
            persona = None
        log_tail = ""
        log_err = None
        log_path = session.get("log_path")
        if log_path:
            try:
                log_tail = APIServerAdapter._tail_file(
                    str(log_path), log_bytes, allowed_dir=str(_agents_db.logs_dir())
                )
            except Exception as exc:  # noqa: BLE001
                log_err = str(exc)
        return {
            "object": "hermes.agent_detail",
            "session": session,
            "log_path": log_path,
            "log_tail": log_tail,
            # Granular per-step activity (newest entries capped) for the live feed.
            "events": events,
            # User messages queued for this (running) agent, not yet consumed.
            "pending_messages": pending_messages,
            # Persona this agent was launched from (null if launched ad-hoc).
            "persona": persona,
            # agent_session_id (a hermes_state session id) is the click-to-chat
            # resume target; surface it explicitly for the frontend.
            "chat_session_id": session.get("agent_session_id") or "",
            "errors": {"log": log_err, "events": events_err},
        }

    @staticmethod
    def build_team_detail(team_id: str) -> Optional[dict]:
        """Full detail for one agent team: members (enriched with each
        teammate's chat-resume session id), the shared task list, the message
        log, and the rollup summary. Returns None if no such team."""
        from plugins.oc_teams import db as _teams_db

        team = _teams_db.get_team(team_id)
        if team is None:
            return None
        errors: dict = {}
        members: list = []
        tasks: list = []
        messages: list = []
        summary: dict = {}
        try:
            members = _teams_db.list_members(team_id, limit=200)
        except Exception as exc:  # noqa: BLE001
            errors["members"] = str(exc)
        try:
            tasks = _teams_db.list_tasks(team_id, limit=500)
        except Exception as exc:  # noqa: BLE001
            errors["tasks"] = str(exc)
        try:
            messages = _teams_db.list_messages(team_id, limit=200)
        except Exception as exc:  # noqa: BLE001
            errors["messages"] = str(exc)
        try:
            summary = _teams_db.team_status_summary(team_id)
        except Exception as exc:  # noqa: BLE001
            errors["summary"] = str(exc)

        # Enrich each member with its background session's hermes chat session
        # id so the frontend can offer "continue chatting" per teammate. Every
        # member is pre-seeded with an empty id so the field is always present,
        # and each lookup is isolated so one failure can't drop the key from the
        # remaining members.
        for m in members:
            m["chat_session_id"] = ""
        try:
            from plugins.oc_agents import db as _agents_db
        except Exception as exc:  # noqa: BLE001 — oc_agents may be absent
            _agents_db = None
            errors["member_sessions"] = str(exc)
        if _agents_db is not None:
            for m in members:
                bg = m.get("bg_session_id")
                if not bg:
                    continue
                try:
                    sess = _agents_db.get_session(bg)
                    m["chat_session_id"] = (sess or {}).get("agent_session_id") or ""
                except Exception as exc:  # noqa: BLE001
                    errors["member_sessions"] = str(exc)

        return {
            "object": "hermes.team_detail",
            "team": team,
            "members": members,
            "tasks": tasks,
            "messages": messages,
            "summary": summary,
            "errors": errors,
        }

    @staticmethod
    def stop_flow(flow_id: str) -> dict:
        """Signal a running flow's background process and mark it stopped.
        Idempotent: returns ``{found: False}`` for unknown runs and
        ``{stopped: False}`` for already-terminal runs; never raises on a
        dead PID."""
        from plugins.oc_flow import db as _flow_db

        run = _flow_db.get_run(flow_id)
        if run is None:
            return {"found": False, "stopped": False}
        if run["status"] not in ("running", "pending"):
            return {"found": True, "stopped": False, "status": run["status"]}
        pid = run.get("pid")
        if pid and run.get("background"):
            try:
                os.kill(int(pid), 15)  # SIGTERM
            except ProcessLookupError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("oc_flow: could not signal pid %s: %s", pid, exc)
        _flow_db.finish_run(flow_id, "stopped", error="stopped by user")
        return {"found": True, "stopped": True, "status": "stopped"}

    async def _handle_flow_detail(self, request: "web.Request") -> "web.Response":
        """GET /api/parallel-agents/flows/{flow_id}"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        flow_id = request.match_info["flow_id"]
        try:
            detail = self.build_flow_detail(flow_id)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: flow detail failed for %s", flow_id)
            return web.json_response({"error": "internal error"}, status=500)
        if detail is None:
            return web.json_response({"error": "flow not found"}, status=404)
        return web.json_response(detail)

    async def _handle_agent_detail(self, request: "web.Request") -> "web.Response":
        """GET /api/parallel-agents/agents/{session_id}"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        try:
            detail = self.build_agent_detail(session_id)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: agent detail failed for %s", session_id)
            return web.json_response({"error": "internal error"}, status=500)
        if detail is None:
            return web.json_response({"error": "agent session not found"}, status=404)
        return web.json_response(detail)

    async def _handle_team_detail(self, request: "web.Request") -> "web.Response":
        """GET /api/parallel-agents/teams/{team_id}"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        team_id = request.match_info["team_id"]
        try:
            detail = self.build_team_detail(team_id)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: team detail failed for %s", team_id)
            return web.json_response({"error": "internal error"}, status=500)
        if detail is None:
            return web.json_response({"error": "team not found"}, status=404)
        return web.json_response(detail)

    async def _handle_agent_stop(self, request: "web.Request") -> "web.Response":
        """POST /api/parallel-agents/agents/{session_id}/stop"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        try:
            from plugins.oc_agents import db as _agents_db
            from plugins.oc_agents import supervisor as _agents_sup

            # Distinguish "no such session" (404) from "exists but not in a
            # stoppable state" (200 + stopped:false), matching flow-stop.
            if _agents_db.get_session(session_id) is None:
                return web.json_response(
                    {"error": "agent session not found"}, status=404
                )
            stopped = _agents_sup.stop(session_id)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: agent stop failed for %s", session_id)
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response({"object": "hermes.agent_stop", "stopped": bool(stopped)})

    async def _handle_agent_send(self, request: "web.Request") -> "web.Response":
        """POST /api/parallel-agents/agents/{session_id}/send  {message}

        Queue a message for a background agent. The detached worker delivers it
        when the agent next asks for input (live steering) and as a follow-up
        turn after its current run, so you can answer or add work without
        restarting it."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        session_id = request.match_info["session_id"]
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        message = (data.get("message") or "").strip()
        if not message:
            return web.json_response({"error": "message is required"}, status=400)
        try:
            from plugins.oc_agents import db as _agents_db

            if _agents_db.get_session(session_id) is None:
                return web.json_response(
                    {"error": "agent session not found"}, status=404
                )
            message_id = _agents_db.add_inbox_message(session_id, message)
            pending = _agents_db.pending_inbox(session_id)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: agent send failed for %s", session_id)
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response(
            {
                "object": "hermes.agent_message",
                "message_id": message_id,
                "pending": len(pending),
            }
        )

    async def _handle_agent_launch(self, request: "web.Request") -> "web.Response":
        """POST /api/parallel-agents/agents  {prompt, name?, model?, persona_id?,
        persona_name?, system_prompt?}

        Dispatch a new background agent from the cockpit. When persona fields are
        supplied the run is tagged with the persona and (if system_prompt is
        given) runs as that persona."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400)
        name = (data.get("name") or "").strip()
        model = (data.get("model") or "").strip()
        persona_id = data.get("persona_id")
        persona_name = (data.get("persona_name") or "").strip()
        system_prompt = (data.get("system_prompt") or "").strip()
        meta: dict = {}
        if persona_id is not None or persona_name:
            meta["persona_id"] = persona_id
            meta["persona_name"] = persona_name
        if system_prompt:
            meta["system_prompt"] = system_prompt
        try:
            from plugins.oc_agents import supervisor as _agents_sup

            session_id = _agents_sup.dispatch(
                prompt, name=name, model=model, meta=meta or None
            )
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: agent launch failed")
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response(
            {"object": "hermes.agent_launch", "session_id": session_id}
        )

    async def _handle_flow_stop(self, request: "web.Request") -> "web.Response":
        """POST /api/parallel-agents/flows/{flow_id}/stop"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        flow_id = request.match_info["flow_id"]
        try:
            result = self.stop_flow(flow_id)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: flow stop failed for %s", flow_id)
            return web.json_response({"error": "internal error"}, status=500)
        if not result.get("found"):
            return web.json_response({"error": "flow not found"}, status=404)
        return web.json_response({"object": "hermes.flow_stop", **result})

    async def _handle_team_send_message(self, request: "web.Request") -> "web.Response":
        """POST /api/parallel-agents/teams/{team_id}/messages  {from,to,body}"""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        team_id = request.match_info["team_id"]
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        body = (data.get("body") or "").strip()
        if not body:
            return web.json_response({"error": "body is required"}, status=400)
        from_member = (data.get("from") or "user").strip() or "user"
        to_member = (data.get("to") or "*").strip() or "*"
        try:
            from plugins.oc_teams import db as _teams_db

            if _teams_db.get_team(team_id) is None:
                return web.json_response({"error": "team not found"}, status=404)
            message_id = _teams_db.send_message(team_id, from_member, to_member, body)
        except Exception:  # noqa: BLE001
            logger.exception("parallel-agents: team send-message failed for %s", team_id)
            return web.json_response({"error": "internal error"}, status=500)
        return web.json_response(
            {"object": "hermes.team_message", "message_id": message_id}
        )

    # Loopback base for the on-box Open Design daemon (the in-OpenComputer
    # "Open Design" panel backend). Overridable for tests / non-default ports.
    _OPEN_DESIGN_DAEMON_BASE = os.environ.get(
        "OC_OPEN_DESIGN_DAEMON_URL", "http://127.0.0.1:17456"
    )

    async def _handle_open_design_proxy(self, request: "web.Request") -> "web.Response":
        """ANY /api/od/{path} — authenticated passthrough to the on-box Open
        Design daemon (loopback only).

        This is how the workspace frontend reaches a user's Open Design panel on
        their VM *without* exposing the daemon publicly: the request rides the
        agent's existing dashboard-token-authenticated tunnel, and we forward it
        to the daemon on loopback. The daemon never gets a public listener.
        """
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        try:
            import aiohttp  # lazy: optional dep of this module
        except Exception:  # noqa: BLE001
            return web.json_response(
                {"error": "aiohttp unavailable for Open Design proxy"}, status=503
            )

        # SSRF guard: the target base is a fixed loopback URL; the only
        # caller-controlled part is the trailing path, which we treat as opaque
        # path segments (no scheme/host injection possible via the match).
        sub = request.match_info.get("path", "")
        base = self._OPEN_DESIGN_DAEMON_BASE.rstrip("/")
        target = f"{base}/{sub}" if sub else base
        if request.query_string:
            target = f"{target}?{request.query_string}"

        # Forward method, body, and a minimal header set. Drop hop-by-hop and
        # auth headers (the daemon is loopback-trusted; our own bearer must not
        # leak onward).
        drop = {
            "host", "connection", "content-length", "authorization",
            "accept-encoding", "origin", "referer", "cookie",
        }
        fwd_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in drop
        }
        body = None
        if request.method not in ("GET", "HEAD"):
            body = await request.read()

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    request.method, target, headers=fwd_headers, data=body,
                ) as resp:
                    payload = await resp.read()
                    out_headers = {
                        k: v for k, v in resp.headers.items()
                        if k.lower() not in {"transfer-encoding", "content-encoding", "content-length", "connection"}
                    }
                    return web.Response(
                        body=payload, status=resp.status, headers=out_headers,
                    )
        except Exception as exc:  # noqa: BLE001 — daemon may be down
            return web.json_response(
                {
                    "error": (
                        "Open Design daemon is not reachable on this machine. "
                        "Start it with: tools-dev start daemon --daemon-port 17456 --web-port 17573"
                    ),
                    "detail": str(exc),
                },
                status=503,
            )

    async def _handle_create_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs — create a new cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        try:
            body = await request.json()
            name = (body.get("name") or "").strip()
            schedule = (body.get("schedule") or "").strip()
            prompt = body.get("prompt", "")
            deliver = body.get("deliver", "local")
            skills = body.get("skills")
            repeat = body.get("repeat")

            if not name:
                return web.json_response({"error": "Name is required"}, status=400)
            if len(name) > self._MAX_NAME_LENGTH:
                return web.json_response(
                    {"error": f"Name must be ≤ {self._MAX_NAME_LENGTH} characters"}, status=400,
                )
            if not schedule:
                return web.json_response({"error": "Schedule is required"}, status=400)
            if len(prompt) > self._MAX_PROMPT_LENGTH:
                return web.json_response(
                    {"error": f"Prompt must be ≤ {self._MAX_PROMPT_LENGTH} characters"}, status=400,
                )
            if prompt and _scan_cron_prompt is not None:
                scan_error = _scan_cron_prompt(prompt)
                if scan_error:
                    return web.json_response({"error": scan_error}, status=400)
            if repeat is not None and (not isinstance(repeat, int) or repeat < 1):
                return web.json_response({"error": "Repeat must be a positive integer"}, status=400)

            kwargs = {
                "prompt": prompt,
                "schedule": schedule,
                "name": name,
                "deliver": deliver,
                "origin": self._cron_origin_from_request(request),
            }
            if skills:
                kwargs["skills"] = skills
            if repeat is not None:
                kwargs["repeat"] = repeat

            job = _cron_create(**kwargs)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_get_job(self, request: "web.Request") -> "web.Response":
        """GET /api/jobs/{job_id} — get a single cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_get(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_update_job(self, request: "web.Request") -> "web.Response":
        """PATCH /api/jobs/{job_id} — update a cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            body = await request.json()
            # Whitelist allowed fields to prevent arbitrary key injection
            sanitized = {k: v for k, v in body.items() if k in self._UPDATE_ALLOWED_FIELDS}
            if not sanitized:
                return web.json_response({"error": "No valid fields to update"}, status=400)
            # Validate lengths if present
            if "name" in sanitized and len(sanitized["name"]) > self._MAX_NAME_LENGTH:
                return web.json_response(
                    {"error": f"Name must be ≤ {self._MAX_NAME_LENGTH} characters"}, status=400,
                )
            if "prompt" in sanitized and len(sanitized["prompt"]) > self._MAX_PROMPT_LENGTH:
                return web.json_response(
                    {"error": f"Prompt must be ≤ {self._MAX_PROMPT_LENGTH} characters"}, status=400,
                )
            if sanitized.get("prompt") and _scan_cron_prompt is not None:
                scan_error = _scan_cron_prompt(sanitized["prompt"])
                if scan_error:
                    return web.json_response({"error": scan_error}, status=400)
            job = _cron_update(job_id, sanitized)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_delete_job(self, request: "web.Request") -> "web.Response":
        """DELETE /api/jobs/{job_id} — delete a cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            success = _cron_remove(job_id)
            if not success:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_pause_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/pause — pause a cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_pause(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_resume_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/resume — resume a paused cron job."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_resume(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_run_job(self, request: "web.Request") -> "web.Response":
        """POST /api/jobs/{job_id}/run — trigger immediate execution."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        cron_err = self._check_jobs_available()
        if cron_err:
            return cron_err
        job_id, id_err = self._check_job_id(request)
        if id_err:
            return id_err
        try:
            job = _cron_trigger(job_id)
            if not job:
                return web.json_response({"error": "Job not found"}, status=404)
            return web.json_response({"job": job})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ------------------------------------------------------------------
    # Output extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response_conversation_history(
        conversation_history: List[Dict[str, Any]],
        user_message: Any,
        result: Dict[str, Any],
        final_response: Any,
    ) -> List[Dict[str, Any]]:
        """Build the stored Responses transcript without duplicating history."""
        prior = list(conversation_history)
        current_user = {"role": "user", "content": user_message}
        agent_messages = result.get("messages") if isinstance(result, dict) else None

        if isinstance(agent_messages, list) and agent_messages:
            turn_start = APIServerAdapter._response_messages_turn_start_index(
                conversation_history,
                user_message,
                result,
            )
            if turn_start:
                return list(agent_messages)

            full_history = prior
            full_history.append(current_user)
            full_history.extend(agent_messages)
            return full_history

        full_history = prior
        full_history.append(current_user)
        full_history.append({"role": "assistant", "content": final_response})
        return full_history

    @staticmethod
    def _response_messages_turn_start_index(
        conversation_history: List[Dict[str, Any]],
        user_message: Any,
        result: Dict[str, Any],
    ) -> int:
        """Detect transcript-shaped result["messages"] and return turn start."""
        agent_messages = result.get("messages") if isinstance(result, dict) else None
        if not isinstance(agent_messages, list) or not agent_messages:
            return 0

        prior = list(conversation_history)
        current_user = {"role": "user", "content": user_message}
        expected_prefix = prior + [current_user]
        if agent_messages[:len(expected_prefix)] == expected_prefix:
            return len(expected_prefix)
        if prior and agent_messages[:len(prior)] == prior:
            return len(prior)
        return 0

    @classmethod
    def _turn_transcript_messages(
        cls,
        conversation_history: List[Dict[str, Any]],
        user_message: Any,
        result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return this turn's assistant/tool messages in client-safe shape.

        The streaming SSE contract delivers all assistant text as
        ``assistant.delta`` events under one ``message_id`` interleaved with
        ``tool.*`` events, and a single ``assistant.completed`` carrying only
        the final reply.  A client that accumulates deltas into one buffer
        cannot reconstruct *intermediate* assistant text segments that preceded
        tool calls — so when the page is re-opened mid/post-stream those
        segments appear lost, even though state.db persisted them correctly.

        Emitting the authoritative per-turn transcript on ``run.completed`` lets
        any SSE consumer reconcile its live view against ground truth without a
        separate ``GET /messages`` round-trip.  Purely additive: clients that
        ignore the field are unaffected.  Refs #34703.
        """
        agent_messages = result.get("messages") if isinstance(result, dict) else None
        if not isinstance(agent_messages, list) or not agent_messages:
            return []
        start = cls._response_messages_turn_start_index(
            conversation_history, user_message, result
        )
        turn = agent_messages[start:]
        out: List[Dict[str, Any]] = []
        for msg in turn:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") not in {"assistant", "tool"}:
                continue
            out.append(cls._message_response(msg))
        return out

    @staticmethod
    def _extract_output_items(result: Dict[str, Any], start_index: int = 0) -> List[Dict[str, Any]]:
        """
        Build the output item array from the agent's messages.

        Walks *result["messages"]* starting at *start_index* and emits:
        - ``function_call`` items for each tool_call on assistant messages
        - ``function_call_output`` items for each tool-role message
        - a final ``message`` item with the assistant's text reply
        """
        items: List[Dict[str, Any]] = []
        messages = result.get("messages", [])
        if start_index > 0:
            messages = messages[start_index:]

        for msg in messages:
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    items.append({
                        "type": "function_call",
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                        "call_id": tc.get("id", ""),
                    })
            elif role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": msg.get("content", ""),
                })

        # Final assistant message
        final = result.get("final_response", "")
        if not final:
            final = result.get("error", "(No response generated)")

        items.append({
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": final,
                }
            ],
        })
        return items

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        reasoning_callback=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        status_callback=None,
        agent_ref: Optional[list] = None,
        gateway_session_key: Optional[str] = None,
        model_override: Optional[str] = None,
        reasoning_config_override: Optional[Dict[str, Any]] = None,
        approval_notify_callback=None,
        agent_id_slug: Optional[str] = None,
        replace_system_prompt: bool = False,
    ) -> tuple:
        """
        Create an agent and run a conversation in a thread executor.

        Returns ``(result_dict, usage_dict)`` where *usage_dict* contains
        ``input_tokens``, ``output_tokens`` and ``total_tokens``.

        If *agent_ref* is a one-element list, the AIAgent instance is stored
        at ``agent_ref[0]`` before ``run_conversation`` begins.  This allows
        callers (e.g. the SSE writer) to call ``agent.interrupt()`` from
        another thread to stop in-progress LLM calls.

        If *approval_notify_callback* is provided, it is registered as the
        gateway approval notifier for this run's session so that a command or
        execute_code that ESCALATES (smart mode) surfaces an approval request
        to the caller's stream instead of dead-ending on a missing listener.
        Mirrors the wiring in ``_handle_runs``. Additive: when None (default)
        behavior is unchanged.
        """
        loop = asyncio.get_running_loop()
        _approval_skey = gateway_session_key or session_id or ""

        def _run():
            from gateway.session_context import clear_session_vars, set_session_vars

            tokens = set_session_vars(
                platform="api_server",
                chat_id=session_id or "",
                session_key=gateway_session_key or session_id or "",
                session_id=session_id or "",
            )
            # Per-agent profile isolation: scope this run's home to the agent's
            # profile dir so memory (and any home-scoped state) load from
            # agent-profiles/{slug}/ instead of the shared default home. The
            # session DB is passed explicitly (DEFAULT_DB_PATH is frozen at
            # import, so the override alone would not redirect it).
            _home_token = None
            _profile_db = None
            if agent_id_slug:
                from hermes_constants import (
                    get_hermes_home,
                    set_hermes_home_override,
                )
                _profile_db = self._get_agent_profile_db(agent_id_slug)
                # Fail closed: never silently fall back to the shared main db for
                # an agent-scoped turn. Doing so would leak this agent's
                # conversation into the main agent's history and break resume
                # (reads route to the agent's profile db, which would be empty).
                if _profile_db is None:
                    raise RuntimeError(
                        f"agent-profile db unavailable for {agent_id_slug!r}"
                    )
                _profile_dir = get_hermes_home() / "agent-profiles" / agent_id_slug
                _home_token = set_hermes_home_override(str(_profile_dir))
            _notify_registered = False
            if approval_notify_callback is not None and _approval_skey:
                try:
                    from tools.approval import register_gateway_notify
                    register_gateway_notify(_approval_skey, approval_notify_callback)
                    _notify_registered = True
                except Exception:
                    _notify_registered = False
            try:
                agent = self._create_agent(
                    ephemeral_system_prompt=ephemeral_system_prompt,
                    session_id=session_id,
                    stream_delta_callback=stream_delta_callback,
                    reasoning_callback=reasoning_callback,
                    tool_progress_callback=tool_progress_callback,
                    tool_start_callback=tool_start_callback,
                    tool_complete_callback=tool_complete_callback,
                    status_callback=status_callback,
                    model_override=model_override,
                    reasoning_config_override=reasoning_config_override,
                    gateway_session_key=gateway_session_key,
                    session_db_override=_profile_db,
                    is_agent_profile=bool(agent_id_slug),
                    replace_system_prompt=replace_system_prompt,
                )
                if agent_ref is not None:
                    agent_ref[0] = agent
                effective_task_id = session_id or str(uuid.uuid4())
                result = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    task_id=effective_task_id,
                )
                usage = {
                    "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                    "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                    "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
                }
                # Include the effective session ID in the result so callers
                # (e.g. X-OpenComputer-Session-Id header) can track compression-
                # triggered session rotations. (#16938)
                _eff_sid = getattr(agent, "session_id", session_id)
                if isinstance(_eff_sid, str) and _eff_sid:
                    result["session_id"] = _eff_sid
                return result, usage
            finally:
                if _notify_registered:
                    try:
                        from tools.approval import unregister_gateway_notify
                        unregister_gateway_notify(_approval_skey)
                    except Exception:
                        pass
                clear_session_vars(tokens)
                if _home_token is not None:
                    try:
                        from hermes_constants import reset_hermes_home_override
                        reset_hermes_home_override(_home_token)
                    except Exception:
                        pass

        # Run inside a copied context so any ContextVar mutation in _run (e.g.
        # the agent-profile home override) is sandboxed to a throwaway context
        # and can never leak into other tasks sharing the executor's threads —
        # matching gateway/run.py's _run_in_executor_with_context pattern.
        from contextvars import copy_context

        _ctx = copy_context()
        return await loop.run_in_executor(None, _ctx.run, _run)

    # ------------------------------------------------------------------
    # /v1/runs — structured event streaming
    # ------------------------------------------------------------------

    _MAX_CONCURRENT_RUNS = 10  # Prevent unbounded resource allocation
    _RUN_STREAM_TTL = 300  # seconds before orphaned runs are swept
    _RUN_STATUS_TTL = 3600  # seconds to retain terminal run status for polling

    def _set_run_status(self, run_id: str, status: str, **fields: Any) -> Dict[str, Any]:
        """Update pollable run status without exposing private agent objects."""
        now = time.time()
        current = self._run_statuses.get(run_id, {})
        current.update({
            "object": "hermes.run",
            "run_id": run_id,
            "status": status,
            "updated_at": now,
        })
        current.setdefault("created_at", fields.pop("created_at", now))
        current.update(fields)
        self._run_statuses[run_id] = current
        return current

    def _make_run_event_callback(self, run_id: str, loop: "asyncio.AbstractEventLoop"):
        """Return a tool_progress_callback that pushes structured events to the run's SSE queue."""
        def _push(event: Dict[str, Any]) -> None:
            self._set_run_status(
                run_id,
                self._run_statuses.get(run_id, {}).get("status", "running"),
                last_event=event.get("event"),
            )
            q = self._run_streams.get(run_id)
            if q is None:
                return
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except Exception:
                pass

        def _callback(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs):
            ts = time.time()
            if event_type == "tool.started":
                _push({
                    "event": "tool.started",
                    "run_id": run_id,
                    "timestamp": ts,
                    "tool": tool_name,
                    "preview": preview,
                })
            elif event_type == "tool.completed":
                _push({
                    "event": "tool.completed",
                    "run_id": run_id,
                    "timestamp": ts,
                    "tool": tool_name,
                    "duration": round(kwargs.get("duration", 0), 3),
                    "error": kwargs.get("is_error", False),
                })
            elif event_type == "reasoning.available":
                _push({
                    "event": "reasoning.available",
                    "run_id": run_id,
                    "timestamp": ts,
                    "text": preview or "",
                })
            # _thinking and subagent_progress are intentionally not forwarded

        return _callback

    async def _handle_runs(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs — start an agent run, return run_id immediately."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # Long-term memory scope header (see chat_completions for details).
        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        # Enforce concurrency limit
        if len(self._run_streams) >= self._MAX_CONCURRENT_RUNS:
            return web.json_response(
                _openai_error(f"Too many concurrent runs (max {self._MAX_CONCURRENT_RUNS})", code="rate_limit_exceeded"),
                status=429,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response(_openai_error("Invalid JSON"), status=400)

        raw_input = body.get("input")
        if not raw_input:
            return web.json_response(_openai_error("Missing 'input' field"), status=400)

        user_message = raw_input if isinstance(raw_input, str) else (raw_input[-1].get("content", "") if isinstance(raw_input, list) else "")
        if not user_message:
            return web.json_response(_openai_error("No user message found in input"), status=400)

        instructions = body.get("instructions")
        previous_response_id = body.get("previous_response_id")

        # Accept explicit conversation_history from the request body.
        # Precedence: explicit conversation_history > previous_response_id.
        conversation_history: List[Dict[str, str]] = []
        raw_history = body.get("conversation_history")
        if raw_history:
            if not isinstance(raw_history, list):
                return web.json_response(
                    _openai_error("'conversation_history' must be an array of message objects"),
                    status=400,
                )
            for i, entry in enumerate(raw_history):
                if not isinstance(entry, dict) or "role" not in entry or "content" not in entry:
                    return web.json_response(
                        _openai_error(f"conversation_history[{i}] must have 'role' and 'content' fields"),
                        status=400,
                    )
                conversation_history.append({"role": str(entry["role"]), "content": str(entry["content"])})
            if previous_response_id:
                logger.debug("Both conversation_history and previous_response_id provided; using conversation_history")

        stored_session_id = None
        if not conversation_history and previous_response_id:
            stored = self._response_store.get(previous_response_id)
            if stored:
                conversation_history = list(stored.get("conversation_history", []))
                stored_session_id = stored.get("session_id")
                if instructions is None:
                    instructions = stored.get("instructions")

        # When input is a multi-message array, extract all but the last
        # message as conversation history (the last becomes user_message).
        # Only fires when no explicit history was provided.
        if not conversation_history and isinstance(raw_input, list) and len(raw_input) > 1:
            for msg in raw_input[:-1]:
                if isinstance(msg, dict) and msg.get("role") and msg.get("content"):
                    content = msg["content"]
                    if isinstance(content, list):
                        # Flatten multi-part content blocks to text
                        content = " ".join(
                            part.get("text", "") for part in content
                            if isinstance(part, dict) and part.get("type") == "text"
                        )
                    conversation_history.append({"role": msg["role"], "content": str(content)})

        run_id = f"run_{uuid.uuid4().hex}"
        session_id = body.get("session_id") or stored_session_id or run_id
        approval_session_key = gateway_session_key or session_id or run_id
        ephemeral_system_prompt = instructions
        loop = asyncio.get_running_loop()
        q: "asyncio.Queue[Optional[Dict]]" = asyncio.Queue()
        created_at = time.time()
        self._run_streams[run_id] = q
        self._run_streams_created[run_id] = created_at
        self._run_approval_sessions[run_id] = approval_session_key

        event_cb = self._make_run_event_callback(run_id, loop)

        # Also wire stream_delta_callback so message.delta events flow through.
        def _text_cb(delta: Optional[str]) -> None:
            if delta is None:
                return
            try:
                loop.call_soon_threadsafe(q.put_nowait, {
                    "event": "message.delta",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "delta": delta,
                })
            except Exception:
                pass

        self._set_run_status(
            run_id,
            "queued",
            created_at=created_at,
            session_id=session_id,
            model=body.get("model", self._model_name),
        )

        async def _run_and_close():
            try:
                self._set_run_status(run_id, "running")
                agent = self._create_agent(
                    ephemeral_system_prompt=ephemeral_system_prompt,
                    session_id=session_id,
                    stream_delta_callback=_text_cb,
                    tool_progress_callback=event_cb,
                    gateway_session_key=gateway_session_key,
                )
                self._active_run_agents[run_id] = agent

                def _approval_notify(approval_data: Dict[str, Any]) -> None:
                    event = dict(approval_data or {})
                    event.update({
                        "event": "approval.request",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "choices": ["once", "session", "always", "deny"],
                    })
                    self._set_run_status(
                        run_id,
                        "waiting_for_approval",
                        last_event="approval.request",
                    )
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, event)
                    except Exception:
                        pass

                def _run_sync():
                    from gateway.session_context import clear_session_vars, set_session_vars
                    from tools.approval import (
                        register_gateway_notify,
                        reset_current_session_key,
                        set_current_session_key,
                        unregister_gateway_notify,
                    )

                    effective_task_id = session_id or run_id
                    approval_token = None
                    session_tokens = []
                    try:
                        # Bind approval/session identity for this API run via
                        # contextvars so concurrent runs do not share process
                        # environment state.
                        approval_token = set_current_session_key(approval_session_key)
                        session_tokens = set_session_vars(
                            platform="api_server",
                            session_key=approval_session_key,
                        )
                        register_gateway_notify(approval_session_key, _approval_notify)
                        r = agent.run_conversation(
                            user_message=user_message,
                            conversation_history=conversation_history,
                            task_id=effective_task_id,
                        )
                    finally:
                        try:
                            unregister_gateway_notify(approval_session_key)
                        finally:
                            if approval_token is not None:
                                try:
                                    reset_current_session_key(approval_token)
                                except Exception:
                                    pass
                            if session_tokens:
                                try:
                                    clear_session_vars(session_tokens)
                                except Exception:
                                    pass
                    u = {
                        "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                        "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                        "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
                    }
                    return r, u

                result, usage = await asyncio.get_running_loop().run_in_executor(None, _run_sync)
                # Check for structured failure (non-retryable client errors like
                # 401/400 return failed=True instead of raising, so the except
                # block below never fires — issue #15561).
                if isinstance(result, dict) and result.get("failed"):
                    error_msg = result.get("error") or "agent run failed"
                    q.put_nowait({
                        "event": "run.failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "error": error_msg,
                    })
                    self._set_run_status(
                        run_id,
                        "failed",
                        error=error_msg,
                        last_event="run.failed",
                    )
                else:
                    final_response = result.get("final_response", "") if isinstance(result, dict) else ""
                    q.put_nowait({
                        "event": "run.completed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "output": final_response,
                        "usage": usage,
                    })
                    self._set_run_status(
                        run_id,
                        "completed",
                        output=final_response,
                        usage=usage,
                        last_event="run.completed",
                    )
            except asyncio.CancelledError:
                self._set_run_status(
                    run_id,
                    "cancelled",
                    last_event="run.cancelled",
                )
                try:
                    q.put_nowait({
                        "event": "run.cancelled",
                        "run_id": run_id,
                        "timestamp": time.time(),
                    })
                except Exception:
                    pass
                raise
            except Exception as exc:
                logger.exception("[api_server] run %s failed", run_id)
                self._set_run_status(
                    run_id,
                    "failed",
                    error=str(exc),
                    last_event="run.failed",
                )
                try:
                    q.put_nowait({
                        "event": "run.failed",
                        "run_id": run_id,
                        "timestamp": time.time(),
                        "error": str(exc),
                    })
                except Exception:
                    pass
            finally:
                # If the asyncio wrapper is cancelled (for example via
                # /stop), the executor thread can still be blocked waiting
                # on an approval Event.  Unregistering here releases those
                # waits immediately; the in-thread unregister is harmlessly
                # idempotent on normal completion.
                try:
                    from tools.approval import unregister_gateway_notify

                    unregister_gateway_notify(approval_session_key)
                except Exception:
                    pass
                # Sentinel: signal SSE stream to close
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
                self._active_run_agents.pop(run_id, None)
                self._active_run_tasks.pop(run_id, None)
                self._run_approval_sessions.pop(run_id, None)

        task = asyncio.create_task(_run_and_close())
        self._active_run_tasks[run_id] = task
        try:
            self._background_tasks.add(task)
        except TypeError:
            pass
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._background_tasks.discard)

        response_headers = (
            {"X-OpenComputer-Session-Key": gateway_session_key} if gateway_session_key else {}
        )
        return web.json_response(
            {"run_id": run_id, "status": "started"},
            status=202,
            headers=response_headers,
        )

    async def _handle_get_run(self, request: "web.Request") -> "web.Response":
        """GET /v1/runs/{run_id} — return pollable run status for external UIs."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]
        status = self._run_statuses.get(run_id)
        if status is None:
            return web.json_response(
                _openai_error(f"Run not found: {run_id}", code="run_not_found"),
                status=404,
            )
        return web.json_response(status)

    async def _handle_run_events(self, request: "web.Request") -> "web.StreamResponse":
        """GET /v1/runs/{run_id}/events — SSE stream of structured agent lifecycle events."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]

        # Allow subscribing slightly before the run is registered (race condition window)
        for _ in range(20):
            if run_id in self._run_streams:
                break
            await asyncio.sleep(0.05)
        else:
            return web.json_response(_openai_error(f"Run not found: {run_id}", code="run_not_found"), status=404)

        q = self._run_streams[run_id]

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                if event is None:
                    # Run finished — send final SSE comment and close
                    await response.write(b": stream closed\n\n")
                    break
                payload = f"data: {json.dumps(event)}\n\n"
                await response.write(payload.encode())
        except Exception as exc:
            logger.debug("[api_server] SSE stream error for run %s: %s", run_id, exc)
        finally:
            self._run_streams.pop(run_id, None)
            self._run_streams_created.pop(run_id, None)

        return response


    async def _handle_run_approval(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs/{run_id}/approval — resolve a pending run approval."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]
        status = self._run_statuses.get(run_id)
        if status is None:
            return web.json_response(
                _openai_error(f"Run not found: {run_id}", code="run_not_found"),
                status=404,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response(_openai_error("Invalid JSON"), status=400)

        raw_choice = str(body.get("choice", "")).strip().lower()
        aliases = {"approve": "once", "approved": "once", "allow": "once"}
        choice = aliases.get(raw_choice, raw_choice)
        allowed = {"once", "session", "always", "deny"}
        if choice not in allowed:
            return web.json_response(
                _openai_error(
                    "Invalid approval choice; expected one of: once, session, always, deny",
                    code="invalid_approval_choice",
                ),
                status=400,
            )

        approval_session_key = self._run_approval_sessions.get(run_id)
        if not approval_session_key:
            return web.json_response(
                _openai_error(
                    f"Run has no active approval session: {run_id}",
                    code="approval_not_active",
                ),
                status=409,
            )

        resolve_all = (
            _coerce_request_bool(body.get("all"), default=False)
            or _coerce_request_bool(body.get("resolve_all"), default=False)
        )
        try:
            from tools.approval import resolve_gateway_approval

            resolved = resolve_gateway_approval(
                approval_session_key,
                choice,
                resolve_all=resolve_all,
            )
        except Exception as exc:
            logger.exception("[api_server] approval resolution failed for run %s", run_id)
            return web.json_response(_openai_error(str(exc)), status=500)

        if resolved <= 0:
            return web.json_response(
                _openai_error(
                    f"Run has no pending approval: {run_id}",
                    code="approval_not_pending",
                ),
                status=409,
            )

        self._set_run_status(run_id, "running", last_event="approval.responded")
        q = self._run_streams.get(run_id)
        if q is not None:
            try:
                q.put_nowait({
                    "event": "approval.responded",
                    "run_id": run_id,
                    "timestamp": time.time(),
                    "choice": choice,
                    "resolved": resolved,
                })
            except Exception:
                pass

        return web.json_response({
            "object": "hermes.run.approval_response",
            "run_id": run_id,
            "choice": choice,
            "resolved": resolved,
        })

    async def _handle_stop_run(self, request: "web.Request") -> "web.Response":
        """POST /v1/runs/{run_id}/stop — interrupt a running agent."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        run_id = request.match_info["run_id"]
        agent = self._active_run_agents.get(run_id)
        task = self._active_run_tasks.get(run_id)

        if agent is None and task is None:
            return web.json_response(_openai_error(f"Run not found: {run_id}", code="run_not_found"), status=404)

        self._set_run_status(run_id, "stopping", last_event="run.stopping")

        if agent is not None:
            try:
                agent.interrupt("Stop requested via API")
            except Exception:
                pass

        if task is not None and not task.done():
            task.cancel()
            # Bounded wait: run_conversation() executes in the default
            # executor thread which task.cancel() cannot preempt — we rely on
            # agent.interrupt() above to break the loop. Cap the wait so a
            # slow/unresponsive interrupt can't hang this handler.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[api_server] stop for run %s timed out after 5s; "
                    "agent may still be finishing the current step",
                    run_id,
                )
            except (asyncio.CancelledError, Exception):
                pass

        return web.json_response({"run_id": run_id, "status": "stopping"})

    async def _sweep_orphaned_runs(self) -> None:
        """Periodically clean up run streams that were never consumed."""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            stale = [
                run_id
                for run_id, created_at in list(self._run_streams_created.items())
                if now - created_at > self._RUN_STREAM_TTL
            ]
            for run_id in stale:
                logger.debug("[api_server] sweeping orphaned run %s", run_id)
                try:
                    from tools.approval import unregister_gateway_notify

                    approval_session_key = self._run_approval_sessions.get(run_id)
                    if approval_session_key:
                        unregister_gateway_notify(approval_session_key)
                except Exception:
                    pass
                self._run_streams.pop(run_id, None)
                self._run_streams_created.pop(run_id, None)
                self._active_run_agents.pop(run_id, None)
                self._active_run_tasks.pop(run_id, None)
                self._run_approval_sessions.pop(run_id, None)

            stale_statuses = [
                run_id
                for run_id, status in list(self._run_statuses.items())
                if status.get("status") in {"completed", "failed", "cancelled"}
                and now - float(status.get("updated_at", 0) or 0) > self._RUN_STATUS_TTL
            ]
            for run_id in stale_statuses:
                self._run_statuses.pop(run_id, None)

    # ------------------------------------------------------------------
    # BasePlatformAdapter interface
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Start the aiohttp web server."""
        if not AIOHTTP_AVAILABLE:
            logger.warning("[%s] aiohttp not installed", self.name)
            return False

        try:
            mws = [mw for mw in (cors_middleware, body_limit_middleware, security_headers_middleware) if mw is not None]
            self._app = web.Application(middlewares=mws, client_max_size=MAX_REQUEST_BYTES)
            assert self._app is not None
            self._app.router.add_get("/health", self._handle_health)
            self._app.router.add_get("/health/detailed", self._handle_health_detailed)
            self._app.router.add_get("/v1/health", self._handle_health)
            self._app.router.add_get("/v1/models", self._handle_models)
            self._app.router.add_get("/v1/oc/model_availability", self._handle_oc_model_availability)
            self._app.router.add_get("/v1/capabilities", self._handle_capabilities)
            self._app.router.add_get("/v1/skills", self._handle_skills)
            self._app.router.add_get("/v1/toolsets", self._handle_toolsets)
            # Session/client control surface (thin wrappers over SessionDB + _run_agent)
            self._app.router.add_get("/api/sessions", self._handle_list_sessions)
            self._app.router.add_post("/api/sessions", self._handle_create_session)
            self._app.router.add_get("/api/sessions/{session_id}", self._handle_get_session)
            self._app.router.add_patch("/api/sessions/{session_id}", self._handle_patch_session)
            self._app.router.add_delete("/api/sessions/{session_id}", self._handle_delete_session)
            self._app.router.add_get("/api/sessions/{session_id}/messages", self._handle_session_messages)
            self._app.router.add_post("/api/sessions/{session_id}/fork", self._handle_fork_session)
            self._app.router.add_post("/api/sessions/{session_id}/chat", self._handle_session_chat)
            self._app.router.add_post("/api/sessions/{session_id}/chat/stream", self._handle_session_chat_stream)
            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
            self._app.router.add_post("/v1/responses", self._handle_responses)
            self._app.router.add_get("/v1/responses/{response_id}", self._handle_get_response)
            self._app.router.add_delete("/v1/responses/{response_id}", self._handle_delete_response)
            # Cron jobs management API
            self._app.router.add_get("/api/jobs", self._handle_list_jobs)
            self._app.router.add_post("/api/jobs", self._handle_create_job)
            self._app.router.add_get("/api/jobs/{job_id}", self._handle_get_job)
            self._app.router.add_patch("/api/jobs/{job_id}", self._handle_update_job)
            self._app.router.add_delete("/api/jobs/{job_id}", self._handle_delete_job)
            self._app.router.add_post("/api/jobs/{job_id}/pause", self._handle_pause_job)
            self._app.router.add_post("/api/jobs/{job_id}/resume", self._handle_resume_job)
            self._app.router.add_post("/api/jobs/{job_id}/run", self._handle_run_job)
            # Parallel-agents read-only surface (oc_flow / oc_agents / oc_teams plugins)
            self._app.router.add_get("/api/parallel-agents", self._handle_parallel_agents)
            # Parallel-agents drill-down: per-entity detail, control, and chat bridge.
            self._app.router.add_get("/api/parallel-agents/flows/{flow_id}", self._handle_flow_detail)
            self._app.router.add_post("/api/parallel-agents/agents", self._handle_agent_launch)
            self._app.router.add_get("/api/parallel-agents/agents/{session_id}", self._handle_agent_detail)
            self._app.router.add_get("/api/parallel-agents/teams/{team_id}", self._handle_team_detail)
            self._app.router.add_post("/api/parallel-agents/agents/{session_id}/stop", self._handle_agent_stop)
            self._app.router.add_post("/api/parallel-agents/agents/{session_id}/send", self._handle_agent_send)
            self._app.router.add_post("/api/parallel-agents/flows/{flow_id}/stop", self._handle_flow_stop)
            self._app.router.add_post("/api/parallel-agents/teams/{team_id}/messages", self._handle_team_send_message)
            self._app.router.add_get("/v1/parallel-agents/events", self._handle_parallel_agents_events)
            self._app.router.add_get("/api/memory", self._handle_get_memory)
            # Artifacts (claude.ai-style viewer): list + download-by-id. The web
            # BFF proxy calls the /api/v1/ paths; the non-v1 aliases keep parity
            # with the rest of the /api/sessions surface.
            self._app.router.add_get("/api/v1/sessions/{session_id}/artifacts", self._handle_artifact_list)
            self._app.router.add_get("/api/v1/sessions/{session_id}/artifacts/{artifact_id}/download", self._handle_artifact_download)
            self._app.router.add_get("/api/sessions/{session_id}/artifacts", self._handle_artifact_list)
            self._app.router.add_get("/api/sessions/{session_id}/artifacts/{artifact_id}/download", self._handle_artifact_download)
            # Generated-image serving: the web BFF proxies /api/chat/file/{id}
            # here so <img> tags can load images the image_generate tool saved to
            # $HERMES_HOME/cache/images/. Served by basename, confined to that dir.
            self._app.router.add_get("/api/files/{file_id}", self._handle_oc_image_file)
            # Authenticated passthrough to the on-box Open Design daemon so the
            # workspace "Open Design" panel reaches it over the agent's existing
            # tunnel without exposing the daemon publicly.
            self._app.router.add_route("*", "/api/od", self._handle_open_design_proxy)
            self._app.router.add_route("*", "/api/od/{path:.*}", self._handle_open_design_proxy)
            # Structured event streaming
            self._app.router.add_post("/v1/runs", self._handle_runs)
            self._app.router.add_get("/v1/runs/{run_id}", self._handle_get_run)
            self._app.router.add_get("/v1/runs/{run_id}/events", self._handle_run_events)
            self._app.router.add_post("/v1/runs/{run_id}/approval", self._handle_run_approval)
            self._app.router.add_post("/v1/runs/{run_id}/stop", self._handle_stop_run)
            # Store the adapter after native routes are registered. Local OpenComputer-Relay
            # bootstrap shims use this key as a feature-detection hook; registering
            # native routes first lets those shims no-op instead of shadowing the
            # upstream session-control handlers.
            self._app["api_server_adapter"] = self

            # Start background sweep to clean up orphaned (unconsumed) run streams
            sweep_task = asyncio.create_task(self._sweep_orphaned_runs())
            try:
                self._background_tasks.add(sweep_task)
            except TypeError:
                pass
            if hasattr(sweep_task, "add_done_callback"):
                sweep_task.add_done_callback(self._background_tasks.discard)

            # Refuse to start without authentication. The API server can
            # dispatch terminal-capable agent work, so every deployment needs
            # an explicit API_SERVER_KEY regardless of bind address.
            if not self._api_key:
                logger.error(
                    "[%s] Refusing to start: API_SERVER_KEY is required for the API server, "
                    "including loopback-only binds on %s.",
                    self.name, self._host,
                )
                return False

            # Refuse to start network-accessible with a placeholder key.
            # Ported from openclaw/openclaw#64586.
            if is_network_accessible(self._host) and self._api_key:
                try:
                    from hermes_cli.auth import has_usable_secret
                    if not has_usable_secret(self._api_key, min_length=8):
                        logger.error(
                            "[%s] Refusing to start: API_SERVER_KEY is set to a "
                            "placeholder value. Generate a real secret "
                            "(e.g. `openssl rand -hex 32`) and set API_SERVER_KEY "
                            "before exposing the API server on %s.",
                            self.name, self._host,
                        )
                        return False
                except ImportError:
                    pass

            # Port conflict detection — fail fast if port is already in use
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
                    _s.settimeout(1)
                    _s.connect(('127.0.0.1', self._port))
                logger.error('[%s] Port %d already in use. Set a different port in config.yaml: platforms.api_server.port', self.name, self._port)
                return False
            except (ConnectionRefusedError, OSError):
                pass  # port is free

            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self._host, self._port)
            await self._site.start()

            self._mark_connected()
            logger.info(
                "[%s] API server listening on http://%s:%d (model: %s)",
                self.name, self._host, self._port, self._model_name,
            )
            return True

        except Exception as e:
            logger.error("[%s] Failed to start API server: %s", self.name, e)
            return False

    async def disconnect(self) -> None:
        """Stop the aiohttp web server and release all owned resources.

        Closes the ResponseStore SQLite connection in addition to stopping
        the aiohttp web server. Without this, every adapter instance leaks
        2 file descriptors (the database file and its WAL sidecar) — the
        reconnect loop in ``gateway.run`` constructs a fresh adapter on
        every retry, so 2 fds/retry × 300s backoff cap ≈ 12 fds/hour, which
        exhausts the default 2560 fd limit after ~12h of failed reconnects
        and turns the whole gateway into a zombie
        (OSError: [Errno 24] Too many open files, #37011).
        """
        self._mark_disconnected()
        if self._response_store is not None:
            try:
                self._response_store.close()
            except Exception:
                logger.debug(
                    "Failed to close response store for %s", self.name, exc_info=True,
                )
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        logger.info("[%s] API server stopped", self.name)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        Not used — HTTP request/response cycle handles delivery directly.
        """
        return SendResult(success=False, error="API server uses HTTP request/response, not send()")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about the API server."""
        return {
            "name": "API Server",
            "type": "api",
            "host": self._host,
            "port": self._port,
        }


# ---------------------------------------------------------------------------
# Model-availability probe — powers the web prompt-bar picker's autonomy.
#
# There is no free signal for whether a given model currently routes: the
# router's /v1/models catalog is a curated subset (it omits some routable ids
# like opus-4-7) and carries no credit/affordability data, and there's no
# credits endpoint. The only truthful signal is an actual minimal generation.
# So we probe each model with max_tokens=1 (no agent loop, no tools, no system
# prompt) — the cheapest possible call — and cache the verdict per TTL so this
# is one probe-set per window, not continuous polling. The client overlays the
# verdict on its static defaults, so a credit-gated model auto-enables the
# moment funding lands and auto-disables if it starts failing, with no code
# edit. Model-agnostic: works for any chat_completions provider.
# ---------------------------------------------------------------------------

_DEFAULT_AVAIL_MODELS = (
    # Anthropic
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    # OpenAI (via OC Router) — curated chat/reasoning set surfaced in the
    # prompt-bar picker. Probed live so the UI reflects what the router serves.
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2-pro",
)
_MODEL_AVAIL_TTL_SECONDS = 600.0
# key: frozenset(model ids) -> (monotonic_ts, result_dict). Best-effort, no
# lock: a rare concurrent miss just issues a duplicate cheap probe.
_model_avail_cache: Dict[Any, tuple] = {}


async def _probe_one_model(
    session, base_url: str, api_key: Optional[str], model: str, probe_max_tokens: int
):
    """Classify one model's availability with a minimal completion.

    ``probe_max_tokens`` matches the budget the agent really requests so the
    provider's credit-affordability gate (``402 ... can only afford N tokens``)
    fires identically to real usage — a tiny ``max_tokens`` would falsely pass
    (1 token is affordable even when the agent's real request isn't). The model
    still only emits a few tokens for the ``"."`` prompt; ``max_tokens`` is the
    cap, not a forced length, so the probe stays cheap for funded models and
    is free (402, no generation) for credit-gated ones.
    """
    import aiohttp  # lazy: aiohttp is an optional dep of this module

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "max_tokens": probe_max_tokens,
        "messages": [{"role": "user", "content": "."}],
        "stream": False,
    }
    try:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            status = resp.status
            text = (await resp.text())[:2000]
    except Exception as exc:  # network/timeout — ambiguous, don't disable
        return model, {"available": None, "reason": f"probe-error:{type(exc).__name__}"}
    if status == 200:
        return model, {"available": True, "reason": "ok"}
    low = text.lower()
    # A 400 specifically about max_tokens means the model IS reachable — our
    # probe just over-asked. Treat as available (the model itself works).
    if status == 400 and "max_tokens" in low:
        return model, {"available": True, "reason": "ok"}
    if status == 402 or "more credits" in low or "insufficient" in low or "afford" in low:
        return model, {"available": False, "reason": "credits"}
    if (
        status == 404
        or "not found" in low
        or "no available accounts" in low
        or "no endpoints" in low
        or "no allowed providers" in low
    ):
        return model, {"available": False, "reason": "unsupported"}
    # 401/403/5xx/other — ambiguous; never falsely disable a possibly-working model.
    return model, {"available": None, "reason": f"http-{status}"}


async def _probe_model_availability(models: List[str]) -> Dict[str, Any]:
    """Probe (cached) the availability of ``models`` against the configured provider."""
    import asyncio  # lazy

    key = frozenset(models)
    now = time.monotonic()
    cached = _model_avail_cache.get(key)
    if cached and now - cached[0] < _MODEL_AVAIL_TTL_SECONDS:
        return cached[1]

    try:
        from gateway.run import _resolve_runtime_agent_kwargs
        rk = _resolve_runtime_agent_kwargs()
        base_url = rk.get("base_url")
        api_key = rk.get("api_key")
        api_mode = rk.get("api_mode")
    except Exception as exc:
        return {"availability": {}, "probed_at": int(time.time()), "error": f"provider-resolve:{exc}"}

    if not base_url or (api_mode not in (None, "", "chat_completions", "openai")):
        # Non-chat_completions providers aren't probed here; client uses static defaults.
        availability = {m: {"available": None, "reason": "unprobed-api-mode"} for m in models}
        result = {"availability": availability, "probed_at": int(time.time()), "api_mode": api_mode}
    else:
        # Match the agent's real output budget so the credit-affordability gate
        # fires the same way (clamped to a safe band: high enough to trip a
        # near-empty wallet, low enough to stay under any model's output ceiling
        # and avoid a max_tokens 400).
        # Default 64000 — the effective output budget the agent requests for
        # these Claude models (the credit 402 reports "you requested up to
        # 64000"); a smaller probe under-shoots the affordability gate and
        # false-positives a credit-gated model. The 400-max_tokens handler in
        # _probe_one_model covers any provider whose ceiling is lower.
        try:
            probe_max = int(rk.get("max_tokens") or 64000)
        except Exception:
            probe_max = 64000
        probe_max = max(4096, min(probe_max, 64000))
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                pairs = await asyncio.gather(
                    *[_probe_one_model(session, base_url, api_key, m, probe_max) for m in models]
                )
            result = {
                "availability": {m: info for m, info in pairs},
                "probed_at": int(time.time()),
                "probe_max_tokens": probe_max,
            }
        except Exception as exc:
            return {"availability": {}, "probed_at": int(time.time()), "error": str(exc)}

    _model_avail_cache[key] = (time.monotonic(), result)
    return result
