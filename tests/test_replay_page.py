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
    assert "g.has_detail" in html  # archived-game link gating shipped


def test_homepage_nav_links_to_replay(client):
    # /replay is reachable from the shared top nav (promoted out of the footer).
    r = client.get("/")
    assert r.status_code == 200
    assert '<a href="/replay" class="site-nav-link">Replay value</a>' in r.text


def test_replay_page_ships_midline_label_style(client):
    r = client.get("/replay")
    assert r.status_code == 200
    assert ".shape-mid-label" in r.text  # 50% label styled via _SHARED_HEAD


def test_replay_page_has_winner_explainer_and_lead_label(client):
    r = client.get("/replay")
    assert r.status_code == 200
    html = r.text
    # Winner-first explainer copy (authored literally on this static page).
    assert "every game climbs to the top" in html
    assert "nail-biter" in html
    # Only the lead card opts into the 50% label.
    assert "midLabel: lead" in html
