"""Adversarial / hostile probes for the truth-under-failure reconciler.

Goal: BREAK ``classify`` / ``reconcile_and_emit``. Each probe asserts the
ROBUST behavior, so a failing assertion exposes a real bug for the human to fix.

Focus areas (per the reconciler's own docstring promises):
  - clock skew (now < started_at) must not crash or falsely flip terminal,
  - a just-started run with all-None heartbeats must NOT be called stalled while
    it is inside the startup grace,
  - a run with a pid but no started_at (no anchor for the grace window),
  - exactly-at-boundary windows (now - last == window),
  - a terminal/completed run is ALWAYS a no-op, even with a dead pid,
  - pid_alive raising must be handled, not crash classify,
  - reconcile_and_emit twice over the same dead run writes exactly one terminal.

Stdlib + pytest only. The spine fixture points HERMES_OC_RUNS_DB at a tmp file
and resets db._local exactly like the existing reconciler tests do.
"""

from __future__ import annotations

import pytest

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events, reconciler
from plugins.oc_runs.reconciler import ReconcileConfig, RunLiveness

CFG = ReconcileConfig(progress_window=120.0, liveness_window=30.0, absolute_timeout=1800.0)
NOW = 10_000.0


def _alive(_pid, _start=None):
    return True


def _dead(_pid, _start=None):
    return False


# --------------------------------------------------------------------------- #
# Clock skew: now < started_at (NTP step back, monotonic vs wall mismatch).
# --------------------------------------------------------------------------- #

def test_clock_skew_now_before_started_does_not_crash_or_flip():
    # A worker whose started_at is in the (apparent) future. Pid alive, fresh
    # beats. The robust answer is a no-op: we must not panic and must not
    # fabricate a timeout or a stall from a negative elapsed time.
    run = RunLiveness(run_id="agents:skew", status="working", pid=4242,
                      started_at=NOW + 500, last_liveness_at=NOW + 500,
                      last_progress_at=NOW + 500)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "none", f"clock skew falsely produced action={v.action!r}"
    assert v.event is None


def test_clock_skew_negative_elapsed_never_reads_as_timeout():
    # started_at far in the future => now - started_at is very negative. That
    # must NOT satisfy the absolute_timeout branch (which uses '>').
    run = RunLiveness(run_id="agents:skew2", status="working", pid=4242,
                      started_at=NOW + 999_999, last_liveness_at=NOW,
                      last_progress_at=NOW)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.reason != "timeout", "negative elapsed was misread as a timeout"
    assert v.action != "failed"


# --------------------------------------------------------------------------- #
# Just-started run, all-None heartbeats, inside the grace window: NOT stalled.
# --------------------------------------------------------------------------- #

def test_just_started_all_none_heartbeats_inside_grace_is_noop():
    # The classic startup race: the worker registered started_at but has not
    # emitted any heartbeat yet, and we reconcile 5s later. The docstring
    # promises a startup grace so this is NEVER falsely flipped to stalled.
    run = RunLiveness(run_id="agents:young", status="working", pid=4242,
                      started_at=NOW - 5, last_liveness_at=None,
                      last_progress_at=None)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "none", (
        f"a brand-new run inside the grace window was flipped to {v.action!r} "
        "despite having no heartbeats yet"
    )


# --------------------------------------------------------------------------- #
# pid set but started_at None: there is no anchor for the grace window.
# --------------------------------------------------------------------------- #

def test_pid_alive_started_at_none_no_beats_is_not_falsely_stalled():
    # A live process that has not recorded started_at and has not beaten yet.
    # Without a started_at anchor the grace branch cannot fire, so this run
    # falls straight into heartbeat staleness and gets flipped to 'stalled'
    # even though the process is provably ALIVE and may simply be brand new.
    # The robust behavior for an unknown-age, provably-alive run is to NOT
    # fabricate a terminal stall.
    run = RunLiveness(run_id="agents:noanchor", status="working", pid=4242,
                      started_at=None, last_liveness_at=None,
                      last_progress_at=None)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action != "stalled", (
        "a provably-alive run with no started_at anchor and no heartbeats was "
        f"falsely flipped to stalled (action={v.action!r}); the grace window "
        "only applies when started_at is set, so age-unknown alive runs get "
        "wrongly killed"
    )


def test_pid_dead_started_at_none_still_fails():
    # The flip-side: a dead pid is dead regardless of a missing started_at.
    run = RunLiveness(run_id="agents:deadnoanchor", status="working", pid=4242,
                      started_at=None, last_liveness_at=NOW, last_progress_at=NOW)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_dead)
    assert v.action == "failed"
    assert v.reason == "process_died"


# --------------------------------------------------------------------------- #
# Exactly-at-boundary windows: now - last == window.
# --------------------------------------------------------------------------- #

def test_liveness_exactly_at_boundary_is_not_stale():
    # now - last_liveness == liveness_window exactly. The staleness test uses
    # strict '>', so an event landing precisely on the boundary is still fresh,
    # i.e. NOT wedged. Progress also exactly on its boundary => not stale.
    # Run is past grace (age > liveness_window) so heartbeats are judged.
    run = RunLiveness(run_id="agents:edge", status="working", pid=4242,
                      started_at=NOW - 200,  # past grace, well under timeout
                      last_liveness_at=NOW - CFG.liveness_window,
                      last_progress_at=NOW - CFG.progress_window)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "none", (
        f"events exactly on the staleness boundary were treated as stale "
        f"(action={v.action!r}); boundary should be inclusive-fresh"
    )


def test_absolute_timeout_exactly_at_boundary_does_not_fire():
    # now - started_at == absolute_timeout exactly; '>' means NOT yet timed out.
    run = RunLiveness(run_id="agents:tobound", status="working", pid=4242,
                      started_at=NOW - CFG.absolute_timeout,
                      last_liveness_at=NOW - 1, last_progress_at=NOW - 1)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.reason != "timeout", (
        "timeout fired exactly at the boundary; '>' should require strictly "
        "exceeding the cap"
    )
    assert v.action == "none"


def test_grace_exactly_at_boundary_judges_heartbeats():
    # now - started_at == liveness_window exactly. Grace uses strict '<', so at
    # the boundary the run is no longer young and heartbeats ARE judged. With
    # both beats None this is a genuine stall (process alive but never beat past
    # grace). Robust answer: stalled.
    run = RunLiveness(run_id="agents:gracebound", status="working", pid=4242,
                      started_at=NOW - CFG.liveness_window,
                      last_liveness_at=None, last_progress_at=None)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "stalled"
    assert v.reason == "no_heartbeat"


# --------------------------------------------------------------------------- #
# Terminal runs are always no-ops, even with a dead pid / blown timeout.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status", sorted(reconciler.TERMINAL_STATUSES))
def test_terminal_status_is_always_noop_even_with_dead_pid(status):
    run = RunLiveness(run_id=f"agents:{status}", status=status, pid=4242,
                      started_at=NOW - 999_999, last_liveness_at=None,
                      last_progress_at=None)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_dead)
    assert v.action == "none", f"terminal status {status!r} was re-flipped to {v.action!r}"
    assert v.event is None


def test_is_terminal_flag_overrides_dead_pid():
    run = RunLiveness(run_id="agents:flag", status="working", pid=4242,
                      started_at=NOW - 999_999, is_terminal=True,
                      last_liveness_at=None, last_progress_at=None)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_dead)
    assert v.action == "none"
    assert v.event is None


# --------------------------------------------------------------------------- #
# pid_alive raising an exception must be handled, not crash classify.
# --------------------------------------------------------------------------- #

def test_pid_alive_raising_does_not_crash_classify():
    def _boom(_pid, _start=None):
        raise RuntimeError("psutil exploded")

    run = RunLiveness(run_id="agents:boom", status="working", pid=4242,
                      started_at=NOW - 100, last_liveness_at=NOW - 1,
                      last_progress_at=NOW - 1)
    try:
        v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_boom)
    except Exception as e:  # noqa: BLE001
        pytest.fail(
            "classify let a pid_alive exception propagate and crash the whole "
            f"reconcile pass ({type(e).__name__}: {e}); one bad probe should "
            "not take down classification of the batch"
        )
    # A probe that cannot determine liveness must fail safe, not silently treat
    # the process as alive-and-healthy.
    assert v.action != "none" or v.reason == "", (
        f"liveness was indeterminate yet classify returned a clean no-op "
        f"(action={v.action!r}); an exploding probe should not read as healthy"
    )


# --------------------------------------------------------------------------- #
# emit idempotency against the real spine.
# --------------------------------------------------------------------------- #

def _reset_spine_local():
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            try:
                if attr == "conn" and spine_db._local.conn is not None:
                    spine_db._local.conn.close()
            except Exception:
                pass
            delattr(spine_db._local, attr)


@pytest.fixture()
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset_spine_local()
    yield
    _reset_spine_local()


def test_emit_twice_same_dead_run_writes_exactly_one_terminal(spine):
    run = RunLiveness(run_id="agents:dead", status="working", pid=4242,
                      started_at=NOW - 5, last_liveness_at=NOW - 1,
                      last_progress_at=NOW - 1)
    reconciler.reconcile_and_emit([run], now=NOW, cfg=CFG, pid_alive=_dead)
    reconciler.reconcile_and_emit([run], now=NOW + 60, cfg=CFG, pid_alive=_dead)
    terminals = [e for e in spine_db.tail_since(0) if e["type"] in events.TERMINAL_TYPES]
    assert len(terminals) == 1, (
        f"two reconcile passes wrote {len(terminals)} terminal events; the "
        "per-run dedupe_key must collapse re-emits to exactly one"
    )
    assert terminals[0]["type"] == events.RUN_FAILED


def test_emit_stalled_then_failed_same_run_both_terminal_distinct_keys(spine):
    # First pass: alive but wedged -> stalled (dedupe reconciler:stalled).
    # Second pass: the wedged process has since died -> failed (dedupe
    # reconciler:failed). These are different dedupe keys, so the spine ends up
    # holding BOTH a stalled and a failed terminal for the run. That is a real
    # truthfulness hole: a run cannot honestly be both stalled and failed. The
    # robust spine should not present two contradictory terminal verdicts.
    stalled_run = RunLiveness(run_id="agents:dup", status="working", pid=4242,
                              started_at=NOW - 600, last_liveness_at=NOW - 600,
                              last_progress_at=NOW - 600)
    reconciler.reconcile_and_emit([stalled_run], now=NOW, cfg=CFG, pid_alive=_alive)
    reconciler.reconcile_and_emit([stalled_run], now=NOW + 60, cfg=CFG, pid_alive=_dead)
    terminals = [e for e in spine_db.tail_since(0) if e["type"] in events.TERMINAL_TYPES]
    types = sorted(e["type"] for e in terminals)
    assert len(terminals) == 1, (
        f"the spine holds {len(terminals)} contradictory terminal events for "
        f"one run: {types}; a run should not be both stalled and failed"
    )


def test_slow_emits_non_terminal_status_only(spine):
    run = RunLiveness(run_id="agents:slow", status="working", pid=4242,
                      started_at=NOW - 600, last_liveness_at=NOW - 1,
                      last_progress_at=NOW - 600)
    reconciler.reconcile_and_emit([run], now=NOW, cfg=CFG, pid_alive=_alive)
    all_ev = spine_db.tail_since(0)
    assert [e for e in all_ev if e["type"] in events.TERMINAL_TYPES] == [], (
        "a slow-but-healthy run leaked a terminal event into the spine"
    )
    assert any(e["type"] == events.RUN_STATUS for e in all_ev)
