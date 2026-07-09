from unittest.mock import patch

from src.data.espn_api import fetch_team_style_stats


def _cat(name, names):
    return {"name": name, "names": names}


def _team_cat(name, split_id, values):
    return {"name": name, "splitId": split_id, "values": values}


# Offensive stat order used by both the label schema and each team's values.
_OFF = [
    "avgFieldGoalsMade",
    "avgFieldGoalsAttempted",
    "avgThreePointFieldGoalsAttempted",
    "avgFreeThrowsAttempted",
    "avgTurnovers",
    "avgOffensiveRebounds",
    "avgAssists",
    "offensiveReboundPct",
]
_GEN = ["gamesPlayed"]


def _off_vals(fgm, fga, tpa, fta, tov, oreb, ast, orebpct):
    return [fgm, fga, tpa, fta, tov, oreb, ast, orebpct]


def _make_team(espn_id, own, opp, gp):
    # displayName is deliberately junk — resolution is by ESPN id, not name.
    return {
        "team": {"id": espn_id, "displayName": "ignored"},
        "categories": [
            _team_cat("general", "0", [gp]),
            _team_cat("offensive", "0", own),
            _team_cat("offensive", "900", opp),
        ],
    }


def test_fetch_team_style_stats_derives_metrics():
    payload = {
        "categories": [_cat("general", _GEN), _cat("offensive", _OFF)],
        "teams": [
            _make_team(
                5,
                own=_off_vals(30, 70, 20, 15, 12, 9, 20, 0.25),
                opp=_off_vals(28, 68, 18, 14, 16, 8, 18, 0.24),
                gp=30,
            )
        ],
    }
    with (
        patch("src.data.espn_api._get", return_value=payload),
        patch(
            "src.data.espn_api.fetch_team_id_map", return_value={5: "New York Liberty"}
        ),
    ):
        rows = fetch_team_style_stats(2026)
    assert len(rows) == 1
    r = rows[0]
    assert r["team"] == "New York Liberty"
    assert r["games_played"] == 30
    assert abs(r["three_pa_rate"] - 20 / 70) < 1e-9
    assert abs(r["ft_rate"] - 15 / 70) < 1e-9
    assert abs(r["assist_rate"] - 20 / 30) < 1e-9
    # ESPN's offensiveReboundPct (a fraction) is scaled to a percent: 0.25 -> 25.0
    assert r["oreb_pct"] == 25.0
    # poss_own = 70 + 0.44*15 - 9 + 12 = 79.6 ; poss_opp = 68 + 0.44*14 - 8 + 16 = 82.16
    # pace = (79.6 + 82.16)/2 = 80.88 ; def_pressure = 16/82.16
    assert abs(r["pace"] - 80.88) < 1e-6
    assert abs(r["def_pressure"] - 16 / 82.16) < 1e-9
    # opp 3PA rate = opponent 3PA / opponent FGA = 18 / 68
    assert abs(r["opp_3pa_rate"] - 18 / 68) < 1e-9


def test_fetch_team_style_stats_skips_incomplete_team():
    payload = {
        "categories": [_cat("general", _GEN), _cat("offensive", _OFF)],
        "teams": [
            {
                "team": {"id": 5},
                "categories": [
                    _team_cat("offensive", "0", _off_vals(0, 0, 0, 0, 0, 0, 0, 0.0)),
                ],
            },  # resolves by id but has no opponent/general split -> skipped
        ],
    }
    with (
        patch("src.data.espn_api._get", return_value=payload),
        patch("src.data.espn_api.fetch_team_id_map", return_value={5: "Broken"}),
    ):
        rows = fetch_team_style_stats(2026)
    assert rows == []


def test_fetch_team_style_stats_skips_unmapped_id():
    """A byteam team whose ESPN id isn't in the /teams map is skipped —
    resolution is by id, so an unknown id never reaches our teams table."""
    payload = {
        "categories": [_cat("general", _GEN), _cat("offensive", _OFF)],
        "teams": [
            _make_team(
                999,
                own=_off_vals(30, 70, 20, 15, 12, 9, 20, 0.25),
                opp=_off_vals(28, 68, 18, 14, 16, 8, 18, 0.24),
                gp=30,
            )
        ],
    }
    with (
        patch("src.data.espn_api._get", return_value=payload),
        patch(
            "src.data.espn_api.fetch_team_id_map",
            return_value={5: "New York Liberty"},
        ),
    ):
        rows = fetch_team_style_stats(2026)
    assert rows == []  # id 999 not in the map
