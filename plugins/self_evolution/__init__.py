"""self_evolution — the unified flywheel: fire SENSE→DREAM→ENRICH→EVOLVE as one flow.

This plugin is the conductor that actually CLOSES the self-evolution loop in running code.
It does NOT add a core tool. It exposes:

* ``/self-evolve`` slash command (status / run / plan)
* ``hermes self-evolve {status,run,plan}`` terminal command
* an opt-in self-scheduling cron job (default OFF) — the nightly "wake up smarter" trigger.

Each organ stays independent + fail-soft; this just sequences them and hands the dream's
promoted facts to the playbook synthesizer. See :mod:`plugins.self_evolution.cycle`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import cycle

logger = logging.getLogger("hermes.plugins.self_evolution")

_SLASH_HELP = """Self-evolution — run the whole loop (outcomes→dream→cross-engine→playbook).
  /self-evolve            show status (what's enabled, last run)
  /self-evolve run        run one full cycle now
  /self-evolve run force  bypass the dreaming debounce
  /self-evolve plan       dry-run: show what WOULD happen, write nothing"""


def run(*, force: bool = False, plan: bool = False) -> dict:
    """Synchronous entrypoint (CLI / cron) — drives the async cycle.

    Loop-safe: the scheduled cron fires inside the gateway's running event loop,
    where ``asyncio.run`` raises "cannot be called from a running event loop"
    (this is why the nightly cycle silently never ran). Fall back to a worker
    thread with its own loop in that case.
    """
    coro = cycle.run_cycle(force=force, plan=plan)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


# --- status -----------------------------------------------------------------
def _status() -> dict:
    out: dict = {"organs": {}}
    for name, mod, flag in (
        ("outcomes", "plugins.outcomes.config", "load_outcomes_config"),
        ("playbook_synthesizer", "plugins.playbook_synthesizer.config", "load_playbook_config"),
    ):
        try:
            m = __import__(mod, fromlist=[flag])
            cfg = getattr(m, flag)()
            out["organs"][name] = {"enabled": getattr(cfg, "enabled", None)}
        except Exception as exc:  # noqa: BLE001
            out["organs"][name] = {"error": f"{type(exc).__name__}"}
    try:
        from plugins.dreaming.config import load_dreaming_config

        dc = load_dreaming_config()
        out["organs"]["dreaming"] = {"enabled": dc.enabled, "review_mode": dc.review_mode}
    except Exception as exc:  # noqa: BLE001
        out["organs"]["dreaming"] = {"error": f"{type(exc).__name__}"}
    try:
        from .config import load_self_evolution_config

        sc = load_self_evolution_config()
        out["enabled"] = sc.enabled
        out["schedule"] = sc.schedule or "(off)"
    except Exception:  # noqa: BLE001
        out["enabled"] = False
        out["schedule"] = "(off)"
    return out


def _render_status(st: dict) -> str:
    lines = ["Self-evolution loop",
             f"  scheduled: {st.get('enabled')}   schedule: {st.get('schedule')}",
             "  organs:"]
    for name, o in st.get("organs", {}).items():
        if "error" in o:
            lines.append(f"    ? {name:22s} {o['error']}")
        else:
            extra = "  review_mode" if o.get("review_mode") else ""
            lines.append(f"    {'on ' if o.get('enabled') else 'off'} {name:22s}{extra}")
    return "\n".join(lines)


# --- slash + CLI ------------------------------------------------------------
def _handle_slash(raw_args: str):
    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"
    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP
    if sub == "plan":
        return cycle.render(run(plan=True))
    if sub == "run":
        force = len(parts) > 1 and parts[1].lower() in ("force", "-f", "--force")
        try:
            return cycle.render(run(force=force))
        except Exception as exc:  # noqa: BLE001
            return f"self-evolve run failed: {type(exc).__name__}: {exc}"
    if sub == "schedule":
        return schedule_background_job()
    return _render_status(_status())


def _cli_setup(subparser) -> None:
    sub = subparser.add_subparsers(dest="se_cmd")
    sub.add_parser("status", help="show loop status").set_defaults(func=lambda a: _cli_status())
    p_run = sub.add_parser("run", help="run one full cycle now")
    p_run.add_argument("--force", action="store_true", help="bypass dreaming debounce")
    p_run.set_defaults(func=lambda a: _cli_run(force=getattr(a, "force", False)))
    sub.add_parser("plan", help="dry-run").set_defaults(func=lambda a: _cli_plan())
    p_sched = sub.add_parser("schedule", help="register the nightly cron from config")
    p_sched.add_argument("--cron", default=None, help="override the cron expression")
    p_sched.set_defaults(func=lambda a: _cli_schedule(getattr(a, "cron", None)))
    subparser.set_defaults(func=lambda a: _cli_status())


def _cli_status() -> int:
    print(_render_status(_status()))
    return 0


def _cli_plan() -> int:
    print(cycle.render(run(plan=True)))
    return 0


def _cli_run(*, force: bool) -> int:
    print(cycle.render(run(force=force)))
    return 0


def _cli_schedule(cron: str | None) -> int:
    print(schedule_background_job(schedule=cron))
    return 0


def _cli_handle(args) -> int:
    func = getattr(args, "func", None)
    return func(args) if func else _cli_status()


# --- background self-scheduling (mirrors dream_orchestrator) -----------------
def _home_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "self_evolution"
    except Exception:  # noqa: BLE001
        import os

        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "self_evolution"


def schedule_background_job(*, schedule: str | None = None) -> str:
    from .config import load_self_evolution_config

    cfg = load_self_evolution_config()
    sched = (schedule or cfg.schedule or "").strip()
    if not sched:
        return ("No schedule set. Set `self_evolution.schedule` in config.yaml "
                "(e.g. \"every 6 hours\") then re-run, or invoke `hermes self-evolve run`.")
    try:
        from cron.jobs import create_job

        # Idempotent: drop any prior self-evolve-tick job so re-registering (e.g. after a
        # schedule change) replaces rather than duplicates it.
        try:
            from cron.jobs import list_jobs, remove_job

            for j in list_jobs(include_disabled=True):
                if j.get("name") == "self-evolve-tick":
                    remove_job(j.get("id") or "self-evolve-tick")
        except Exception as exc:  # noqa: BLE001 — best-effort dedup
            logger.debug("self_evolution: could not dedup prior cron jobs (%s)", exc)

        home = _home_dir()
        home.mkdir(parents=True, exist_ok=True)
        script = home / "self_evolve_tick.py"
        # The tick prints a one-line summary so the cron's local delivery shows what
        # the nightly cycle did; failures are surfaced (not silently swallowed) but
        # never raise out of the tick.
        script.write_text(
            "from plugins.self_evolution import run\n"
            "from plugins.self_evolution import cycle as _cycle\n"
            "try:\n"
            "    print(_cycle.render(run(force=False)))\n"
            "except Exception as _exc:\n"
            "    print(f'self-evolution tick failed: {type(_exc).__name__}: {_exc}')\n",
            encoding="utf-8",
        )
        job = create_job(prompt=None, schedule=sched, name="self-evolve-tick",
                         script=str(script), no_agent=True)
        return f"Scheduled self-evolution '{sched}' (job {job.get('id', '?')})."
    except Exception as exc:  # noqa: BLE001
        return (f"Could not register cron job ({type(exc).__name__}: {exc}). "
                f"Self-evolution still runs on `hermes self-evolve run`.")


def register(ctx) -> None:
    ctx.register_command(
        "self-evolve",
        _handle_slash,
        description="Run the whole self-evolution loop (outcomes→dream→cross-engine→playbook)",
        args_hint="[status|run|plan]",
    )
    try:
        ctx.register_cli_command(
            "self-evolve",
            help="Self-evolution loop: status, run, plan",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Fire the unified self-evolution flywheel",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("self_evolution: CLI registration failed (%s)", exc)
    logger.debug("self_evolution plugin registered")
