def test_page_renders_with_nav(client):
    r = client.get("/shot-making")
    assert r.status_code == 200
    html = r.text
    assert "Shot-making" in html
    assert 'href="/shot-making"' in html and "is-active" in html
    assert "/api/shot-making" in html  # client fetch wired
    # nav links to every view (spec: shared nav on every page)
    for href in (
        "/",
        "/rankings",
        "/replay",
        "/style",
        "/playoff-odds",
        "/transparency",
    ):
        assert f'href="{href}"' in html


def test_nav_exactly_one_active(client):
    html = client.get("/shot-making").text
    assert html.count('aria-current="page"') == 1


def test_page_tokens_fully_replaced(client):
    html = client.get("/shot-making").text
    assert "%%" not in html


def test_page_has_sort_toggle_and_helpers_injected(client):
    html = client.get("/shot-making").text
    assert 'id="shots-sort-toggle"' in html
    assert 'data-sort="added"' in html and 'data-sort="per100"' in html
    assert "function fmtSigned" in html
    assert "function dietBar" in html


def test_page_ships_loading_and_table_scaffold(client):
    html = client.get("/shot-making").text
    assert 'id="shots-status"' in html
    assert ">Loading…</p>" in html
    assert 'id="board" hidden' in html


def test_homepage_nav_links_to_shot_making(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<a href="/shot-making" class="site-nav-link">Shot-making</a>' in r.text


def test_og_shot_making_png(client):
    r = client.get("/og-shot-making.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert client.head("/og-shot-making.png").status_code == 200


def test_shot_making_page_og_image_points_to_own_card(client):
    html = client.get("/shot-making").text
    assert "/og-shot-making.png" in html
    assert "/og-home.png" not in html


def test_shot_making_page_injects_shot_chart_js():
    from src.api.routes import render_shot_making

    html = render_shot_making()
    assert "%%SHOT_CHART_JS%%" not in html  # token was substituted
    assert "buildShotChartSvg" in html  # helper shipped into page
    assert "shot-panel" in html  # panel scaffold present


def test_shot_making_panel_has_full_page_permalink():
    from src.api.routes import render_shot_making

    html = render_shot_making()
    assert "/player/${encodeURIComponent(data.athlete_id)}" in html
    assert "Full page" in html


def test_shot_making_page_ships_the_bridge_css_and_helper(client):
    html = client.get("/shot-making").text
    # the helper must be injected, not just referenced (already true via
    # %%SHOT_MAKING_JS%% before this task; kept as an injection guard)
    assert "function vsLeagueBridge(" in html
    # the CSS rules below are new in this task — the JS emits class="bridge-track"
    # / class="bridge-seg is-diet" (no leading dots), so these dotted selectors
    # only exist once the page's own <style> block carries them
    assert ".bridge-track" in html
    assert ".bridge-seg.is-diet" in html
    assert ".bridge-head" in html


def test_shot_making_page_explains_the_league_anchor_honestly(client):
    html = client.get("/shot-making").text
    # the rewritten note (Step 5) states both anchors now that they're genuinely
    # leaguewide, replacing the old qualified-pool-only phrasing
    assert "League average this season:" in html
    assert "League-average xPPS this season" not in html
    # F2: the ▲/▽ marker column is the most likely place to misread "▲ = good",
    # so the note itself must carry the honesty caveat, not just .bridge-key
    # (which a reader only sees after expanding a row).
    assert "that's her shot diet, not shot quality." in html


def test_bridge_spans_the_full_expand_panel_width(client):
    """The bridge is a THIRD child of `.shot-panel`'s 2-column grid. Without an
    explicit full-width span, auto-placement puts it in column 1 and pushes the
    chart into the narrow column with the zones dropping to a second row.

    This is invisible to the browser walk: `.shot-panel` collapses to one column
    at <=768px and the walk only runs 320/360/390/430. String-assert instead,
    mirroring the `.sort-toggle[hidden]` guard in test_playoff_odds_page.py."""
    html = client.get("/shot-making").text
    assert ".shot-panel .bridge { grid-column: 1 / -1; }" in html
