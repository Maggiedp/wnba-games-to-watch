# Real pbp column/type names verified against load_pbp(2024):
#   score cols: "home_score", "away_score" (both present)
#   date col:   "game_date" (present)
#   possession-ending events are FAMILIES, not single literals, so the fixture and
#   the implementation match on the real taxonomy:
#     - made shot:        scoring_play == True  (any made-shot type_text)
#     - turnover:         "Turnover" in type_text and != "No Turnover"
#                         (e.g. "Lost Ball Turnover", "Bad Pass\nTurnover", ...)
#     - defensive rebound: type_text == "Defensive Rebound" (verbatim)
#     - end period:       type_text == "End Period" (verbatim)
import datetime as dt
import pandas as pd
from src.darko.stints import build_stint_rows


def _scripted_game():
    """Team 100 scores 2 then turns it over; team 200 scores 3. No subs."""
    box = pd.DataFrame(
        {
            "game_id": [1] * 10,
            "athlete_id": list(range(1, 11)),
            "team_id": [100] * 5 + [200] * 5,
            "starter": [True] * 10,
            "minutes": [20.0] * 10,
        }
    )
    pbp = pd.DataFrame(
        {
            "game_id": [1, 1, 1],
            "sequence_number": [1, 2, 3],
            # real-taxonomy possession-ending values, one per category:
            "type_text": ["Jump Shot", "Lost Ball Turnover", "Jump Shot"],
            "scoring_play": [True, False, True],
            "team_id": [100, 100, 200],
            "athlete_id_1": [None, None, None],
            "athlete_id_2": [None, None, None],
            # team 100 is the away side, team 200 is home. In real pbp the
            # home/away score columns are keyed to home_team_id, NOT the arg order
            # of home_team/away_team, so the fixture exercises that mapping.
            "home_team_id": [200, 200, 200],
            "away_team_id": [100, 100, 100],
            "home_score": [0, 0, 3],
            "away_score": [2, 2, 2],
            "game_date": [dt.date(2024, 6, 1)] * 3,
        }
    )
    return pbp, box


def test_build_stint_rows_offense_and_defense_split():
    pbp, box = _scripted_game()
    rows = build_stint_rows(pbp, box, game_id=1, home_team=100, away_team=200)
    # team 100 had possessions and scored 2; team 200 scored 3
    off100 = rows[(rows["off_players"].apply(lambda s: 1 in s))]
    off200 = rows[(rows["off_players"].apply(lambda s: 6 in s))]
    assert off100["points"].sum() == 2
    assert off200["points"].sum() == 3
    assert (rows["possessions"] > 0).all()
    assert rows["off_players"].apply(len).eq(5).all()
    assert rows["def_players"].apply(len).eq(5).all()


def test_build_stint_rows_points_invariant_to_arg_order():
    """Points map by the pbp home_team_id, not the home_team/away_team arg order:
    swapping the two team args must not move points to the wrong offense."""
    pbp, box = _scripted_game()
    rows = build_stint_rows(pbp, box, game_id=1, home_team=200, away_team=100)
    off100 = rows[(rows["off_players"].apply(lambda s: 1 in s))]
    off200 = rows[(rows["off_players"].apply(lambda s: 6 in s))]
    assert off100["points"].sum() == 2
    assert off200["points"].sum() == 3


def _defensive_rebound_game():
    """Team 100 (home) misses a shot; team 200 (away) grabs the Defensive Rebound,
    then team 200 scores.

    In real wehoop pbp, the Defensive Rebound row's team_id is the REBOUNDING
    (defending) team (200) — the OPPOSITE of the team whose missed-shot possession
    just ended (100). The ended possession on that rebound belongs to the SHOOTER
    (team 100), so it must be charged to team 100's offense (0 points), NOT to the
    rebounder (team 200). Scores are forward-filled, so the rebound row scores 0."""
    box = pd.DataFrame(
        {
            "game_id": [2] * 10,
            "athlete_id": list(range(1, 11)),
            "team_id": [100] * 5 + [200] * 5,
            "starter": [True] * 10,
            "minutes": [20.0] * 10,
        }
    )
    pbp = pd.DataFrame(
        {
            "game_id": [2, 2, 2],
            "sequence_number": [1, 2, 3],
            # missed jump shot (not scoring), then DEFENSIVE REBOUND by the
            # OPPONENT, then a made shot by the rebounding team.
            "type_text": ["Jump Shot", "Defensive Rebound", "Made Shot"],
            "scoring_play": [False, False, True],
            # team 100 shoots & misses; team 200 rebounds (team_id is the REBOUNDER);
            # team 200 then scores.
            "team_id": [100, 200, 200],
            "athlete_id_1": [None, None, None],
            "athlete_id_2": [None, None, None],
            "home_team_id": [100, 100, 100],
            "away_team_id": [200, 200, 200],
            # forward-filled scores: 0 through the rebound, +2 on team 200's make.
            "home_score": [0, 0, 0],
            "away_score": [0, 0, 2],
            "game_date": [dt.date(2024, 6, 1)] * 3,
        }
    )
    return pbp, box


def test_defensive_rebound_possession_charged_to_shooter():
    """The possession that ends on a defensive rebound belongs to the SHOOTER
    (team 100), not the rebounder (team 200): off_players must be team 100's
    lineup with 0 points; team 200's made shot stays with team 200."""
    pbp, box = _defensive_rebound_game()
    rows = build_stint_rows(pbp, box, game_id=2, home_team=100, away_team=200)

    # The def-rebound possession (0 points) is owned by team 100 (athlete 1..5).
    off100 = rows[rows["off_players"].apply(lambda s: 1 in s)]
    off200 = rows[rows["off_players"].apply(lambda s: 6 in s)]

    # Team 100 owns the (missed-shot, def-rebound) possession: 0 points.
    assert off100["points"].sum() == 0
    # Team 100 must have at least one possession (the def-rebound possession).
    assert off100["possessions"].sum() >= 1
    # Team 200 scored 2 on its own offense.
    assert off200["points"].sum() == 2
    # Every stint keeps full 5-on-5 lineups.
    assert rows["off_players"].apply(len).eq(5).all()
    assert rows["def_players"].apply(len).eq(5).all()
