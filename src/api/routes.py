"""API routes and response models for WNBA Games to Watch."""

import html as _html
import json
import logging
import math
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from src.api import blurbs
from src.data.espn_api import today_et
from src.db.queries import (
    get_game_fields,
    get_head_to_head,
    get_playoff_probabilities,
    get_teams_by_ids,
)
from src.db.schema import DailyRanking, Game


_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _load_template(name: str) -> str:
    """Read a template or JS partial shipped alongside this module.

    Also loads the shared/detail JS partials under ``templates/js/`` that are
    injected into those pages' ``<script>`` blocks (see ``_SHARED_JS`` /
    ``_WP_CHART_JS``).

    For STATIC pages only (homepage, transparency): the caller substitutes
    trusted build-time constants with plain ``str.replace`` of ``%%TOKEN%%``
    markers, which does no HTML escaping. Data-bearing pages (the game detail
    page) render through the jinja2 ``_jinja_env`` with autoescaping instead —
    never feed ESPN/DB-derived values through the ``%%TOKEN%%`` path.
    """
    with open(os.path.join(_TEMPLATE_DIR, name), encoding="utf-8") as f:
        return f.read()


_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
    # Templates ship frozen in the container image and never change at runtime
    # (the homepage/transparency pages are likewise precomputed at import), so
    # skip the per-request mtime stat that auto_reload would otherwise do.
    auto_reload=False,
)


def escape_html(s: object) -> str:
    return _html.escape(str(s), quote=True)


logger = logging.getLogger(__name__)


class GameResponse(BaseModel):
    """Response model for a game."""

    date: str
    time: str
    time_utc: str | None = None
    team_a: str
    team_b: str
    team_a_abbr: str = ""
    team_b_abbr: str = ""
    team_a_logo: str = ""
    team_b_logo: str = ""
    # quality_score / overall_score are None when a completed game has no
    # DailyRanking row (e.g. a missed daily-update day backfilled later).
    quality_score: float | None = None
    importance_score: float | None = None
    overall_score: float | None = None
    broadcaster: str = ""
    team_a_playoff_prob: float | None = None
    team_b_playoff_prob: float | None = None
    win_prob_a: float | None = None
    espn_id: str | None = None
    game_status: str | None = (
        None  # STATUS_SCHEDULED | STATUS_IN_PROGRESS | STATUS_FINAL
    )
    final_score_a: int | None = None
    final_score_b: int | None = None
    excitement_index: float | None = None

    model_config = ConfigDict(from_attributes=True)


class PlayoffOddsResponse(BaseModel):
    """Per-team round-by-round playoff probabilities for the standings section."""

    team: str
    abbreviation: str
    logo_url: str
    make_playoffs_prob: float  # 0.0–1.0
    reach_semis_prob: float  # 0.0–1.0; 0 if not yet computed
    reach_finals_prob: float  # 0.0–1.0
    win_championship_prob: float  # 0.0–1.0


def format_games_response(
    rankings: list[DailyRanking],
    session: Session,
    game_status_by_espn_id: dict[str, str] | None = None,
) -> list[GameResponse]:
    """Format DailyRanking objects into GameResponse objects."""
    if not rankings:
        return []

    team_ids = {r.team_a_id for r in rankings} | {r.team_b_id for r in rankings}
    teams = get_teams_by_ids(session, team_ids)
    fields = get_game_fields(
        session, [(r.date, r.team_a_id, r.team_b_id) for r in rankings]
    )
    today = today_et()
    prob_by_team_id = get_playoff_probabilities(session, today)

    def _make_playoffs_prob(team_id: int) -> float | None:
        rec = prob_by_team_id.get(team_id)
        return rec.make_playoffs_prob if rec is not None else None

    results = []
    for ranking in rankings:
        team_a = teams.get(ranking.team_a_id)
        team_b = teams.get(ranking.team_b_id)

        if not team_a or not team_b:
            logger.warning(
                f"Team not found for ranking: {ranking.team_a_id}, {ranking.team_b_id}"
            )
            continue

        key = (ranking.date, ranking.team_a_id, ranking.team_b_id)
        gf = fields.get(key)
        time_val = gf.time if gf else ""
        time_utc_val = gf.time_utc if gf else None
        espn_id = gf.espn_id if gf else None
        final_score_a = gf.final_score_a if gf else None
        final_score_b = gf.final_score_b if gf else None
        excitement_index = gf.excitement_index if gf else None
        # Game.broadcaster (via gf) wins over DailyRanking.broadcaster — the
        # former reflects the latest ESPN data, the latter froze at scoring.
        broadcaster = gf.broadcaster if gf else ranking.broadcaster
        game_status = (
            game_status_by_espn_id.get(espn_id)
            if game_status_by_espn_id and espn_id
            else None
        )

        results.append(
            GameResponse(
                date=ranking.date,
                time=time_val,
                time_utc=time_utc_val,
                team_a=team_a.name,
                team_b=team_b.name,
                team_a_abbr=team_a.abbreviation or "",
                team_b_abbr=team_b.abbreviation or "",
                team_a_logo=team_a.logo_url or "",
                team_b_logo=team_b.logo_url or "",
                quality_score=ranking.quality_score,
                importance_score=ranking.importance_score,
                overall_score=ranking.overall_score,
                broadcaster=broadcaster,
                team_a_playoff_prob=_make_playoffs_prob(ranking.team_a_id),
                team_b_playoff_prob=_make_playoffs_prob(ranking.team_b_id),
                win_prob_a=ranking.win_prob_a,
                espn_id=espn_id,
                game_status=game_status,
                final_score_a=final_score_a,
                final_score_b=final_score_b,
                excitement_index=excitement_index,
            )
        )

    return results


_SITE_URL = "https://wumbers.com"
_SITE_TITLE = "WNBA Games to Watch"
_SITE_DESCRIPTION = (
    "A nightly ranking of the best WNBA matchups, weighing team quality and "
    "playoff stakes. Filter by where you can watch."
)

# Shared <head> design tokens: fonts + CSS custom properties + base resets.
# Plain string (not f-string) — braces are literal, no escaping needed.
# Used by the homepage; intended for reuse by the game detail page.
_SHARED_HEAD = """\
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;0,9..144,700;0,9..144,900;1,9..144,500&family=Albert+Sans:wght@400;500;600&display=swap" rel="stylesheet">

        <style>
            :root {
                --navy: #0d1b2a;
                --navy-3: #2b3a52;
                --orange: #ff6b00;
                --orange-deep: #a03c00;
                --live: #d6442e;
                --bg: #f7f5f0;
                --surface: #ffffff;
                --text: #0d1b2a;
                --text-muted: #5a6573;
                --text-subtle: #8a929d;
                --line: #e7e2d8;
                --line-soft: #f0ebe1;

                --display: 'Fraunces', Georgia, 'Times New Roman', serif;
                --body: 'Albert Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            }
            * { margin: 0; padding: 0; box-sizing: border-box; }
            html { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
            body {
                font-family: var(--body);
                background: var(--bg);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                font-size: 16px;
                line-height: 1.45;
            }
            .live-pill {
                display: none;
                align-items: center;
                gap: 5px;
                font-family: var(--display);
                font-weight: 700;
                font-size: 0.8rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                color: var(--live);
                line-height: 1;
            }
            .live-pill.is-live { display: inline-flex; }
            .live-dot {
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: var(--live);
                flex: none;
            }
            @media (prefers-reduced-motion: no-preference) {
                .live-dot { animation: live-breathe 1.8s ease-in-out infinite; }
            }
            @keyframes live-breathe {
                0%, 100% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.35; transform: scale(0.78); }
            }
            .chart { width: 100%; overflow-x: auto; }
            .chart svg { max-width: 100%; height: auto; display: block; }
            .empty { color: var(--text-subtle); font-style: italic; }
            .legend { display: flex; flex-wrap: wrap; gap: 8px 14px; margin-top: 12px; font-size: .8rem; }
            .legend-row { display: inline-flex; align-items: baseline; gap: 7px; padding: 3px 11px; border: 1px solid var(--line); border-radius: 999px; background: transparent; color: var(--text-muted); cursor: pointer; font: inherit; font-size: .82rem; transition: background .12s ease, color .12s ease, border-color .12s ease; }
            .legend-row .rank { color: var(--text-subtle); font-variant-numeric: tabular-nums; font-size: .72rem; }
            .legend-row .value { color: var(--text-subtle); font-variant-numeric: tabular-nums; }
            .legend-row .delta { font-variant-numeric: tabular-nums; font-size: .72rem; font-weight: 600; }
            .legend-row .delta.up { color: #1a7f4b; }
            .legend-row .delta.down { color: #c0392b; }
            .legend-row .delta.flat { color: var(--text-subtle); }
            .legend-row:hover, .legend-row.active, .legend-row:focus-visible { background: var(--orange); border-color: var(--orange); color: #fff; outline: none; }
            .legend-row:hover .rank, .legend-row.active .rank, .legend-row:hover .value, .legend-row.active .value, .legend-row:hover .delta, .legend-row.active .delta { color: rgba(255, 255, 255, .82); }
            .trend-line { fill: none; stroke: var(--navy); stroke-opacity: .15; stroke-width: 1.4; pointer-events: none; transition: stroke-opacity .12s ease, stroke-width .12s ease; }
            .trend-line.hi { stroke: var(--orange); stroke-opacity: 1; stroke-width: 2.6; }
            .trend-hit { fill: none; stroke: transparent; stroke-width: 12; pointer-events: stroke; cursor: pointer; }
            .trend-label { fill: var(--text-subtle); font-size: 9.5px; font-variant-numeric: tabular-nums; cursor: pointer; }
            .trend-label.hi { fill: var(--orange); font-weight: 600; }\
"""

# SVG win-probability line-chart builder + live header for the game detail
# page. Source of truth is templates/js/detail_chart.js (single-sourced +
# Node-tested); interpolated via {{ wp_chart_js | safe }} into the detail
# page's <script>. Self-contained: uses only local vars, the .wp-chart-svg
# CSS class, and the shared escapeHtml/isLiveStatus from shared.js.
_WP_CHART_JS = _load_template("js/detail_chart.js")

# Shared client-side JS helpers used by all rendered pages (homepage,
# transparency, detail). Source of truth is templates/js/shared.js (single-
# sourced + Node-tested); injected via %%SHARED_JS%% (.replace pages) or
# {{ shared_js | safe }} (jinja detail page) so it can't drift between pages.
_SHARED_JS = _load_template("js/shared.js")

# Homepage-only pure JS helpers (excitement scoring, win-prob text, the
# completed-rebucket core). Source of truth is templates/js/homepage_helpers.js
# (single-sourced + Node-tested); injected via %%HOMEPAGE_HELPERS_JS%% after
# %%SHARED_JS%% (deps-first: winProbText uses shared.js escapeHtml).
_HOMEPAGE_HELPERS_JS = _load_template("js/homepage_helpers.js")

# Multi-team trend line chart (buildLineChartSvg + renderTeamTrendChart),
# single-sourced + Node-tested; injected via %%LINE_CHART_JS%% after
# %%SHARED_JS%% (deps-first: it uses shared.js escapeHtml). Used by the
# transparency Elo chart and the homepage playoff-odds chart.
_LINE_CHART_JS = _load_template("js/line_chart.js")

_HOMEPAGE_HTML = (
    _load_template("homepage.html")
    .replace("%%SITE_TITLE%%", _SITE_TITLE)
    .replace("%%SITE_DESCRIPTION%%", _SITE_DESCRIPTION)
    .replace("%%SITE_URL%%", _SITE_URL)
    .replace("%%SHARED_HEAD%%", _SHARED_HEAD)
    .replace("%%SHARED_JS%%", _SHARED_JS)
    .replace("%%LINE_CHART_JS%%", _LINE_CHART_JS)
    .replace("%%HOMEPAGE_HELPERS_JS%%", _HOMEPAGE_HELPERS_JS)
)

_TRANSPARENCY_HTML = (
    _load_template("transparency.html")
    .replace("%%SITE_TITLE%%", _SITE_TITLE)
    .replace("%%SITE_URL%%", _SITE_URL)
    .replace("%%SHARED_HEAD%%", _SHARED_HEAD)
    .replace("%%SHARED_JS%%", _SHARED_JS)
)

_RANKINGS_HTML = (
    _load_template("rankings.html")
    .replace("%%SITE_TITLE%%", _SITE_TITLE)
    .replace("%%SITE_URL%%", _SITE_URL)
    .replace("%%SHARED_HEAD%%", _SHARED_HEAD)
    .replace("%%SHARED_JS%%", _SHARED_JS)
    .replace("%%LINE_CHART_JS%%", _LINE_CHART_JS)
)


def render_game_detail(session: Session, espn_id: str) -> str | None:
    """Render the detail page for one game, or None if the espn_id is unknown."""
    game = session.query(Game).filter(Game.espn_id == espn_id).first()
    if game is None:
        return None

    teams = get_teams_by_ids(session, {game.team_a_id, game.team_b_id})
    team_a = teams.get(game.team_a_id)
    team_b = teams.get(game.team_b_id)
    if team_a is None or team_b is None:
        return None

    ranking = (
        session.query(DailyRanking)
        .filter(
            DailyRanking.date == game.date,
            DailyRanking.team_a_id == game.team_a_id,
            DailyRanking.team_b_id == game.team_b_id,
        )
        .first()
    )

    # Derive the season from the game's own date (ISO year prefix), not a
    # hardcoded literal, so H2H doesn't silently go empty in a future season.
    season_year = int(game.date[:4])
    h2h = get_head_to_head(
        session, game.team_a_id, game.team_b_id, season_year=season_year
    )

    return _render_game_detail_html(game, team_a, team_b, ranking, h2h)


_DETAIL_STYLE = """
            /* ---------- Header ---------- */
            .header {
                background: var(--navy);
                color: white;
                padding: 22px 32px 24px;
                border-bottom: 4px solid var(--orange);
            }
            .header-inner { max-width: 760px; margin: 0 auto; }
            .back-link {
                font-family: var(--body);
                font-size: 0.82rem;
                font-weight: 500;
                letter-spacing: 0.04em;
                color: #b9c4d4;
                text-decoration: none;
            }
            .back-link:hover { color: white; }

            /* ---------- Page shell ---------- */
            main {
                max-width: 760px;
                margin: 0 auto;
                padding: 36px 24px 80px;
                width: 100%;
                flex: 1;
            }
            .eyebrow {
                font-family: var(--body);
                font-size: 0.74rem;
                font-weight: 600;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                color: var(--text-subtle);
            }
            h1.matchup {
                font-family: var(--display);
                font-variation-settings: 'opsz' 144;
                font-weight: 900;
                font-size: clamp(2rem, 6vw, 3.1rem);
                line-height: 1.04;
                letter-spacing: -0.02em;
                margin: 10px 0 0;
            }
            h1.matchup .slash { color: var(--orange); font-weight: 500; }

            /* ---------- Overall score + summary ---------- */
            .overall-block {
                display: flex;
                align-items: baseline;
                gap: 16px;
                margin: 28px 0 6px;
            }
            .overall-num {
                font-family: var(--display);
                font-variation-settings: 'opsz' 144;
                font-weight: 900;
                font-size: 4rem;
                line-height: 0.9;
                color: var(--orange);
                font-feature-settings: 'tnum' on;
            }
            .overall-num.empty { color: var(--text-subtle); }
            .overall-label {
                font-family: var(--body);
                font-size: 0.74rem;
                font-weight: 600;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                color: var(--text-muted);
            }
            .summary {
                font-size: 1.05rem;
                color: var(--text-muted);
                max-width: 60ch;
                margin-bottom: 8px;
            }

            /* ---------- Sections ---------- */
            section { margin-top: 40px; }
            .section-title {
                font-family: var(--display);
                font-variation-settings: 'opsz' 72;
                font-weight: 700;
                font-size: 1.4rem;
                letter-spacing: -0.01em;
                margin-bottom: 16px;
            }

            /* ---------- Win-prob tug-of-war ---------- */
            .wp-bar {
                display: flex;
                height: 44px;
                border-radius: 10px;
                overflow: hidden;
                border: 1px solid var(--line);
            }
            .wp-seg {
                display: flex;
                align-items: center;
                color: white;
                font-family: var(--body);
                font-weight: 600;
                font-size: 0.9rem;
                white-space: nowrap;
                overflow: hidden;
            }
            .wp-seg.a { background: var(--orange); padding-left: 14px; }
            .wp-seg.b { background: var(--navy); justify-content: flex-end; padding-right: 14px; }
            .wp-seg.neutral { background: var(--navy-3); }
            .wp-note {
                margin-top: 12px;
                color: var(--text-muted);
                font-size: 0.98rem;
            }
            .wp-note.muted { color: var(--text-subtle); font-style: italic; }
            .wp-note-qualifier {
                display: block;
                margin-top: 4px;
                font-size: 0.68rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--text-subtle);
                font-weight: 500;
            }

            /* ---------- Breakdown mini-bars ---------- */
            .breakdown-block { margin-bottom: 24px; }
            .mini-bar-label {
                font-family: var(--body);
                font-size: 0.74rem;
                font-weight: 600;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: var(--text-muted);
            }
            .mini-bar-track {
                display: block;
                height: 9px;
                background: var(--line-soft);
                border-radius: 999px;
                overflow: hidden;
                margin: 8px 0 10px;
            }
            .mini-bar-fill {
                display: block;
                height: 100%;
                border-radius: 999px;
            }
            .mini-bar-fill.quality { background: linear-gradient(90deg, #ff6b00, #ff9540); }
            .mini-bar-fill.importance { background: linear-gradient(90deg, #2b3a52, #5a6573); }
            .breakdown-text { color: var(--text-muted); font-size: 0.98rem; }
            .importance-movers { margin-top: 12px; }
            .importance-movers .movers-heading {
                font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em;
                color: var(--text-muted); margin: 0 0 4px;
            }
            .importance-movers ul { margin: 0; padding-left: 18px; }
            .importance-movers li { font-size: 0.9rem; line-height: 1.5; }

            /* ---------- How this is scored ---------- */
            details.scored {
                margin-top: 28px;
                border-top: 1px solid var(--line);
                padding-top: 16px;
            }
            details.scored summary {
                cursor: pointer;
                font-family: var(--body);
                font-size: 0.74rem;
                font-weight: 600;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: var(--text-muted);
                list-style: none;
            }
            details.scored summary::-webkit-details-marker { display: none; }
            details.scored summary::before { content: '+ '; color: var(--orange); }
            details.scored[open] summary::before { content: '– '; }
            details.scored p {
                margin-top: 14px;
                color: var(--text-muted);
                font-size: 0.95rem;
                max-width: 64ch;
            }
            details.scored p + p { margin-top: 10px; }

            /* ---------- Head-to-head ---------- */
            .h2h-row {
                display: grid;
                grid-template-columns: 110px 1fr auto;
                gap: 12px;
                align-items: baseline;
                padding: 12px 0;
                border-bottom: 1px solid var(--line-soft);
            }
            .h2h-row:last-child { border-bottom: none; }
            .h2h-date { color: var(--text-subtle); font-size: 0.85rem; }
            .h2h-score {
                font-family: var(--display);
                font-weight: 600;
                font-feature-settings: 'tnum' on;
            }
            .h2h-excite {
                font-size: 0.8rem;
                color: var(--text-subtle);
                font-style: italic;
            }
            .h2h-empty { color: var(--text-muted); font-style: italic; }

            /* ---------- WP chart slot ---------- */
            #wp-chart { margin-top: 4px; min-height: 1px; }
            .chart-placeholder { color: var(--text-subtle); font-style: italic; font-size: 0.95rem; }
            .wp-chart-header {
                font-size: 0.95rem;
                color: var(--text);
                margin-bottom: 6px;
            }
            .wp-chart-header .wp-chart-status { color: var(--text-muted); }
            /* Live win-probability readout (leading team + current WP%) */
            .wp-live-status {
                font-family: var(--body);
                font-size: 0.72rem;
                font-weight: 600;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: var(--live);
                display: flex;
                align-items: center;
                gap: 7px;
                margin-bottom: 8px;
            }
            .wp-live-status .wp-chart-status { color: var(--text-muted); letter-spacing: 0.05em; }
            .wp-live-readout { display: flex; align-items: baseline; gap: 9px; }
            .wp-live-team {
                font-family: var(--body);
                font-size: 0.8rem;
                font-weight: 700;
                letter-spacing: 0.1em;
                text-transform: uppercase;
                color: var(--text-muted);
            }
            .wp-live-pct {
                font-family: var(--display);
                font-variation-settings: 'opsz' 72;
                font-weight: 900;
                font-size: 2.1rem;
                line-height: 0.95;
                color: var(--orange);
                font-feature-settings: 'tnum' on;
            }
            .wp-live-label {
                font-family: var(--body);
                font-size: 0.72rem;
                font-weight: 600;
                letter-spacing: 0.06em;
                text-transform: uppercase;
                color: var(--text-subtle);
                margin: 5px 0 12px;
            }
            .wp-chart-svg {
                width: 100%;
                height: 150px;
                display: block;
                overflow: visible;
            }
"""


def _detail_meta_line(game) -> str:
    """Uppercase eyebrow: DATE · TIME · BROADCASTER (broadcaster optional).

    The date+time are wrapped in a `.meta-when` span carrying `time_utc`; the
    detail page's script localizes them to the viewer's timezone on load (the
    rest of the site derives local display from `time_utc` too). The server text
    is the ET fallback shown if JS is off or `time_utc` is missing.
    """
    when_parts = []
    if game.date:
        try:
            dt = datetime.strptime(game.date, "%Y-%m-%d")
            friendly_date = f"{dt.strftime('%a %b')} {dt.day}"  # e.g. "Tue Jun 2"
        except ValueError:
            friendly_date = game.date
        when_parts.append(escape_html(friendly_date))
    if game.time:
        when_parts.append(escape_html(game.time))
    when = " · ".join(when_parts)
    time_utc = escape_html(game.time_utc or "")
    line = f'<span class="meta-when" data-time-utc="{time_utc}">{when}</span>'

    broadcaster = (game.broadcaster or "").strip()
    if broadcaster and broadcaster.upper() != "TBD":
        line += f" · {escape_html(broadcaster)}"
    return line


def _detail_win_prob_section(ranking, team_a, team_b) -> str:
    """Tug-of-war bar + Elo blurb. Neutral 50/50 when not simulated."""
    abbr_a = escape_html(team_a.abbreviation or team_a.name)
    abbr_b = escape_html(team_b.abbreviation or team_b.name)
    win_prob_a = None if ranking is None else ranking.win_prob_a

    if win_prob_a is None:
        return f"""
                <div class="wp-bar" role="img" aria-label="Win probability not simulated">
                    <span class="wp-seg neutral a" style="width: 50%">{abbr_a}</span>
                    <span class="wp-seg neutral b" style="width: 50%">{abbr_b}</span>
                </div>
                <p class="wp-note muted">Not simulated.</p>"""

    pct_a = win_prob_a * 100
    pct_b = 100 - pct_a
    note = escape_html(blurbs.win_prob_blurb(win_prob_a, team_a.name, team_b.name))
    return f"""
                <div class="wp-bar" role="img" aria-label="{abbr_a} {pct_a:.0f} percent, {abbr_b} {pct_b:.0f} percent">
                    <span class="wp-seg a" style="width: {pct_a:.1f}%">{abbr_a} {pct_a:.0f}%</span>
                    <span class="wp-seg b" style="width: {pct_b:.1f}%">{pct_b:.0f}% {abbr_b}</span>
                </div>
                <p class="wp-note">{note}<span class="wp-note-qualifier">Pregame projection</span></p>"""


def _coerce_fraction(value) -> float | None:
    """Return value as a finite float, or None if it isn't a usable number.

    Rejects bools, None, strings, and NaN/inf so a schema-skewed
    importance_detail row degrades gracefully instead of raising mid-render.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return value


def _short_team_name(name: str) -> str:
    """Drop the nickname (last word) so "New York Liberty" -> "New York".

    WNBA team names are "<City> <Nickname>"; the city alone reads cleanly in
    the repeated "if <team> wins" clauses next to the full bold mover name.
    Returns the full string unchanged for single-word or empty input.
    """
    parts = str(name).split()
    return " ".join(parts[:-1]) if len(parts) > 1 else str(name)


def _importance_movers_html(ranking) -> str:
    """Render the 'What's at stake' directional-odds block, or '' when absent.

    Reads ranking.importance_detail (JSON written by daily_update). Each mover
    line shows the team's odds under each game outcome. Returns '' for missing,
    malformed, or empty payloads so the caller falls back to the bar + blurb.
    """
    raw = getattr(ranking, "importance_detail", None)
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    # The payload is server-authored, but a schema-skewed or hand-edited row
    # must degrade to the bar+blurb fallback, never 500 the detail page.
    if not isinstance(data, dict):
        return ""
    movers = data.get("movers")
    if not isinstance(movers, list):
        return ""

    odds_label = (
        "title odds" if data.get("metric") == "championship" else "playoff odds"
    )
    a_team = escape_html(_short_team_name(data.get("if_a_team", "Team A")))
    b_team = escape_html(_short_team_name(data.get("if_b_team", "Team B")))

    lines = []
    for m in movers:
        if not isinstance(m, dict):
            continue
        if_a = _coerce_fraction(m.get("if_a"))
        if_b = _coerce_fraction(m.get("if_b"))
        if if_a is None or if_b is None:
            continue
        team = escape_html(m.get("team", ""))
        if_a_pct = max(0.0, min(1.0, if_a)) * 100
        if_b_pct = max(0.0, min(1.0, if_b)) * 100
        lines.append(
            f"<li><strong>{team}</strong> {odds_label}: "
            f"<strong>{if_a_pct:.0f}%</strong> if {a_team} wins → "
            f"<strong>{if_b_pct:.0f}%</strong> if {b_team} wins</li>"
        )
    if not lines:
        return ""
    return (
        '<div class="importance-movers">'
        '<p class="movers-heading">What\'s at stake</p>'
        f"<ul>{''.join(lines)}</ul></div>"
    )


def _detail_breakdown_section(ranking, team_a, team_b) -> str:
    """Quality (orange) + Importance (navy) mini-bars with blurbs."""
    if ranking is None:
        return """
                <div class="breakdown-block">
                    <span class="mini-bar-label">Quality — not simulated · 60% of score</span>
                    <span class="mini-bar-track" aria-hidden="true"></span>
                    <p class="breakdown-text">Not simulated, so there's no quality score for this game.</p>
                </div>
                <div class="breakdown-block">
                    <span class="mini-bar-label">Importance — not simulated · 40% of score</span>
                    <span class="mini-bar-track" aria-hidden="true"></span>
                    <p class="breakdown-text">Not simulated, so there's no importance score for this game.</p>
                </div>"""

    quality = ranking.quality_score
    importance = ranking.importance_score
    q_pct = 0.0 if quality is None else max(0.0, min(100.0, quality))
    q_label = "not simulated" if quality is None else f"{quality:.0f}"
    q_text = escape_html(
        blurbs.quality_blurb(
            quality or 0.0,
            team_a.bpi_rating or 0.0,
            team_b.bpi_rating or 0.0,
            team_a.name,
            team_b.name,
        )
    )

    if importance is None:
        i_label = "Importance — not simulated · 40% of score"
        i_track = '<span class="mini-bar-track" aria-hidden="true"></span>'
    else:
        i_pct = max(0.0, min(100.0, importance))
        i_label = f"Importance — {importance:.0f} · 40% of score"
        i_track = (
            '<span class="mini-bar-track" aria-hidden="true">'
            f'<span class="mini-bar-fill importance" style="width: {i_pct:.1f}%"></span></span>'
        )
    i_text = escape_html(blurbs.importance_blurb(importance))
    movers_html = _importance_movers_html(ranking)

    return f"""
                <div class="breakdown-block">
                    <span class="mini-bar-label">Quality — {q_label} · 60% of score</span>
                    <span class="mini-bar-track" aria-hidden="true"><span class="mini-bar-fill quality" style="width: {q_pct:.1f}%"></span></span>
                    <p class="breakdown-text">{q_text}</p>
                </div>
                <div class="breakdown-block">
                    <span class="mini-bar-label">{i_label}</span>
                    {i_track}
                    <p class="breakdown-text">{i_text}</p>
                    {movers_html}
                </div>"""


def _detail_h2h_section(game, team_a, team_b, h2h) -> str:
    """Ledger of completed season meetings; 'First meeting' when empty."""
    if not h2h:
        return '<p class="h2h-empty">First meeting of the season.</p>'

    name_by_id = {game.team_a_id: team_a, game.team_b_id: team_b}
    rows = []
    for g in h2h:
        ta = name_by_id.get(g.team_a_id)
        tb = name_by_id.get(g.team_b_id)
        abbr_a = (ta.abbreviation or ta.name) if ta else "?"
        abbr_b = (tb.abbreviation or tb.name) if tb else "?"
        sa = "" if g.final_score_a is None else g.final_score_a
        sb = "" if g.final_score_b is None else g.final_score_b
        score = f"{escape_html(abbr_a)} {escape_html(sa)} – {escape_html(sb)} {escape_html(abbr_b)}"
        excite = ""
        if g.excitement_index is not None:
            excite = (
                f'<span class="h2h-excite">excitement {g.excitement_index:.0f}</span>'
            )
        rows.append(
            f"""
                <div class="h2h-row">
                    <span class="h2h-date">{escape_html(g.date or "")}</span>
                    <span class="h2h-score">{score}</span>
                    {excite}
                </div>"""
        )
    return "".join(rows)


def _render_game_detail_html(game, team_a, team_b, ranking, h2h) -> str:
    name_a = team_a.name
    name_b = team_b.name
    title = f"{name_a} vs {name_b} — {_SITE_TITLE}"
    meta_line = _detail_meta_line(game)

    if ranking is None or ranking.overall_score is None:
        overall_html = '<span class="overall-num empty">—</span>'
        summary = "Not simulated — no overall score for this game yet."
    else:
        overall_html = f'<span class="overall-num">{ranking.overall_score:.0f}</span>'
        summary = (
            f"{name_a} vs {name_b} scores "
            f"{ranking.overall_score:.0f} out of 100 overall — "
            "60% matchup quality, 40% playoff importance."
        )

    return _jinja_env.get_template("game_detail.html").render(
        title=title,
        summary=summary,
        name_a=name_a,
        name_b=name_b,
        abbr_a=team_a.abbreviation or "",
        abbr_b=team_b.abbreviation or "",
        espn_id=game.espn_id or "",
        # Gate pre-tipoff polling to today's games so the LIVE pill/chart appear
        # at tipoff without a reload, while a tab left open on a far-future game
        # doesn't poll ESPN forever. Rendered as "true"/"false" via | lower.
        is_today=game.date == today_et(),
        season_year=game.date[:4],
        site_url=_SITE_URL,
        meta_line=meta_line,
        overall_html=overall_html,
        wp_section=_detail_win_prob_section(ranking, team_a, team_b),
        breakdown_section=_detail_breakdown_section(ranking, team_a, team_b),
        h2h_section=_detail_h2h_section(game, team_a, team_b, h2h),
        shared_head=_SHARED_HEAD,
        detail_style=_DETAIL_STYLE,
        wp_chart_js=_WP_CHART_JS,
        shared_js=_SHARED_JS,
    )


def render_homepage() -> str:
    return _HOMEPAGE_HTML


def render_transparency() -> str:
    """Server-rendered /transparency page. Static shell — all data is fetched
    client-side from /api/elo-history and /api/calibration, so it carries no
    per-request data and uses the same trusted-constant %%TOKEN%% substitution
    as the homepage (see _load_template)."""
    return _TRANSPARENCY_HTML


def render_rankings() -> str:
    """Server-rendered /rankings page. Static shell — Elo data is fetched
    client-side from /api/elo-history, so it carries no per-request data."""
    return _RANKINGS_HTML
