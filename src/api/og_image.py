"""Per-game social-preview (og:image) card rendering.

A pure renderer (`render_game_card`) draws a 1200x630 PNG from primitive
values; a thin DB wrapper (`render_game_card_png`) fetches a game and
delegates. Kept out of routes.py to keep that module focused.
"""

from __future__ import annotations

import functools
import io
import os
from collections import Counter
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from src.data.espn_api import clock_season
from src.db.queries import get_shot_making, get_shots_for_player, get_teams_by_ids
from src.db.schema import DailyRanking, Game
from src.scoring.shot_making import player_headline

WIDTH, HEIGHT = 1200, 630

_NAVY = (13, 27, 42)
_ORANGE = (255, 107, 0)
_OFFWHITE = (245, 243, 238)
_MUTED = (150, 165, 180)

_FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "Fraunces.ttf")


# Axis order in the bundled variable font is [opsz, wght, SOFT, WONK]
# (verified via ImageFont.get_variation_axes in Task 1 — the bracketed
# filename order is NOT the runtime axis order). opsz pinned high for
# display sizing; weight varies per call; softness/wonk left at 0.
def _load_font(size: int, weight: float) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(_FONT_PATH, size)
    try:
        font.set_variation_by_axes([144.0, weight, 0.0, 0.0])
    except Exception:
        # Static font or unsupported axes — fall back to default instance.
        pass
    return font


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    weight: float,
    min_size: int = 28,
) -> ImageFont.FreeTypeFont:
    """A weight-`weight` font at the largest tried size whose `text` width fits `max_width`."""
    size = start_size
    while size >= min_size:
        font = _load_font(size, weight)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return _load_font(min_size, weight)


def _format_date(date_str: str) -> str:
    """'2026-06-04' -> 'Jun 4'. Returns the raw string if unparseable."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str or ""
    return f"{dt.strftime('%b')} {dt.day}"


def _draw_base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A fresh navy 1200x630 canvas with the top-left wumbers wordmark drawn."""
    img = Image.new("RGB", (WIDTH, HEIGHT), _NAVY)
    draw = ImageDraw.Draw(img)
    draw.ellipse((60, 56, 88, 84), fill=_ORANGE)
    draw.text((100, 52), "wumbers", font=_load_font(34, 600.0), fill=_ORANGE)
    return img, draw


def _centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: float,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
) -> None:
    w = draw.textlength(text, font=font)
    draw.text(((WIDTH - w) / 2, y), text, font=font, fill=fill)


def _to_png(img: Image.Image) -> bytes:
    """Encode a PIL image as PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@functools.cache
def render_home_card() -> bytes:
    """Static homepage brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "watching tonight", 1080, 130, 900.0)
    _centered(draw, "What's worth", 210, headline_font, _OFFWHITE)
    _centered(draw, "watching tonight", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "WNBA games ranked by quality and playoff stakes",
        520,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


@functools.cache
def render_transparency_card() -> bytes:
    """Static /transparency brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "the numbers", 1080, 150, 900.0)
    _centered(draw, "Behind", 190, headline_font, _OFFWHITE)
    _centered(draw, "the numbers", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "How wumbers scores every WNBA game",
        540,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


@functools.cache
def render_rankings_card() -> bytes:
    """Static /rankings brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "Rankings", 1080, 150, 900.0)
    _centered(draw, "Power", 190, headline_font, _OFFWHITE)
    _centered(draw, "Rankings", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "Where every WNBA team stands, by Elo",
        540,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


@functools.cache
def render_replay_card() -> bytes:
    """Static /replay brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "Replay", 1080, 150, 900.0)
    _centered(draw, "Replay", 190, headline_font, _OFFWHITE)
    _centered(draw, "Value", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "WNBA games ranked by how they played out",
        540,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


@functools.cache
def render_style_card() -> bytes:
    """Static /style brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "Style", 1080, 150, 900.0)
    _centered(draw, "Team", 190, headline_font, _OFFWHITE)
    _centered(draw, "Style", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "How every WNBA team plays, as a fingerprint",
        540,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


@functools.cache
def render_playoff_odds_card() -> bytes:
    """Static /playoff-odds brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "Playoff", 1080, 150, 900.0)
    _centered(draw, "Playoff", 190, headline_font, _OFFWHITE)
    _centered(draw, "Picture", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "Every WNBA team's title odds, simulated daily",
        540,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


@functools.cache
def render_shot_making_card() -> bytes:
    """Static /shot-making brand card (1200x630 PNG bytes). Content is constant."""
    img, draw = _draw_base()
    headline_font = _fit_font(draw, "Making", 1080, 150, 900.0)
    _centered(draw, "Shot", 190, headline_font, _OFFWHITE)
    _centered(draw, "Making", 350, headline_font, _ORANGE)
    _centered(
        draw,
        "Which WNBA players beat their shot profile",
        540,
        _load_font(34, 400.0),
        _MUTED,
    )
    return _to_png(img)


def render_game_card(
    name_a: str,
    name_b: str,
    overall: float | None,
    date_str: str,
    broadcaster: str,
) -> bytes:
    """Render the 1200x630 social card as PNG bytes."""
    img, draw = _draw_base()

    # --- Matchup, centered, shrink-to-fit ---
    # Use a plain ASCII slash: Fraunces has no U+2571 box-drawing glyph (the
    # site's HTML separator), which renders as tofu in the rasterized card.
    matchup = f"{name_a}  /  {name_b}"
    matchup_font = _fit_font(draw, matchup, max_width=1080, start_size=72, weight=600.0)
    _centered(draw, matchup, 196, matchup_font, _OFFWHITE)

    # --- Overall score, big and centered ---
    score_text = f"{overall:.0f}" if overall is not None else "—"
    _centered(draw, score_text, 300, _load_font(170, 900.0), _ORANGE)

    _centered(draw, "OVERALL", 500, _load_font(30, 600.0), _OFFWHITE)

    # --- Footer ---
    footer_font = _load_font(30, 400.0)
    left_parts = [_format_date(date_str)]
    if broadcaster:
        left_parts.append(broadcaster)
    left = " · ".join(left_parts)
    draw.text((60, 556), left, font=footer_font, fill=_OFFWHITE)

    if overall is not None:
        right = "60% quality · 40% stakes"
        rw = draw.textlength(right, font=footer_font)
        draw.text((WIDTH - 60 - rw, 556), right, font=footer_font, fill=_MUTED)

    return _to_png(img)


def render_game_card_png(session: Session, espn_id: str) -> bytes | None:
    """Fetch a game by espn_id and render its social card, or None if unknown."""
    game = session.query(Game).filter(Game.espn_id == espn_id).first()
    if game is None:
        return None

    teams = get_teams_by_ids(session, {game.team_a_id, game.team_b_id})
    team_a = teams.get(game.team_a_id)
    team_b = teams.get(game.team_b_id)
    if team_a is None or team_b is None:
        return None

    ranking = (
        session.query(DailyRanking)
        .filter(
            DailyRanking.date == game.date,
            DailyRanking.team_a_id == game.team_a_id,
            DailyRanking.team_b_id == game.team_b_id,
        )
        .first()
    )
    overall = ranking.overall_score if ranking is not None else None

    return render_game_card(
        name_a=team_a.name,
        name_b=team_b.name,
        overall=overall,
        date_str=game.date,
        broadcaster=game.broadcaster or "",
    )


def render_player_card(name: str, team: str, headline: str) -> bytes:
    """Render the 1200x630 shareable player card as PNG bytes. Text only (name,
    team, headline). `headline` is ASCII-signed (see shot_making.player_headline)
    because Fraunces has no U+2212 glyph — a Unicode minus would tofu on the
    rasterized card."""
    img, draw = _draw_base()
    name_font = _fit_font(draw, name, max_width=1080, start_size=110, weight=900.0)
    _centered(draw, name, 210, name_font, _OFFWHITE)
    if team:
        _centered(draw, team, 360, _load_font(40, 600.0), _MUTED)
    _centered(draw, headline, 470, _load_font(46, 700.0), _ORANGE)
    _centered(draw, "Shot making · wumbers", 560, _load_font(28, 400.0), _MUTED)
    return _to_png(img)


def render_player_card_png(session: Session, athlete_id: str) -> bytes | None:
    """Fetch a player and render the shareable OG card, or None if no shots.

    Resolves the same qualified/sub-threshold headline as render_player_page
    (routes.py) — a deliberate 2-caller duplication of that rank/team logic
    per the repo's extract-on-3rd-caller convention.
    """
    season = clock_season()

    # Check the ranked board first: a qualified player (the common path — the
    # leaderboard only links ≥100-FGA players) carries name/team/stats on the
    # board row, so we can skip the full raw-shots query entirely. One pass
    # yields both the row and its rank.
    board = get_shot_making(session, season)
    board.sort(key=lambda r: r.points_added, reverse=True)
    me = None
    rank = None
    for i, r in enumerate(board):
        if r.athlete_id == athlete_id:
            me, rank = r, i + 1
            break

    if me is not None:
        name = me.athlete_name
        team = me.team_abbr
        headline = player_headline(me.fga, me.points_added, rank, len(board))
    else:
        # Not on the board: sub-threshold (has shots but no ranked row) or
        # unknown. Only now fetch the raw shots — needed for the name, the FGA
        # count, and to 404 an unknown athlete.
        rows = get_shots_for_player(session, season, athlete_id)
        if not rows:
            return None
        name = rows[0].athlete_name
        # get_shots_for_player has no ORDER BY, so rows[0].team_abbr is
        # nondeterministic for a traded player — pick deterministically like
        # render_player_page's sub-threshold branch (routes.py): the team the
        # player took the most shots for, ties broken by abbreviation. Must
        # stay byte-for-byte identical to that branch so the page and this
        # card never disagree.
        shot_counts = Counter(r.team_abbr for r in rows)
        team = max(shot_counts, key=lambda t: (shot_counts[t], t))
        headline = player_headline(len(rows), None, None, None)

    return render_player_card(name=name, team=team, headline=headline)
