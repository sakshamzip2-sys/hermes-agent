"""Per-chat specialized-agent (persona) bindings for messaging channels.

The web UI selects a specialized "gallery" agent per request via ``oc_agent_id``.
Messaging channels (Telegram, WhatsApp, WhatsApp Business, Discord, Slack, ...)
have no such per-request field, so this module lets a chat be *bound* to a
specialized agent: every subsequent turn in that chat runs as the bound agent
(its own SOUL/membrane, toolsets, model, and isolated ``agent-profiles/<slug>``
state) until the binding is cleared.

This module owns three concerns, all dependency-light and defensive (a failure
here must never break a chat turn — every public function swallows and logs):

1. Binding persistence  — ``get_bound_slug`` / ``set_bound_slug`` / ``clear_bound_slug``
   keyed by (platform, chat_id, thread_id), stored as atomic JSON under the
   gateway home so a restart keeps each chat on its chosen agent.
2. Catalog + validation — ``list_known_agents`` / ``agent_exists`` / ``is_valid_slug``
   over the shipped specialized agents (``profile_templates/`` ∪ ``.hermes/agents``
   manifests ∪ existing ``agent-profiles/`` dirs).
3. Config defaults      — ``default_slug_for_platform`` resolves an operator-set
   default agent for a platform from ``config.yaml`` (``personas:`` block).

Note on home resolution: binding reads/writes happen OUTSIDE the per-turn
``set_hermes_home_override`` window (in command handling and pre-run history
setup), so ``get_hermes_home()`` correctly returns the base home, not a
profile dir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Same slug grammar the api_server uses for ``oc_agent_id`` — lowercase, no path
# traversal, bounded length. Keeping these identical means a slug that binds on a
# channel is exactly a slug the web UI / api_server would accept.
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_MAX_SLUG_LEN = 64

# Obvious non-product profiles that exist only as test/scratch state dirs and
# must never appear in the user-facing ``/agent`` list.
_CATALOG_DENYLIST_SUBSTR = ("echobot", "-test", "test-", "overwrite")

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Slug validation + catalog
# ---------------------------------------------------------------------------

def is_valid_slug(slug: Any) -> bool:
    """True when *slug* matches the shared ``oc_agent_id`` grammar."""
    if not isinstance(slug, str):
        return False
    s = slug.strip().lower()
    return bool(s) and len(s) <= _MAX_SLUG_LEN and _SLUG_RE.fullmatch(s) is not None


def normalize_slug(slug: Any) -> Optional[str]:
    """Return the normalized slug, or None if it is not a valid slug."""
    if not is_valid_slug(slug):
        return None
    return slug.strip().lower()


def _repo_root() -> Path:
    # gateway/persona_bindings.py -> repo root is two parents up.
    return Path(__file__).resolve().parents[1]


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except Exception:  # pragma: no cover - defensive
        return Path(os.path.expanduser("~/.hermes"))


def _denied(slug: str) -> bool:
    return any(bad in slug for bad in _CATALOG_DENYLIST_SUBSTR)


def list_known_agents() -> List[str]:
    """Sorted list of shipped specialized-agent slugs offered on channels.

    The curated gallery == the ``profile_templates/`` dirs: these are the agents
    that ship a full membrane (SOUL/MEMORY/USER/...) and are meant to be chatted
    with directly (atlas, coder, deep-research, finance, knowledge-work, ledger,
    legal, sage, ...). Delegation-only sub-agents (e.g. the ``ce-*`` reviewers)
    live as bare manifests and are intentionally excluded from the menu — though
    ``agent_exists`` stays permissive, so a known slug can still be bound by name.
    """
    found: set[str] = set()
    try:
        tmpl = _repo_root() / "profile_templates"
        if tmpl.is_dir():
            for child in tmpl.iterdir():
                if child.is_dir() and is_valid_slug(child.name) and not _denied(child.name):
                    found.add(child.name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("persona catalog: profile_templates scan failed: %s", exc)
    return sorted(found)


def agent_exists(slug: Any) -> bool:
    """True when *slug* is a valid, resolvable specialized agent.

    Accepts a slug that has a ``profile_templates/<slug>`` template, an existing
    ``agent-profiles/<slug>`` dir, or a loaded agent manifest. Permissive on
    purpose: never block a real agent on a catalog technicality.
    """
    s = normalize_slug(slug)
    if not s:
        return False
    try:
        if (_repo_root() / "profile_templates" / s).is_dir():
            return True
    except Exception:
        pass
    try:
        if (_hermes_home() / "agent-profiles" / s).is_dir():
            return True
    except Exception:
        pass
    try:
        from tools.agent_defs import get_agent_definition
        if get_agent_definition(s) is not None:
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Binding persistence
# ---------------------------------------------------------------------------

def _store_path() -> Path:
    return _hermes_home() / "persona_bindings.json"


def binding_key(source: Any) -> Optional[str]:
    """Stable per-chat key: ``<platform>:<chat_id>[:t<thread_id>]``.

    A thread/topic (Discord thread, Telegram forum topic) binds independently of
    its parent channel so different topics in one server can run different agents.
    """
    if source is None:
        return None
    platform = getattr(source, "platform", None)
    platform = getattr(platform, "value", platform)  # Platform enum -> str
    chat_id = getattr(source, "chat_id", None)
    if not platform or not chat_id:
        return None
    key = f"{platform}:{chat_id}"
    thread_id = getattr(source, "thread_id", None)
    if thread_id:
        key = f"{key}:t{thread_id}"
    return key


def _read_all() -> Dict[str, str]:
    path = _store_path()
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        binds = data.get("bindings") if isinstance(data, dict) else None
        if not isinstance(binds, dict):
            return {}
        return {k: v for k, v in binds.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("persona bindings: read failed (%s); treating as empty", exc)
        return {}


def _write_all(binds: Dict[str, str]) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"bindings": binds}, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)  # atomic
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("persona bindings: write failed: %s", exc)


def get_bound_slug(source: Any) -> Optional[str]:
    """Return the slug this chat is bound to, or None. Never raises."""
    key = binding_key(source)
    if not key:
        return None
    with _lock:
        slug = _read_all().get(key)
    return slug if (slug and is_valid_slug(slug)) else None


def set_bound_slug(source: Any, slug: str) -> bool:
    """Bind this chat to *slug*. Returns True on success. Never raises."""
    key = binding_key(source)
    s = normalize_slug(slug)
    if not key or not s:
        return False
    with _lock:
        binds = _read_all()
        binds[key] = s
        _write_all(binds)
    logger.info("persona bindings: %s -> %s", key, s)
    return True


def clear_bound_slug(source: Any) -> Optional[str]:
    """Clear this chat's binding. Returns the previous slug or None. Never raises."""
    key = binding_key(source)
    if not key:
        return None
    with _lock:
        binds = _read_all()
        prev = binds.pop(key, None)
        if prev is not None:
            _write_all(binds)
    if prev:
        logger.info("persona bindings: cleared %s (was %s)", key, prev)
    return prev


# ---------------------------------------------------------------------------
# Operator-configured defaults (config.yaml ``personas`` block)
# ---------------------------------------------------------------------------

def default_slug_for_platform(platform: Any, config: Any) -> Optional[str]:
    """Resolve an operator default agent for *platform* from gateway config.

    Config shape (all optional)::

        personas:
          default: finance                 # applies to every platform
          defaults:
            telegram: deep-research        # per-platform override

    Returns a valid, existing slug or None. Explicit per-chat bindings always
    win over this default (resolved by the caller).
    """
    try:
        if not isinstance(config, dict):
            return None
        personas = config.get("personas")
        if not isinstance(personas, dict):
            return None
        plat = getattr(platform, "value", platform)
        per = personas.get("defaults")
        slug = None
        if isinstance(per, dict) and plat:
            slug = per.get(plat) or per.get(str(plat))
        if not slug:
            slug = personas.get("default")
        s = normalize_slug(slug)
        if s and agent_exists(s):
            return s
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("persona default resolve failed: %s", exc)
    return None
