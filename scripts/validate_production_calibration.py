#!/usr/bin/env python3
"""Audit the calibration of the win probabilities the site actually showed.

Unlike validate_elo.py (which replays the model fresh against historical
results), this reads the *production* predictions frozen in
daily_rankings.win_prob_a for completed games and scores them against actual
outcomes. Because win_prob_a freezes at the last pre-game value, this stays
time-honest even as Elo params change.

Run from the repo root with the venv active:
    python -m scripts.validate_production_calibration [SEASON]
"""

from __future__ import annotations

import sys

from src.db.schema import get_session
from src.db.queries import get_calibration_pairs
from src.scoring.calibration import compute_calibration


def main() -> int:
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    session = get_session()
    try:
        pairs = get_calibration_pairs(session, season)
    finally:
        session.close()

    result = compute_calibration(pairs)
    if result.n == 0:
        print(f"No completed {season} games with a stored win_prob_a yet.")
        return 0

    print(f"Production win-prob calibration — {season}")
    print(f"  N games : {result.n}")
    print(f"  Brier   : {result.brier:.4f}  (0 = perfect, 0.25 = coin flip)")
    print(f"  {'bucket':<12}{'n':>5}{'predicted':>12}{'actual':>10}")
    for b in result.buckets:
        print(
            f"  {b.lo:.0%}-{b.hi:.0%}".ljust(14)
            + f"{b.count:>5}"
            + f"{b.predicted_mean:>12.1%}"
            + f"{b.actual_rate:>10.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
