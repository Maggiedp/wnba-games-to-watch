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


def test_playoff_odds_page_ships_loading_and_empty_state(client):
    # A page the user navigates to must not render a blank shell when the odds
    # list is empty (the pre-6AM daily-run window / a missed run / an API error).
    # It starts with a Loading… placeholder and swaps in an explicit empty
    # message; the table is hidden until data arrives.
    html = client.get("/playoff-odds").text
    assert 'id="playoff-status"' in html  # status/loading element present
    assert ">Loading…</p>" in html  # initial placeholder
    assert 'id="playoff-table" hidden' in html  # table hidden until data loads
    # Distinct no-data vs failed-load copy (empty is not an error).
    assert "first simulation runs" in html  # empty-state copy
    assert "Refresh to try again" in html  # error-state copy


def test_playoff_odds_page_uses_card_and_explainer(client):
    # The table is framed in a surface card (like /rankings, /replay) with a
    # per-view explainer balancing the Rounds/Seeds toggle, and the Champ column
    # uses its own heat ramp (champHeatAlpha) rather than a slivering bar.
    html = client.get("/playoff-odds").text
    assert 'class="playoff-card"' in html
    assert 'id="playoff-explainer"' in html
    assert "function champHeatAlpha" in html  # helper injected for the Champ tint
    # .sort-toggle's display rule overrides the [hidden] attribute's UA
    # display:none, so this rule must ship or the view toggle shows before data
    # loads and the incomplete-snapshot gate silently breaks (browser-only).
    assert ".sort-toggle[hidden]" in html


def test_homepage_nav_links_to_playoff_odds(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<a href="/playoff-odds" class="site-nav-link">Playoff odds</a>' in r.text
