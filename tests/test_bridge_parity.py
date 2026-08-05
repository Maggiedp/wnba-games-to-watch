"""The bridge is rendered twice — JS for the /shot-making panel, Python for the
server-rendered /player page. They show the same player on two surfaces, so any
drift is directly visible. This test is the lockstep guarantee."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.api.routes import _vs_league_bridge_html

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
