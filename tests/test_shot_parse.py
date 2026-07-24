import json
from src.data import espn_api
from src.data.espn_api import _shot_point_value, _parse_distance_ft


def _load(monkeypatch):
    payload = json.load(open("tests/fixtures/espn_summary_401857083.json"))
    monkeypatch.setattr(espn_api, "_get", lambda *a, **k: payload)
    return espn_api.fetch_shots("401857083")


def test_distance_and_point_value_parse():
    assert _parse_distance_ft("makes 23-foot three point jumper") == 23.0
    assert _parse_distance_ft("makes layup") is None
    assert _shot_point_value("makes 23-foot three point jumper", 3) == 3
    assert _shot_point_value("misses 25-foot three point jumper", 0) == 3
    assert _shot_point_value("makes 5-foot two point shot", 2) == 2


def test_fetch_shots_excludes_free_throws_and_attributes_shooter(monkeypatch):
    shots = _load(monkeypatch)
    # No free throws.
    assert all("Free Throw" not in s["shot_type"] for s in shots)
    # Every shot has a resolved shooter name + a 2 or 3 point value.
    assert shots and all(s["athlete_name"] for s in shots)
    assert all(s["points"] in (0, 2, 3) for s in shots)
    assert all(s["point_value"] in (2, 3) for s in shots)
    # A missed three-pointer (points=0) is still classified as a 3, not a 2 —
    # the bug this test guards against: ESPN gives points=0 on a miss, and
    # only the play text (not scoreValue) reveals it was a three.
    assert any(s["made"] is False and s["point_value"] == 3 for s in shots)
    # The blocked layup is attributed to the shooter (Juskaite), not the blocker.
    juskaite = [s for s in shots if s["athlete_name"] == "Laura Juskaite"]
    assert any(
        s["made"] is False and s["shot_type"] == "Driving Layup Shot" for s in juskaite
    )


def test_fetch_shots_unique_play_ids(monkeypatch):
    shots = _load(monkeypatch)
    ids = [s["play_id"] for s in shots]
    assert len(ids) == len(set(ids))


def test_fetch_shots_skips_unresolvable_shooter(monkeypatch):
    payload = {
        "boxscore": {"players": []},
        "plays": [
            {
                "id": "1",
                "shootingPlay": True,
                "scoringPlay": False,
                "scoreValue": 0,
                "type": {"text": "Jump Shot"},
                "participants": [{"athlete": {"id": "999"}}],
                "text": "misses",
                "coordinate": {"x": 1, "y": 2},
            }
        ],
    }
    monkeypatch.setattr(espn_api, "_get", lambda *a, **k: payload)
    assert espn_api.fetch_shots("999") == []
