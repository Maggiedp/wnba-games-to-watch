"""API routes and response models for WNBA Games to Watch."""

import html as _html
import json
import logging
import math
import os
from datetime import datetime

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
    """Read an HTML template shipped alongside this module.

    For FULLY-STATIC pages only: the caller substitutes build-time constants
    with plain ``str.replace`` of ``%%TOKEN%%`` markers, which does no HTML
    escaping. Don't extend that pattern to data-bearing pages (the detail /
    transparency pages interpolate per-request game data) — move those to a
    real template engine (jinja2) with autoescaping instead.
    """
    with open(os.path.join(_TEMPLATE_DIR, name), encoding="utf-8") as f:
        return f.read()


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
            }\
"""

# SVG win-probability line-chart builder for the game detail page. Plain string
# (not f-string) — braces are SINGLE. Defined exactly once here; interpolated via
# {_WP_CHART_JS} into the detail page's <script> (the homepage no longer renders
# the chart). Self-contained: only uses local vars + the .wp-chart-svg CSS class.
_WP_CHART_JS = """
            function buildWpSvg(plays, homeAbbr, awayAbbr) {
                if (!plays || plays.length < 2) return '';
                // The labels are dropped into innerHTML below, and team
                // abbreviations are external ESPN/DB data — escape them (via the
                // shared escapeHtml) so a poisoned value can't inject markup.
                const homeLbl = escapeHtml(homeAbbr);
                const awayLbl = escapeHtml(awayAbbr);
                const W = 500, H = 150;
                const padL = 36, padR = 8, padT = 8, padB = 8;
                const cW = W - padL - padR;
                const cH = H - padT - padB;
                const midY = padT + cH / 2;
                const N = plays.length;

                const pts = plays.map((p, i) => [
                    padL + (i / (N - 1)) * cW,
                    padT + (1 - p.home_pct) * cH
                ]);

                const periodBounds = [];
                for (let i = 1; i < plays.length; i++) {
                    if (plays[i].period !== plays[i - 1].period) {
                        periodBounds.push(pts[i][0].toFixed(1));
                    }
                }

                const firstX = pts[0][0].toFixed(1);
                const lastX = pts[N - 1][0].toFixed(1);
                const botY = (H - padB).toFixed(1);

                // Home fill: always below the line (orange)
                const homePoly = [[firstX, botY],
                    ...pts.map(p => [p[0].toFixed(1), p[1].toFixed(1)]),
                    [lastX, botY]].map(p => `${p[0]},${p[1]}`).join(' ');

                const linePath = 'M ' + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L ');
                const [dotX, dotY] = pts[N - 1];
                const pBounds = periodBounds.map(x =>
                    `<line x1="${x}" y1="${padT}" x2="${x}" y2="${H - padB}" stroke="#e7e2d8" stroke-width="1" stroke-dasharray="2,2"/>`
                ).join('');

                return `<svg class="wp-chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Win probability chart">
                    <text x="${padL - 4}" y="${padT + 5}" text-anchor="end" font-size="9" fill="#8a929d">${awayLbl}</text>
                    <text x="${padL - 4}" y="${midY + 3}" text-anchor="end" font-size="9" fill="#8a929d">50%</text>
                    <text x="${padL - 4}" y="${H - padB}" text-anchor="end" font-size="9" fill="#8a929d">${homeLbl}</text>
                    <polygon points="${homePoly}" fill="rgba(255,107,0,0.15)"/>
                    <line x1="${padL}" y1="${midY.toFixed(1)}" x2="${W - padR}" y2="${midY.toFixed(1)}" stroke="#c8c2b8" stroke-width="1" stroke-dasharray="3,3"/>
                    ${pBounds}
                    <path d="${linePath}" fill="none" stroke="var(--orange)" stroke-width="1.5" stroke-linejoin="round"/>
                    <circle cx="${dotX.toFixed(1)}" cy="${dotY.toFixed(1)}" r="3" fill="var(--orange)"/>
                </svg>`;
            }
"""

# Shared client-side JS helpers used by both the homepage and the detail page.
# Plain string (not f-string) — braces are SINGLE. Interpolated via
# {_SHARED_JS} into each page's <script> so the XSS-escaping table and the
# live-status check are single-sourced and can't drift between pages.
_SHARED_JS = """
            function escapeHtml(s) {
                return String(s).replace(/[&<>"']/g, c => ({
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                })[c]);
            }

            // ESPN reports STATUS_HALFTIME between halves and STATUS_END_PERIOD
            // between quarters. Both are "live" for rendering and polling.
            function isLiveStatus(status) {
                return status === 'STATUS_IN_PROGRESS'
                    || status === 'STATUS_HALFTIME'
                    || status === 'STATUS_END_PERIOD';
            }
"""

_HOMEPAGE_HTML = (
    _load_template("homepage.html")
    .replace("%%SITE_TITLE%%", _SITE_TITLE)
    .replace("%%SITE_DESCRIPTION%%", _SITE_DESCRIPTION)
    .replace("%%SITE_URL%%", _SITE_URL)
    .replace("%%SHARED_HEAD%%", _SHARED_HEAD)
    .replace("%%SHARED_JS%%", _SHARED_JS)
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
                <p class="wp-note">{note}</p>"""


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
    name_a = escape_html(team_a.name)
    name_b = escape_html(team_b.name)
    # name_a/name_b are already escape_html'd; title is safe in attribute position.
    title = f"{name_a} vs {name_b} — {_SITE_TITLE}"

    meta_line = _detail_meta_line(game)

    if ranking is None or ranking.overall_score is None:
        overall_html = '<span class="overall-num empty">—</span>'
        summary = "Not simulated — no overall score for this game yet."
    else:
        overall_html = f'<span class="overall-num">{ranking.overall_score:.0f}</span>'
        summary = (
            f"{team_a.name} vs {team_b.name} scores "
            f"{ranking.overall_score:.0f} out of 100 overall — "
            "60% matchup quality, 40% playoff importance."
        )
    summary = escape_html(summary)

    wp_section = _detail_win_prob_section(ranking, team_a, team_b)
    breakdown_section = _detail_breakdown_section(ranking, team_a, team_b)
    h2h_section = _detail_h2h_section(game, team_a, team_b, h2h)
    espn_id = escape_html(game.espn_id or "")
    home_abbr_js = json.dumps(team_a.abbreviation or "")
    away_abbr_js = json.dumps(team_b.abbreviation or "")

    return f"""<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <meta name="description" content="{summary}">
        <meta property="og:title" content="{title}">
        <meta property="og:description" content="{summary}">
        <meta property="og:type" content="article">
        <meta property="og:url" content="{_SITE_URL}/game/{espn_id}">
        <meta property="og:image" content="{_SITE_URL}/game/{espn_id}/og.png">
        <meta property="og:image:width" content="1200">
        <meta property="og:image:height" content="630">
        <meta property="og:image:type" content="image/png">
        <meta property="og:image:alt" content="{summary}">
        <meta name="twitter:card" content="summary_large_image">
        <meta name="twitter:title" content="{title}">
        <meta name="twitter:description" content="{summary}">
        <meta name="twitter:image" content="{_SITE_URL}/game/{espn_id}/og.png">
{_SHARED_HEAD}
{_DETAIL_STYLE}
        </style>
    </head>
    <body>
        <header class="header">
            <div class="header-inner">
                <a class="back-link" href="/">&larr; back to rankings</a>
            </div>
        </header>
        <main>
            <p class="eyebrow">{meta_line}</p>
            <h1 class="matchup">{name_a} <span class="slash">&#9585;</span> {name_b}</h1>

            <div class="overall-block">
                {overall_html}
                <span class="overall-label">Overall</span>
            </div>
            <p class="summary">{summary}</p>

            <section>
                <h2 class="section-title">Win probability</h2>
                {wp_section}
            </section>

            <section>
                <h2 class="section-title">Why it's ranked here</h2>
                {breakdown_section}
                <details class="scored">
                    <summary>How this is scored</summary>
                    <p>Overall is a weighted blend: 60% matchup quality plus 40% playoff importance.</p>
                    <p>Quality is the harmonic mean of the two teams' ESPN BPI ratings, normalized on the live &plusmn;8 BPI spread — it rewards games where both teams are strong, not just one.</p>
                    <p>Importance is the swing in playoff odds this game produces in a Monte Carlo simulation, measured against a season-start ceiling.</p>
                    <p>Win probability is separate from quality: it's an Elo rating (with a +50 home-court bump), not BPI.</p>
                </details>
            </section>

            <section>
                <h2 class="section-title">Head-to-head &middot; {game.date[:4]}</h2>
                {h2h_section}
            </section>

            <section>
                <h2 class="section-title">Win-probability chart</h2>
                <div id="wp-chart" data-espn-id="{espn_id}"></div>
                <p class="chart-placeholder">Appears once the game tips off.</p>
            </section>
        </main>
        <script>
{_WP_CHART_JS}
{_SHARED_JS}

            // Localize the eyebrow date/time to the viewer's timezone (the ET
            // text rendered server-side is the fallback), matching the rest of
            // the site, which derives local display from time_utc.
            (function () {{
                const el = document.querySelector('.meta-when');
                if (!el || !el.dataset.timeUtc) return;
                const d = new Date(el.dataset.timeUtc);
                if (isNaN(d)) return;
                const ds = d.toLocaleDateString(undefined, {{ weekday: 'short', month: 'short', day: 'numeric' }});
                const ts = d.toLocaleTimeString(undefined, {{ hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }});
                el.textContent = ds + ' · ' + ts;
            }})();

            (function () {{
                const HOME_ABBR = {home_abbr_js};
                const AWAY_ABBR = {away_abbr_js};
                const chartEl = document.getElementById('wp-chart');
                const placeholderEl = document.querySelector('.chart-placeholder');
                if (!chartEl) return;
                const espnId = chartEl.dataset.espnId;
                if (!espnId) return;

                let pollTimer = null;
                let backoffIdx = 0;
                let hasChart = false;  // true once a real chart has been drawn

                const LIVE_INTERVAL = 30000;
                // Backoff for transient failures, mirroring the homepage
                // live-status poll (30s → 60s → 120s → 300s, then holds).
                const BACKOFF_MS = [30000, 60000, 120000, 300000];

                function stopPoll() {{
                    if (pollTimer) {{ clearTimeout(pollTimer); pollTimer = null; }}
                }}

                function scheduleNext(delayMs) {{
                    stopPoll();
                    pollTimer = setTimeout(load, delayMs);
                }}

                function showPlaceholder() {{
                    chartEl.innerHTML = '';
                    if (placeholderEl) placeholderEl.style.display = '';
                }}

                function showMessage(msg) {{
                    if (placeholderEl) placeholderEl.style.display = 'none';
                    chartEl.innerHTML = `<p class="chart-placeholder" style="display:block">${{escapeHtml(msg)}}</p>`;
                }}

                // Transient ESPN/API blip (network error, 5xx, or bad JSON):
                // /api/live-wp returns 502 on ESPN failure by design, so a single
                // hiccup must NOT kill the chart. Keep any chart already drawn and
                // retry with backoff instead of replacing it with an error.
                function transientFail() {{
                    if (!hasChart) showPlaceholder();
                    const delay = BACKOFF_MS[Math.min(backoffIdx, BACKOFF_MS.length - 1)];
                    backoffIdx++;
                    scheduleNext(delay);
                }}

                // Header line: away score / home score, then period+clock (live) or "Final".
                function headerHtml(data) {{
                    const away = `${{escapeHtml(AWAY_ABBR)}} ${{escapeHtml(String(data.away_score))}}`;
                    const home = `${{escapeHtml(HOME_ABBR)}} ${{escapeHtml(String(data.home_score))}}`;
                    const last = data.plays[data.plays.length - 1];
                    const period = last && last.period ? last.period : 0;
                    let status;
                    if (data.status === 'STATUS_HALFTIME') {{
                        status = 'Halftime';
                    }} else if (data.status === 'STATUS_END_PERIOD') {{
                        status = period <= 4 ? `End Q${{escapeHtml(String(period))}}` : 'End OT';
                    }} else if (isLiveStatus(data.status)) {{
                        const q = period <= 4 ? `Q${{escapeHtml(String(period))}}` : 'OT';
                        const clock = last && last.clock ? ` ${{escapeHtml(String(last.clock))}}` : '';
                        status = `${{q}}${{clock}}`;
                    }} else {{
                        status = 'Final';
                    }}
                    const statusHtml = status ? ` <span class="wp-chart-status">&middot; ${{status}}</span>` : '';
                    return `<div class="wp-chart-header">${{away}} &nbsp; ${{home}}${{statusHtml}}</div>`;
                }}

                function render(data) {{
                    const plays = (data && data.plays) || [];
                    if (!data || !data.status || data.status === 'STATUS_SCHEDULED' || plays.length === 0) {{
                        showPlaceholder();
                        return;
                    }}
                    if (placeholderEl) placeholderEl.style.display = 'none';
                    chartEl.innerHTML = headerHtml(data) + buildWpSvg(plays, HOME_ABBR, AWAY_ABBR);
                    hasChart = true;
                }}

                async function load() {{
                    let resp;
                    try {{
                        resp = await fetch(`/api/live-wp?espn_id=${{encodeURIComponent(espnId)}}`);
                    }} catch (e) {{
                        transientFail();  // network error — retry with backoff
                        return;
                    }}
                    if (resp.status === 404) {{
                        // Terminal: unknown/removed id. Stop; show the message only
                        // if we never managed to draw a chart.
                        stopPoll();
                        if (!hasChart) showMessage('Chart unavailable.');
                        return;
                    }}
                    if (!resp.ok) {{
                        transientFail();  // 5xx etc. — retry with backoff
                        return;
                    }}
                    let data;
                    try {{
                        data = await resp.json();
                    }} catch (e) {{
                        transientFail();  // bad JSON — retry with backoff
                        return;
                    }}
                    backoffIdx = 0;  // success resets the backoff sequence
                    render(data);
                    if (isLiveStatus(data.status)) {{
                        scheduleNext(LIVE_INTERVAL);
                    }} else {{
                        stopPoll();  // final / scheduled — nothing more to poll
                    }}
                }}

                load();
            }})();
        </script>
    </body>
    </html>"""


def render_homepage() -> str:
    return _HOMEPAGE_HTML


def render_transparency() -> str:
    """Server-rendered /transparency page. Data is fetched client-side from
    /api/elo-history and /api/calibration so this stays a thin shell."""
    return (
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Behind the numbers · {_SITE_TITLE}</title>
<meta name="description" content="How {_SITE_TITLE} scores games: team Elo over time and win-probability calibration.">

<meta property="og:title" content="Behind the numbers · {_SITE_TITLE}">
<meta property="og:description" content="How {_SITE_TITLE} scores games: team Elo over time and win-probability calibration.">
<meta property="og:type" content="website">
<meta property="og:url" content="{_SITE_URL}/transparency">
<meta property="og:image" content="{_SITE_URL}/og-transparency.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:type" content="image/png">
<meta property="og:image:alt" content="How wumbers scores every WNBA game">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Behind the numbers · {_SITE_TITLE}">
<meta name="twitter:description" content="How {_SITE_TITLE} scores games: team Elo over time and win-probability calibration.">
<meta name="twitter:image" content="{_SITE_URL}/og-transparency.png">

<link rel="icon" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='14' fill='%23ff6b00'/><path d='M2 16 h28 M16 2 v28' stroke='%230d1b2a' stroke-width='2' fill='none'/><path d='M5 7 C 11 12 21 12 27 7' stroke='%230d1b2a' stroke-width='2' fill='none'/><path d='M5 25 C 11 20 21 20 27 25' stroke='%230d1b2a' stroke-width='2' fill='none'/></svg>">

{_SHARED_HEAD}
            .wrap {{ max-width: 920px; width: 100%; margin: 0 auto; padding: 24px 16px 64px; }}
            h1 {{ font-family: var(--display); font-size: 1.7rem; font-weight: 600; color: var(--navy); margin: 0 0 4px; }}
            h2 {{ font-family: var(--display); font-size: 1.2rem; font-weight: 600; color: var(--navy); margin: 0 0 4px; }}
            .sub {{ color: var(--text-muted); margin: 0 0 28px; }}
            section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
            .desc {{ color: var(--text-muted); font-size: .9rem; margin: 0 0 16px; }}
            .chart {{ width: 100%; overflow-x: auto; }}
            .chart svg {{ max-width: 100%; height: auto; display: block; }}
            .legend {{ display: flex; flex-wrap: wrap; gap: 8px 14px; margin-top: 12px; font-size: .8rem; }}
            .empty {{ color: var(--text-subtle); font-style: italic; }}
            a.back {{ color: var(--orange-deep); text-decoration: none; font-size: .9rem; }}
            .legend-row {{ display: inline-flex; align-items: baseline; gap: 7px; padding: 3px 11px; border: 1px solid var(--line); border-radius: 999px; background: transparent; color: var(--text-muted); cursor: pointer; font: inherit; font-size: .82rem; transition: background .12s ease, color .12s ease, border-color .12s ease; }}
            .legend-row .rank {{ color: var(--text-subtle); font-variant-numeric: tabular-nums; font-size: .72rem; }}
            .legend-row .rating {{ color: var(--text-subtle); font-variant-numeric: tabular-nums; }}
            .legend-row:hover, .legend-row.active, .legend-row:focus-visible {{ background: var(--orange); border-color: var(--orange); color: #fff; outline: none; }}
            .legend-row:hover .rank, .legend-row.active .rank, .legend-row:hover .rating, .legend-row.active .rating {{ color: rgba(255, 255, 255, .82); }}
            .elo-line {{ fill: none; stroke: var(--navy); stroke-opacity: .15; stroke-width: 1.4; pointer-events: none; transition: stroke-opacity .12s ease, stroke-width .12s ease; }}
            .elo-line.hi {{ stroke: var(--orange); stroke-opacity: 1; stroke-width: 2.6; }}
            .elo-hit {{ fill: none; stroke: transparent; stroke-width: 12; pointer-events: stroke; cursor: pointer; }}
            .elo-label {{ fill: var(--text-subtle); font-size: 9.5px; font-variant-numeric: tabular-nums; cursor: pointer; }}
            .elo-label.hi {{ fill: var(--orange); font-weight: 600; }}
            .cal-layout {{ display: grid; grid-template-columns: auto 1fr; gap: 32px; align-items: center; }}
            .cal-read p {{ margin: 0 0 12px; color: var(--text-muted); font-size: .92rem; line-height: 1.5; }}
            .cal-foot {{ color: var(--text-subtle) !important; font-size: .82rem !important; }}
            .cal-sub {{ font-family: var(--display); font-size: 1rem; font-weight: 600; color: var(--navy); margin: 24px 0 12px; padding-top: 14px; border-top: 1px solid var(--line-soft); }}
            @media (max-width: 640px) {{ .cal-layout {{ grid-template-columns: 1fr; }} }}
        </style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/">&larr; Back to games</a>
  <h1>Behind the numbers</h1>
  <p class="sub">How the model moves, and how accurate it has been.</p>

  <section>
    <h2>Elo ratings over time</h2>
    <p class="desc">Each team's Elo rating entering every game this season.
       Replayed from results — higher is stronger.</p>
    <div id="elo-chart" class="chart"><p class="empty">Loading…</p></div>
    <div id="elo-legend" class="legend"></div>
  </section>
  <section>
    <h2>Win-probability calibration</h2>
    <p class="desc">How our predicted win probabilities line up with how often teams
       actually win — dots on the dashed line are perfectly calibrated.</p>
    <h3 class="cal-sub">This season</h3>
    <div class="cal-layout">
      <div id="calibration-chart" class="chart"><p class="empty">Loading…</p></div>
      <div id="calibration-summary" class="cal-read"></div>
    </div>
    <h3 class="cal-sub">Backtest · 2017&ndash;2025</h3>
    <div class="cal-layout">
      <div id="backtest-chart" class="chart"></div>
      <div id="backtest-summary" class="cal-read"></div>
    </div>
  </section>
</div>

<script>
"""
        + _SHARED_JS
        + """
const MIN_CAL_GAMES = 25;

// Static 2017-2025 backtest of the deployed Elo model (K=16, H=50, reg=0.5,
// MOV on), from `python -m scripts.validate_elo`. Time-honest (each prediction
// uses only prior games). Regenerate and update if the Elo hyperparameters or
// historical data change.
const BACKTEST = {
  brier: 0.214, pickAcc: 0.671, n: 1910, seasons: '2017–2025',
  buckets: [
    { predicted_mean: 0.164, actual_rate: 0.250, count: 44 },
    { predicted_mean: 0.319, actual_rate: 0.315, count: 349 },
    { predicted_mean: 0.504, actual_rate: 0.492, count: 664 },
    { predicted_mean: 0.693, actual_rate: 0.684, count: 686 },
    { predicted_mean: 0.844, actual_rate: 0.898, count: 167 },
  ],
};

function renderBacktest() {
  const mount = document.getElementById('backtest-chart');
  const summary = document.getElementById('backtest-summary');
  if (!mount) return;
  mount.innerHTML = buildReliabilitySvg(BACKTEST.buckets);
  summary.innerHTML =
    `<p>Replaying every game from ${BACKTEST.seasons} and scoring each prediction against ` +
    `what actually happened — no peeking ahead. The model picks the winner about ` +
    `<strong>${Math.round(BACKTEST.pickAcc * 100)}%</strong> of the time, and predicted ` +
    `win rates match actual within about 1% across the middle of the range.</p>` +
    `<p class="cal-foot">Across ${BACKTEST.n.toLocaleString()} games (2017&ndash;2025).</p>`;
}

function buildLineChartSvg(series, opts) {
  // series: [{label, abbr, points:[{x,y}]}] — lines styled/highlighted via CSS
  // (.elo-line[.hi]); each path and its end-label carry data-team = the index.
  const W = opts.width, H = opts.height;
  const PL = 40, PR = 48, PT = 16, PB = 28;  // extra right pad for end-labels
  const xs = series.flatMap(s => s.points.map(p => p.x));
  const ys = series.flatMap(s => s.points.map(p => p.y));
  if (!xs.length) return '<p class="empty">No data yet.</p>';
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(...ys), ymax = Math.max(...ys);
  const sx = x => PL + (xmax === xmin ? 0 : (x - xmin) / (xmax - xmin)) * (W - PL - PR);
  const sy = y => H - PB - (ymax === ymin ? 0 : (y - ymin) / (ymax - ymin)) * (H - PT - PB);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Team Elo ratings over time">`;
  for (let i = 0; i <= 4; i++) {
    const val = ymin + (ymax - ymin) * i / 4;
    const y = sy(val);
    svg += `<line x1="${PL}" y1="${y}" x2="${W-PR}" y2="${y}" stroke="#ece6da"/>`;
    svg += `<text x="${PL-8}" y="${y+3}" text-anchor="end" font-size="10" fill="#8a929d">${Math.round(val)}</text>`;
  }
  for (const t of (opts.xTicks || [])) {
    const x = sx(t.x);
    svg += `<line x1="${x}" y1="${PT}" x2="${x}" y2="${H-PB}" stroke="#f2ede3"/>`;
    svg += `<text x="${x}" y="${H-PB+15}" text-anchor="middle" font-size="10" fill="#8a929d">${t.label}</text>`;
  }
  const paths = series.map(s => s.points.length
    ? s.points.map((p, k) => `${k ? 'L' : 'M'}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ')
    : '');
  for (let i = 0; i < series.length; i++) {
    if (paths[i]) svg += `<path class="elo-line" data-team="${i}" d="${paths[i]}"/>`;
  }
  // Direct end-of-line labels (abbreviation), nudged apart so they don't stack.
  const ends = [];
  for (let i = 0; i < series.length; i++) {
    const pts = series[i].points;
    if (!pts.length) continue;  // guard, mirroring the line/path loop
    ends.push({ i, abbr: series[i].abbr, y: sy(pts[pts.length - 1].y) });
  }
  ends.sort((a, b) => a.y - b.y);
  const gap = 11;
  for (let k = 1; k < ends.length; k++) {
    if (ends[k].y - ends[k - 1].y < gap) ends[k].y = ends[k - 1].y + gap;
  }
  const overflow = ends.length ? ends[ends.length - 1].y - (H - PB) : 0;
  if (overflow > 0) for (const e of ends) e.y -= overflow;  // shift stack up to fit
  for (const e of ends) {
    svg += `<text class="elo-label" data-team="${e.i}" x="${W-PR+5}" y="${e.y.toFixed(1)}" dominant-baseline="middle">${escapeHtml(e.abbr)}</text>`;
  }
  // Invisible wide hit paths on top so the thin lines are easy to hover.
  for (let i = 0; i < series.length; i++) {
    if (paths[i]) svg += `<path class="elo-hit" data-team="${i}" d="${paths[i]}"/>`;
  }
  svg += '</svg>';
  return svg;
}

async function loadElo() {
  const mount = document.getElementById('elo-chart');
  const legend = document.getElementById('elo-legend');
  try {
    const res = await fetch('/api/elo-history');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    const names = Object.keys(data.teams || {});
    if (!names.length) { mount.innerHTML = '<p class="empty">No Elo history yet.</p>'; return; }
    const allDates = [...new Set(names.flatMap(n => data.teams[n].map(p => p.date)))].sort();
    const dayIndex = Object.fromEntries(allDates.map((d, i) => [d, i]));
    const abbrevs = data.abbrevs || {};
    const series = names.map(n => {
      const pts = data.teams[n];
      return { label: n, abbr: abbrevs[n] || n.slice(0, 3).toUpperCase(),
               last: pts[pts.length - 1].rating,
               points: pts.map(p => ({ x: dayIndex[p.date], y: p.rating })) };
    });
    series.sort((a, b) => b.last - a.last);  // legend doubles as a standings list
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const seen = new Set();
    const xTicks = [];
    for (const d of allDates) {
      const ym = d.slice(0, 7);
      if (seen.has(ym)) continue;
      seen.add(ym);
      xTicks.push({ x: dayIndex[d], label: MONTHS[parseInt(d.slice(5, 7), 10) - 1] });
    }
    mount.innerHTML = buildLineChartSvg(series, { width: 860, height: 360, xTicks });
    const svg = mount.querySelector('svg');
    legend.innerHTML = series.map((s, i) =>
      `<button class="legend-row" type="button" data-team="${i}">` +
      `<span class="rank">${i + 1}</span>${escapeHtml(s.label)}` +
      `<span class="rating">${Math.round(s.last)}</span></button>`).join('');
    // Hover/focus a team to lift its line out of the muted cloud.
    const setHi = (i, on) => {
      const path = svg && svg.querySelector(`.elo-line[data-team="${i}"]`);
      const label = svg && svg.querySelector(`.elo-label[data-team="${i}"]`);
      const row = legend.querySelector(`.legend-row[data-team="${i}"]`);
      if (path) { path.classList.toggle('hi', on); if (on) path.parentNode.appendChild(path); }
      if (label) { label.classList.toggle('hi', on); if (on) label.parentNode.appendChild(label); }
      if (row) row.classList.toggle('active', on);
    };
    legend.querySelectorAll('.legend-row').forEach(row => {
      const i = row.dataset.team;
      row.addEventListener('mouseenter', () => setHi(i, true));
      row.addEventListener('mouseleave', () => setHi(i, false));
      row.addEventListener('focus', () => setHi(i, true));
      row.addEventListener('blur', () => setHi(i, false));
    });
    // Hovering the line itself (via a wide transparent hit path) or its end
    // label highlights the same team.
    (svg ? svg.querySelectorAll('.elo-hit, .elo-label') : []).forEach(el => {
      const i = el.dataset.team;
      el.addEventListener('mouseenter', () => setHi(i, true));
      el.addEventListener('mouseleave', () => setHi(i, false));
    });
  } catch (e) {
    mount.innerHTML = '<p class="empty">Could not load Elo history.</p>';
  }
}

function buildReliabilitySvg(buckets) {
  const W = 360, H = 360, P = 44;
  const sx = v => P + v * (W - 2*P);
  const sy = v => H - P - v * (H - 2*P);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Win-probability calibration">`;
  svg += `<rect x="${P}" y="${P}" width="${W-2*P}" height="${H-2*P}" fill="none" stroke="#e7e2d8"/>`;
  // perfect-calibration identity line (navy, dashed)
  svg += `<line x1="${sx(0)}" y1="${sy(0)}" x2="${sx(1)}" y2="${sy(1)}" stroke="#0d1b2a" stroke-opacity="0.3" stroke-dasharray="4 4"/>`;
  svg += `<text x="${W/2}" y="${H-8}" text-anchor="middle" font-size="11" fill="#5a6573">Predicted win probability</text>`;
  svg += `<text x="14" y="${H/2}" text-anchor="middle" font-size="11" fill="#5a6573" transform="rotate(-90 14 ${H/2})">Actual win rate</text>`;
  const maxN = Math.max(1, ...buckets.map(b => b.count));
  for (const b of buckets) {
    const cx = sx(b.predicted_mean), cy = sy(b.actual_rate);
    const rad = 4 + 8 * (b.count / maxN);  // dot size ~ games in the bucket
    svg += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${rad.toFixed(1)}" fill="#ff6b00" fill-opacity="0.85" stroke="#a03c00" stroke-width="1"/>`;
    svg += `<text x="${cx.toFixed(1)}" y="${(cy - rad - 4).toFixed(1)}" text-anchor="middle" font-size="9" fill="#8a929d">${b.count}</text>`;
  }
  svg += '</svg>';
  return svg;
}

async function loadCalibration() {
  const mount = document.getElementById('calibration-chart');
  const summary = document.getElementById('calibration-summary');
  try {
    const res = await fetch('/api/calibration');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (!data.n) {
      mount.innerHTML = '<p class="empty">No completed games yet.</p>';
      summary.innerHTML = '';
      return;
    }
    if (data.n < MIN_CAL_GAMES) {
      // Not enough games for a stable curve — collapse to one quiet line (no
      // loud placeholder); the 2017-2025 backtest below carries model quality.
      const layout = mount.closest('.cal-layout');
      if (layout) layout.style.display = 'block';
      mount.innerHTML = '';
      summary.innerHTML =
        `<p class="cal-foot" style="margin:0">This season's calibration appears once about ` +
        `<strong>${MIN_CAL_GAMES}</strong> games are completed — <strong>${data.n}</strong> ` +
        `so far. The backtest below shows how the model does over a full history.</p>`;
      return;
    }
    mount.innerHTML = buildReliabilitySvg(data.buckets || []);
    summary.innerHTML =
      `<p>Each dot groups games we gave a similar win chance; its height is how often those ` +
      `teams actually won. Dots on the dashed line are perfectly calibrated, and a dot's size ` +
      `is how many games it covers.</p>` +
      `<p class="cal-foot">Across ${data.n} completed games this season.</p>`;
  } catch (e) {
    mount.innerHTML = '<p class="empty">Could not load calibration.</p>';
  }
}

loadElo();
loadCalibration();
renderBacktest();
</script>
</body>
</html>"""
    )
