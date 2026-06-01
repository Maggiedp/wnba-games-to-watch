from src.scoring.elo import build_elo_timeline

# Shape matches EloReplay.history entries (see elo.py): each game records the
# pre-game rating both teams carried into it.
HISTORY = [
    {
        "team_a": "Aces",
        "team_b": "Storm",
        "pre_a": 1500.0,
        "pre_b": 1480.0,
        "winner": "Aces",
        "date": "2025-09-01",
    },  # prior season, must be excluded
    {
        "team_a": "Aces",
        "team_b": "Storm",
        "pre_a": 1600.0,
        "pre_b": 1450.0,
        "winner": "Aces",
        "date": "2026-05-10",
    },
    {
        "team_a": "Storm",
        "team_b": "Aces",
        "pre_a": 1440.0,
        "pre_b": 1615.0,
        "winner": "Storm",
        "date": "2026-05-15",
    },
]


def test_filters_to_season_prefix():
    tl = build_elo_timeline(HISTORY, "2026")
    # No 2025 points leak in.
    assert all(p["date"].startswith("2026") for pts in tl.values() for p in pts)


def test_per_team_pregame_points_in_date_order():
    tl = build_elo_timeline(HISTORY, "2026")
    assert tl["Aces"] == [
        {"date": "2026-05-10", "rating": 1600.0},
        {"date": "2026-05-15", "rating": 1615.0},
    ]
    assert tl["Storm"] == [
        {"date": "2026-05-10", "rating": 1450.0},
        {"date": "2026-05-15", "rating": 1440.0},
    ]


def test_empty_history():
    assert build_elo_timeline([], "2026") == {}
