"""Shared fail-closed exit gate for the --recompute backfill scripts.

Third-caller extraction (backfill_game_shapes, backfill_excitement,
backfill_importance) per the trigger recorded in scripts/CLAUDE.md.
"""

import logging

logger = logging.getLogger(__name__)


def recompute_gate(failures: list[str], noun: str, extra_hint: str = "") -> int:
    """Return 1 and log if any stored row could not be refreshed, else 0.

    Fails closed so the operator re-runs until the data converges.
    """
    if not failures:
        return 0
    hint = f" {extra_hint}" if extra_hint else ""
    logger.error(
        f"Recompute INCOMPLETE: {len(failures)} stored {noun}(s) could not be "
        f"refreshed (stale values kept): {failures}. Re-run to retry.{hint}"
    )
    return 1
