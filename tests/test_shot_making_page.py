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
    # the JS emits class="bridge-axis" / class="bridge-mark is-expected" (no
    # leading dots), so these dotted selectors only exist once the page's own
    # <style> block carries them
    assert ".bridge-axis" in html
    assert ".bridge-mark.is-expected" in html
    assert ".bridge-mark.is-actual" in html
    assert ".bridge-head" in html


def test_shot_making_page_grounds_the_ring_against_the_panel(client):
    """The expected-value ring is an opaque disc that knocks the arrow tail out
    of its interior, so its fill must match whatever it sits on. The expand
    panel is --surface (white) while the shared rule defaults to the page's
    --bg (cream); without the override the ring renders as a visible cream
    disc on white. Browser-only symptom — no parse or layout error."""
    html = client.get("/shot-making").text
    assert "--bridge-ground: var(--surface)" in html


def test_shot_making_page_explains_the_league_anchor_honestly(client):
    html = client.get("/shot-making").text
    # the rewritten note (Step 5) states both anchors now that they're genuinely
    # leaguewide, replacing the old qualified-pool-only phrasing
    assert "League average this season:" in html
    assert "League-average xPPS this season" not in html
    # F2: the ▲/▽ marker column is the most likely place to misread "▲ = good",
    # so the note itself must carry the honesty caveat. It is now the ONLY
    # place that does: the expand panel's own caveat line (.bridge-key, "shot
    # diet ... isn't graded") went away with the waterfall, because the chart
    # that replaced it never grades a diet axis — the ring is labelled "league
    # average, from her spots", which is descriptive by construction.
    assert "that's her shot diet, not shot quality." in html


def test_bridge_spans_the_full_expand_panel_width(client):
    """The bridge is a THIRD child of `.shot-panel`'s 2-column grid. Without an
    explicit full-width span, auto-placement puts it in column 1 and pushes the
    chart into the narrow column with the zones dropping to a second row.

    The AUTHORITY on this invariant is now `assertShotPanelLayout` in
    tests/browser/overflow.test.js, which measures the rendered geometry at
    desktop widths (verified by deliberate break). This string-assert is kept
    only as the cheap non-browser duplicate — it still fails on a machine with
    no Chrome, where the walk cannot run at all. Being a text match, it is the
    weaker check: a browser can drop a rule whose text IS present (see the
    stray-`<style>` bug in PR #120), and a benign reformat fails it. If the two
    ever disagree, believe the browser."""
    html = client.get("/shot-making").text
    assert ".shot-panel .bridge { grid-column: 1 / -1;" in html


def _bridge_css_block(template_name: str) -> str:
    """The vs-league chart's CSS between its BRIDGE-CSS sentinels."""
    from src.api.routes import _load_template

    src = _load_template(template_name)
    start = src.index("/* BRIDGE-CSS-START")
    return src[start : src.index("/* BRIDGE-CSS-END */", start)]


def test_bridge_css_is_identical_in_both_templates():
    """The block is duplicated in shot_making.html and player.html (a 2-caller
    dupe under the repo's extract-on-the-3rd-caller rule), but it is not
    decorative: it carries `--lab-min`, which an inline min() clamp emitted by
    BOTH renderers depends on, and `--bridge-ground`, which the ring's opaque
    fill depends on. A drift between the copies is a browser-only bug on one
    surface with nothing else to catch it — and this block was rewritten three
    times in three review rounds, which is exactly when copies drift.

    The sentinel opens BELOW the header comment, which names the other template
    and is the one line that legitimately differs; everything inside must match
    byte for byte."""
    a = _bridge_css_block("shot_making.html")
    b = _bridge_css_block("player.html")
    assert a == b, "the vs-league CSS blocks have drifted between the two templates"
    # Guard the sentinels themselves against a well-meaning cleanup, and pin the
    # two cross-language contracts the block owns.
    assert "--lab-min: 9em" in a
    assert "position: static" in a  # the <=560px stacked-label mode
    assert "var(--bridge-ground, var(--bg))" in a


def _shot_chart_caption(template_name: str) -> str:
    """The caption under the shot chart, with wrapping whitespace collapsed.

    The two templates use different class names (`cap` vs `shot-cap`) and wrap
    the text differently, so compare the words, not the bytes."""
    import re

    from src.api.routes import _load_template

    src = _load_template(template_name)
    m = re.search(r'<p class="(?:shot-)?cap">(.*?)</p>', src, re.S)
    assert m, f"no shot-chart caption found in {template_name}"
    return " ".join(m.group(1).split())


def test_shot_chart_caption_is_identical_in_both_templates():
    """The same chart renders on /shot-making's expand panel and /player/{id},
    so one caption describes both. PR #128 already found these two surfaces
    drifted once (the legend wording), and this caption states the chart's
    read: it must not say "each dot is one shot", which stopped being true when
    marks became per-cell aggregates."""
    a = _shot_chart_caption("shot_making.html")
    b = _shot_chart_caption("player.html")
    assert a == b, "the shot-chart captions have drifted between the two templates"
    # The claim the aggregation rewrite invalidated.
    assert "one shot" not in a
    # The two things a reader needs to decode a mark.
    assert "more attempts" in a
    assert "points added" in a
