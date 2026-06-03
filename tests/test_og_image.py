"""Tests for src/api/og_image.py — per-game social-preview card."""

import io

from PIL import Image

from src.api.og_image import render_game_card


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
