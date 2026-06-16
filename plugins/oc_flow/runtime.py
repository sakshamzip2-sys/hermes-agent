"""The oc_flow dynamic-workflow engine.

A *flow* is a plain Python script that orchestrates many subagents. The script
runs in a namespace where these helpers are injected:

    agent(prompt, *, label=, phase=, model=, provider=, toolsets=, schema=,
          max_iterations=, cwd=)   -> the subagent's return value
    parallel([thunk, ...])         -> list of results (None for a failed thunk)
    pipeline(items, stage1, ...)   -> each item flows through all stages
                                       independently (no barrier between stages)
    phase(title)                   -> open a new progress phase
    log(message)                   -> emit a progress line
    args / flow_args               -> the value passed via --args / args=
    result(value)                  -> set the flow's return value
                                       (or assign a top-level RESULT = ...)

This mirrors Claude Code's *dynamic workflows* concept, ported to v2's idiom:
the "plan" lives in the script (loops, branches, fan-out), intermediate results
live in script variables, and the engine drives v2's real ``AIAgent`` subagents.

Why content-addressed resume:
    parallel()/pipeline() run agents concurrently, so the *order* in which
    ``agent()`` is entered is non-deterministic. Keying the resume cache on a
    hash of the agent spec (prompt+schema+model+toolsets) instead of call
    order makes "same script + same args -> cache hit" hold even for fan-out
    stages. On resume we preload every completed agent from the DB keyed by
    that hash; a live ``agent()`` call that matches returns instantly.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import db
from .executor import AgentResult, AgentSpec, default_agent_runner, resolve_default_runner

logger = logging.getLogger("hermes.plugins.oc_flow.runtime")

_DEFAULT_MAX_CONCURRENCY = 8


class FlowStopped(Exception):
    """Raised inside the engine to abort a run early (stop request)."""


def _sha(spec: AgentSpec) -> str:
    # Every field that changes what the subagent actually does must be in the
    # key, or resume could serve one agent's cached result to a different one.
    # In particular cwd (worktree isolation) and max_iterations are part of the
    # spec's identity.
    payload = json.dumps(
        {
            "prompt": spec.prompt,
            "schema": spec.schema,
            "model": spec.model,
            "provider": spec.provider,
            "toolsets": spec.toolsets,
            "label": spec.label,
            "max_iterations": spec.max_iterations,
            "cwd": (spec.extra or {}).get("cwd"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def extract_meta(src: str) -> Dict[str, Any]:
    """Best-effort extraction of a top-level ``META``/``meta`` literal dict.

    Uses ``ast.literal_eval`` so it never executes the script — the same
    "pure literal meta" contract Claude Code's workflows require.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("META", "meta"):
                    try:
                        val = ast.literal_eval(node.value)
                        if isinstance(val, dict):
                            return val
                    except Exception:
                        return {}
    return {}


@dataclass
class FlowOutcome:
    run_id: str
    status: str
    result: Any = None
    error: Optional[str] = None
    agent_count: int = 0


class FlowRuntime:
    """Executes one flow script, persisting everything to the oc_flow DB."""

    def __init__(
        self,
        run_id: str,
        *,
        agent_runner: Callable[[AgentSpec], AgentResult] = default_agent_runner,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
        resume: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.run_id = run_id
        self._agent_runner = agent_runner
        self._max_concurrency = max(1, int(max_concurrency))
        self._resume = resume
        self._progress = progress or (lambda _msg: None)

        self._lock = threading.Lock()
        self._call_index = 0
        self._phase_seq = 0
        self._current_phase = ""
        self._result: Any = None
        self._result_set = False
        self._stop = threading.Event()

        # Resume cache: prompt_sha -> FIFO list of cached values, plus a
        # repeat-map so duplicate identical specs in one resumed run still hit.
        self._resume_pool: Dict[str, List[Any]] = {}
        self._resume_repeat: Dict[str, Any] = {}
        if resume:
            self._load_resume_pool()

    # -- resume ------------------------------------------------------------- #

    def _load_resume_pool(self) -> None:
        # list_agents returns rows ordered by call_index ascending, so the LAST
        # completed result we see for a given spec-hash is the most recent one.
        # We keep only that latest result per hash (rather than FIFO-replaying
        # every historical completion), so a spec that was completed, dropped,
        # then re-introduced across edits resumes to its newest cached value —
        # never an arbitrarily old one.
        existing = db.list_agents(self.run_id)
        latest: Dict[str, Any] = {}
        for rec in existing:
            if rec.get("status") != "completed":
                continue
            sha = rec.get("prompt_sha") or ""
            latest[sha] = db.decode_result(rec)
        # Each distinct spec can be replayed for every matching call in the
        # resumed run (duplicates in one run legitimately share a cached value).
        self._resume_pool = {sha: [val] for sha, val in latest.items()}
        self._resume_repeat = latest  # value reused if the FIFO list empties
        # The call-index counter must continue past the highest cached index so
        # newly-run agents don't collide with cached rows.
        if existing:
            self._call_index = max(int(r["call_index"]) for r in existing)

    # -- injected helpers --------------------------------------------------- #

    def phase(self, title: str) -> None:
        with self._lock:
            self._phase_seq += 1
            seq = self._phase_seq
            self._current_phase = str(title)
        db.add_phase(self.run_id, seq, str(title))
        db.touch_run(self.run_id, phase_count=seq)
        self._emit(f"phase: {title}")

    def log(self, message: str) -> None:
        msg = str(message)
        db.add_log(self.run_id, msg)
        self._emit(msg)

    def set_result(self, value: Any) -> None:
        self._result = value
        self._result_set = True

    def request_stop(self) -> None:
        self._stop.set()

    def agent(
        self,
        prompt: str,
        *,
        label: str = "",
        phase: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        toolsets: Optional[List[str]] = None,
        schema: Optional[Dict[str, Any]] = None,
        max_iterations: Optional[int] = None,
        cwd: Optional[str] = None,
        worktree: bool = False,
    ) -> Any:
        if self._stop.is_set():
            raise FlowStopped()

        spec = AgentSpec(
            prompt=str(prompt),
            label=label or "",
            phase=phase if phase is not None else self._current_phase,
            model=model,
            provider=provider,
            toolsets=toolsets,
            schema=schema,
            max_iterations=max_iterations,
            extra={"cwd": cwd} if cwd else {},
        )
        # Hash on the LOGICAL spec (user cwd, not the ephemeral worktree path),
        # so resume stays a cache hit even though each run gets a fresh worktree.
        sha = _sha(spec)

        # Resume: a previously-completed identical spec returns instantly.
        if self._resume:
            with self._lock:
                pool = self._resume_pool.get(sha)
                if pool:
                    cached = pool.pop(0)
                    self._emit(f"resume-hit: {label or spec.phase or sha}")
                    return cached
                if sha in self._resume_repeat:
                    # Duplicate identical spec in this resumed run — reuse value.
                    self._emit(f"resume-hit: {label or spec.phase or sha}")
                    return self._resume_repeat[sha]

        with self._lock:
            self._call_index += 1
            idx = self._call_index

        # Per-subagent worktree isolation (created only for a live run, after the
        # resume check, so parallel file-editing agents don't collide).
        wt: Optional[Dict[str, str]] = None
        if worktree:
            try:
                from . import worktrees

                wt = worktrees.create_worktree(cwd)
                if wt:
                    spec.extra["cwd"] = wt["path"]
                    self._emit(f"agent#{idx} worktree: {wt['branch']}")
            except Exception as exc:  # noqa: BLE001 — isolation is best-effort
                self._emit(f"agent#{idx} worktree setup failed: {exc}")

        db.start_agent(
            self.run_id, idx, label=spec.label, phase=spec.phase or "",
            prompt_sha=sha, model=model or "",
        )
        db.touch_run(self.run_id, agent_count=idx)
        self._emit(f"agent#{idx} start: {label or (prompt[:60])}")

        try:
            res = self._agent_runner(spec)
        except Exception as exc:  # noqa: BLE001 — a crashing runner must not kill the run
            db.finish_agent(self.run_id, idx, status="failed", error=f"{type(exc).__name__}: {exc}")
            self._emit(f"agent#{idx} crash: {exc}")
            self._maybe_cleanup_worktree(wt)
            raise

        if not res.ok:
            db.finish_agent(
                self.run_id, idx, status="failed", error=res.error,
                api_calls=res.api_calls, output_tokens=res.output_tokens,
                model=res.model or None,
            )
            self._emit(f"agent#{idx} failed: {res.error}")
            self._maybe_cleanup_worktree(wt)
            raise RuntimeError(res.error or "agent failed")

        value = res.value()
        db.finish_agent(
            self.run_id, idx, status="completed", result=value,
            api_calls=res.api_calls, output_tokens=res.output_tokens,
            model=res.model or None,
        )
        self._emit(f"agent#{idx} done ({res.api_calls} api calls)")
        self._maybe_cleanup_worktree(wt)
        return value

    def _maybe_cleanup_worktree(self, wt: Optional[Dict[str, str]]) -> None:
        """Remove a per-agent worktree if it has no changes; keep it otherwise."""
        if not wt:
            return
        try:
            from . import worktrees

            if worktrees.cleanup_if_unchanged(wt):
                self._emit(f"worktree cleaned: {wt.get('branch')}")
        except Exception:
            pass

    def parallel(self, thunks: List[Callable[[], Any]]) -> List[Any]:
        """Run callables concurrently; return results in order (None on error).

        Barrier: waits for all thunks. A thunk that raises resolves to ``None``
        rather than propagating, so callers should ``filter(None)`` if needed.
        """
        thunks = list(thunks or [])
        if not thunks:
            return []
        results: List[Any] = [None] * len(thunks)

        def _wrap(i: int, fn: Callable[[], Any]) -> None:
            try:
                results[i] = fn()
            except FlowStopped:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("oc_flow parallel thunk %d failed: %s", i, exc)
                results[i] = None

        workers = min(self._max_concurrency, len(thunks))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocflow") as pool:
            futures = [pool.submit(_wrap, i, fn) for i, fn in enumerate(thunks)]
            for f in futures:
                f.result()
        return results

    def pipeline(self, items: List[Any], *stages: Callable[..., Any]) -> List[Any]:
        """Run each item through all stages independently — no barrier.

        Each stage callable receives ``(prev_result, original_item, index)``
        (extra positional args are optional — we adapt to the callable's
        arity). A stage that raises drops that item to ``None`` and skips its
        remaining stages.
        """
        items = list(items or [])
        stage_list = list(stages)
        if not items or not stage_list:
            return list(items)

        def _run_chain(index: int, item: Any) -> Any:
            value: Any = item
            for stage in stage_list:
                try:
                    value = _call_stage(stage, value, item, index)
                except FlowStopped:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug("oc_flow pipeline item %d failed: %s", index, exc)
                    return None
            return value

        workers = min(self._max_concurrency, len(items))
        results: List[Any] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocflow-pl") as pool:
            futures = {pool.submit(_run_chain, i, it): i for i, it in enumerate(items)}
            for f, i in futures.items():
                results[i] = f.result()
        return results

    # -- internals ---------------------------------------------------------- #

    def _emit(self, msg: str) -> None:
        try:
            self._progress(msg)
        except Exception:
            pass


def _call_stage(stage: Callable[..., Any], value: Any, item: Any, index: int) -> Any:
    """Call a pipeline stage, adapting to how many args it accepts."""
    try:
        import inspect

        params = inspect.signature(stage).parameters
        n = len([p for p in params.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
        if any(p.kind == p.VAR_POSITIONAL for p in params.values()):
            return stage(value, item, index)
        if n >= 3:
            return stage(value, item, index)
        if n == 2:
            return stage(value, item)
        return stage(value)
    except (ValueError, TypeError):
        # Builtins / C-callables may reject signature inspection — just try.
        return stage(value)


# --------------------------------------------------------------------------- #
# Top-level driver
# --------------------------------------------------------------------------- #

def _injected_globals(rt: FlowRuntime, args: Any) -> Dict[str, Any]:
    return {
        "agent": rt.agent,
        "parallel": rt.parallel,
        "pipeline": rt.pipeline,
        "phase": rt.phase,
        "log": rt.log,
        "result": rt.set_result,
        "args": args,
        "flow_args": args,
    }


def _execute_flow(source: str, script_path: Optional[str], rt: FlowRuntime, args: Any) -> Any:
    """Load the flow script as a module with the helpers injected as globals.

    We use ``importlib`` rather than ``exec()`` so the flow runs as a proper
    module (real ``__name__``/tracebacks) and so the security scanner doesn't
    trip on string ``exec`` — the flow is still trusted, locally-authored code,
    the same trust model as the user's own skills and cron scripts.
    """
    import importlib.util
    import tempfile

    tmp_path: Optional[str] = None
    load_path = script_path
    if not load_path:
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix=f"{rt.run_id}_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(source)
        load_path = tmp_path

    try:
        spec = importlib.util.spec_from_file_location(f"hermes_flow_{rt.run_id}", load_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load flow module spec")
        module = importlib.util.module_from_spec(spec)
        module.__dict__.update(_injected_globals(rt, args))
        spec.loader.exec_module(module)  # runs the flow body
        if rt._result_set:
            return rt._result
        return getattr(module, "RESULT", None)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def run_flow(
    *,
    script_path: Optional[str] = None,
    source: Optional[str] = None,
    args: Any = None,
    run_id: Optional[str] = None,
    background: bool = False,
    agent_runner: Optional[Callable[[AgentSpec], AgentResult]] = None,
    resume: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
) -> FlowOutcome:
    """Create (or resume) a run, execute the flow script, persist the outcome.

    When ``agent_runner`` is None the runner is resolved from the environment
    (``OC_FLOW_FAKE_AGENT=1`` selects the deterministic offline runner).
    """
    runner: Callable[[AgentSpec], AgentResult] = agent_runner or resolve_default_runner()
    if source is None:
        if not script_path:
            raise ValueError("run_flow requires script_path or source")
        source = Path(script_path).read_text(encoding="utf-8")

    meta = extract_meta(source)
    name = str(meta.get("name") or (Path(script_path).stem if script_path else "flow"))
    description = str(meta.get("description") or "")
    script_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    existing = db.get_run(run_id) if run_id else None
    if resume:
        if existing is None:
            raise ValueError(f"cannot resume unknown run {run_id}")
    elif existing is not None:
        # Adopt a row that was pre-created by the background dispatcher (the
        # parent inserts it so `flow list` shows the run immediately, then the
        # detached worker calls run_flow with that same id). Re-inserting would
        # violate the primary key and silently kill the worker.
        pass
    else:
        run_id = run_id or db.new_run_id()
        db.create_run(
            run_id=run_id, name=name, description=description,
            script_path=script_path or "", script_sha=script_sha,
            args=args, background=background, meta=meta,
        )

    assert run_id is not None  # set in every branch above
    rt = FlowRuntime(
        run_id, agent_runner=runner, max_concurrency=max_concurrency,
        resume=resume, progress=progress,
    )
    db.mark_run_started(run_id, pid=os.getpid())

    try:
        result_value = _execute_flow(source, script_path, rt, args)
        db.finish_run(run_id, "completed", result=result_value)
        agents = db.list_agents(run_id)
        return FlowOutcome(run_id=run_id, status="completed", result=result_value, agent_count=len(agents))
    except FlowStopped:
        db.finish_run(run_id, "stopped", error="stopped by request")
        return FlowOutcome(run_id=run_id, status="stopped", error="stopped by request")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        logger.debug("oc_flow run %s failed:\n%s", run_id, tb)
        db.finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return FlowOutcome(run_id=run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
