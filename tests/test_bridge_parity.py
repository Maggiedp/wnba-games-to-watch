"""The bridge is rendered twice — JS for the /shot-making panel, Python for the
server-rendered /player page. They show the same player on two surfaces, so any
drift is directly visible. This test is the lockstep guarantee."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.api.routes import _vs_league_bridge_html, _xpps_marker

JS_DIR = Path(__file__).resolve().parents[1] / "src" / "api" / "templates" / "js"

CASES = [
    # (expected_pps, actual_pps, avg_xpps, avg_pps, scale, rank_label)
    (0.932, 1.144, 1.027, 1.037, 0.285, "#12 of 113 in points per shot"),
    (1.177, 1.152, 1.027, 1.037, 0.285, None),  # easy diet, cold making
    (1.111, 0.762, 1.027, 1.037, 0.285, "#113 of 113 in points per shot"),
    (1.027, 1.037, 1.027, 1.037, 0.285, None),  # dead-on average, both near-zero
    (1.037, 1.047, 1.027, 1.037, 0.285, None),  # inside the 0.03 near band
    (1.0625, 1.0, 1.0, 1.0, 0.5, None),  # gap == 0.0625, exact .toFixed(3) tie
    (1.0005, 1.0, 1.0, 1.0, 0.25, None),  # gap == 0.0005, exact .toFixed(3) tie
    # Label-side edge overrides, which position rather than arrow direction
    # decides — both renderers must agree on WHICH side flips. The row that
    # sets bridge_scale lands on an edge, so these are live cases, not synthetic:
    (1.037, 1.322, 1.027, 1.037, 0.285, None),  # actual at 100% -> PPS flips left
    (0.742, 1.037, 1.027, 1.037, 0.285, None),  # expected at 0% -> xPPS flips right
]


def _render_with_node(case):
    src = (JS_DIR / "shot_making_helpers.js").read_text()
    expected_pps, actual_pps, ax, ap, scale, rank = case
    script = (
        src
        + "\nconsole.log(vsLeagueBridge("
        + json.dumps({"expected_pps": expected_pps, "actual_pps": actual_pps})
        + ", "
        + json.dumps({"avg_xpps": ax, "avg_pps": ap})
        + f", {scale}, "
        + json.dumps(rank)
        + "));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("case", CASES)
def test_python_bridge_matches_js_bridge(case):
    expected_pps, actual_pps, ax, ap, scale, rank = case
    py = _vs_league_bridge_html(
        expected_pps, actual_pps, {"avg_xpps": ax, "avg_pps": ap}, scale, rank
    )
    # F6a: without this, the test would pass vacuously if both renderers
    # regressed to returning "" (e.g. a broken anchor/scale guard) — pin that
    # a real bridge actually rendered, not just that the two sides agree.
    assert py.startswith('<div class="bridge">')
    assert py == _render_with_node(case)


def test_python_bridge_degrades_without_anchors_or_scale():
    assert (
        _vs_league_bridge_html(0.9, 1.1, {"avg_xpps": None, "avg_pps": None}, 0.2, None)
        == ""
    )
    assert (
        _vs_league_bridge_html(0.9, 1.1, {"avg_xpps": 1.0, "avg_pps": 1.0}, None, None)
        == ""
    )
    assert (
        _vs_league_bridge_html(0.9, 1.1, {"avg_xpps": 1.0, "avg_pps": 1.0}, 0, None)
        == ""
    )


def test_xpps_marker_uses_js_toFixed_semantics_at_a_tie():
    """F3 regression: _xpps_marker used Python's banker's round(x, 3), while its
    JS twin xppsMarker() uses Number(x.toFixed(3)) (ties away from zero). At
    league_avg == 1.0625 the two disagreed — Python round(1.0625, 3) == 1.062
    (banker's, rounds to even) but "1.0625".toFixed(3) == "1.063" (JS, ties
    away from zero) — so the glyph could differ between /player and
    /shot-making for the same player. Routing through _js_to_fixed closes the
    gap; pin the previously-divergent cases (verified by hand against both
    the old round(x, 3) implementation and the current one) so they can't
    silently regress.

    league_avg == 1.0625 rounds to "1.063" under toFixed but 1.062 under
    round(). xpps == 1.062 is UNCHANGED by either rounding rule, so:
      - old code:  round(1.062, 3) == round(1.0625, 3) -> 1.062 == 1.062 -> "-"
      - new code:  1.062 (js_to_fixed) <  1.063 (js_to_fixed)             -> "v"
    """
    assert _xpps_marker(1.062, 1.0625) == "▽"
    # xpps == 1.063 is likewise unchanged by rounding, and sits on the other
    # side of the same tie:
    #   - old code:  round(1.063, 3) >  round(1.0625, 3) -> 1.063 > 1.062  -> "^"
    #   - new code:  1.063 (js_to_fixed) == 1.063 (js_to_fixed)            -> "-"
    assert _xpps_marker(1.063, 1.0625) == "–"
