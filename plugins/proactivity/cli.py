"""``hermes proactivity`` terminal subcommand.

Subcommands:
  status            show config + tracked events
  track <title>     track an event (``--in 2h`` for an upcoming one)
  enable / disable  flip proactivity.enabled in config.yaml (consent gate)
"""

from __future__ import annotations

import argparse


def setup(subparser: argparse.ArgumentParser) -> None:
    sub = subparser.add_subparsers(dest="proact_cmd")

    p_status = sub.add_parser("status", help="show proactivity config + tracked events")
    p_status.set_defaults(func=_cmd_status)

    p_track = sub.add_parser("track", help="track an event for a later check-in")
    p_track.add_argument("title", nargs="+", help="event title")
    p_track.add_argument("--in", dest="in_dur", default="", help="upcoming, e.g. 2h, 30m, 1d")
    p_track.set_defaults(func=_cmd_track)

    p_en = sub.add_parser("enable", help="enable proactive check-ins (consent)")
    p_en.set_defaults(func=_cmd_enable)
    p_dis = sub.add_parser("disable", help="disable proactive check-ins")
    p_dis.set_defaults(func=_cmd_disable)

    subparser.set_defaults(func=_cmd_status)


def handle(args: argparse.Namespace) -> int:
    func = getattr(args, "func", None)
    return func(args) if func else _cmd_status(args)


def _cmd_status(args: argparse.Namespace) -> int:
    from . import _handle_proactivity

    print(_handle_proactivity("status"))
    return 0


def _cmd_track(args: argparse.Namespace) -> int:
    from . import _handle_track

    title = " ".join(getattr(args, "title", []))
    in_dur = getattr(args, "in_dur", "")
    raw = f"{title} in {in_dur}" if in_dur else title
    print(_handle_track(raw))
    return 0


def _cmd_enable(args: argparse.Namespace) -> int:
    return _set_enabled(True)


def _cmd_disable(args: argparse.Namespace) -> int:
    return _set_enabled(False)


def _set_enabled(value: bool) -> int:
    """Persist proactivity.enabled to config.yaml (best effort)."""
    try:
        from hermes_cli.config import load_config, save_config

        cfg = load_config() or {}
        block = cfg.get("proactivity")
        if not isinstance(block, dict):
            block = {}
        block["enabled"] = value
        cfg["proactivity"] = block
        save_config(cfg)
        print(f"proactivity.enabled = {value}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            f"Could not write config.yaml ({type(exc).__name__}: {exc}).\n"
            f"Set `proactivity.enabled: {str(value).lower()}` manually."
        )
        return 1
