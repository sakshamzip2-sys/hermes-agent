"""Skill usage telemetry + provenance tracking for the Curator feature.

Tracks per-skill usage metadata in a sidecar JSON file (~/.hermes/skills/.usage.json)
keyed by skill name. Counters are bumped by the existing skill tools (skill_view,
skill_manage); the curator orchestrator reads the derived activity timestamp to
decide lifecycle transitions.

Design notes:
  - Sidecar, not frontmatter. Keeps operational telemetry out of user-authored
    SKILL.md content and avoids conflict pressure for bundled/hub skills.
  - Atomic writes via tempfile + os.replace (same pattern as .bundled_manifest).
  - All counter bumps are best-effort: failures log at DEBUG and return silently.
    A broken sidecar never breaks the underlying tool call.
  - Provenance filter: curator-managed skills are explicitly marked when
    created through skill_manage. Bundled / hub-installed skills stay
    off-limits, and manually authored skills are not inferred from location.

Lifecycle states:
    active    -> default
    stale     -> unused > stale_after_days (config)
    archived  -> unused > archive_after_days (config); moved to .archive/
    pinned    -> opt-out from auto transitions (boolean flag, orthogonal to state)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from hermes_constants import get_hermes_home
from agent.skill_utils import is_excluded_skill_path

logger = logging.getLogger(__name__)

# fcntl is Unix-only; on Windows use msvcrt for file locking.
msvcrt = None
try:
    import fcntl
except ImportError:  # pragma: no cover - platform-specific fallback
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass


STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_VALID_STATES = {STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED}

# Load-bearing bundled built-ins the curator must NEVER archive or consolidate,
# regardless of ``curator.prune_builtins``, pin state, or LLM judgment. These
# back advertised UX paths (e.g. ``plan`` powers the ``/plan`` slash-command
# flow and is referenced in tips/docs/fresh-profile seeding); silently archiving
# one turns its slash command into "Unknown command" with no signal to the user.
# Protection is by skill ``name`` (frontmatter ``name:``), matching the keys used
# throughout this module. Keep this list tiny and intentional — it is not a
# substitute for ``curator.prune_builtins: false``, which exempts ALL built-ins.
PROTECTED_BUILTIN_SKILLS: Set[str] = {
    "plan",
}


def is_protected_builtin(skill_name: str) -> bool:
    """Whether *skill_name* is a load-bearing built-in the curator never touches.

    Protected built-ins are exempt from archival and consolidation on every
    path: the automatic state-transition walk, the LLM consolidation pass (they
    are dropped from the candidate list), and direct ``archive_skill`` calls.
    """
    return skill_name in PROTECTED_BUILTIN_SKILLS


def _skills_dir() -> Path:
    return get_hermes_home() / "skills"


def _usage_file() -> Path:
    return _skills_dir() / ".usage.json"


@contextmanager
def _usage_file_lock():
    """Serialize .usage.json read-modify-write cycles across processes."""
    lock_path = _usage_file().with_suffix(".json.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None and msvcrt is None:
        yield
        return

    if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
        lock_path.write_text(" ", encoding="utf-8")

    fd = open(lock_path, "r+" if msvcrt else "a+", encoding="utf-8")
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
        elif msvcrt:
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                pass
        fd.close()


def _archive_dir() -> Path:
    return _skills_dir() / ".archive"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp defensively for activity comparisons."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def latest_activity_at(record: Dict[str, Any]) -> Optional[str]:
    """Return the newest actual activity timestamp for a usage record.

    "Activity" means a skill was used, viewed, or patched. Creation time is
    intentionally excluded so callers can still distinguish never-active skills;
    lifecycle code can fall back to ``created_at`` as its own anchor.
    """
    latest_dt: Optional[datetime] = None
    latest_raw: Optional[str] = None
    for key in ("last_used_at", "last_viewed_at", "last_patched_at"):
        raw = record.get(key)
        dt = _parse_iso_timestamp(raw)
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_raw = str(raw)
    return latest_raw


def activity_count(record: Dict[str, Any]) -> int:
    """Return the total observed activity count across use/view/patch events."""
    total = 0
    for key in ("use_count", "view_count", "patch_count"):
        try:
            total += int(record.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


# ---------------------------------------------------------------------------
# Provenance — which skills are agent-created (and thus eligible for curation)
# ---------------------------------------------------------------------------

def _read_bundled_manifest_names() -> Set[str]:
    """Return the set of skill names that were seeded from the bundled repo.

    Reads ~/.hermes/skills/.bundled_manifest (format: "name:hash" per line).
    Returns empty set if the file is missing or unreadable.
    """
    manifest = _skills_dir() / ".bundled_manifest"
    if not manifest.exists():
        return set()
    names: Set[str] = set()
    try:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            name = line.split(":", 1)[0].strip()
            if name:
                names.add(name)
    except OSError as e:
        logger.debug("Failed to read bundled manifest: %s", e)
    return names


def _read_hub_installed_names() -> Set[str]:
    """Return the set of skill names installed via the Skills Hub.

    Reads ~/.hermes/skills/.hub/lock.json (see tools/skills_hub.py :: HubLockFile).
    """
    lock_path = _skills_dir() / ".hub" / "lock.json"
    if not lock_path.exists():
        return set()
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            installed = data.get("installed") or {}
            if isinstance(installed, dict):
                names = {str(k) for k in installed.keys()}
                skills_dir = _skills_dir()
                for entry in installed.values():
                    if not isinstance(entry, dict):
                        continue
                    install_path = entry.get("install_path")
                    if not isinstance(install_path, str) or not install_path.strip():
                        continue
                    skill_dir = Path(install_path)
                    if not skill_dir.is_absolute():
                        skill_dir = skills_dir / skill_dir
                    try:
                        resolved = skill_dir.resolve()
                        resolved.relative_to(skills_dir.resolve())
                    except (OSError, ValueError):
                        continue
                    skill_md = resolved / "SKILL.md"
                    if skill_md.exists():
                        names.add(_read_skill_name(skill_md, fallback=resolved.name))
                return names
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read hub lock file: %s", e)
    return set()


def _prune_builtins_enabled() -> bool:
    """Whether bundled built-in skills are eligible for curator pruning.

    Reads ``curator.prune_builtins`` from config (default True). Lazy import
    keeps this module importable without the CLI config layer (e.g. in the
    update/sync context); on any failure we fall back to the default. The real
    safety against a mass-prune is the curator's seed-on-first-sight, not this
    flag — built-ins only archive after a fresh inactivity window.
    """
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        cur = cfg.get("curator") if isinstance(cfg, dict) else None
        if isinstance(cur, dict):
            return bool(cur.get("prune_builtins", True))
    except Exception as e:  # pragma: no cover — best-effort config read
        logger.debug("Failed to read curator.prune_builtins: %s", e)
    return True


def _suppressed_file() -> Path:
    return _skills_dir() / ".curator_suppressed"


def read_suppressed_names() -> Set[str]:
    """Built-in skills the curator pruned — the re-seeder must leave archived.

    One skill name per line in ``~/.hermes/skills/.curator_suppressed``. This is
    what makes pruning a built-in durable: without it, ``oc update`` would
    re-copy the bundled skill on the next sync.
    """
    path = _suppressed_file()
    if not path.exists():
        return set()
    names: Set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line)
    except OSError as e:
        logger.debug("Failed to read curator suppression list: %s", e)
    return names


def _write_suppressed_names(names: Set[str]) -> None:
    path = _suppressed_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = "\n".join(sorted(names)) + ("\n" if names else "")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".curator_suppressed_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to write curator suppression list: %s", e, exc_info=True)


def add_suppressed_name(skill_name: str) -> None:
    """Record that a built-in skill was pruned, so sync won't restore it."""
    if not skill_name:
        return
    names = read_suppressed_names()
    if skill_name not in names:
        names.add(skill_name)
        _write_suppressed_names(names)


def remove_suppressed_name(skill_name: str) -> None:
    """Clear a built-in's suppression entry (e.g. on restore)."""
    if not skill_name:
        return
    names = read_suppressed_names()
    if skill_name in names:
        names.discard(skill_name)
        _write_suppressed_names(names)


def list_agent_created_skill_names() -> List[str]:
    """Enumerate skills the curator may manage.

    Always includes agent-authored skills (those marked in ``.usage.json`` via
    ``skill_manage(action="create")``). When ``curator.prune_builtins`` is
    enabled, bundled built-in skills are ALSO included even though they have no
    agent-created usage record — their inactivity clock is anchored on first
    sight (see ``apply_automatic_transitions``). Hub-installed skills are never
    included; manually authored skills are not inferred from filesystem
    location.
    """
    base = _skills_dir()
    if not base.exists():
        return []
    hub = _read_hub_installed_names()
    bundled = _read_bundled_manifest_names()
    prune_builtins = _prune_builtins_enabled()
    usage = load_usage()

    names: List[str] = []
    # Top-level SKILL.md files (flat layout) AND nested category/skill/SKILL.md
    for skill_md in base.rglob("SKILL.md"):
        # Skip OpenComputer metadata, VCS, virtualenv/dependency, and cache dirs
        if is_excluded_skill_path(skill_md):
            continue
        try:
            skill_md.relative_to(base)
        except ValueError:
            continue
        name = _read_skill_name(skill_md, fallback=skill_md.parent.name)
        # Hub-installed skills are always off-limits.
        if name in hub:
            continue
        # Protected built-ins are never curation candidates — exempt from the
        # automatic transition walk AND the LLM consolidation pass.
        if is_protected_builtin(name):
            continue
        if name in bundled:
            # Built-ins are only candidates when pruning is enabled. They never
            # carry a curator-managed record, so the record gate is skipped.
            if not prune_builtins:
                continue
            names.append(name)
            continue
        # Agent-authored (or local-manual) skills must opt in via their record.
        if not _is_curator_managed_record(usage.get(name)):
            continue
        names.append(name)
    return sorted(set(names))


def list_archived_skill_names() -> List[str]:
    """Enumerate skills in ``~/.hermes/skills/.archive/``.

    Archive layout is flat (``.archive/<skill>/``) as set by ``archive_skill``,
    so the directory name is the skill name. Used by ``oc curator
    list-archived`` to help users pass a name to ``oc curator restore``.
    """
    archive_root = _archive_dir()
    if not archive_root.exists():
        return []
    return sorted({p.name for p in archive_root.iterdir() if p.is_dir()})


def _read_skill_name(skill_md: Path, fallback: str) -> str:
    """Parse the `name:` field from a SKILL.md YAML frontmatter."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return fallback
    in_frontmatter = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and stripped.startswith("name:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            if value:
                return value
    return fallback


def is_agent_created(skill_name: str) -> bool:
    """Whether *skill_name* is neither bundled nor hub-installed."""
    off_limits = _read_bundled_manifest_names() | _read_hub_installed_names()
    return skill_name not in off_limits


def is_hub_installed(skill_name: str) -> bool:
    """Whether *skill_name* was installed via the Skills Hub."""
    return skill_name in _read_hub_installed_names()


def is_bundled(skill_name: str) -> bool:
    """Whether *skill_name* was seeded from the bundled repo skills."""
    return skill_name in _read_bundled_manifest_names()


def is_curation_eligible(skill_name: str) -> bool:
    """Whether the curator may track/archive *skill_name*.

    Agent-created skills are always eligible. Bundled built-ins become eligible
    only when ``curator.prune_builtins`` is enabled. Hub-installed skills are
    NEVER eligible — they have an external upstream owner. Protected built-ins
    (``PROTECTED_BUILTIN_SKILLS``) are NEVER eligible regardless of any flag —
    they back load-bearing UX and must never be archived or consolidated.
    """
    if is_protected_builtin(skill_name):
        return False
    if is_hub_installed(skill_name):
        return False
    if is_bundled(skill_name):
        return _prune_builtins_enabled()
    return True


def _is_curator_managed_record(record: Any) -> bool:
    """Return True when a usage record opts a skill into curator management."""
    if not isinstance(record, dict):
        return False
    return record.get("created_by") == "agent" or record.get("agent_created") is True


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------

def _empty_record() -> Dict[str, Any]:
    return {
        "created_by": None,
        "use_count": 0,
        "view_count": 0,
        "last_used_at": None,
        "last_viewed_at": None,
        "patch_count": 0,
        "last_patched_at": None,
        "created_at": _now_iso(),
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
        # ------------------------------------------------------------------
        # Outcome-quality metrics (Part 2, Slice 3). These are a READ-ONLY
        # signal for the human reviewer and MUST NEVER drive an auto-prune /
        # auto-archive decision (the curator review prompt forbids using usage
        # as a quality signal, curator.py:391-394). All default to None until
        # the first sample lands, except ``sample_count`` which is 0 so rolling
        # means stay computable. Loading an old sidecar that predates these
        # fields backfills them with these defaults (see ``get_record`` /
        # ``agent_created_report`` ``setdefault`` backfill) — no crash, no
        # behavior change.
        # ------------------------------------------------------------------
        "success_rate": None,
        "avg_latency_ms": None,
        "cost_per_run": None,
        "user_rating": None,
        "sample_count": 0,
    }


def load_usage() -> Dict[str, Dict[str, Any]]:
    """Read the entire .usage.json map. Returns empty dict on missing/corrupt."""
    path = _usage_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    # Defensive: coerce any non-dict values to a fresh empty record
    clean: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            clean[str(k)] = v
    return clean


def save_usage(data: Dict[str, Dict[str, Any]]) -> None:
    """Write the usage map atomically. Best-effort — errors are logged, not raised."""
    path = _usage_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".usage_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to write %s: %s", path, e, exc_info=True)


def get_record(skill_name: str) -> Dict[str, Any]:
    """Return the record for *skill_name*, creating a fresh one if missing."""
    data = load_usage()
    rec = data.get(skill_name)
    if not isinstance(rec, dict):
        return _empty_record()
    # Backfill any missing keys so callers don't need to handle old files
    base = _empty_record()
    for k, v in base.items():
        rec.setdefault(k, v)
    return rec


def seed_record_if_missing(skill_name: str) -> None:
    """Persist a baseline usage record for a curation-eligible skill.

    Built-ins carry no usage record until something touches them, which leaves
    their inactivity clock with no anchor. Seeding a record here fixes
    ``created_at`` to the moment the curator first sees the skill, so the
    archive/stale clock measures non-use FROM THEN — not from epoch. No-op when
    a record already exists or the skill isn't curation-eligible.
    """
    if not skill_name or not is_curation_eligible(skill_name):
        return
    try:
        with _usage_file_lock():
            data = load_usage()
            if isinstance(data.get(skill_name), dict):
                return
            data[skill_name] = _empty_record()
            save_usage(data)
    except Exception as e:
        logger.debug("skill_usage.seed_record_if_missing(%s) failed: %s", skill_name, e, exc_info=True)


def _mutate(skill_name: str, mutator, *, require_curation_eligible: bool = False) -> None:
    """Load, apply *mutator(record)* in place, save. Best-effort.

    By default this records telemetry for ANY skill — bundled, hub-installed,
    or agent-created — because usage tracking is pure observability and is
    orthogonal to whether a skill is ever curated. Lifecycle mutators
    (``set_state``, ``set_pinned``, ``mark_agent_created``) pass
    ``require_curation_eligible=True`` so they never write meaningless state
    onto a skill the curator can't manage (e.g. an ``archived`` flag on a
    hub-installed skill).
    """
    if not skill_name:
        return
    try:
        if require_curation_eligible and not is_curation_eligible(skill_name):
            return
        with _usage_file_lock():
            data = load_usage()
            rec = data.get(skill_name)
            if not isinstance(rec, dict):
                rec = _empty_record()
            mutator(rec)
            data[skill_name] = rec
            save_usage(data)
    except Exception as e:
        logger.debug("skill_usage._mutate(%s) failed: %s", skill_name, e, exc_info=True)


# ---------------------------------------------------------------------------
# Public counter-bump helpers — telemetry for ALL skills (observability only)
# ---------------------------------------------------------------------------

def bump_view(skill_name: str) -> None:
    """Bump view_count and last_viewed_at. Called from skill_view().

    Tracks every skill regardless of provenance — built-ins and hub skills
    included. Usage telemetry is observability, not a curation signal.
    """
    def _apply(rec: Dict[str, Any]) -> None:
        rec["view_count"] = int(rec.get("view_count") or 0) + 1
        rec["last_viewed_at"] = _now_iso()
    _mutate(skill_name, _apply)


def bump_use(skill_name: str) -> None:
    """Bump use_count and last_used_at. Called when a skill is actively used
    (e.g. loaded into the prompt path or referenced from an assistant turn).

    Tracks every skill regardless of provenance.
    """
    def _apply(rec: Dict[str, Any]) -> None:
        rec["use_count"] = int(rec.get("use_count") or 0) + 1
        rec["last_used_at"] = _now_iso()
    _mutate(skill_name, _apply)


def bump_patch(skill_name: str) -> None:
    """Bump patch_count and last_patched_at. Called from skill_manage (patch/edit).

    Tracks every skill regardless of provenance.
    """
    def _apply(rec: Dict[str, Any]) -> None:
        rec["patch_count"] = int(rec.get("patch_count") or 0) + 1
        rec["last_patched_at"] = _now_iso()
    _mutate(skill_name, _apply)


def _running_mean(
    prev_mean: Optional[float], prev_count: int, sample: float
) -> float:
    """Sample-count-weighted incremental mean.

    ``prev_mean`` is the mean over ``prev_count`` prior samples (None when there
    were none); folding in ``sample`` yields the mean over ``prev_count + 1``
    samples. Pure arithmetic, no I/O — the storage layer owns persistence.
    """
    if prev_mean is None or prev_count <= 0:
        return float(sample)
    return (float(prev_mean) * prev_count + float(sample)) / (prev_count + 1)


def record_skill_outcome(
    skill_name: str,
    *,
    turn_score: Optional[float] = None,
    latency_ms: Optional[float] = None,
    cost: Optional[float] = None,
    user_rating: Optional[float] = None,
) -> None:
    """Fold one observed run's quality signals into a skill's rolling averages.

    Updates ``success_rate`` (from ``turn_score``), ``avg_latency_ms``,
    ``cost_per_run``, and ``user_rating`` as sample-count-weighted running means,
    incrementing ``sample_count`` once per call that carries at least one signal.
    Every metric is optional: a ``None`` argument leaves that metric unchanged.
    Atomic-write + flock via ``_mutate`` exactly like ``bump_use``/``bump_view``/
    ``bump_patch``.

    This is a READ-ONLY health signal for the human reviewer. It is NEVER read by
    ``apply_automatic_transitions`` or any other auto-prune / auto-archive path,
    honoring the curator review rule that usage is not a quality signal
    (curator.py:391-394). Tracks any skill regardless of provenance, mirroring
    the other counter bumps.
    """
    # Nothing to record — avoid bumping sample_count for an empty observation.
    if turn_score is None and latency_ms is None and cost is None and user_rating is None:
        return

    def _apply(rec: Dict[str, Any]) -> None:
        try:
            prev_count = int(rec.get("sample_count") or 0)
        except (TypeError, ValueError):
            prev_count = 0

        def _prev(key: str) -> Optional[float]:
            raw = rec.get(key)
            if raw is None:
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        if turn_score is not None:
            rec["success_rate"] = _running_mean(
                _prev("success_rate"), prev_count, float(turn_score)
            )
        if latency_ms is not None:
            rec["avg_latency_ms"] = _running_mean(
                _prev("avg_latency_ms"), prev_count, float(latency_ms)
            )
        if cost is not None:
            rec["cost_per_run"] = _running_mean(
                _prev("cost_per_run"), prev_count, float(cost)
            )
        if user_rating is not None:
            rec["user_rating"] = _running_mean(
                _prev("user_rating"), prev_count, float(user_rating)
            )
        rec["sample_count"] = prev_count + 1

    _mutate(skill_name, _apply)


def mark_agent_created(skill_name: str) -> None:
    """Opt a skill created by skill_manage into curator management.

    Viewing or invoking a manually authored skill may still create telemetry,
    but only this explicit marker makes it eligible for automatic curation.
    """
    def _apply(rec: Dict[str, Any]) -> None:
        rec["created_by"] = "agent"
    _mutate(skill_name, _apply, require_curation_eligible=True)


def set_state(skill_name: str, state: str) -> None:
    """Set lifecycle state. No-op if *state* is invalid or the skill isn't
    curator-manageable (hub skills, or built-ins with pruning disabled)."""
    if state not in _VALID_STATES:
        logger.debug("set_state: invalid state %r for %s", state, skill_name)
        return
    def _apply(rec: Dict[str, Any]) -> None:
        rec["state"] = state
        if state == STATE_ARCHIVED:
            rec["archived_at"] = _now_iso()
        elif state == STATE_ACTIVE:
            rec["archived_at"] = None
    _mutate(skill_name, _apply, require_curation_eligible=True)


def set_pinned(skill_name: str, pinned: bool) -> None:
    def _apply(rec: Dict[str, Any]) -> None:
        rec["pinned"] = bool(pinned)
    _mutate(skill_name, _apply, require_curation_eligible=True)


def forget(skill_name: str) -> None:
    """Drop a skill's usage entry entirely. Called when the skill is deleted."""
    if not skill_name:
        return
    try:
        with _usage_file_lock():
            data = load_usage()
            if skill_name in data:
                del data[skill_name]
                save_usage(data)
    except Exception as e:
        logger.debug("skill_usage.forget(%s) failed: %s", skill_name, e, exc_info=True)


# ---------------------------------------------------------------------------
# Archive / restore
# ---------------------------------------------------------------------------

def archive_skill(skill_name: str) -> Tuple[bool, str]:
    """Move a curator-eligible skill directory to ~/.hermes/skills/.archive/.

    Returns (ok, message). Never archives hub-installed skills. Bundled
    built-ins are only archivable when ``curator.prune_builtins`` is enabled;
    when one is archived, its name is added to the suppression list so the
    update-time re-seeder leaves it archived instead of restoring it.
    """
    if not is_curation_eligible(skill_name):
        if is_protected_builtin(skill_name):
            return False, (
                f"skill '{skill_name}' is a protected built-in; it backs "
                "load-bearing UX and is never archived or consolidated"
            )
        if is_hub_installed(skill_name):
            return False, f"skill '{skill_name}' is hub-installed; never archive"
        return False, (
            f"skill '{skill_name}' is a bundled built-in; enable "
            "curator.prune_builtins to allow pruning it"
        )

    skill_dir = _find_skill_dir(skill_name)
    if skill_dir is None:
        return False, f"skill '{skill_name}' not found"

    archive_root = _archive_dir()
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"failed to create archive dir: {e}"

    # Flatten any category nesting into a single ".archive/<skill>/" so restores
    # are simple. If a collision exists, append a timestamp.
    dest = archive_root / skill_dir.name
    if dest.exists():
        dest = archive_root / f"{skill_dir.name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    try:
        skill_dir.rename(dest)
    except OSError as e:
        # Cross-device — fall back to shutil.move
        import shutil
        try:
            shutil.move(str(skill_dir), str(dest))
        except Exception as e2:
            return False, f"failed to archive: {e2}"

    # Pruning a built-in only sticks if the re-seeder is told to leave it alone.
    if is_bundled(skill_name):
        add_suppressed_name(skill_name)

    set_state(skill_name, STATE_ARCHIVED)
    return True, f"archived to {dest}"


def restore_skill(skill_name: str) -> Tuple[bool, str]:
    """Move an archived skill back to ~/.hermes/skills/. Restores to the flat
    top-level layout; original category nesting is NOT reconstructed.

    Refuses to restore under a name that now collides with a hub-installed
    skill — that would shadow the upstream version. Also refuses to restore
    over a bundled built-in UNLESS ``curator.prune_builtins`` is enabled (in
    which case built-ins are curator-managed and restoring is the documented
    way to lift a prune). Restoring clears any suppression entry so future
    updates may re-seed the built-in again.
    """
    # Hub skills always have an external upstream owner — never shadow them.
    if is_hub_installed(skill_name):
        return False, (
            f"skill '{skill_name}' is now hub-installed; "
            "restore would shadow the upstream version"
        )
    # A bundled built-in is upstream-owned UNLESS prune_builtins is on. With the
    # flag off, restoring over it would shadow the bundled version.
    if is_bundled(skill_name) and not _prune_builtins_enabled():
        return False, (
            f"skill '{skill_name}' is now bundled; "
            "restore would shadow the upstream version"
        )
    archive_root = _archive_dir()
    if not archive_root.exists():
        return False, "no archive directory"

    # Try exact name match first, then the timestamped-duplicate fallback.
    # Recursive walk handles nested archive layouts (e.g. .archive/<category>/<skill>/)
    # left behind by older archive paths or external imports.
    candidates = [p for p in archive_root.rglob("*") if p.is_dir() and p.name == skill_name]
    if not candidates:
        # A name collision makes archive_skill() disambiguate by appending its
        # UTC timestamp ("<skill>-YYYYMMDDHHMMSS", a 14-digit suffix), so only
        # that exact shape is another copy of THIS skill. A bare
        # startswith(f"{skill_name}-") also swallows unrelated sibling skills —
        # restoring "git" would otherwise pull an archived "git-helpers" out of
        # the archive and rename it to "git", destroying the sibling's only
        # copy. Require the suffix to be the timestamp archive_skill writes.
        prefix = f"{skill_name}-"
        candidates = sorted(
            [
                p for p in archive_root.rglob("*")
                if p.is_dir()
                and p.name.startswith(prefix)
                and len(p.name) - len(prefix) == 14
                and p.name[len(prefix):].isdigit()
            ],
            reverse=True,
        )
    if not candidates:
        return False, f"skill '{skill_name}' not found in archive"

    src = candidates[0]
    dest = _skills_dir() / skill_name
    if dest.exists():
        return False, f"destination already exists: {dest}"

    try:
        src.rename(dest)
    except OSError:
        import shutil
        try:
            shutil.move(str(src), str(dest))
        except Exception as e:
            return False, f"failed to restore: {e}"

    # Restoring a pruned built-in lifts its suppression so updates can manage it.
    remove_suppressed_name(skill_name)

    set_state(skill_name, STATE_ACTIVE)
    return True, f"restored to {dest}"


def _find_skill_dir(skill_name: str) -> Optional[Path]:
    """Locate the directory for a skill by its frontmatter `name:` field.

    Handles both flat (~/.hermes/skills/<skill>/SKILL.md) and category-nested
    (~/.hermes/skills/<category>/<skill>/SKILL.md) layouts.
    """
    base = _skills_dir()
    if not base.exists():
        return None
    for skill_md in base.rglob("SKILL.md"):
        if is_excluded_skill_path(skill_md):
            continue
        if _read_skill_name(skill_md, fallback=skill_md.parent.name) == skill_name:
            return skill_md.parent
    return None


# ---------------------------------------------------------------------------
# Reporting — for the curator CLI / slash command
# ---------------------------------------------------------------------------

def agent_created_report() -> List[Dict[str, Any]]:
    """Return a list of {name, state, pinned, last_activity_at, ...}
    records for every curator-managed skill. Missing usage records are
    backfilled with defaults so callers can always index fields.

    Each row carries ``_persisted``: True when a real record exists in
    ``.usage.json``, False when the row is a fresh backfill (e.g. a built-in
    seen for the first time). The curator uses this to seed the inactivity
    clock instead of treating an unrecorded skill as ancient.
    """
    data = load_usage()
    rows: List[Dict[str, Any]] = []
    for name in list_agent_created_skill_names():
        raw = data.get(name)
        persisted = isinstance(raw, dict)
        rec: Dict[str, Any] = raw if isinstance(raw, dict) else _empty_record()
        base = _empty_record()
        for k, v in base.items():
            rec.setdefault(k, v)
        row = {"name": name, **rec, "_persisted": persisted}
        row["last_activity_at"] = latest_activity_at(row)
        row["activity_count"] = activity_count(row)
        rows.append(row)
    return rows


def provenance(skill_name: str) -> str:
    """Classify a skill's origin: 'hub', 'bundled', or 'agent'.

    'agent' covers both agent-authored and local manually-authored skills —
    anything not seeded from the bundled repo or installed via the hub.
    """
    if is_hub_installed(skill_name):
        return "hub"
    if is_bundled(skill_name):
        return "bundled"
    return "agent"


def usage_report() -> List[Dict[str, Any]]:
    """Return usage telemetry for EVERY skill on disk, with provenance.

    Unlike ``agent_created_report()`` (which is scoped to curator-managed
    candidates), this surfaces all skills — bundled built-ins and
    hub-installed included — so callers can answer "how often is this skill
    used" independent of whether it's ever curated. Rows carry a
    ``provenance`` field ('agent' | 'bundled' | 'hub') and ``_persisted``
    (whether a real ``.usage.json`` record backs the row).
    """
    base = _skills_dir()
    if not base.exists():
        return []
    data = load_usage()
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for skill_md in base.rglob("SKILL.md"):
        if is_excluded_skill_path(skill_md):
            continue
        name = _read_skill_name(skill_md, fallback=skill_md.parent.name)
        if name in seen:
            continue
        seen.add(name)
        raw = data.get(name)
        persisted = isinstance(raw, dict)
        rec: Dict[str, Any] = raw if isinstance(raw, dict) else _empty_record()
        base_rec = _empty_record()
        for k, v in base_rec.items():
            rec.setdefault(k, v)
        row = {
            "name": name,
            **rec,
            "provenance": provenance(name),
            "_persisted": persisted,
        }
        row["last_activity_at"] = latest_activity_at(row)
        row["activity_count"] = activity_count(row)
        rows.append(row)
    return sorted(rows, key=lambda r: r["name"])


# ---------------------------------------------------------------------------
# Skill health view — read-only outcome-quality signal (Part 2, Slice 3)
# ---------------------------------------------------------------------------

# Valid sort orders for ``sort_skill_health``. The workspace consumes the data
# and may rank by any of these. These are presentation orderings only — they
# never feed a transition decision.
_HEALTH_SORTS = ("most_used", "most_successful", "most_expensive", "most_failing")


def skill_health_view() -> List[Dict[str, Any]]:
    """Per-skill health rows: existing counts PLUS outcome-quality metrics.

    Returns one row per skill on disk (every provenance) carrying the usage
    counters (``use_count``/``view_count``/``patch_count``/``activity_count``)
    and the additive quality metrics (``success_rate``/``avg_latency_ms``/
    ``cost_per_run``/``user_rating``/``sample_count``), plus ``provenance`` and
    ``_persisted``. Metrics are ``None`` until the first sample lands.

    This is a pure read. It is a health signal for the human reviewer and for
    the workspace to rank/display; it is NEVER consulted by
    ``apply_automatic_transitions`` or any auto-prune path (curator.py:391-394).
    Use :func:`sort_skill_health` to rank the returned rows.
    """
    rows: List[Dict[str, Any]] = []
    for r in usage_report():
        rows.append(
            {
                "name": r["name"],
                "provenance": r.get("provenance"),
                "state": r.get("state"),
                "pinned": bool(r.get("pinned")),
                "use_count": int(r.get("use_count") or 0),
                "view_count": int(r.get("view_count") or 0),
                "patch_count": int(r.get("patch_count") or 0),
                "activity_count": int(r.get("activity_count") or 0),
                "success_rate": r.get("success_rate"),
                "avg_latency_ms": r.get("avg_latency_ms"),
                "cost_per_run": r.get("cost_per_run"),
                "user_rating": r.get("user_rating"),
                "sample_count": int(r.get("sample_count") or 0),
                "last_activity_at": r.get("last_activity_at"),
                "_persisted": r.get("_persisted", False),
            }
        )
    return rows


def sort_skill_health(
    rows: List[Dict[str, Any]], order: str = "most_used"
) -> List[Dict[str, Any]]:
    """Return *rows* (from :func:`skill_health_view`) sorted by *order*.

    Supported orders (``_HEALTH_SORTS``):
      - ``most_used``        — by ``use_count`` descending.
      - ``most_successful``  — by ``success_rate`` descending (unsampled last).
      - ``most_expensive``   — by ``cost_per_run`` descending (unsampled last).
      - ``most_failing``     — by ``success_rate`` ascending (unsampled last).

    Does not mutate the input. An unknown *order* falls back to ``most_used``.
    Rows with a ``None`` metric always sort AFTER rows that have a value, in
    every order, so "no data yet" never masquerades as best or worst. Ties break
    by ``name`` for a stable, deterministic ordering.
    """
    if order not in _HEALTH_SORTS:
        order = "most_used"

    def _num(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    if order == "most_used":
        # use_count is always a concrete int; simple descending sort.
        return sorted(
            rows,
            key=lambda r: (-int(r.get("use_count") or 0), str(r.get("name") or "")),
        )

    if order == "most_expensive":
        metric, reverse = "cost_per_run", True
    elif order == "most_failing":
        metric, reverse = "success_rate", False
    else:  # most_successful
        metric, reverse = "success_rate", True

    def _key(r: Dict[str, Any]) -> Tuple[int, float, str]:
        v = _num(r.get(metric))
        # Push None metrics to the END regardless of direction: present rows get
        # has_value=0 (sort first), absent rows has_value=1 (sort last). Within
        # the present group we negate for descending or keep for ascending.
        has_value = 0 if v is not None else 1
        if v is None:
            sort_v = 0.0
        else:
            sort_v = -v if reverse else v
        return (has_value, sort_v, str(r.get("name") or ""))

    return sorted(rows, key=_key)
