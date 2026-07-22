"""The shared top nav (promoted out of the footer) appears on every page, links
to every view, marks the current page active, and the homepage footer is slimmed
to meta links only."""

# path -> the nav label marked active on that page. The five (href, label) pairs
# are also every link the nav must expose on every page (discoverability).
NAV = (
    ("/", "Games"),
    ("/rankings", "Power rankings"),
    ("/replay", "Replay value"),
    ("/style", "Team style"),
    ("/playoff-odds", "Playoff odds"),
    ("/transparency", "Behind the numbers"),
)


def test_site_nav_on_every_page(client):
    """Every page renders the nav, links to all five views, and marks exactly
    its own view active."""
    for active_path, active_label in NAV:
        r = client.get(active_path)
        assert r.status_code == 200, active_path
        assert 'class="site-nav' in r.text, active_path  # base or masthead variant
        # Every view is reachable from every page.
        for href, label in NAV:
            assert f'href="{href}"' in r.text, (active_path, href)
            assert label in r.text, (active_path, label)
        # Exactly the current page's item is active.
        assert r.text.count('aria-current="page"') == 1, active_path
        assert (
            f'<a href="{active_path}" class="site-nav-link is-active" '
            f'aria-current="page">{active_label}</a>'
        ) in r.text, active_path


def test_homepage_nav_masthead_variant_and_slim_footer(client):
    """Homepage nav uses the in-masthead variant, and the footer is slimmed to
    meta links (the three view links moved to the nav)."""
    html = client.get("/").text
    assert "site-nav--in-masthead" in html
    start = html.index('class="footer-links"')
    footer = html[start : html.index("</p>", start)]
    assert "How it works" in footer
    assert "Methodology" in footer
    # View links live in the nav now, not the footer meta line.
    assert "Power rankings" not in footer
    assert "Replay value" not in footer
    assert "Behind the numbers" not in footer
