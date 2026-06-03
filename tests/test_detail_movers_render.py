import json
from types import SimpleNamespace
from src.api.routes import _importance_movers_html


def test_playoffs_movers_render():
    ranking = SimpleNamespace(
        importance_detail=json.dumps(
            {
                "metric": "playoffs",
                "if_a_team": "Seattle Storm",
                "if_b_team": "Chicago Sky",
                "movers": [{"team": "Connecticut Sun", "if_a": 0.58, "if_b": 0.41}],
            }
        )
    )
    html = _importance_movers_html(ranking)
    assert "Connecticut Sun" in html
    assert "playoff odds" in html
    assert "58%" in html and "41%" in html
    assert "Seattle Storm" in html and "Chicago Sky" in html


def test_championship_wording():
    ranking = SimpleNamespace(
        importance_detail=json.dumps(
            {
                "metric": "championship",
                "if_a_team": "Las Vegas Aces",
                "if_b_team": "New York Liberty",
                "movers": [{"team": "Las Vegas Aces", "if_a": 0.70, "if_b": 0.30}],
            }
        )
    )
    html = _importance_movers_html(ranking)
    assert "title odds" in html and "70%" in html


def test_none_and_empty_render_nothing():
    assert _importance_movers_html(SimpleNamespace(importance_detail=None)) == ""
    empty = SimpleNamespace(
        importance_detail=json.dumps({"metric": "playoffs", "movers": []})
    )
    assert _importance_movers_html(empty) == ""
    assert _importance_movers_html(SimpleNamespace(importance_detail="not json")) == ""


def test_breakdown_section_includes_movers():
    from src.api.routes import _detail_breakdown_section

    ranking = SimpleNamespace(
        quality_score=50.0,
        importance_score=60.0,
        importance_detail=json.dumps(
            {
                "metric": "playoffs",
                "if_a_team": "Seattle Storm",
                "if_b_team": "Chicago Sky",
                "movers": [{"team": "Connecticut Sun", "if_a": 0.58, "if_b": 0.41}],
            }
        ),
    )
    team_a = SimpleNamespace(name="Seattle Storm", bpi_rating=2.0)
    team_b = SimpleNamespace(name="Chicago Sky", bpi_rating=1.0)
    html = _detail_breakdown_section(ranking, team_a, team_b)
    assert "Connecticut Sun" in html and "What's at stake" in html
