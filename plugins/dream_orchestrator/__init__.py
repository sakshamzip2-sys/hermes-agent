"""Unified dreaming — connect v2's three separate dreamers behind one command.

v2 ships three independent "dreamers" that never talk to each other:

* **local**  — ``plugins.dreaming`` consolidates session history into MEMORY.md.
* **honcho** — the Honcho memory server runs its own ``schedule_dream`` cycle.
* **gbrain** — the ``gbrain serve --http`` server runs an ``autopilot-cycle`` job.

This plugin is the conductor. It does NOT add a core tool. It exposes:

* ``/dream-all`` slash command (CLI + gateway sessions)
* ``hermes dream-all {status,run,plan}`` terminal command
* an opt-in self-scheduling cron job (default OFF) that runs the cycle in the
  background, mirroring ``plugins.proactivity``'s launcher+cron pattern.

PHASE 1 — orchestrate + report: each dreamer is driven through a common
:class:`~plugins.dream_orchestrator.targets.DreamTarget` adapter with a health
probe; a down target is SKIPPED cleanly. Runs are idempotent and serialised via a
sqlite ledger + global lock (``$HERMES_HOME/dreaming/orchestrator.db``).

PHASE 2 — one-way cross-feed (default dry-run): pull NEW high-confidence,
provenance-bearing upstream outputs (Honcho conclusions, GBrain facts) into
MEMORY.md candidates, run them through the EXISTING local diversity gate, and tag
each with provenance so the local dreamer excludes them next time (no recursion).
See :mod:`plugins.dream_orchestrator.importer`.

PROTECTED INVARIANTS:
* Everything that writes state defaults OFF / dry-run (``enabled: false``,
  ``schedule: ""``, ``cross_feed.dry_run: true``). The command works on-demand.
* Cross-feed is strictly one-way: Honcho -> GBrain -> local. Nothing flows up.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .targets import build_targets

logger = logging.getLogger("hermes.plugins.dream_orchestrator")

_SLASH_HELP = """Unified dreaming — runs all three dreamers. Subcommands:
  /dream-all              combined status of local + honcho + gbrain
  /dream-all run          run every enabled dreamer now (+ cross-feed if on)
  /dream-all run force    bypass the local debounce interval
  /dream-all plan         dry-run: show what WOULD happen, write nothing"""


# ---------------------------------------------------------------------------
# Profile-scoped paths
# ---------------------------------------------------------------------------
def _home_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "dreaming"
    except Exception:  # noqa: BLE001
        import os

        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "dreaming"


def _store():
    from .store import OrchestratorStore

    return OrchestratorStore(_home_dir() / "orchestrator.db")


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------
def run_all(*, force: bool = False, plan: bool = False, config=None) -> dict:
    """Run (or plan) every enabled dreamer once. Returns a combined summary dict.

    ``plan=True`` is a dry-run: it probes health and reports what WOULD run,
    writing nothing (no triggers, no cross-feed, no ledger row).
    """
    from .config import load_orchestrator_config

    cfg = config or load_orchestrator_config()
    targets = build_targets(cfg.targets)
    store = _store()
    run_id = store.new_run_id()
    started_at = time.time()

    summary: dict = {
        "dream_run_id": run_id,
        "plan": plan,
        "force": force,
        "started_at": started_at,
        "targets": [],
        "cross_feed": None,
        "locked_out": False,
    }

    # --- PLAN: probe-only dry run -----------------------------------------
    if plan:
        for t in targets:
            ok, detail = t.health()
            summary["targets"].append({
                "name": t.name,
                "status": "would_run" if ok else "would_skip",
                "detail": detail,
            })
        cf = cfg.cross_feed
        summary["cross_feed"] = {
            "enabled": cf.enabled,
            "dry_run": cf.dry_run,
            "note": ("would import upstream conclusions/facts (dry_run)"
                     if cf.enabled else "cross-feed disabled"),
        }
        summary["finished_at"] = time.time()
        return summary

    # --- RUN: take the global lock (idempotency / no concurrent runs) ------
    if not store.acquire_lock(run_id):
        last = store.last_run()
        summary["locked_out"] = True
        summary["finished_at"] = time.time()
        summary["detail"] = "another dream-all run is in progress"
        if last:
            summary["last_run"] = last
        return summary

    try:
        for t in targets:
            ok, detail = t.health()
            if not ok:
                summary["targets"].append({
                    "name": t.name, "status": "skipped", "detail": detail, "data": {},
                })
                continue
            result = t.trigger(force=force)
            summary["targets"].append(result.to_dict())

        # --- PHASE 2: one-way cross-feed (after upstream dreamers ran) -----
        if cfg.cross_feed.enabled:
            try:
                from .importer import run_cross_feed

                cf_summary = run_cross_feed(cfg.cross_feed, store)
                summary["cross_feed"] = cf_summary.to_dict()
            except Exception as exc:  # noqa: BLE001 — cross-feed is best-effort
                logger.warning("dream_orchestrator: cross-feed failed: %s", exc)
                summary["cross_feed"] = {"error": f"{type(exc).__name__}: {exc}"}

        summary["finished_at"] = time.time()
        store.record_run(run_id, summary, started_at=started_at,
                         finished_at=summary["finished_at"])
        return summary
    finally:
        store.release_lock(run_id)


def status() -> dict:
    """Combined live status: each dreamer's health + the last orchestrated run."""
    from .config import load_orchestrator_config

    cfg = load_orchestrator_config()
    targets = build_targets(cfg.targets)
    out: dict = {
        "enabled": cfg.enabled,
        "schedule": cfg.schedule or "(off)",
        "cross_feed": {"enabled": cfg.cross_feed.enabled,
                       "dry_run": cfg.cross_feed.dry_run},
        "targets": [],
        "last_run": None,
    }
    for t in targets:
        ok, detail = t.health()
        entry = {"name": t.name, "healthy": ok, "detail": detail}
        # Enrich local with its own status (last run + counts).
        if t.name == "local":
            entry.update(_local_status())
        out["targets"].append(entry)
    out["last_run"] = _store().last_run()
    return out


def _local_status() -> dict:
    try:
        from plugins.dreaming.config import load_dreaming_config
        from plugins.dreaming.runner import _store_path
        from plugins.dreaming.store import DreamStore

        cfg = load_dreaming_config()
        store = DreamStore(_store_path())
        last = store.last_run_ts()
        last_str = "never"
        if last:
            import datetime as _dt

            last_str = _dt.datetime.fromtimestamp(last).isoformat(timespec="seconds")
        runs = store.recent_runs(limit=1)
        return {
            "local_enabled": cfg.enabled,
            "local_last_run": last_str,
            "local_last_counts": runs[0] if runs else {},
        }
    except Exception as exc:  # noqa: BLE001
        return {"local_error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Human-readable rendering (shared by slash + CLI)
# ---------------------------------------------------------------------------
def render_run(summary: dict) -> str:
    if summary.get("locked_out"):
        return "dream-all: another run is in progress — skipped."
    head = "Unified dream " + ("plan (dry-run)" if summary.get("plan") else "run")
    lines = [f"{head}  [{summary.get('dream_run_id', '?')}]"]
    for t in summary.get("targets", []):
        mark = {"ok": "✓", "would_run": "·", "skipped": "–",
                "would_skip": "–", "error": "✗", "disabled": "–"}.get(t["status"], "?")
        lines.append(f"  {mark} {t['name']:7s} [{t['status']}] {t.get('detail', '')}")
    cf = summary.get("cross_feed")
    if cf:
        if "error" in cf:
            lines.append(f"  cross-feed: error: {cf['error']}")
        elif "note" in cf:
            lines.append(f"  cross-feed: {cf['note']}")
        else:
            mode = "DRY-RUN" if cf.get("dry_run") else "LIVE"
            lines.append(
                f"  cross-feed [{mode}]: previewed={len(cf.get('previewed', []))} "
                f"promoted={len(cf.get('promoted', []))} "
                f"skipped={cf.get('skipped_existing', 0)} "
                f"dropped_dup={cf.get('dropped_diversity', 0)}"
            )
            for line in cf.get("previewed", [])[:5]:
                lines.append(f"      ~ {line}")
    return "\n".join(lines)


def render_status(st: dict) -> str:
    lines = [
        "Unified dreaming (orchestrator)",
        f"  enabled: {st['enabled']}   schedule: {st['schedule']}   "
        f"cross-feed: {st['cross_feed']['enabled']} "
        f"(dry_run={st['cross_feed']['dry_run']})",
        "  dreamers:",
    ]
    for t in st["targets"]:
        mark = "✓" if t["healthy"] else "✗"
        lines.append(f"    {mark} {t['name']:7s} {t['detail']}")
        if t["name"] == "local" and t.get("local_last_run"):
            counts = t.get("local_last_counts", {})
            c = (f"promoted={counts.get('promoted', 0)} held={counts.get('held', 0)} "
                 f"dropped={counts.get('dropped', 0)}") if counts else ""
            lines.append(f"        last local run: {t['local_last_run']}  {c}")
    last = st.get("last_run")
    if last:
        import datetime as _dt

        when = _dt.datetime.fromtimestamp(last["started_at"]).isoformat(timespec="seconds")
        n_ok = sum(1 for x in last["summary"].get("targets", []) if x["status"] == "ok")
        lines.append(f"  last dream-all: {when}  ({n_ok} dreamer(s) ran)  "
                     f"[{last['dream_run_id']}]")
    else:
        lines.append("  last dream-all: never")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Background self-scheduling (mirrors plugins.proactivity)
# ---------------------------------------------------------------------------
def _launcher_path() -> Path:
    """Write (idempotently) the tiny launcher the cron job runs; return its path."""
    home = _home_dir()
    home.mkdir(parents=True, exist_ok=True)
    script = home / "dream_all_tick.py"
    body = (
        "# Auto-generated unified-dreaming tick. Runs one orchestrated cycle.\n"
        "try:\n"
        "    from plugins.dream_orchestrator import run_all\n"
        "    run_all(force=False)\n"
        "except Exception:\n"
        "    pass\n"
    )
    try:
        if not script.exists() or script.read_text(encoding="utf-8") != body:
            script.write_text(body, encoding="utf-8")
    except OSError:
        pass
    return script


def schedule_background_job(*, schedule: str | None = None) -> str:
    """Register a recurring cron job that runs the unified cycle. Best-effort."""
    from .config import load_orchestrator_config

    cfg = load_orchestrator_config()
    sched = (schedule or cfg.schedule or "").strip()
    if not sched:
        return ("No schedule set. Set `dream_orchestrator.schedule` in config.yaml "
                "(e.g. \"every 6 hours\") and re-run, or invoke `hermes dream-all run`.")
    try:
        from cron.jobs import create_job

        script = _launcher_path()
        job = create_job(
            prompt=None,
            schedule=sched,
            name="dream-all-tick",
            script=str(script),
            no_agent=True,
        )
        return f"Scheduled unified dreaming '{sched}' (job {job.get('id', '?')})."
    except Exception as exc:  # noqa: BLE001
        return (f"Could not register the cron job ({type(exc).__name__}: {exc}). "
                f"Unified dreaming still runs whenever you invoke `hermes dream-all run`.")


# ---------------------------------------------------------------------------
# Slash + CLI handlers
# ---------------------------------------------------------------------------
def _handle_slash(raw_args: str):
    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"

    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP
    if sub == "plan":
        return render_run(run_all(plan=True))
    if sub == "run":
        force = len(parts) > 1 and parts[1].lower() in ("force", "--force", "-f")
        try:
            return render_run(run_all(force=force))
        except Exception as exc:  # noqa: BLE001
            return f"dream-all run failed: {type(exc).__name__}: {exc}"
    return render_status(status())


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def register(ctx) -> None:
    """Plugin entry point — wire the slash command and the CLI subcommand."""
    ctx.register_command(
        "dream-all",
        _handle_slash,
        description="Run all three dreamers (local + honcho + gbrain) and report",
        args_hint="[status|run|plan]",
    )
    try:
        ctx.register_cli_command(
            "dream-all",
            help="Unified dreaming: status, run, plan (local + honcho + gbrain)",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Orchestrate the local, Honcho, and GBrain dreamers as one",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("dream_orchestrator: could not register CLI command: %s", exc)

    logger.debug("dream_orchestrator plugin registered")
