"""Runtime control flags for the pipeline (Rev 12 SRE-19).

Module-level flags consulted by the pipeline orchestrator on each tick.
Admin endpoints flip these to pause/resume the whole pipeline or skip a
specific calculator without a redeploy.

The pipeline-side check (``app.processing.pipeline``) is wired by Lane M2
in a follow-up; that lane is the owner of pipeline.py. Until then the
flags are settable via the admin API and observable here, so the surface
is exercised even when the pipeline does not yet honour them. Once the
pipeline reads from this module the same flags apply atomically.

Threading model: all mutators and readers are short critical sections.
Sets are tiny (a handful of calculator names) so a coarse lock is
cheaper than per-name atomic ops. Readers MUST snapshot via the helpers
below — never iterate the underlying set directly across an ``await``
boundary.
"""

from __future__ import annotations

import threading
from typing import Final

# Coarse lock for paused flag + skipped set. Both mutations are O(1).
_LOCK: Final[threading.Lock] = threading.Lock()
_PAUSED: bool = False
_SKIPPED_CALCULATORS: set[str] = set()


def is_paused() -> bool:
    """Return True if the pipeline is currently paused.

    Pipeline.py should consult this at the top of each tick and skip the
    calculator phase when True (audit/heartbeat persistence is fine).
    """
    with _LOCK:
        return _PAUSED


def set_paused(paused: bool) -> None:
    """Flip the global pause flag."""
    global _PAUSED
    with _LOCK:
        _PAUSED = bool(paused)


def is_calculator_skipped(name: str) -> bool:
    """Return True if ``name`` is in the skip set.

    ``name`` is the canonical lower-case calculator name (e.g. ``hiro``,
    ``gex``, ``vanna_charm``). Matching is exact.
    """
    with _LOCK:
        return name in _SKIPPED_CALCULATORS


def add_skipped_calculator(name: str) -> None:
    """Add ``name`` to the skip set (idempotent)."""
    with _LOCK:
        _SKIPPED_CALCULATORS.add(name)


def remove_skipped_calculator(name: str) -> None:
    """Remove ``name`` from the skip set if present."""
    with _LOCK:
        _SKIPPED_CALCULATORS.discard(name)


def snapshot_skipped() -> list[str]:
    """Return a sorted snapshot of currently-skipped calculator names."""
    with _LOCK:
        return sorted(_SKIPPED_CALCULATORS)


def reset_for_tests() -> None:
    """Test helper — clears all runtime flags."""
    global _PAUSED
    with _LOCK:
        _PAUSED = False
        _SKIPPED_CALCULATORS.clear()
