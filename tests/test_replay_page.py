def test_replay_page_renders_with_tokens_replaced(client):
    r = client.get("/replay")
    assert r.status_code == 200
    html = r.text
    assert "Replay Value" in html
    assert "%%" not in html  # every %%TOKEN%% was replaced
    assert "/og-replay.png" in html  # og card wired up
    assert "function buildShapeSvg" in html  # renderer injected
    assert 'id="replay-grid"' in html
    assert 'data-sort="comeback"' in html
