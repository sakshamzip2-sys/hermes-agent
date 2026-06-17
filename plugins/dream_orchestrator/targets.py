"""Dream targets — one adapter per dreamer behind a common interface.

Three dreamers exist in v2, separate and not talking to each other:

* **local**  — ``plugins.dreaming`` (in-process). Promotes facts to MEMORY.md.
* **honcho** — the Honcho memory server (REST ``schedule_dream``).
* **gbrain** — the running ``gbrain serve --http`` server (HTTP MCP ``submit_job``).

Each adapter implements :class:`DreamTarget`: a ``health()`` probe and a
``trigger()`` call. A down/unconfigured target is **skipped cleanly** (its result
carries ``status="skipped"``) — it is never fatal to the orchestration. Adapters
never raise into the orchestrator; failures surface as ``status="error"`` with a
human ``detail``.

The orchestrator only knows this interface; all transport/config detail is here.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("hermes.plugins.dream_orchestrator.targets")

_HTTP_TIMEOUT = 30.0
_HEALTH_TIMEOUT = 5.0


@dataclass
class TargetResult:
    """Outcome of one target's health/trigger within a run."""

    name: str
    status: str  # "ok" | "skipped" | "error" | "disabled" | "would_run" (plan)
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status,
                "detail": self.detail, "data": self.data}


class DreamTarget:
    """Common interface for a dreamer the orchestrator can drive."""

    name = "base"

    def health(self) -> tuple[bool, str]:
        """Return ``(reachable, detail)``. Never raises."""
        raise NotImplementedError

    def trigger(self, *, force: bool = False) -> TargetResult:
        """Kick off this dreamer's cycle. Never raises."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# LOCAL — in-process plugins.dreaming
# ---------------------------------------------------------------------------
class LocalDreamTarget(DreamTarget):
    name = "local"

    def health(self) -> tuple[bool, str]:
        try:
            from plugins.dreaming import candidates as candmod

            sdb = candmod.default_state_db_path()
            if sdb is None:
                return (False, "state.db not resolvable")
            if not sdb.exists():
                return (False, f"state.db missing ({sdb})")
            return (True, f"state.db at {sdb}")
        except Exception as exc:  # noqa: BLE001
            return (False, f"{type(exc).__name__}: {exc}")

    def trigger(self, *, force: bool = False) -> TargetResult:
        try:
            import asyncio

            from plugins.dreaming.runner import run_dream_cycle

            summary = asyncio.run(run_dream_cycle(force=force))
            counts = summary.counts()
            promoted = [r.candidate.raw_text for r in summary.promoted]
            return TargetResult(
                name=self.name,
                status="ok",
                detail=(f"promoted={counts['promoted']} updated={counts['updated']} "
                        f"held={counts['held']} dropped={counts['dropped']} "
                        f"evaluated={counts['evaluated']}"),
                data={"counts": counts, "promoted": promoted},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream_orchestrator: local trigger failed: %s", exc)
            return TargetResult(self.name, "error", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# HONCHO — REST schedule_dream
# ---------------------------------------------------------------------------
def _honcho_config():
    """Resolve (base_url, workspace, peer, api_key) from the honcho plugin config."""
    from plugins.memory.honcho.client import HonchoClientConfig

    cfg = HonchoClientConfig.from_global_config()
    base_url = (cfg.base_url or "").rstrip("/")
    # The Honcho REST surface lives under /v3/workspaces/<ws>/...; strip a
    # trailing version segment the user may have baked into base_url so we never
    # produce /v3/v3/... (mirrors the honcho client's own normalisation).
    base_url = re.sub(r"/v\d+/*$", "", base_url).rstrip("/")
    peer = getattr(cfg, "ai_peer", None) or cfg.workspace_id
    return base_url, cfg.workspace_id, peer, cfg.api_key, cfg.enabled


class HonchoDreamTarget(DreamTarget):
    name = "honcho"

    def health(self) -> tuple[bool, str]:
        try:
            import httpx

            base_url, ws, _peer, _key, enabled = _honcho_config()
            if not base_url:
                return (False, "no honcho base_url configured")
            if not enabled:
                return (False, "honcho disabled in config")
            # A cheap liveness probe — the OpenAPI doc is always served.
            r = httpx.get(f"{base_url}/openapi.json", timeout=_HEALTH_TIMEOUT)
            if r.status_code < 500:
                return (True, f"{base_url} (workspace={ws})")
            return (False, f"server {r.status_code} at {base_url}")
        except Exception as exc:  # noqa: BLE001
            return (False, f"{type(exc).__name__}: {exc}")

    def trigger(self, *, force: bool = False) -> TargetResult:
        try:
            import httpx

            base_url, ws, peer, api_key, enabled = _honcho_config()
            if not base_url or not enabled:
                return TargetResult(self.name, "skipped",
                                    "honcho not configured/enabled")
            url = f"{base_url}/v3/workspaces/{ws}/schedule_dream"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            body = {"observer": peer, "dream_type": "omni"}
            r = httpx.post(url, json=body, headers=headers, timeout=_HTTP_TIMEOUT)
            if r.status_code in (200, 202, 204):
                return TargetResult(
                    name=self.name, status="ok",
                    detail=f"schedule_dream accepted (HTTP {r.status_code})",
                    data={"http_status": r.status_code, "observer": peer,
                          "workspace": ws, "dream_type": "omni"},
                )
            return TargetResult(
                name=self.name, status="error",
                detail=f"schedule_dream HTTP {r.status_code}: {r.text[:200]}",
                data={"http_status": r.status_code},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream_orchestrator: honcho trigger failed: %s", exc)
            return TargetResult(self.name, "error", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# GBRAIN — running server's HTTP MCP (submit_job name="autopilot-cycle")
# ---------------------------------------------------------------------------
_GBRAIN_URL = "http://127.0.0.1:3131/mcp"
# The gbrain dream/consolidation cycle is the "autopilot-cycle" built-in job
# type (confirmed via tools/list on the running server). We do NOT shell out
# `gbrain dream` — the CLI would deadlock on the single-writer PGLite lock the
# serve process holds. We submit the job over the server's own HTTP MCP instead.
_GBRAIN_DREAM_JOB = "autopilot-cycle"


def _gbrain_token() -> str:
    """Read GBRAIN_MCP_TOKEN from the environment or ~/.hermes/.env."""
    tok = os.environ.get("GBRAIN_MCP_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from hermes_cli.config import load_env

        return (load_env().get("GBRAIN_MCP_TOKEN") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _gbrain_rpc(method: str, params: dict, *, token: str, timeout: float) -> dict:
    """One JSON-RPC call to the gbrain HTTP MCP. Parses SSE-framed responses."""
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = httpx.post(_GBRAIN_URL, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    text = r.text
    # The server replies as SSE ("event: message\ndata: {...}") OR plain JSON.
    m = re.search(r"data:\s*(\{.*\})", text, re.DOTALL)
    raw = m.group(1) if m else text
    return json.loads(raw)


class GBrainDreamTarget(DreamTarget):
    name = "gbrain"

    def health(self) -> tuple[bool, str]:
        token = _gbrain_token()
        if not token:
            return (False, "GBRAIN_MCP_TOKEN not set")
        try:
            obj = _gbrain_rpc("tools/list", {}, token=token, timeout=_HEALTH_TIMEOUT)
            tools = {t.get("name") for t in obj.get("result", {}).get("tools", [])}
            if "submit_job" not in tools:
                return (False, "server reachable but submit_job tool absent")
            return (True, f"{_GBRAIN_URL} ({len(tools)} tools)")
        except Exception as exc:  # noqa: BLE001
            return (False, f"{type(exc).__name__}: {exc}")

    def trigger(self, *, force: bool = False) -> TargetResult:
        token = _gbrain_token()
        if not token:
            return TargetResult(self.name, "skipped", "GBRAIN_MCP_TOKEN not set")
        try:
            obj = _gbrain_rpc(
                "tools/call",
                {"name": "submit_job",
                 "arguments": {"name": _GBRAIN_DREAM_JOB, "data": {}}},
                token=token, timeout=_HTTP_TIMEOUT,
            )
            if "error" in obj:
                return TargetResult(self.name, "error",
                                    f"submit_job error: {obj['error']}")
            job = _parse_gbrain_job(obj)
            job_id = job.get("id")
            status = job.get("status")
            # A `gbrain jobs work` worker (Postgres engine) usually drains the
            # cycle within seconds — poll for the REAL outcome rather than
            # assuming it never ran. If nothing is draining the queue it stays
            # "waiting" and we say so honestly.
            final = status
            if job_id is not None and status in ("waiting", "running", "queued", None):
                import time as _t

                for _ in range(15):  # ~15s budget
                    _t.sleep(1.0)
                    js = self.job_status(job_id)
                    cur = js.get("status") if js else None
                    if cur:
                        final = cur
                        if cur in ("completed", "failed", "error", "cancelled"):
                            break
            detail = f"submit_job {_GBRAIN_DREAM_JOB} -> job {job_id} ({final})"
            note = ""
            if final in ("waiting", "running", "queued"):
                note = (" — still queued; no worker is draining the queue "
                        "(start the gbrain-worker daemon to execute dreams)")
            elif final in ("failed", "error"):
                note = " — job did not complete; check the gbrain-worker logs"
            return TargetResult(
                name=self.name,
                status=("error" if final in ("failed", "error") else "ok"),
                detail=detail + note,
                data={"job_id": job_id, "job_status": final,
                      "job_name": _GBRAIN_DREAM_JOB},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dream_orchestrator: gbrain trigger failed: %s", exc)
            return TargetResult(self.name, "error", f"{type(exc).__name__}: {exc}")

    def job_status(self, job_id: Any) -> Optional[dict]:
        """Best-effort poll of a submitted job's state (for status reporting)."""
        token = _gbrain_token()
        if not token or job_id is None:
            return None
        try:
            obj = _gbrain_rpc("tools/call",
                              {"name": "get_job", "arguments": {"id": job_id}},
                              token=token, timeout=_HEALTH_TIMEOUT)
            return _parse_gbrain_job(obj)
        except Exception:  # noqa: BLE001
            return None


def _parse_gbrain_job(obj: dict) -> dict:
    """Pull the job dict out of an MCP tools/call result envelope."""
    result = obj.get("result", {})
    content = result.get("content", [])
    if content and isinstance(content, list):
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return {"raw": text}
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def build_targets(toggles: dict[str, bool]) -> list[DreamTarget]:
    """Instantiate the enabled targets in topology order (honcho, gbrain, local).

    Order matters for Phase 2: cross-feed flows strictly one-way
    (honcho -> gbrain -> local), so upstream dreamers run before the local
    importer pulls their fresh outputs.
    """
    ordered: list[tuple[str, type[DreamTarget]]] = [
        ("honcho", HonchoDreamTarget),
        ("gbrain", GBrainDreamTarget),
        ("local", LocalDreamTarget),
    ]
    out: list[DreamTarget] = []
    for key, cls in ordered:
        if toggles.get(key, True):
            out.append(cls())
    return out
