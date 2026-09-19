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

from src.constants import GameStatus, is_live_status
from src.data.espn_api import (
    ESPNAPIError,
    fetch_games_for_range,
    fetch_live_win_probability,
    today_et,
    yesterday_et,
)

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

# Measured 2026-09-19 by driving the real scoring path over a COMPLETED 2026
# season (five plausible final seedings x the real locked field), because no
# postseason game had ever been scored and the previous band here was a guess:
#   QF G1 (0-0)        40.4 - 51.4      SF G1 (0-0)         32.8 - 37.1
#   QF G2 (1-0)        25.2 - 45.0      SF G5 (2-2)         98.89 - 98.92
#   QF G3 (1-1)        98.7 - 98.8      F  G1 (0-0)         27.4 - 30.9
#                                       F  G7 (3-3)         99.20
# The queue's "QF Game 1 should land near 45" held. What the guess missed is
# that a win-or-go-home game is structurally pinned near POSTSEASON_MAX_SWING
# and scores ~99, not ~45 — so the old (25.0, 85.0) band reported FAIL against
# correct behavior on every decisive game in the bracket, starting with a Bo3
# Game 3 in the first round. Only a floor is meaningful; the ceiling is the
# fallback sentinel below.
#
# Do not retune either of these from a single observed value — re-measure.
_POSTSEASON_FLOOR = 20.0

# The slot-matching fallback (the documented likely failure mode) returns the
# literal 100.0. A computed swing cannot reach it: _corrected_swing always
# subtracts a strictly positive noise floor, worth ~0.8 points at 10k sims.
# So test the sentinel EXACTLY. The previous `>= 99.5` threshold sat 0.30 above
# a measured Finals Game 7, and that margin IS the noise floor — it shrinks if
# the simulation count ever rises, which would turn the biggest game of the
# season into a false FAIL.
_POSTSEASON_FALLBACK = 100.0

# Game 1 of a series is never win-or-go-home — true of Bo3, Bo5 and Bo7 alike —
# so an opener has a meaningful ceiling even though a later game does not.
# Measured openers: QF 40.4-51.4, SF 32.8-37.1, F 27.4-30.9; the lowest decisive
# game measured 98.68. 75.0 sits ~23 points clear of both.
#
# This is deliberately derived from "have these two teams already played a
# postseason game", NOT from a reconstructed bracket: a second model of the
# series state in the probe could drift from the real one, which is how the
# third copy of LIVE_STATUSES silently dropped two of three live games on
# 2026-09-17. The cost is that games 2+ keep only the floor and the sentinel —
# their honest range spans ~25 to ~99 depending on series state, and guessing
# it is what this whole change exists to stop doing.
_POSTSEASON_OPENER_CEILING = 75.0

# A WNBA postseason runs about a month (2024: 09-22 to 10-20), so this covers
# it from either end when looking up which matchups have already played.
_POSTSEASON_LOOKBACK_DAYS = 40

# How far back to look for the newest stored snapshot. Covers a missed daily run
# or two without letting a long outage silently diff against ancient standings.
_BASELINE_LOOKBACK_DAYS = 4


# Check names, shared so a rename cannot silently desync the generic-SKIP
# branch in checks_for_night from the check that owns the name.
_NAME_SEED_MOVEMENT = "seed matrix moved vs the 6 AM snapshot"
_NAME_COLUMN_SUPPRESSION = "Playoffs column suppression"


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
    if all(s == GameStatus.FINAL for s in statuses):
        return "settled"
    return "pregame"


def probed_games(games: list[dict]) -> list[dict]:
    """The games that can actually produce a live override.

    A scheduled game contributes no override, so it must not drag the snapshot
    baseline forward onto a date whose 6 AM run has not happened yet.

    Liveness comes from the shared is_live_status, NOT a hand-rolled
    STATUS_IN_PROGRESS check: ESPN reports THREE in-progress states, and the
    real 2026-09-17 slate carried STATUS_HALFTIME and STATUS_END_PERIOD
    alongside it. Matching only STATUS_IN_PROGRESS silently dropped two of the
    three live games from the seed-movement check.
    """
    return [
        g
        for g in games
        if is_live_status(g.get("status")) or g.get("status") == GameStatus.FINAL
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


def live_wp_sample_counts(games: list[dict]) -> dict[str, int]:
    """{event_id: usable win-probability samples} for the in-progress games.

    Calls production's own fetch_live_win_probability, so the probe sees exactly
    what the overlay sees — including samples our sanitizer drops. A count of 0
    everywhere means the overlay had nothing to condition on.

    Limitation, stated rather than engineered around: this does NOT separate
    "ESPN sent no samples" from "ESPN sent samples our parser rejected". Both
    leave the overlay with no input, so both are the same verdict here. If a
    zero is surprising, check the raw array:
        .../wnba/summary?event=<id> -> winprobability
    """
    counts: dict[str, int] = {}
    for g in games:
        if not is_live_status(g.get("status")):
            continue
        event_id = g.get("event_id") or ""
        try:
            payload = fetch_live_win_probability(event_id)
            counts[event_id] = len((payload or {}).get("plays") or [])
        except ESPNAPIError:
            counts[event_id] = 0
    return counts


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


def _overlay_engaged(odds: list[dict]) -> bool:
    """True when the served payload is the live overlay, not the stored snapshot.

    Three checks gate on this: a non-live payload IS (a fallback to) the
    snapshot, so diffing or re-reading it measures nothing about the live path.
    """
    return bool(odds) and bool(odds[0].get("live"))


def check_live_flags(
    odds: list[dict], expect_state: str, wp_available: bool = True
) -> CheckResult:
    """`wp_available` decides whether a stood-down overlay is a defect.

    The overlay conditions on ESPN's per-game win probability. On 2026-09-17
    ESPN published `winprobability: []` for every in-progress game (while
    carrying 116-193 plays; a completed game carried 405 samples), so the
    overlay correctly declined to publish odds it could not condition on.
    Reporting FAIL there blames us for an upstream gap. With no input there is
    nothing to judge, which is this probe's definition of SKIP.

    The distinction is what keeps that SKIP safe: if ESPN DID supply win
    probability and the overlay still did not engage, that is our bug, and it
    stays a FAIL.
    """
    name = f"live flags (expect live_state={expect_state!r})"
    if not odds:
        return CheckResult(name, SKIP, "no odds rows returned")
    row = odds[0]
    got_live, got_state = row.get("live"), row.get("live_state")
    if not got_live:
        if not wp_available:
            return CheckResult(
                name,
                SKIP,
                "ESPN published no usable live win probability for tonight's "
                "games, so the overlay is CORRECTLY standing down rather than "
                "publishing odds it cannot condition on. Not a defect.",
            )
        return CheckResult(
            name,
            FAIL,
            f"overlay did not engage: live={got_live!r} — serving the snapshot, "
            "though ESPN DID supply win probability",
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
    # Two identical reads of the STORED snapshot are trivially identical and
    # say nothing about the live path — the same vacuous green this probe
    # refuses everywhere else.
    if not _overlay_engaged(first):
        return CheckResult(name, SKIP, "overlay not engaged; nothing to compare")
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
    name = _NAME_SEED_MOVEMENT
    # A non-live payload IS (a fallback to) the snapshot, so zero movement is
    # the expected result, not a finding. Judging it would double-report the
    # stood-down overlay that check_live_flags has already classified.
    if not _overlay_engaged(live):
        return CheckResult(name, SKIP, "overlay not engaged; nothing to diff")
    if not snapshot:
        return CheckResult(name, SKIP, "no stored snapshot to compare against")
    if not playing:
        return CheckResult(name, SKIP, "no teams playing")
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
    name = _NAME_COLUMN_SUPPRESSION
    if not odds:
        return CheckResult(name, SKIP, "no odds rows returned")
    dead = playoffs_column_is_dead(odds)
    live = _overlay_engaged(odds)
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


def played_postseason_pairs(history: list[dict]) -> set[frozenset[str]]:
    """Matchups with at least one COMPLETED postseason game behind them.

    Only used to separate a series opener from a later game, so it needs no
    bracket, no round identification and no game count — just whether these
    two teams have met in the postseason yet.
    """
    return {
        frozenset({g["team_a"], g["team_b"]})
        for g in history
        if g.get("season_type") == 3 and g.get("winner_team")
    }


def check_postseason_importance(
    games: list[dict], played_pairs: set[frozenset[str]] | None = None
) -> CheckResult:
    """Judge tonight's postseason importance scores against measured ranges.

    `played_pairs` is the set of matchups that have already played a
    postseason game this year, used only to tell a series opener from a later
    game. None means the history could not be fetched, which disables the
    opener ceiling rather than failing every decisive game.
    """
    name = "postseason importance magnitude"
    scored = [g for g in games if g.get("importance_score") is not None]
    if not scored:
        return CheckResult(name, SKIP, "no postseason game carries an importance score")
    bad = []
    for g in scored:
        v = g["importance_score"]
        pair = frozenset({g.get("team_a", ""), g.get("team_b", "")})
        if v == _POSTSEASON_FALLBACK:
            bad.append(
                f"{g['team_a_abbr']}v{g['team_b_abbr']}={v:.1f} (slot-match fallback)"
            )
        elif v < _POSTSEASON_FLOOR:
            bad.append(
                f"{g['team_a_abbr']}v{g['team_b_abbr']}={v:.1f} "
                f"(below {_POSTSEASON_FLOOR:.0f} — postseason path may not have engaged)"
            )
        elif (
            played_pairs is not None
            and pair not in played_pairs
            and v > _POSTSEASON_OPENER_CEILING
        ):
            bad.append(
                f"{g['team_a_abbr']}v{g['team_b_abbr']}={v:.1f} "
                f"(series opener above {_POSTSEASON_OPENER_CEILING:.0f}; an opener "
                "is never win-or-go-home)"
            )
    listing = ", ".join(
        f"{g['team_a_abbr']}v{g['team_b_abbr']}={g['importance_score']:.1f}"
        for g in scored
    )
    if bad:
        return CheckResult(name, FAIL, "; ".join(bad))
    if played_pairs is None:
        listing += "  [opener ceiling not applied — no postseason history]"
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
    wp_available: bool = True,
    played_pairs: set[frozenset[str]] | None = None,
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
            CheckResult(_NAME_SEED_MOVEMENT, SKIP, why),
            CheckResult(_NAME_COLUMN_SUPPRESSION, SKIP, why),
        ]
    if night == "postseason":
        return [
            check_live_disabled(odds),
            check_postseason_importance(games or [], played_pairs=played_pairs),
        ]
    expect = "live" if night == "live" else "settled"
    return [
        check_live_flags(odds, expect_state=expect, wp_available=wp_available),
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

    # Does the overlay have anything to condition on? Decides whether a
    # stood-down overlay is our defect or an upstream gap.
    wp_counts = live_wp_sample_counts(games) if night == "live" else {}
    wp_available = any(n > 0 for n in wp_counts.values()) if wp_counts else True

    upcoming = _get_json(base, "/api/games/upcoming") if night == "postseason" else []
    today_games = [g for g in upcoming if g.get("date") == today]

    # Series history, for the opener ceiling only. A postseason runs about a
    # month, so look back far enough to cover it from either end. On failure
    # stay None: that drops the ceiling rather than failing every decisive game.
    played_pairs = None
    if night == "postseason":
        try:
            played_pairs = played_postseason_pairs(
                fetch_games_for_range(
                    date_cls.fromisoformat(today) - timedelta(days=_POSTSEASON_LOOKBACK_DAYS),
                    date_cls.fromisoformat(today),
                )
            )
        except ESPNAPIError as e:
            print(f"  (postseason history unavailable: {e})")

    print(f"GAME NIGHT PROBE  {today}  ({base})")
    print(f"  night: {night}  ({len(games)} game(s) on the yesterday-today window)")
    for g in games:
        print(
            f"    {g['team_a']} v {g['team_b']}  {g['status']}  type={g['season_type']}"
        )
    if night in ("live", "settled"):
        print(f"  baseline snapshot: {base_date or 'NONE FOUND'}")
    if wp_counts:
        shown = "  ".join(f"{gid}={n}" for gid, n in sorted(wp_counts.items()))
        print(f"  ESPN live win-prob samples: {shown}")

    night_results = checks_for_night(
        night, odds, snapshot, playing, games=today_games,
        wp_available=wp_available, played_pairs=played_pairs,
    )

    # Repeatability needs a second sample. Past the TTL the server runs a fresh
    # Monte Carlo; that is a determinism test only on a settled slate, where the
    # inputs can no longer move.
    if night in ("live", "settled") and odds:
        frozen = night == "settled"
        # Sample BOTH ends here rather than reusing `odds`: the slate fetch,
        # the baseline search and the per-game ESPN win-prob calls above take
        # long enough to push the gap past the 15s TTL, which silently turned
        # the within-TTL cache check into an uncontrolled one (observed
        # 2026-09-17: it reported a difference that the TTL should have hidden).
        first = _get_json(base, "/api/playoff-odds")
        time.sleep(_CACHE_TTL_S + 2 if frozen else 3)
        second = _get_json(base, "/api/playoff-odds")
        night_results.append(check_repeatability(first, second, frozen=frozen))

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
