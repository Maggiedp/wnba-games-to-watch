from src.api.routes import render_homepage


def test_homepage_defines_is_final_status_helper():
    html = render_homepage()
    # Mirrors isLiveStatus; scoped to STATUS_FINAL only (not postponed/canceled).
    assert "function isFinalStatus(status)" in html
    assert "status === 'STATUS_FINAL'" in html
