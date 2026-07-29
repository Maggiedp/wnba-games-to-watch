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
