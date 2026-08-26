import json

from src.api.routes import _importance_movers_html


class _R:
    def __init__(self, detail):
        self.importance_detail = detail


def test_renders_the_per_mover_level_label():
    payload = json.dumps(
        {
            "metric": "playoffs",
            "if_a_team": "Las Vegas Aces",
            "if_b_team": "Seattle Storm",
            "movers": [
                {"team": "Seattle Storm", "level": "semis", "if_a": 0.41, "if_b": 0.29}
            ],
        }
    )
    html = _importance_movers_html(_R(payload))
    assert "semis odds" in html
    assert "41%" in html and "29%" in html


def test_stored_rows_without_a_level_still_render():
    """Back-compat: rows written before this change have no 'level' key."""
    payload = json.dumps(
        {
            "metric": "championship",
            "if_a_team": "A",
            "if_b_team": "B",
            "movers": [{"team": "B", "if_a": 0.5, "if_b": 0.2}],
        }
    )
    html = _importance_movers_html(_R(payload))
    assert "title odds" in html
