#!/usr/bin/env python3
"""Probe production on a game night and check the live paths behaved.

Two features ship with code that has never executed against a real game:
the live playoff-odds overlay (PR #136, regular season only) and postseason
importance (PR #132). This is the probe for both. It works out what kind of
night it is from ESPN's slate, then runs only the checks that apply.

Read-only: it writes nothing and hits production over HTTP.

A check with no data to judge reports SKIP, never PASS. An off-day run that
printed all-green would be indistinguishable from a working live path, which
is the whole failure mode this exists to rule out.

Run from the repo root with the venv active:
    python -m scripts.verify_game_night [--base-url URL] [--no-logs]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from datetime import date as date_cls, timedelta
from typing import Iterable, NamedTuple

import requests

from src.api.routes import is_live_status
from src.data.espn_api import fetch_games_for_range, today_et, yesterday_et

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"

_BASE_URL = "https://wumbers.com"
_PROJECT = "wnba-games-to-watch"
_HTTP_TIMEOUT = 30

# A 10k Monte Carlo carries ~±0.5pp of jitter run to run. The live overlay and
# the 6 AM snapshot are separate runs, so a cell under 1pp apart could be noise
# rather than tonight's game actually moving a seed.
_SEED_MOVE_EPS = 0.01

# The odds endpoint caches behind a 15s TTL, so two calls closer than that share
# one cached object and prove only the cache. Spacing past it forces a second
# Monte Carlo — which is a determinism test only when the inputs cannot move,
# i.e. after the last final.
_CACHE_TTL_S = 15

# QF Game 1 importance should land near 45. 100 is the slot-matching fallback
# (the documented likely failure mode); a regular-season-sized number means the
# postseason path never engaged at all.
_POSTSEASON_BAND = (25.0, 85.0)

# How far back to look for the newest stored snapshot. Covers a missed daily run
# or two without letting a long outage silently diff against ancient standings.
_BASELINE_LOOKBACK_DAYS = 4


class CheckResult(NamedTuple):
    name: str
    status: str
    detail: str


# --- pure decision logic --------------------------------------------------


def classify_night(games: list[dict]) -> str:
    """One of: off, postseason, live, settled, pregame.

    Postseason is tested BEFORE in-progress on purpose: live mode disables
    itself on a season_type == 3 slate, so a postseason night that classified
    as "live" would report FAIL against correct behavior.
    """
    if not games:
        return "off"
    if any(g.get("season_type") == 3 for g in games):
        return "postseason"
    if any(is_live_status(g.get("status")) for g in games):
        return "live"
    statuses = [g.get("status") for g in games]
    if all(s == "STATUS_FINAL" for s in statuses):
        return "settled"
    return "pregame"


def probed_games(games: list[dict]) -> list[dict]:
    """The games that can actually produce a live override.

    A scheduled game contributes no override, so it must not drag the snapshot
    baseline forward onto a date whose 6 AM run has not happened yet.

    Liveness comes from production's own is_live_status, NOT a hand-rolled
    STATUS_IN_PROGRESS check: ESPN reports THREE in-progress states, and the
    real 2026-09-17 slate carried STATUS_HALFTIME and STATUS_END_PERIOD
    alongside it. Matching only STATUS_IN_PROGRESS silently dropped two of the
    three live games from the seed-movement check.
    """
    return [
        g
        for g in games
        if is_live_status(g.get("status")) or g.get("status") == "STATUS_FINAL"
    ]


def candidate_baseline_dates(today: str, back: int = _BASELINE_LOOKBACK_DAYS) -> list[str]:
    """Dates to try for the baseline snapshot, newest first, bounded at `today`.

    The overlay perturbs whatever the LAST daily run stored, so the baseline is
    the newest snapshot that exists — before 6 AM that is yesterday's, after it
    today's. Two earlier attempts got this wrong in opposite directions: keying
    on `today` SKIPPED the check after midnight (today's snapshot does not exist
    yet), and keying on the earliest probed game's date picks a STALE day once
    the window holds both last night's finals and tonight's live games.

    Bounded at today rather than clamped afterwards: a future-dated row must not
    become the baseline, and clamping a newest-first search can land on an empty
    date instead of the newest real one.
    """
    d = date_cls.fromisoformat(today)
    return [(d - timedelta(days=i)).isoformat() for i in range(back)]


def daily_run_consumed(baseline: str | None, probed: list[dict]) -> bool:
    """True when the 6 AM run has already folded these results into the snapshot.

    get_upcoming_games filters on `winner_id IS NULL`, so once the daily run
    records last night's winners those games leave remaining_games, no override
    can attach, and the overlay correctly stands down. Demanding live flags then
    would FAIL against correct behavior.

    The proof is the newest snapshot being dated AFTER the last probed game.
    store_playoff_probabilities keys every snapshot to today_et() at write time
    (daily_update.py), so a snapshot dated D exists only once the clock reached D
    and that run completed — which is exactly what consumed those results.
    """
    if not baseline or not probed:
        return False
    return baseline > max(g["date"] for g in probed)


def playoffs_column_is_dead(odds: list[dict]) -> bool:
    """Mirror of playoffsColumnIsDead() in playoff_odds_helpers.js.

    The page hides the Playoffs column only when every team is mathematically
    in (1.0) or out (0.0) — never merely because the overlay is live.
    """
    return bool(odds) and all(t.get("make_playoffs_prob") in (0, 1) for t in odds)


def seed_movement(
    live: list[dict], snapshot: list[dict], team_names: Iterable[str]
) -> dict[str, list[tuple[str, float, float]]]:
    """Per-team seed cells that moved more than jitter, for the named teams."""
    wanted = set(team_names)
    before = {t["team"]: t.get("seed_distribution") or {} for t in snapshot}
    moved: dict[str, list[tuple[str, float, float]]] = {}
    for row in live:
        name = row["team"]
        if name not in wanted or name not in before:
            continue
        old, new = before[name], row.get("seed_distribution") or {}
        cells = []
        for seed in sorted(set(old) | set(new), key=lambda s: int(s)):
            o, n = old.get(seed, 0.0), new.get(seed, 0.0)
            if abs(n - o) > _SEED_MOVE_EPS:
                cells.append((seed, o, n))
        if cells:
            moved[name] = cells
    return moved


# --- checks ---------------------------------------------------------------


def check_live_flags(odds: list[dict], expect_state: str) -> CheckResult:
    name = f"live flags (expect live_state={expect_state!r})"
    if not odds:
        return CheckResult(name, SKIP, "no odds rows returned")
    row = odds[0]
    got_live, got_state = row.get("live"), row.get("live_state")
    if not got_live:
        return CheckResult(
            name,
            FAIL,
            f"overlay did not engage: live={got_live!r} — serving the snapshot",
        )
    if got_state != expect_state:
        return CheckResult(name, FAIL, f"live=True but live_state={got_state!r}")
    return CheckResult(name, PASS, f"live=True, live_state={got_state!r}")


def check_live_disabled(odds: list[dict]) -> CheckResult:
    """Postseason: the overlay must stand down rather than publish a number
    built from remaining_games when the bracket runs through bracket_state."""
    name = "live mode disabled (postseason)"
    if not odds:
        return CheckResult(name, SKIP, "no odds rows returned")
    if odds[0].get("live"):
        return CheckResult(
            name, FAIL, "overlay engaged on a postseason slate — it must stand down"
        )
    return CheckResult(name, PASS, "live=False, stored snapshot served")


def check_repeatability(
    first: list[dict], second: list[dict], frozen: bool
) -> CheckResult:
    """Identical payloads across two calls.

    `frozen` says whether the inputs could have moved between them. After the
    last final they cannot, so a difference is a real determinism bug. During a
    live game the win probability moves, so a difference is legitimate and this
    degrades to reporting what changed.
    """
    name = "seed determinism" if frozen else "cache coherence"
    if not first or not second:
        return CheckResult(name, SKIP, "no odds rows to compare")
    if first == second:
        return CheckResult(
            name,
            PASS,
            "two calls identical"
            + (" across a fresh Monte Carlo" if frozen else " within the TTL"),
        )
    changed = sorted(a["team"] for a, b in zip(first, second) if a != b) or [
        "row order or team set"
    ]
    if frozen:
        return CheckResult(
            name, FAIL, f"inputs were frozen but the answer moved: {', '.join(changed)}"
        )
    return CheckResult(
        name, INFO, f"differs (live WP moved, expected): {', '.join(changed)}"
    )


def check_seed_movement(
    live: list[dict], snapshot: list[dict], playing: set[str]
) -> CheckResult:
    name = "seed matrix moved vs the 6 AM snapshot"
    if not snapshot:
        return CheckResult(name, SKIP, "no stored snapshot to compare against")
    if not playing:
        return CheckResult(name, SKIP, "no teams playing")
    if not live:
        return CheckResult(name, SKIP, "no live rows returned")
    moved = seed_movement(live, snapshot, playing)
    if not moved:
        return CheckResult(
            name,
            FAIL,
            f"no seed cell moved >{_SEED_MOVE_EPS:.0%} for {', '.join(sorted(playing))}",
        )
    lines = []
    for team, cells in sorted(moved.items()):
        shifts = "  ".join(f"{s}:{o:.2f}->{n:.2f}" for s, o, n in cells)
        lines.append(f"{team}  {shifts}")
    return CheckResult(name, PASS, "; ".join(lines))


def check_column_suppression(odds: list[dict]) -> CheckResult:
    name = "Playoffs column suppression"
    if not odds:
        return CheckResult(name, SKIP, "no odds rows returned")
    dead = playoffs_column_is_dead(odds)
    live = bool(odds[0].get("live"))
    if dead and live:
        return CheckResult(
            name,
            PASS,
            "field clinched and overlay live — column should be ABSENT (data check "
            "only; confirm by eye at /playoff-odds)",
        )
    if dead:
        return CheckResult(
            name, INFO, "field clinched but overlay not live — column stays visible"
        )
    return CheckResult(
        name, PASS, "field not clinched — column should be PRESENT (mid-race branch)"
    )


def check_postseason_importance(games: list[dict]) -> CheckResult:
    name = "postseason importance magnitude"
    scored = [g for g in games if g.get("importance_score") is not None]
    if not scored:
        return CheckResult(name, SKIP, "no postseason game carries an importance score")
    lo, hi = _POSTSEASON_BAND
    bad = []
    for g in scored:
        v = g["importance_score"]
        if v >= 99.5:
            bad.append(
                f"{g['team_a_abbr']}v{g['team_b_abbr']}={v:.1f} (slot-match fallback)"
            )
        elif not lo <= v <= hi:
            bad.append(
                f"{g['team_a_abbr']}v{g['team_b_abbr']}={v:.1f} (outside {lo:.0f}-{hi:.0f})"
            )
    listing = ", ".join(
        f"{g['team_a_abbr']}v{g['team_b_abbr']}={g['importance_score']:.1f}"
        for g in scored
    )
    if bad:
        return CheckResult(name, FAIL, "; ".join(bad))
    return CheckResult(name, PASS, listing)


def check_logs(hours: int = 12) -> CheckResult:
    name = f"no live-odds: warnings in the last {hours}h"
    if not shutil.which("gcloud"):
        return CheckResult(name, SKIP, "gcloud not on PATH")
    filt = (
        'resource.type="cloud_run_revision" '
        'AND (textPayload:"live-odds:" OR jsonPayload.message:"live-odds:")'
    )
    try:
        out = subprocess.run(
            [
                "gcloud",
                "logging",
                "read",
                filt,
                f"--freshness={hours}h",
                "--limit=20",
                "--format=value(textPayload,jsonPayload.message)",
                f"--project={_PROJECT}",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, SKIP, "gcloud logging read timed out")
    if out.returncode != 0:
        return CheckResult(name, SKIP, f"gcloud failed: {out.stderr.strip()[:120]}")
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    if lines:
        return CheckResult(
            name, FAIL, f"{len(lines)} warning(s); first: {lines[0][:140]}"
        )
    return CheckResult(name, PASS, "none")


def checks_for_night(
    night: str,
    odds: list[dict],
    snapshot: list[dict],
    playing: set[str],
    games: list[dict] | None = None,
) -> list[CheckResult]:
    """The data checks that apply to this kind of night.

    Off and pregame nights return SKIPs rather than an empty list, so a run
    that proved nothing says so out loud instead of reading as a clean pass.
    """
    if night in ("off", "pregame", "consumed"):
        why = {
            "off": "no games today",
            "pregame": "no game has tipped yet",
            "consumed": (
                "the 6 AM run already folded these results in — the overlay is "
                "CORRECTLY standing down, so there is nothing live to observe. "
                "Run during the game, or before the next 6 AM run."
            ),
        }[night]
        return [
            CheckResult("live flags", SKIP, why),
            CheckResult("seed matrix moved vs the 6 AM snapshot", SKIP, why),
            CheckResult("Playoffs column suppression", SKIP, why),
        ]
    if night == "postseason":
        return [
            check_live_disabled(odds),
            check_postseason_importance(games or []),
        ]
    expect = "live" if night == "live" else "settled"
    return [
        check_live_flags(odds, expect_state=expect),
        check_seed_movement(odds, snapshot, playing),
        check_column_suppression(odds),
    ]


# --- production probe -----------------------------------------------------


def _get_json(base: str, path: str) -> list[dict]:
    r = requests.get(f"{base}{path}", timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=_BASE_URL)
    ap.add_argument("--no-logs", action="store_true", help="skip the gcloud log read")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    today = today_et()
    # Match the overlay's own window: a 10pm-ET tip is still in progress after
    # midnight and its Game.date is yesterday, so a today-floored slate would
    # miss exactly the late game worth probing.
    games = fetch_games_for_range(
        date_cls.fromisoformat(yesterday_et()), date_cls.fromisoformat(today)
    )
    night = classify_night(games)
    probed = probed_games(games)
    playing = {g["team_a"] for g in probed} | {g["team_b"] for g in probed}

    odds = _get_json(base, "/api/playoff-odds")

    # Baseline: the NEWEST stored snapshot, which is the state the overlay is
    # perturbing. Searching newest-first from today (never past it) handles both
    # the post-midnight case, where today's snapshot does not exist yet, and the
    # evening case, where the window also holds last night's finals.
    base_date, snapshot = None, []
    for cand in candidate_baseline_dates(today):
        rows = _get_json(base, f"/api/playoff-odds?date={cand}")
        if rows:
            base_date, snapshot = cand, rows
            break

    if night in ("live", "settled") and daily_run_consumed(base_date, probed):
        night = "consumed"

    upcoming = _get_json(base, "/api/games/upcoming") if night == "postseason" else []
    today_games = [g for g in upcoming if g.get("date") == today]

    print(f"GAME NIGHT PROBE  {today}  ({base})")
    print(f"  night: {night}  ({len(games)} game(s) on the yesterday-today window)")
    for g in games:
        print(
            f"    {g['team_a']} v {g['team_b']}  {g['status']}  type={g['season_type']}"
        )
    if night in ("live", "settled"):
        print(f"  baseline snapshot: {base_date or 'NONE FOUND'}")

    night_results = checks_for_night(night, odds, snapshot, playing, games=today_games)

    # Repeatability needs a second sample. Past the TTL the server runs a fresh
    # Monte Carlo; that is a determinism test only on a settled slate, where the
    # inputs can no longer move.
    if night in ("live", "settled") and odds:
        frozen = night == "settled"
        time.sleep(_CACHE_TTL_S + 2 if frozen else 3)
        second = _get_json(base, "/api/playoff-odds")
        night_results.append(check_repeatability(odds, second, frozen=frozen))

    # The log read is auxiliary and deliberately kept OUT of the verdict: on an
    # off day it passes for want of anything to warn about, and letting that
    # stand in for the night checks is exactly the vacuous green this probe
    # exists to refuse.
    aux_results = [] if args.no_logs else [check_logs()]
    results = night_results + aux_results

    print()
    for r in results:
        print(f"  [{r.status}] {r.name}")
        if r.detail:
            print(f"         {r.detail}")

    if night == "postseason":
        print(
            "\n  By eye: open a QF Game 1 detail page and confirm exactly TWO teams at stake."
        )
    elif night in ("live", "settled"):
        print(
            f"\n  By eye: {base}/playoff-odds — check the freshness marker and, after the"
        )
        print("         last final, that polling stops.")

    failed = [r for r in results if r.status == FAIL]
    skipped = [r for r in night_results if r.status == SKIP]
    print()
    if failed:
        print(f"  {len(failed)} FAILED")
        return 1
    if not any(r.status == PASS for r in night_results):
        print(
            f"  Nothing verified — {len(skipped)} night check(s) skipped on a "
            f"{night!r} night. This run proves nothing about the live path."
        )
        return 0
    print(f"  OK ({len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
