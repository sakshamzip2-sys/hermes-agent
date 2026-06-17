"""Read-only aggregator for the three memory planes that power the frontend
**Memory** tab (``GET /api/memory``):

1. **Local** — the agent's built-in Markdown memory: ``MEMORY.md`` / ``USER.md``
   under ``$HERMES_HOME/memories`` plus ``SOUL.md`` at ``$HERMES_HOME/SOUL.md``.
2. **Honcho** — the active external memory provider (sessions, learned
   "conclusions"/facts, peers) queried from the local Honcho REST API.
3. **GBrain** — the structured knowledge graph (status snapshot + recent pages)
   queried from the local ``gbrain serve --http`` instance.

Everything here is strictly read-only and best-effort: any plane that is
unreachable degrades to ``{"enabled": false, "error": "..."}`` so the tab still
renders the planes that are up. Network calls run concurrently with short
timeouts so the endpoint stays snappy.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # httpx is already a hard dependency of the agent
    import httpx
except Exception:  # pragma: no cover - defensive
    httpx = None  # type: ignore

# ---------------------------------------------------------------------------
# Defaults / config resolution
# ---------------------------------------------------------------------------

DEFAULT_HONCHO_BASE = "http://localhost:8000"
DEFAULT_HONCHO_WORKSPACE = "oc-memory"
DEFAULT_GBRAIN_BASE = "http://127.0.0.1:3131"

_TIMEOUT = 8.0
_MAX_FILE_CHARS = 24_000
_MAX_ITEMS = 50


def _honcho_config() -> Dict[str, str]:
    """Resolve the local Honcho base URL + workspace the agent provider uses.

    Mirrors the resolution in ``plugins/memory/honcho/client.py`` but only the
    two fields the dashboard needs. ``~/.honcho/config.json`` wins, then env.
    """
    base = os.environ.get("HONCHO_BASE_URL", "").strip() or None
    workspace: Optional[str] = None
    cfg_path = Path.home() / ".honcho" / "config.json"
    try:
        if cfg_path.exists():
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            base = base or raw.get("baseUrl") or raw.get("base_url")
            workspace = raw.get("workspace")
    except Exception:
        pass
    return {
        "base_url": base or DEFAULT_HONCHO_BASE,
        "workspace": workspace or DEFAULT_HONCHO_WORKSPACE,
    }


def _gbrain_base() -> str:
    return (os.environ.get("GBRAIN_HTTP_URL", "").strip() or DEFAULT_GBRAIN_BASE).rstrip("/")


def _gbrain_token() -> Optional[str]:
    # The /mcp endpoint requires a minted legacy API key as Bearer (the admin
    # bootstrap token is OAuth-only). GBRAIN_MCP_TOKEN holds that minted key.
    return (
        os.environ.get("GBRAIN_MCP_TOKEN", "").strip()
        or os.environ.get("GBRAIN_ADMIN_BOOTSTRAP_TOKEN", "").strip()
        or None
    )


# ---------------------------------------------------------------------------
# Local markdown plane
# ---------------------------------------------------------------------------

def _split_entries(text: str) -> List[str]:
    """Best-effort split of a memory markdown file into discrete entries.

    Honours bullet lists ("- ", "* "), numbered lists, and blank-line-separated
    paragraphs, ignoring headings/comments.
    """
    entries: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        # Skip entry/section delimiters (§ separates memory entries; --- rules)
        # so they don't render as empty bullet rows in the UI.
        if line in ("§", "---", "***", "___"):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        if line:
            entries.append(line)
    return entries[:200]


def _read_local_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    truncated = len(text) > _MAX_FILE_CHARS
    body = text[:_MAX_FILE_CHARS]
    return {
        "name": path.name,
        "path": str(path),
        "content": body,
        "entries": _split_entries(body),
        "bytes": len(text),
        "truncated": truncated,
    }


async def _local_section(hermes_home: Path) -> Dict[str, Any]:
    mem_dir = hermes_home / "memories"
    files: List[Dict[str, Any]] = []
    # MEMORY.md = promoted long-term facts; USER.md = user profile;
    # DREAMS.md = the dreaming plugin's holding pen of candidate insights.
    for name in ("MEMORY.md", "USER.md", "DREAMS.md"):
        f = _read_local_file(mem_dir / name)
        if f:
            files.append(f)
    soul = _read_local_file(hermes_home / "SOUL.md")
    if soul:
        files.append(soul)
    total_entries = sum(len(f["entries"]) for f in files)
    return {
        "enabled": True,
        "source": str(mem_dir),
        "files": files,
        "entry_count": total_entries,
    }


# ---------------------------------------------------------------------------
# Honcho plane (REST against the local instance)
# ---------------------------------------------------------------------------

def _items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
    return []


async def _honcho_section(base_url: str, workspace: str) -> Dict[str, Any]:
    if httpx is None:
        return {"enabled": False, "error": "httpx unavailable"}
    ws_url = f"{base_url.rstrip('/')}/v3/workspaces/{workspace}"
    out: Dict[str, Any] = {
        "enabled": False,
        "base_url": base_url,
        "workspace": workspace,
        "facts": [],
        "sessions": [],
        "peers": [],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as cx:
            # health
            try:
                h = await cx.get(f"{base_url.rstrip('/')}/health")
                out["enabled"] = h.status_code == 200
            except Exception as e:
                out["error"] = f"unreachable: {e}"
                return out

            failures = {"n": 0}

            async def _post(path: str) -> List[Dict[str, Any]]:
                try:
                    r = await cx.post(f"{ws_url}/{path}", json={})
                    if r.status_code == 200:
                        return _items(r.json())
                    failures["n"] += 1
                except Exception:
                    failures["n"] += 1
                return []

            conclusions, sessions, peers = await asyncio.gather(
                _post("conclusions/list"),
                _post("sessions/list"),
                _post("peers/list"),
            )

            # Health is up but every API call failed → reachable-but-unusable
            # (auth/version mismatch). Flag degraded instead of "up but empty".
            if out["enabled"] and failures["n"] >= 3:
                out["degraded"] = True
                out["error"] = "Honcho reachable but API calls failed"

            out["facts"] = [
                {
                    "content": c.get("content") or c.get("conclusion") or "",
                    "created_at": c.get("created_at"),
                    "observer": c.get("observer"),
                    "observed": c.get("observed"),
                }
                for c in conclusions
                if (c.get("content") or c.get("conclusion"))
            ][:_MAX_ITEMS]
            out["sessions"] = [
                {"id": s.get("id"), "created_at": s.get("created_at"),
                 "metadata": s.get("metadata") or {}}
                for s in sessions
            ][:_MAX_ITEMS]
            out["peers"] = [
                (p.get("id") or p.get("name")) for p in peers
                if (p.get("id") or p.get("name"))
            ][:_MAX_ITEMS]
            out["fact_count"] = len(out["facts"])
            out["session_count"] = len(out["sessions"])
    except Exception as e:  # pragma: no cover
        out["error"] = str(e)
    return out


# ---------------------------------------------------------------------------
# GBrain plane (HTTP against `gbrain serve --http`)
# ---------------------------------------------------------------------------

async def _gbrain_jsonrpc(cx: Any, base: str, token: Optional[str],
                          method: str, params: Dict[str, Any]) -> Optional[Any]:
    """Call a GBrain MCP tool over POST /mcp (JSON-RPC 2.0). Returns the
    decoded ``result`` content or None on failure."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": method, "arguments": params},
    }
    try:
        r = await cx.post(f"{base}/mcp", json=body, headers=headers)
        if r.status_code != 200:
            return None
        # MCP streamable-http may return SSE; parse either.
        text = r.text
        data = None
        if text.lstrip().startswith("{"):
            data = r.json()
        else:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except Exception:
                        continue
        if not data:
            return None
        result = data.get("result") or {}
        # MCP tool results carry content blocks; surface structured/text.
        if isinstance(result, dict):
            if "structuredContent" in result:
                return result["structuredContent"]
            content = result.get("content")
            if isinstance(content, list):
                texts = [b.get("text") for b in content if isinstance(b, dict) and b.get("text")]
                joined = "\n".join(t for t in texts if t)
                try:
                    return json.loads(joined)
                except Exception:
                    return joined
        return result
    except Exception:
        return None


def _cycle_timestamp(value: Any) -> Optional[str]:
    """Normalize a GBrain dream-cycle field to a STRING (or None).

    GBrain's ``get_status_snapshot`` may return ``cycle.last_full`` either as a plain ISO
    timestamp (older shape) or as a full run-report object
    (``{name, status, duration_ms, totals, finished_at}``). The frontend renders this value
    directly, so a non-string would crash the whole Memory tab. We extract a timestamp from
    an object (``finished_at``/``ended_at``/``at``), fall back to a readable status string,
    and never let a raw object/list through.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in ("finished_at", "ended_at", "completed_at", "at", "ts", "last_run_at"):
            v = value.get(k)
            if isinstance(v, str) and v:
                return v
        status = value.get("status")
        name = value.get("name")
        if isinstance(status, str):
            return f"{name + ': ' if isinstance(name, str) else ''}{status}"
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None


async def _gbrain_section(base: str, token: Optional[str]) -> Dict[str, Any]:
    if httpx is None:
        return {"enabled": False, "error": "httpx unavailable"}
    out: Dict[str, Any] = {"enabled": False, "base_url": base, "pages": [], "status": {}}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as cx:
            try:
                h = await cx.get(f"{base}/health")
                if h.status_code == 200:
                    out["enabled"] = True
                    try:
                        out["engine"] = h.json().get("engine")
                        out["version"] = h.json().get("version")
                    except Exception:
                        pass
                else:
                    out["error"] = f"health {h.status_code}"
                    return out
            except Exception as e:
                out["error"] = f"unreachable: {e}"
                return out

            # Stats snapshot + recent pages via MCP tools (best-effort).
            stats = await _gbrain_jsonrpc(cx, base, token, "get_stats", {})
            if isinstance(stats, dict):
                out["status"] = {
                    k: stats.get(k)
                    for k in ("page_count", "chunk_count", "embedded_count",
                              "link_count", "tag_count", "timeline_entry_count",
                              "pages_by_type")
                    if k in stats
                } or stats
            pages = await _gbrain_jsonrpc(
                cx, base, token, "list_pages", {"sort": "updated_desc", "limit": _MAX_ITEMS}
            )
            page_items = pages if isinstance(pages, list) else _items(pages)
            out["pages"] = [
                {
                    "slug": p.get("slug") or p.get("page_slug") or p.get("id"),
                    "title": p.get("title") or p.get("slug"),
                    "type": p.get("type"),
                    "updated_at": p.get("updated_at") or p.get("modified_at"),
                }
                for p in (page_items or []) if isinstance(p, dict)
            ][:_MAX_ITEMS]
            out["page_count"] = out.get("status", {}).get("page_count", len(out["pages"]))

            # Richer snapshot — sync sources, embedding coverage, dream-cycle timing.
            snapshot = await _gbrain_jsonrpc(cx, base, token, "get_status_snapshot", {})
            if isinstance(snapshot, dict):
                sync = snapshot.get("sync") or {}
                sources = sync.get("sources") or []
                first = sources[0] if sources and isinstance(sources[0], dict) else {}
                out["embedding_coverage_pct"] = first.get("embedding_coverage_pct")
                out["source_count"] = len(sources)
                out["last_sync_at"] = first.get("last_sync_at")
                cycle = snapshot.get("cycle") or {}
                # GBrain may return these as report OBJECTS, not timestamps — normalize to
                # strings so the frontend never tries to render an object (Memory-tab crash).
                out["dream_cycle"] = {
                    "last_full": _cycle_timestamp(cycle.get("last_full")),
                    "last_targeted": _cycle_timestamp(cycle.get("last_targeted")),
                }
            # Health is up but BOTH MCP tool calls failed → reachable-but-unusable
            # (almost always a bad/missing GBRAIN_MCP_TOKEN). Flag degraded so the
            # UI can show "auth failed" rather than "up but empty".
            if stats is None and pages is None:
                out["degraded"] = True
                out["error"] = "GBrain reachable but MCP tool calls failed (check GBRAIN_MCP_TOKEN)"
    except Exception as e:  # pragma: no cover
        out["error"] = str(e)
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def build_memory_payload(hermes_home: Path) -> Dict[str, Any]:
    """Build the full ``/api/memory`` payload, querying all three planes
    concurrently. Never raises — failed planes report ``enabled: false``."""
    honcho_cfg = _honcho_config()
    local, honcho, gbrain = await asyncio.gather(
        _local_section(hermes_home),
        _honcho_section(honcho_cfg["base_url"], honcho_cfg["workspace"]),
        _gbrain_section(_gbrain_base(), _gbrain_token()),
        return_exceptions=True,
    )

    def _safe(section: Any, name: str) -> Dict[str, Any]:
        if isinstance(section, dict):
            return section
        return {"enabled": False, "error": f"{name} aggregation failed: {section}"}

    return {
        "local": _safe(local, "local"),
        "honcho": _safe(honcho, "honcho"),
        "gbrain": _safe(gbrain, "gbrain"),
    }
