"""Tests for src/api/og_image.py — per-game social-preview card."""

import io

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.og_image import _format_date, render_game_card, render_game_card_png
from src.api.routes import render_game_detail
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


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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
    assert isinstance(png, bytes) and png[:8] == _PNG_MAGIC


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
    assert isinstance(png, bytes) and png[:8] == _PNG_MAGIC


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


def test_og_endpoint_returns_png(env, client):
    _seed_game(env)

    r = client.get("/game/401234/og.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == _PNG_MAGIC


def test_og_endpoint_unknown_id_404(client):
    r = client.get("/game/nope/og.png")
    assert r.status_code == 404


def test_og_public_max_age_does_not_exceed_server_cache_ttl(env, client):
    """The unversioned og.png URL must not advertise a longer public cache than
    the server actually enforces — otherwise a browser/proxy could serve a stale
    card after the daily run recomputes overall_score."""
    _seed_game(env)
    from src.api.app import _OG_CACHE_TTL_S

    r = client.get("/game/401234/og.png")
    assert r.headers["cache-control"] == f"public, max-age={_OG_CACHE_TTL_S}"


def test_og_endpoint_serves_second_request_from_cache(env, client, monkeypatch):
    _seed_game(env)
    from src.api.app import _og_cache

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


def test_draw_base_returns_navy_canvas_with_wordmark():
    from src.api.og_image import _NAVY, _ORANGE, _draw_base

    img, _draw = _draw_base()
    assert img.size == (1200, 630)
    # Background is navy at a far corner untouched by the wordmark.
    assert img.getpixel((1190, 620)) == _NAVY
    # The orange dot center (~x=74, y=70) is painted orange.
    assert img.getpixel((74, 70)) == _ORANGE


def test_render_home_card_returns_1200x630_png():
    from src.api.og_image import render_home_card

    png = render_home_card()
    assert isinstance(png, bytes)
    assert png[:8] == _PNG_MAGIC
    assert _open(png).size == (1200, 630)


def test_render_home_card_is_memoized():
    from src.api.og_image import render_home_card

    assert render_home_card() is render_home_card()


def test_render_transparency_card_returns_1200x630_png():
    from src.api.og_image import render_transparency_card

    png = render_transparency_card()
    assert isinstance(png, bytes)
    assert png[:8] == _PNG_MAGIC
    assert _open(png).size == (1200, 630)


def test_render_transparency_card_is_memoized():
    from src.api.og_image import render_transparency_card

    assert render_transparency_card() is render_transparency_card()


def test_og_home_endpoint_returns_png(client):
    from src.api.app import _OG_STATIC_CACHE_S

    r = client.get("/og-home.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == f"public, max-age={_OG_STATIC_CACHE_S}"
    assert r.content[:8] == _PNG_MAGIC


def test_og_transparency_endpoint_returns_png(client):
    from src.api.app import _OG_STATIC_CACHE_S

    r = client.get("/og-transparency.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == f"public, max-age={_OG_STATIC_CACHE_S}"
    assert r.content[:8] == _PNG_MAGIC


# Crawlers/link unfurlers that HEAD-probe an advertised og:image before GETting
# it must get the same 200 + headers, not a 405. All three og.png routes answer
# HEAD as well as GET.
def test_og_static_endpoints_answer_head(client):
    from src.api.app import _OG_STATIC_CACHE_S

    for path in ("/og-home.png", "/og-transparency.png"):
        r = client.head(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"] == "image/png", path
        assert r.headers["cache-control"] == f"public, max-age={_OG_STATIC_CACHE_S}", (
            path
        )


def test_og_game_endpoint_answers_head(env, client):
    _seed_game(env)
    from src.api.app import _OG_CACHE_TTL_S

    r = client.head("/game/401234/og.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == f"public, max-age={_OG_CACHE_TTL_S}"


def test_og_game_endpoint_head_unknown_id_404(client):
    r = client.head("/game/nope/og.png")
    assert r.status_code == 404


def test_homepage_has_og_image_meta():
    from src.api.routes import render_homepage

    html = render_homepage()
    assert (
        '<meta property="og:image" content="https://wumbers.com/og-home.png">' in html
    )
    assert (
        '<meta name="twitter:image" content="https://wumbers.com/og-home.png">' in html
    )
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:card" content="summary">' not in html


def test_transparency_has_og_image_meta():
    from src.api.routes import render_transparency

    html = render_transparency()
    assert (
        '<meta property="og:image" content="https://wumbers.com/og-transparency.png">'
        in html
    )
    assert (
        '<meta name="twitter:image" content="https://wumbers.com/og-transparency.png">'
        in html
    )
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:card" content="summary">' not in html


def test_detail_head_has_og_image_tags(session, team_ids):
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
    html = render_game_detail(session, "401234")
    assert (
        'property="og:image" content="https://wumbers.com/game/401234/og.png"' in html
    )
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert 'property="og:image:width" content="1200"' in html
    assert (
        'name="twitter:image" content="https://wumbers.com/game/401234/og.png"' in html
    )
