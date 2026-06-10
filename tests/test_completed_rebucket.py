from src.api.routes import render_homepage


def test_homepage_defines_is_final_status_helper():
    html = render_homepage()
    # Mirrors isLiveStatus; scoped to STATUS_FINAL only (not postponed/canceled).
    assert "function isFinalStatus(status)" in html
    assert "status === 'STATUS_FINAL'" in html


def test_applyfilters_excludes_final_games_from_upcoming_and_featured():
    html = render_homepage()
    # Featured-hero candidate guard (a finished game must not become "Top pick").
    assert "if (isFinalStatus(g.game_status)) return false;" in html
    # Upcoming-list guard.
    assert "if (isFinalStatus(game.game_status)) return false;" in html


def test_hydrate_live_wp_refilters_when_a_game_finalizes():
    html = render_homepage()
    # After hydrating live games, if any flipped to FINAL, re-run applyFilters
    # so the finished game drops out of the upcoming list.
    assert "const hadNewlyFinal = live.some(g => isFinalStatus(g.game_status));" in html
    assert "if (hadNewlyFinal) applyFilters();" in html
