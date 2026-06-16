from src.db.queries import replace_elo_history, upsert_team


def _seed_elo(schema):
    session = schema.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0)
    a = session.query(schema.Team).filter_by(name="Aces").one().id
    b = session.query(schema.Team).filter_by(name="Storm").one().id
    replace_elo_history(
        session,
        2026,
        [
            (a, "2026-05-10", 1600.0),
            (b, "2026-05-10", 1450.0),
            (a, "2026-05-15", 1615.0),
        ],
    )
    session.close()


def test_elo_history_shape(env, client):
    _seed_elo(env)

    r = client.get("/api/elo-history?season=2026")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == 2026
    assert body["teams"]["Aces"] == [
        {"date": "2026-05-10", "rating": 1600.0},
        {"date": "2026-05-15", "rating": 1615.0},
    ]
    assert body["teams"]["Storm"] == [{"date": "2026-05-10", "rating": 1450.0}]


def test_elo_history_empty_season(client):
    r = client.get("/api/elo-history?season=1999")
    assert r.status_code == 200
    assert r.json() == {"season": 1999, "teams": {}}


def test_transparency_page_renders(client):
    r = client.get("/transparency")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="calibration-chart"' in r.text
    assert "loadCalibration" in r.text
    assert "Behind the numbers" in r.text
    assert r.text.lstrip().startswith("<!DOCTYPE")  # full document
    assert "%%" not in r.text  # every token placeholder was substituted


def test_calibration_endpoint(env, client):
    from src.db.queries import upsert_daily_ranking
    from src.db.schema import Game

    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Storm").one().id
    # Two completed regular-season games.
    session.add_all(
        [
            Game(
                team_a_id=a,
                team_b_id=b,
                date="2026-05-10",
                time="",
                broadcaster="",
                winner_id=a,
                season_type=2,
                espn_id="e1",
            ),
            Game(
                team_a_id=a,
                team_b_id=b,
                date="2026-05-12",
                time="",
                broadcaster="",
                winner_id=b,
                season_type=2,
                espn_id="e2",
            ),
        ]
    )
    session.commit()
    for d, wp in [("2026-05-10", 0.9), ("2026-05-12", 0.4)]:
        upsert_daily_ranking(
            session,
            date=d,
            team_a_id=a,
            team_b_id=b,
            quality_score=0.0,
            importance_score=None,
            overall_score=0.0,
            broadcaster="",
            win_prob_a=wp,
        )
    session.commit()
    session.close()

    r = client.get("/api/calibration?season=2026")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 2
    assert "brier" in body
    assert isinstance(body["buckets"], list)


def test_calibration_empty_season(client):
    r = client.get("/api/calibration?season=1999")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 0
    assert body["buckets"] == []


def test_calibration_excludes_null_win_prob(env, client):
    from src.db.queries import upsert_daily_ranking
    from src.db.schema import Game

    session = env.get_session()
    upsert_team(session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
    upsert_team(session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0)
    a = session.query(env.Team).filter_by(name="Aces").one().id
    b = session.query(env.Team).filter_by(name="Storm").one().id
    # One completed game WITH a frozen prediction, one WITHOUT (win_prob_a=None).
    session.add_all(
        [
            Game(
                team_a_id=a,
                team_b_id=b,
                date="2026-05-10",
                time="",
                broadcaster="",
                winner_id=a,
                season_type=2,
                espn_id="e1",
            ),
            Game(
                team_a_id=a,
                team_b_id=b,
                date="2026-05-12",
                time="",
                broadcaster="",
                winner_id=b,
                season_type=2,
                espn_id="e2",
            ),
        ]
    )
    session.commit()
    upsert_daily_ranking(
        session,
        date="2026-05-10",
        team_a_id=a,
        team_b_id=b,
        quality_score=0.0,
        importance_score=None,
        overall_score=0.0,
        broadcaster="",
        win_prob_a=0.8,
    )
    upsert_daily_ranking(
        session,
        date="2026-05-12",
        team_a_id=a,
        team_b_id=b,
        quality_score=0.0,
        importance_score=None,
        overall_score=0.0,
        broadcaster="",
        win_prob_a=None,
    )
    session.commit()
    session.close()

    r = client.get("/api/calibration?season=2026")
    assert r.status_code == 200
    # Only the game with a stored win_prob_a counts.
    assert r.json()["n"] == 1


def test_homepage_links_to_transparency():
    from src.api.routes import render_homepage

    assert 'href="/transparency"' in render_homepage()


def test_rankings_legend_escapes_team_names():
    # The Elo legend on /rankings injects team names (from the DB) via innerHTML.
    # They must be HTML-escaped so a poisoned/malformed team name can't execute
    # markup — consistent with the homepage/detail pages, which escape DB/ESPN labels.
    from src.api.routes import render_rankings

    html = render_rankings()
    # Reuses the single-sourced escapeHtml from _SHARED_JS (not a page-local copy).
    assert "function escapeHtml" in html
    assert "escapeHtml(s.label)" in html
    # The raw, unescaped label interpolation must be gone from the legend.
    assert "${s.label}" not in html


def test_transparency_no_longer_has_elo_section(client):
    r = client.get("/transparency")
    assert r.status_code == 200
    assert "Elo ratings over time" not in r.text  # moved to /rankings
    assert 'id="elo-chart"' not in r.text
    assert "loadElo" not in r.text
    assert 'href="/rankings"' in r.text  # cross-link present
    assert 'id="calibration-chart"' in r.text  # calibration stays
    assert "%%" not in r.text


def test_rankings_page_renders(client):
    r = client.get("/rankings")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="elo-chart"' in r.text
    assert 'id="elo-legend"' in r.text
    assert "WNBA Power Rankings" in r.text
    assert "showRankDelta" not in r.text  # rank-movement arrows removed
    assert "/og-rankings.png" in r.text  # og card wired up
    assert r.text.lstrip().startswith("<!DOCTYPE")
    assert "%%" not in r.text  # all tokens substituted
