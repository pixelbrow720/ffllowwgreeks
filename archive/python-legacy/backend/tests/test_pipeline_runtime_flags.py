"""Rev 12 SRE-19 — pipeline runtime flags + admin endpoints.

Pure-function tests for the runtime flag module. The admin endpoint
tests rely on a live FastAPI test client + DB which lives under
``test_api_admin.py`` (DB-backed); they are kept here as smoke
checks against the module surface only.
"""

from __future__ import annotations

import pytest

from app.processing import pipeline_runtime_flags


@pytest.fixture(autouse=True)
def _reset_flags() -> None:
    """Each test starts with a fresh flag state."""
    pipeline_runtime_flags.reset_for_tests()
    yield
    pipeline_runtime_flags.reset_for_tests()


def test_default_state_is_unpaused_with_empty_skip_set() -> None:
    assert pipeline_runtime_flags.is_paused() is False
    assert pipeline_runtime_flags.snapshot_skipped() == []


def test_set_paused_toggles() -> None:
    pipeline_runtime_flags.set_paused(True)
    assert pipeline_runtime_flags.is_paused() is True
    pipeline_runtime_flags.set_paused(False)
    assert pipeline_runtime_flags.is_paused() is False


def test_add_skipped_calculator_is_idempotent() -> None:
    pipeline_runtime_flags.add_skipped_calculator("hiro")
    pipeline_runtime_flags.add_skipped_calculator("hiro")
    assert pipeline_runtime_flags.snapshot_skipped() == ["hiro"]
    assert pipeline_runtime_flags.is_calculator_skipped("hiro") is True
    assert pipeline_runtime_flags.is_calculator_skipped("gex") is False


def test_remove_skipped_calculator_is_safe_when_absent() -> None:
    # Removing a name that was never added must not raise.
    pipeline_runtime_flags.remove_skipped_calculator("never_added")
    assert pipeline_runtime_flags.snapshot_skipped() == []


def test_skipped_snapshot_is_sorted() -> None:
    for name in ("zero_gamma", "hiro", "gex"):
        pipeline_runtime_flags.add_skipped_calculator(name)
    assert pipeline_runtime_flags.snapshot_skipped() == ["gex", "hiro", "zero_gamma"]


def test_calculator_match_is_case_sensitive() -> None:
    pipeline_runtime_flags.add_skipped_calculator("HIRO")
    assert pipeline_runtime_flags.is_calculator_skipped("HIRO") is True
    # Pipeline uses lower-case discriminators; a case mismatch must not
    # silently match.
    assert pipeline_runtime_flags.is_calculator_skipped("hiro") is False


def test_reset_clears_state() -> None:
    pipeline_runtime_flags.set_paused(True)
    pipeline_runtime_flags.add_skipped_calculator("gex")
    pipeline_runtime_flags.reset_for_tests()
    assert pipeline_runtime_flags.is_paused() is False
    assert pipeline_runtime_flags.snapshot_skipped() == []
