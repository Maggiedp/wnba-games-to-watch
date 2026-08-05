"""Seeded, ESPN-stubbed local server for the browser smoke walk.

Run: python -m tests.browser.smoke_server --port 8123 --db /path/to/smoke.db

Never touches the network: the two ESPN entry points bound in src.api.app
(fetch_today_game_statuses, fetch_live_win_probability) are replaced with
deterministic stubs before uvicorn starts — statuses come back as an empty
slate ({} → no client polling) and live WP raises ESPNNotFoundError (detail
pages render their no-chart fallback).
"""

import argparse
import json
import os
from datetime import date, timedelta

# Detail pages the browser walk visits — the seed guarantees these espn_ids
# exist. Mirrored as literals in tests/browser/overflow.test.js.
UPCOMING_DETAIL_ID = "9990001"
COMPLETED_DETAIL_ID = "9980001"

_TEAMS = [
    ("Atlanta Dream", "ATL"),
    ("Chicago Sky", "CHI"),
    ("Connecticut Sun", "CONN"),
    ("Dallas Wings", "DAL"),
    ("Golden State Valkyries", "GSV"),
    ("Indiana Fever", "IND"),
    ("Las Vegas Aces", "LV"),
    ("Los Angeles Sparks", "LA"),
    ("Minnesota Lynx", "MIN"),
    ("New York Liberty", "NY"),
    ("Phoenix Mercury", "PHX"),
    ("Portland Fire", "POR"),
    ("Seattle Storm", "SEA"),
    ("Toronto Tempo", "TOR"),
    ("Washington Mystics", "WSH"),
]

_BROADCASTERS = ["ESPN", "CBS", "ION", ""]


def _curve() -> list:
    """A 21-point zig-zag WP curve (upsert_game_shape JSON-encodes it)."""
    return [
        [i * 120, round(0.5 + (0.35 if i % 2 else -0.25) * min(i / 10, 1.0), 3)]
        for i in range(21)
    ]


def seed(session) -> None:
    """Populate every table the walked pages read. Dates are relative to
    today_et() so the walk never goes stale. Commits."""
    from src.data.espn_api import today_et
    from src.db.queries import (
        upsert_game_shape,
        upsert_playoff_probability,
        upsert_shot_league_avg,
        upsert_shot_making,
        upsert_shots,
        upsert_team_style,
    )
    from src.db.schema import (
        DailyRanking,
        EloHistory,
        Game,
        Team,
    )

    today = date.fromisoformat(today_et())
    season = today.year

    teams = [Team(name=n, abbreviation=a, logo_url="") for n, a in _TEAMS]
    session.add_all(teams)
    session.flush()  # assign ids

    def pair(k):
        return teams[(2 * k) % len(teams)], teams[(2 * k + 1) % len(teams)]

    # --- Upcoming: two games/night for +1..+7 days, plus two far-out games
    # so the 30-day/All presets have content. k=0 is the upcoming detail
    # target (gets an importance_detail movers payload).
    for k in range(16):
        offset = 1 + k // 2 if k < 14 else (12 if k == 14 else 25)
        gdate = (today + timedelta(days=offset)).isoformat()
        a, b = pair(k)
        espn_id = UPCOMING_DETAIL_ID if k == 0 else f"999{k + 1:04d}"
        session.add(
            Game(
                team_a_id=a.id,
                team_b_id=b.id,
                date=gdate,
                time="7:00 PM",
                time_utc=f"{gdate}T23:00:00Z",
                broadcaster=_BROADCASTERS[k % 4],
                espn_id=espn_id,
                season_type=2,
            )
        )
        detail = None
        if k == 0:
            detail = json.dumps(
                {
                    "metric": "playoffs",
                    "if_a_team": a.name,
                    "if_b_team": b.name,
                    "movers": [
                        {"team": a.name, "if_a": 0.91, "if_b": 0.84},
                        {"team": b.name, "if_a": 0.55, "if_b": 0.63},
                    ],
                }
            )
        session.add(
            DailyRanking(
                date=gdate,
                team_a_id=a.id,
                team_b_id=b.id,
                quality_score=45.0 + 3 * (k % 10),
                importance_score=0.2 + 0.04 * (k % 10),
                overall_score=40.0 + 4 * (k % 12),
                broadcaster=_BROADCASTERS[k % 4],
                win_prob_a=0.35 + 0.03 * (k % 10),
                importance_detail=detail,
            )
        )

    # --- Completed: two games/night for -1..-14 days, floored at Jan 1 so
    # the window never crosses into the prior year (the completed/calibration
    # queries are season-year-scoped; without the floor, early-January CI runs
    # would drop below the 25-game calibration gate). k=0 is the completed
    # detail target and reuses pair(0), so the upcoming detail page gets a
    # head-to-head row. First 8 get game_shapes rows (minis + /replay).
    for k in range(28):
        gdate = max(
            today - timedelta(days=1 + k // 2), date(today.year, 1, 1)
        ).isoformat()
        a, b = pair(k)
        espn_id = COMPLETED_DETAIL_ID if k == 0 else f"998{k + 1:04d}"
        home_score = 78 + (k % 15)
        away_score = 70 + ((k * 3) % 20)
        winner = a if home_score > away_score else b
        excitement = round(2.0 + (k % 8), 1)  # spans Close (>=4) and Thriller (>=7.5)
        session.add(
            Game(
                team_a_id=a.id,
                team_b_id=b.id,
                date=gdate,
                time="7:00 PM",
                time_utc=f"{gdate}T23:00:00Z",
                broadcaster=_BROADCASTERS[k % 4],
                espn_id=espn_id,
                season_type=2,
                winner_id=winner.id,
                final_score_a=home_score,
                final_score_b=away_score,
                excitement_index=excitement,
            )
        )
        session.add(
            DailyRanking(
                date=gdate,
                team_a_id=a.id,
                team_b_id=b.id,
                quality_score=50.0,
                importance_score=0.3,
                overall_score=55.0,
                broadcaster=_BROADCASTERS[k % 4],
                win_prob_a=0.3 + 0.015 * k,  # spread for the calibration buckets
            )
        )
        if k < 8:
            upsert_game_shape(
                session,
                espn_id=espn_id,
                season=season,
                date=gdate,
                home_team=a.name,
                away_team=b.name,
                home_abbr=a.abbreviation,
                away_abbr=b.abbreviation,
                home_score=home_score,
                away_score=away_score,
                winner="home" if winner is a else "away",
                excitement=excitement,
                tension=0.4 + 0.05 * k,
                comeback=0.2,
                lead_changes=3 + k,
                winner_low_wp=0.18,
                curve=_curve(),
            )

    # --- Playoff odds for today: every team non-null seed_distribution
    # (seedsViewAvailable requires EVERY row non-null; eliminated teams get
    # a valid empty {} — exercises both cell kinds in the Seeds view).
    for i, t in enumerate(teams):
        make = max(0.0, round(1.0 - i * 0.075, 3))
        seed_dist = {str(s): round(make / 8, 3) for s in range(1, 9)} if make else {}
        upsert_playoff_probability(
            session,
            date=today.isoformat(),
            team_id=t.id,
            probability=make,
            reach_semis_prob=make * 0.5,
            reach_finals_prob=make * 0.25,
            win_championship_prob=make * 0.12,
            seed_distribution=json.dumps(seed_dist),
        )

    # --- Elo history: 10 weekly points per team (for /rankings).
    for i, t in enumerate(teams):
        for w in range(10):
            session.add(
                EloHistory(
                    team_id=t.id,
                    date=(today - timedelta(days=7 * (9 - w))).isoformat(),
                    rating=1420.0 + 12 * i + 5 * w * ((-1) ** i),
                )
            )

    # --- Team style: one row per team (for /style).
    for i, t in enumerate(teams):
        upsert_team_style(
            session,
            season=season,
            team_id=t.id,
            pace=78.0 + 0.5 * i,
            three_pa_rate=0.24 + 0.01 * i,
            ft_rate=0.18 + 0.008 * i,
            oreb_pct=0.20 + 0.007 * i,
            assist_rate=0.55 + 0.01 * i,
            def_pressure=0.14 + 0.006 * i,
            opp_3pa_rate=0.40 - 0.01 * i,
            games_played=20,
            commit=False,
        )

    # --- Shot-making: one leaderboard row per team (for /shot-making). Varied
    # points_added (+ and -) and diet mixes so the walk overflow-checks the
    # populated table, legend, and diet bars at phone widths.
    _diets = [
        {"rim": 0.5, "mid": 0.3, "three": 0.2},
        {"three": 0.5, "mid": 0.3, "rim": 0.2},
        {"rim": 0.6, "mid": 0.3, "floater": 0.1},
        {"three": 0.4, "mid": 0.34, "rim": 0.2, "floater": 0.06},
    ]
    for i, t in enumerate(teams):
        fga = 300 + 8 * i
        made = int(fga * (0.42 + 0.004 * i))
        # xPPS multiplier varies by player (rather than a flat 1.05) so the
        # "shot diet" bridge segment for shooter 0 — the rank-1 player, and the
        # only one the browser walk actually renders (top leaderboard row's
        # expand panel + /player/smoke-shooter-0) — is a sizeable LEFT-extending
        # gap vs the seeded avg_xpps=1.027 anchor, not a uniform small
        # right-extending one. points_added still spans positive/negative below.
        xpps_mult = 0.90 + 0.01 * i
        expected = fga * xpps_mult
        actual = expected + (7.0 - i) * 3.0  # spans positive and negative
        upsert_shot_making(
            session,
            season,
            f"smoke-shooter-{i}",
            athlete_name=f"Shooter {t.abbreviation} {i}",
            team_id=str(t.id),
            team_abbr=t.abbreviation,
            fga=fga,
            made=made,
            actual_pts=round(actual, 2),
            expected_pts=round(expected, 2),
            points_added=round(actual - expected, 2),
            points_added_per_100=round((actual - expected) / fga * 100, 2),
            actual_pps=round(actual / fga, 3),
            expected_pps=round(expected / fga, 3),
            diet=json.dumps(_diets[i % len(_diets)]),
            commit=False,
        )

    # Raw shots for one seeded shooter so the /shot-making expand panel has a
    # chart + zones to render in the browser walk.
    first = teams[0]
    _panel_shots = [
        {
            "play_id": f"pw{j}",
            "athlete_id": "smoke-shooter-0",
            "athlete_name": f"Shooter {first.abbreviation} 0",
            "team_id": str(first.id),
            "team_abbr": first.abbreviation,
            "shot_type": "Jump Shot" if j % 2 else "Layup Shot",
            "distance_ft": 24.0 if j % 2 else 3.0,
            "coord_x": 2 + (j * 7) % 46,
            "coord_y": (j * 5) % 26,
            "points": (3 if j % 2 else 2) if j % 3 else 0,
            "point_value": 3 if j % 2 else 2,
            "made": bool(j % 3),
        }
        for j in range(120)
    ]
    upsert_shots(session, "smoke-shot-game", season, _panel_shots, commit=False)

    # All-league anchors so the bridge actually renders in the walk. Without this
    # row both surfaces take the graceful no-bridge path and the walk silently
    # stops covering it.
    upsert_shot_league_avg(
        session, season, avg_xpps=1.027, avg_pps=1.037, fga=25790, commit=False
    )

    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--db", required=True, help="path for the seeded sqlite file")
    args = parser.parse_args()

    # Must precede any src.api.app import — it calls init_db() at import time.
    os.environ["DATABASE_URL"] = f"sqlite:///{args.db}"

    from src.db import schema

    schema.init_db()
    seed(schema.get_session())

    import src.api.app as app_module
    from src.data.espn_api import ESPNNotFoundError

    app_module.fetch_today_game_statuses = lambda game_date: {}

    def _no_live_wp(espn_id, timeout=10):
        raise ESPNNotFoundError(f"smoke stub: no live WP for {espn_id}")

    app_module.fetch_live_win_probability = _no_live_wp

    import uvicorn

    uvicorn.run(app_module.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
