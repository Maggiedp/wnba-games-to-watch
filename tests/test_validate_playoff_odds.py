from scripts.validate_playoff_odds import (
    _build_standings,
    _get_actual_playoffs,
    _get_remaining_games,
)
from src.scoring.tiebreakers import PLAYOFF_TEAMS


def _game(team_a, team_b, date_str, winner, season_type=2):
    return {
        "team_a": team_a,
        "team_b": team_b,
        "date": date_str,
        "winner_team": winner,
        "season_type": season_type,
    }


def test_build_standings_basic():
    games = [
        _game("Atlanta Dream", "Chicago Sky", "2025-06-01", "Atlanta Dream"),
        _game("Connecticut Sun", "Dallas Wings", "2025-06-02", "Dallas Wings"),
    ]
    s = _build_standings(games, {})
    assert s["Atlanta Dream"]["wins"] == 1
    assert s["Atlanta Dream"]["losses"] == 0
    assert s["Chicago Sky"]["wins"] == 0
    assert s["Chicago Sky"]["losses"] == 1
    assert s["Dallas Wings"]["wins"] == 1
    assert s["Connecticut Sun"]["losses"] == 1


def test_build_standings_up_to_date():
    games = [
        _game("Atlanta Dream", "Chicago Sky", "2025-06-01", "Atlanta Dream"),
        _game("Atlanta Dream", "Connecticut Sun", "2025-07-01", "Connecticut Sun"),
    ]
    s = _build_standings(games, {}, up_to_date="2025-06-15")
    assert s["Atlanta Dream"]["wins"] == 1
    assert s["Atlanta Dream"]["losses"] == 0
    assert "Connecticut Sun" not in s


def test_build_standings_excludes_preseason():
    games = [
        _game(
            "Atlanta Dream", "Chicago Sky", "2025-05-01", "Atlanta Dream", season_type=1
        ),
        _game("Atlanta Dream", "Chicago Sky", "2025-06-01", "Chicago Sky"),
    ]
    s = _build_standings(games, {})
    assert s["Atlanta Dream"]["wins"] == 0
    assert s["Atlanta Dream"]["losses"] == 1


def test_build_standings_h2h():
    games = [
        _game("Atlanta Dream", "Chicago Sky", "2025-06-01", "Atlanta Dream"),
        _game("Chicago Sky", "Atlanta Dream", "2025-06-10", "Atlanta Dream"),
    ]
    s = _build_standings(games, {})
    assert s["Atlanta Dream"]["h2h"]["Chicago Sky"] == [2, 0]
    assert s["Chicago Sky"]["h2h"]["Atlanta Dream"] == [0, 2]


def test_build_standings_attaches_elo():
    games = [_game("Atlanta Dream", "Chicago Sky", "2025-06-01", "Atlanta Dream")]
    elo = {"Atlanta Dream": 1600.0, "Chicago Sky": 1400.0}
    s = _build_standings(games, elo)
    assert s["Atlanta Dream"]["elo"] == 1600.0
    assert s["Chicago Sky"]["elo"] == 1400.0


def test_get_remaining_games_filters_by_date():
    standings = {"Atlanta Dream": {}, "Chicago Sky": {}}
    games = [
        _game("Atlanta Dream", "Chicago Sky", "2025-07-01", "Atlanta Dream"),
        _game("Atlanta Dream", "Chicago Sky", "2025-08-01", None),
    ]
    remaining = _get_remaining_games(games, standings, after_date="2025-07-15")
    assert remaining == [("Atlanta Dream", "Chicago Sky")]


def test_get_remaining_games_excludes_unknown_teams():
    standings = {"Atlanta Dream": {}, "Chicago Sky": {}}
    games = [
        _game("Atlanta Dream", "Chicago Sky", "2025-08-01", None),
        _game("Connecticut Sun", "Dallas Wings", "2025-08-05", None),
    ]
    remaining = _get_remaining_games(games, standings, after_date="2025-07-15")
    assert remaining == [("Atlanta Dream", "Chicago Sky")]


def test_get_remaining_games_excludes_preseason():
    standings = {"Atlanta Dream": {}, "Chicago Sky": {}}
    games = [
        _game("Atlanta Dream", "Chicago Sky", "2025-08-01", None, season_type=1),
    ]
    remaining = _get_remaining_games(games, standings, after_date="2025-07-15")
    assert remaining == []


def test_get_actual_playoffs_top_8():
    """8 teams with distinct win totals; top 8 by wins make the playoffs.

    Synthetic team names are safe here because all win totals are unique —
    resolve_seeding skips tiebreaker code (including TEAM_CONFERENCES lookups)
    for solo-member win buckets.
    """
    teams = [f"Team_{c}" for c in "ABCDEFGHIJKL"]  # 12 teams
    games = []
    for i, team in enumerate(teams):
        n_wins = len(teams) - 1 - i  # 11, 10, 9, ..., 0
        for w in range(n_wins):
            opp = teams[(i + w + 1) % len(teams)]
            games.append(_game(team, opp, f"2025-06-{w + 1:02d}", team))
    elo = {t: 1500.0 for t in teams}
    playoffs = _get_actual_playoffs(games, elo)
    assert len(playoffs) == PLAYOFF_TEAMS
    assert playoffs == set(teams[:8])  # Team_A (11 wins) through Team_H (4 wins)
