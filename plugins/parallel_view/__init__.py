"""parallel_view: the pure-fold projection from the run-event spine to a RunView.

Folds the append-only oc_runs log into one normalized view per run that the
cockpit renders. It adds zero ordering semantics and is never a source of truth:
the spine is. One normalized state vocabulary maps every native status once, and
an unmapped status falls through to ``unknown`` (never to ``running``), so a
schema gap can never hide a truth-under-failure violation.
"""
