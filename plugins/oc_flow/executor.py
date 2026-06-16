"""Headless subagent execution for the oc_flow runtime.

The runtime's ``agent(prompt)`` helper ultimately calls :func:`run_agent_task`,
which builds a real v2 ``AIAgent`` with full config/credential resolution — the
same way ``hermes -z`` (oneshot) builds one — and runs a single conversation.

Two design choices make the rest of the system testable and safe:

* **Dependency injection.** The runtime never imports ``AIAgent`` directly; it
  takes an ``agent_runner`` callable (defaulting to :func:`run_agent_task`).
  Unit tests inject a fake runner so the engine — phases, parallelism,
  pipelines, resume cache — is exercised with zero token spend.
* **Per-task isolation.** Each call gets a unique ``task_id`` so terminal state
  and file-op tracking are pooled per subagent (the same isolation
  ``batch_runner`` relies on), and so Phase-4 worktree isolation can inject a
  ``cwd`` override against that ``task_id`` without touching the agent core.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("hermes.plugins.oc_flow.executor")


@dataclass
class AgentResult:
    """Outcome of one subagent run."""

    text: str = ""
    structured: Any = None          # populated when a schema was requested
    ok: bool = True
    error: Optional[str] = None
    api_calls: int = 0
    output_tokens: int = 0
    model: str = ""

    def value(self) -> Any:
        """The value the flow script receives back from ``agent(...)``."""
        return self.structured if self.structured is not None else self.text


@dataclass
class AgentSpec:
    """Everything needed to run one subagent (also what gets cached/hashed)."""

    prompt: str
    label: str = ""
    phase: str = ""
    model: Optional[str] = None
    provider: Optional[str] = None
    toolsets: Optional[List[str]] = None
    schema: Optional[Dict[str, Any]] = None
    max_iterations: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def _schema_instruction(schema: Dict[str, Any]) -> str:
    return (
        "\n\n---\nIMPORTANT: Respond with ONLY a single JSON value that conforms "
        "to this JSON Schema. No prose, no markdown fences, no explanation — "
        "just the JSON.\n\nJSON Schema:\n" + json.dumps(schema, indent=2)
    )


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _extract_json(text: str) -> Any:
    """Best-effort parse of a JSON value out of a model's text response."""
    if text is None:
        raise ValueError("empty response")
    stripped = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Fall back: grab the first balanced {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except Exception:
                continue
    raise ValueError("no JSON value found in response")


def _resolve_runtime(model: Optional[str], provider: Optional[str]):
    """Resolve (effective_model, runtime_provider_dict) like oneshot does."""
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider

    cfg = load_config()
    model_cfg = cfg.get("model") or {}
    if isinstance(model_cfg, str):
        cfg_model = model_cfg
    else:
        cfg_model = model_cfg.get("default") or model_cfg.get("model") or ""

    env_model = os.getenv("HERMES_INFERENCE_MODEL", "").strip()
    effective_model = (model or "").strip() or env_model or cfg_model
    effective_provider = (provider or "").strip() or None

    # When a model is explicitly requested without a provider, auto-detect the
    # provider that serves it (parity with `/model <name>`), same as oneshot.
    if effective_provider is None and (model or env_model):
        try:
            from hermes_cli.models import detect_provider_for_model

            cfg_provider = ""
            if isinstance(model_cfg, dict):
                cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
            current = cfg_provider or os.getenv("HERMES_INFERENCE_PROVIDER", "").strip().lower() or "auto"
            detected = detect_provider_for_model(effective_model, current)
            if detected:
                effective_provider, effective_model = detected
        except Exception as exc:  # noqa: BLE001
            logger.debug("oc_flow: provider auto-detect failed: %s", exc)

    runtime = resolve_runtime_provider(requested=effective_provider, target_model=effective_model or None)
    return effective_model, runtime, cfg


def _default_toolsets(cfg: Dict[str, Any]) -> Optional[List[str]]:
    try:
        from hermes_cli.tools_config import _get_platform_tools

        return sorted(_get_platform_tools(cfg, "cli"))
    except Exception:
        return None


def run_agent_task(spec: AgentSpec) -> AgentResult:
    """Build a headless ``AIAgent`` and run one conversation for ``spec``."""
    # Non-interactive: auto-approve tool/shell prompts (a prompt would hang).
    os.environ.setdefault("HERMES_YOLO_MODE", "1")
    os.environ.setdefault("HERMES_ACCEPT_HOOKS", "1")

    try:
        from run_agent import AIAgent
    except Exception as exc:  # noqa: BLE001
        return AgentResult(ok=False, error=f"AIAgent import failed: {exc}")

    try:
        effective_model, runtime, cfg = _resolve_runtime(spec.model, spec.provider)
    except Exception as exc:  # noqa: BLE001
        return AgentResult(ok=False, error=f"provider resolution failed: {exc}")

    toolsets = spec.toolsets if spec.toolsets is not None else _default_toolsets(cfg)

    prompt = spec.prompt
    if spec.schema:
        prompt = prompt + _schema_instruction(spec.schema)

    task_id = "flow-" + uuid.uuid4().hex[:10]

    # Apply any cwd / env overrides (Phase-4 worktree isolation wires in here).
    cwd = spec.extra.get("cwd")
    if cwd:
        try:
            from tools.terminal_tool import register_task_env_overrides

            register_task_env_overrides(task_id, {"cwd": cwd})
        except Exception as exc:  # noqa: BLE001
            logger.debug("oc_flow: cwd override failed: %s", exc)

    try:
        session_db = None
        try:
            from hermes_state import SessionDB

            session_db = SessionDB()
        except Exception:
            session_db = None

        agent_kwargs: Dict[str, Any] = {
            "api_key": runtime.get("api_key"),
            "base_url": runtime.get("base_url"),
            "provider": runtime.get("provider"),
            "api_mode": runtime.get("api_mode"),
            "model": effective_model,
            "enabled_toolsets": toolsets,
            "max_iterations": spec.max_iterations or _flow_max_iterations(cfg),
            "quiet_mode": True,
            "platform": "cli",
            "session_db": session_db,
            "credential_pool": runtime.get("credential_pool"),
            "skip_context_files": True,
            "skip_memory": True,
        }
        agent = AIAgent(**agent_kwargs)
        # Suppress any interactive/streaming display in headless mode (same
        # attributes oneshot.py sets on its agent).
        for _attr in ("suppress_status_output", "stream_delta_callback", "tool_gen_callback"):
            try:
                setattr(agent, _attr, True if _attr == "suppress_status_output" else None)
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        return AgentResult(ok=False, error=f"agent construction failed: {exc}")

    def _run_once(p: str) -> Dict[str, Any]:
        result = agent.run_conversation(p, task_id=task_id)
        return result or {}

    try:
        result = _run_once(prompt)
    except Exception as exc:  # noqa: BLE001
        return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}", model=effective_model)

    text = _final_text(result)
    api_calls = int(result.get("api_calls") or 0)
    out_tokens = _output_tokens(result)

    if not spec.schema:
        return AgentResult(text=text, ok=True, api_calls=api_calls, output_tokens=out_tokens, model=effective_model)

    # Structured output: parse, with one corrective retry.
    try:
        structured = _extract_json(text)
        return AgentResult(text=text, structured=structured, ok=True,
                           api_calls=api_calls, output_tokens=out_tokens, model=effective_model)
    except Exception:
        try:
            retry = _run_once(
                "Your previous response was not valid JSON. Respond again with ONLY "
                "the JSON value conforming to the schema — no prose, no fences."
            )
            rtext = _final_text(retry)
            structured = _extract_json(rtext)
            return AgentResult(text=rtext, structured=structured, ok=True,
                               api_calls=api_calls + int(retry.get("api_calls") or 0),
                               output_tokens=out_tokens + _output_tokens(retry), model=effective_model)
        except Exception as exc:  # noqa: BLE001
            return AgentResult(text=text, ok=False, error=f"structured-output parse failed: {exc}",
                               api_calls=api_calls, output_tokens=out_tokens, model=effective_model)


def _flow_max_iterations(cfg: Dict[str, Any]) -> int:
    flow_cfg = cfg.get("flow") if isinstance(cfg, dict) else None
    if isinstance(flow_cfg, dict):
        try:
            return int(flow_cfg.get("max_iterations") or 30)
        except Exception:
            return 30
    return 30


def _final_text(result: Dict[str, Any]) -> str:
    """Pull the last assistant text out of a run_conversation result dict."""
    if not isinstance(result, dict):
        return str(result or "")
    # `final_response` is the canonical key run_conversation sets (what
    # agent.chat() returns); prefer it, then other convenience fields, then
    # fall back to walking the messages for the last assistant text.
    for key in ("final_response", "final", "response", "content"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v
    messages = result.get("messages") or []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                joined = "".join(parts).strip()
                if joined:
                    return joined
    return ""


def _output_tokens(result: Dict[str, Any]) -> int:
    if not isinstance(result, dict):
        return 0
    for key in ("output_tokens", "completion_tokens"):
        v = result.get(key)
        if isinstance(v, int):
            return v
    usage = result.get("usage")
    if isinstance(usage, dict):
        v = usage.get("output_tokens") or usage.get("completion_tokens")
        if isinstance(v, int):
            return v
    return 0


def fake_agent_runner(spec: AgentSpec) -> AgentResult:
    """Deterministic, no-LLM runner for tests and offline smoke-checks.

    Activated by ``OC_FLOW_FAKE_AGENT=1`` so the entire CLI→runtime→DB path can
    be exercised without credentials or token spend. For a schema request it
    returns a minimal stub that satisfies the schema's declared shape; for a
    plain request it echoes a short marker derived from the prompt.
    """
    if spec.schema:
        return AgentResult(
            text="(fake structured output)",
            structured=_stub_for_schema(spec.schema),
            ok=True, api_calls=1, output_tokens=8, model="fake",
        )
    marker = (spec.label or spec.prompt or "").strip().splitlines()[0][:80] if (spec.label or spec.prompt) else ""
    return AgentResult(text=f"[fake] {marker}", ok=True, api_calls=1, output_tokens=8, model="fake")


def _stub_for_schema(schema: Dict[str, Any]) -> Any:
    """Produce a minimal value that conforms to a (subset of) JSON Schema."""
    t = schema.get("type")
    if t == "object":
        props = schema.get("properties") or {}
        required = schema.get("required") or list(props.keys())
        return {k: _stub_for_schema(props.get(k, {})) for k in required}
    if t == "array":
        return []
    if t == "string":
        return schema.get("description", "fake")[:40] or "fake"
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    return None


def resolve_default_runner() -> Callable[[AgentSpec], AgentResult]:
    """Pick the runner: the fake one when OC_FLOW_FAKE_AGENT is truthy."""
    val = os.getenv("OC_FLOW_FAKE_AGENT", "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return fake_agent_runner
    return run_agent_task


# The runtime imports this name; tests override it via the agent_runner param.
default_agent_runner: Callable[[AgentSpec], AgentResult] = run_agent_task
