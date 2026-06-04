"""Tests for src/api/og_image.py — per-game social-preview card."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.og_image import _format_date, render_game_card, render_game_card_png
from src.db.queries import upsert_daily_ranking, upsert_game, upsert_team
from src.db.schema import Base


@pytest.fixture(autouse=True)
def _clear_og_cache():
    """The endpoint cache is a module global; clear it around each test so a
    cached card can't leak across tests that reuse an espn_id (mirrors the
    live-wp cache guard in test_live_wp_endpoint.py)."""
    import src.api.app as app_module

    app_module._og_cache.clear()
    yield
    app_module._og_cache.clear()


def _open(png_bytes):
    return Image.open(io.BytesIO(png_bytes))


def test_render_game_card_scored_returns_1200x630_png():
    png = render_game_card(
        name_a="Seattle Storm",
        name_b="Las Vegas Aces",
        overall=87.0,
        date_str="2026-06-04",
        broadcaster="ESPN",
    )
    assert isinstance(png, bytes)
    img = _open(png)
    assert img.format == "PNG"
    assert img.size == (1200, 630)


def test_render_game_card_scoreless_still_renders():
    png = render_game_card(
        name_a="Seattle Storm",
        name_b="Las Vegas Aces",
        overall=None,
        date_str="2026-06-04",
        broadcaster="",
    )
    img = _open(png)
    assert img.size == (1200, 630)


def test_render_game_card_long_names_render():
    # Two of the longest WNBA names side-by-side must not raise.
    png = render_game_card(
        name_a="Connecticut Sun",
        name_b="Minnesota Lynx",
        overall=64.0,
        date_str="2026-07-15",
        broadcaster="Prime Video",
    )
    assert _open(png).size == (1200, 630)


def test_format_date_valid():
    assert _format_date("2026-06-04") == "Jun 4"


def test_format_date_none():
    assert _format_date(None) == ""


def test_format_date_garbage():
    assert _format_date("not-a-date") == "not-a-date"


def test_scoreless_card_omits_methodology_footer():
    scored = _open(render_game_card("A", "B", 87.0, "2026-06-04", "ESPN"))
    scoreless = _open(render_game_card("A", "B", None, "2026-06-04", "ESPN"))
    # The right-side "60% quality · 40% stakes" footer is drawn only when scored,
    # so the bottom-right region must differ between the two cards.
    box = (700, 540, 1180, 600)  # bottom-right footer area
    assert scored.crop(box).tobytes() != scoreless.crop(box).tobytes()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def team_ids(session):
    a = upsert_team(
        session, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0
    )
    b = upsert_team(
        session, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0
    )
    return a.id, b.id


def test_render_game_card_png_unknown_id_returns_none(session):
    assert render_game_card_png(session, "does-not-exist") is None


def test_render_game_card_png_scored_returns_png_bytes(session, team_ids):
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-04",
        time="7:00 PM ET",
        broadcaster="ESPN",
        espn_id="401234",
    )
    upsert_daily_ranking(
        session,
        date="2026-06-04",
        team_a_id=a_id,
        team_b_id=b_id,
        quality_score=50.0,
        importance_score=0.3,
        overall_score=87.0,
        broadcaster="ESPN",
    )
    png = render_game_card_png(session, "401234")
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_game_card_png_scoreless_game_renders(session, team_ids):
    a_id, b_id = team_ids
    upsert_game(
        session,
        team_a_id=a_id,
        team_b_id=b_id,
        date="2026-06-04",
        time="",
        broadcaster="",
        espn_id="401999",
    )
    # No daily_ranking row -> not simulated.
    png = render_game_card_png(session, "401999")
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """File-backed sqlite shared across seed + request sessions (mirrors
    tests/test_transparency_endpoints.py)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from src.db import schema

    schema._engine = None
    schema._session_factory = None
    schema.init_db()
    yield schema
    schema._engine = None
    schema._session_factory = None


def _seed_game(schema, espn_id="401234", overall=87.0):
    s = schema.get_session()
    try:
        a = upsert_team(
            s, name="Storm", abbreviation="SEA", logo_url="", bpi_rating=0.0
        )
        b = upsert_team(s, name="Aces", abbreviation="LV", logo_url="", bpi_rating=0.0)
        upsert_game(
            s,
            team_a_id=a.id,
            team_b_id=b.id,
            date="2026-06-04",
            time="7:00 PM ET",
            broadcaster="ESPN",
            espn_id=espn_id,
        )
        upsert_daily_ranking(
            s,
            date="2026-06-04",
            team_a_id=a.id,
            team_b_id=b.id,
            quality_score=50.0,
            importance_score=0.3,
            overall_score=overall,
            broadcaster="ESPN",
        )
    finally:
        s.close()


def test_og_endpoint_returns_png(env):
    _seed_game(env)
    from src.api.app import app

    r = TestClient(app).get("/game/401234/og.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_og_endpoint_unknown_id_404(env):
    from src.api.app import app

    r = TestClient(app).get("/game/nope/og.png")
    assert r.status_code == 404


def test_og_endpoint_serves_second_request_from_cache(env, monkeypatch):
    _seed_game(env)
    from src.api.app import app, _og_cache

    client = TestClient(app)
    first = client.get("/game/401234/og.png")
    assert first.status_code == 200
    assert "401234" in _og_cache

    # The warm path must not re-render: blow up if the renderer is called again.
    def _boom(*args, **kwargs):
        raise AssertionError("render should not run on a cache hit")

    monkeypatch.setattr("src.api.og_image.render_game_card_png", _boom)
    second = client.get("/game/401234/og.png")
    assert second.status_code == 200
    assert second.content == first.content
