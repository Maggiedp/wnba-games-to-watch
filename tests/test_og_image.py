"""Tests for src/api/og_image.py — per-game social-preview card."""

import io

from PIL import Image

from src.api.og_image import _format_date, render_game_card


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
