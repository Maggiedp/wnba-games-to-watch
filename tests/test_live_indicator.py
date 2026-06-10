from src.api.routes import render_homepage


def test_shared_head_has_live_pill_css():
    html = render_homepage()
    # tuned-red token (not a generic red), scoped to live state
    assert "--live:" in html
    assert ".live-pill" in html
    assert ".live-dot" in html


def test_live_dot_animation_is_reduced_motion_guarded():
    html = render_homepage()
    assert "@keyframes live-breathe" in html
    # the pulse must only run when the user hasn't asked to reduce motion
    assert "prefers-reduced-motion: no-preference" in html


def test_homepage_defines_live_pill_helper():
    html = render_homepage()
    assert "function livePillHtml(game)" in html
    # helper emits the pill markup with the per-game id hook
    assert 'class="live-pill' in html
    assert "data-live-id=" in html


def test_all_three_homepage_renderers_emit_the_pill():
    html = render_homepage()
    # renderGameRow, renderGameCard, renderFeatured each call the helper
    assert html.count("livePillHtml(game)") >= 3
