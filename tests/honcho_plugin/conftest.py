"""Test isolation for the Honcho plugin suite.

``get_honcho_client`` caches the client in a process-wide singleton slot (an
intentional production optimization). Across a full test run, one test that
builds a real client leaves it cached, so a later test patching ``honcho.Honcho``
gets the stale cached client instead of its mock (e.g.
``test_passes_timeout_from_config`` failing only in the full suite). Resetting
the slot before each test makes every test hermetic.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_honcho_singleton():
    try:
        from plugins.memory.honcho.client import reset_honcho_client
    except Exception:
        reset_honcho_client = None
    if reset_honcho_client:
        reset_honcho_client()
    yield
    if reset_honcho_client:
        reset_honcho_client()
