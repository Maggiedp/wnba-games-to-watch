#!/usr/bin/env python3
"""Freeze the Elo backtest game set into a test fixture.

Fetches the same historical game set scripts/validate_elo.py evaluates
(2016 warm-up + 2017–2025 eval, skip 2020) and writes the fields
replay_games needs to tests/fixtures/elo_backtest_games.json.gz.

tests/test_elo_backtest_pin.py replays this fixture with the deployed Elo
defaults and asserts the recomputed Brier / pick accuracy still match the
constants published in transparency.html and METHODOLOGY.md. Re-run this
script (and re-run validate_elo.py + update the published constants) when
the eval window changes — e.g. when 2026 joins the calibration baseline.

Fails closed on partial data: fetch_games_for_range swallows per-month
ESPNAPIErrors with a logged warning and keeps going, so a transient ESPN
failure would otherwise silently freeze an incomplete sample. Any warning
from src.data.espn_api during the fetch aborts without writing.

Run from the repo root with the venv active:
    python -m scripts.freeze_elo_backtest_fixture
"""

from __future__ import annotations

import gzip
import json
import logging
import os

import src.data.espn_api as espn_api
from scripts.validate_elo import _EVAL_START, _fetch_all_games

_FIXTURE_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "tests",
        "fixtures",
        "elo_backtest_games.json.gz",
    )
)
_FIELDS = (
    "date",
    "event_id",
    "team_a",
    "team_b",
    "winner_team",
    "final_score_a",
    "final_score_b",
)


class _WarningTrap(logging.Handler):
    """Record warnings so a lossy fetch can abort the freeze."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def main() -> None:
    trap = _WarningTrap()
    espn_api.logger.addHandler(trap)
    try:
        games = _fetch_all_games()
    finally:
        espn_api.logger.removeHandler(trap)

    if trap.messages:
        print("Aborting — fetch was lossy, refusing to freeze a partial sample:")
        for msg in trap.messages:
            print(f"  {msg}")
        raise SystemExit(1)
    if not games:
        print("No games fetched — aborting.")
        raise SystemExit(1)

    games.sort(key=lambda g: (g.get("date", ""), g.get("event_id", "")))
    payload = {
        "eval_start": _EVAL_START,
        "games": [{f: g.get(f) for f in _FIELDS} for g in games],
    }
    os.makedirs(os.path.dirname(_FIXTURE_PATH), exist_ok=True)
    with gzip.open(_FIXTURE_PATH, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    n_eval = sum(1 for g in payload["games"] if g["date"] >= _EVAL_START)
    print(
        f"Wrote {len(payload['games'])} games ({n_eval} in eval window) to {_FIXTURE_PATH}"
    )


if __name__ == "__main__":
    main()
