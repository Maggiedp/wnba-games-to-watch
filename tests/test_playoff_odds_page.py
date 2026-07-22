def test_playoff_odds_page_renders_with_tokens_replaced(client):
    r = client.get("/playoff-odds")
    assert r.status_code == 200
    html = r.text
    assert "Playoff Picture" in html  # H1
    assert "%%" not in html  # every %%TOKEN%% was replaced
    assert "/og-playoff-odds.png" in html  # og card wired up
    assert 'id="playoff-table"' in html  # table scaffold
    assert 'id="playoff-view-toggle"' in html  # Rounds/Seeds toggle present
    assert "function seedsViewAvailable" in html  # helper injected
    assert "function renderPlayoffPicture" in html  # page script present


def test_playoff_odds_page_has_no_collapse_toggle(client):
    # The full page is always visible — the homepage's Show/Hide button and its
    # collapse machinery must NOT come along.
    html = client.get("/playoff-odds").text
    assert 'id="playoff-toggle"' not in html
    assert "Show playoff picture" not in html
    assert "setupPlayoffToggle" not in html


def test_homepage_nav_links_to_playoff_odds(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<a href="/playoff-odds" class="site-nav-link">Playoff odds</a>' in r.text
