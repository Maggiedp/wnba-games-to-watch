"""API routes and response models for WNBA Games to Watch."""

import html as _html
import json
import logging

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


_SITE_URL = "https://wnba-games-to-watch-1068218371131.us-central1.run.app"
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
                // abbreviations are external ESPN/DB data — escape them so a
                // poisoned value can't inject markup into viewers' pages.
                const escLbl = s => String(s).replace(/[&<>"']/g, c => ({
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                }[c]));
                const homeLbl = escLbl(homeAbbr);
                const awayLbl = escLbl(awayAbbr);
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

_HOMEPAGE_HTML = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{_SITE_TITLE}</title>
        <meta name="description" content="{_SITE_DESCRIPTION}">

        <meta property="og:title" content="{_SITE_TITLE}">
        <meta property="og:description" content="{_SITE_DESCRIPTION}">
        <meta property="og:type" content="website">
        <meta property="og:url" content="{_SITE_URL}">
        <meta name="twitter:card" content="summary">
        <meta name="twitter:title" content="{_SITE_TITLE}">
        <meta name="twitter:description" content="{_SITE_DESCRIPTION}">

        <link rel="icon" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='14' fill='%23ff6b00'/><path d='M2 16 h28 M16 2 v28' stroke='%230d1b2a' stroke-width='2' fill='none'/><path d='M5 7 C 11 12 21 12 27 7' stroke='%230d1b2a' stroke-width='2' fill='none'/><path d='M5 25 C 11 20 21 20 27 25' stroke='%230d1b2a' stroke-width='2' fill='none'/></svg>">

{_SHARED_HEAD}

            /* ---------- Header ---------- */
            .header {{
                background: var(--navy);
                color: white;
                padding: 28px 32px 32px;
                border-bottom: 4px solid var(--orange);
            }}
            .header-inner {{
                max-width: 1100px;
                margin: 0 auto;
                display: flex;
                justify-content: space-between;
                align-items: flex-end;
                gap: 16px;
            }}
            .wordmark {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 144;
                font-weight: 700;
                font-size: clamp(2rem, 5vw, 3rem);
                line-height: 1;
                letter-spacing: -0.025em;
            }}
            .wordmark em {{
                font-style: italic;
                font-weight: 500;
                color: var(--orange);
            }}
            .tagline {{
                font-family: var(--body);
                font-size: 0.92rem;
                color: #8a9bb0;
                margin-top: 10px;
                max-width: 42ch;
                font-weight: 400;
            }}
            .header-link {{
                background: transparent;
                color: #b8c2d0;
                border: 1px solid var(--navy-3);
                padding: 8px 14px;
                border-radius: 4px;
                font-size: 0.82rem;
                font-weight: 500;
                cursor: pointer;
                font-family: var(--body);
                white-space: nowrap;
                letter-spacing: 0.02em;
                transition: color 0.15s, border-color 0.15s;
            }}
            .header-link:hover {{ color: var(--orange); border-color: var(--orange); }}

            /* ---------- Playoff Picture ---------- */
            .playoff-picture {{
                margin: 24px 0;
            }}
            .playoff-picture-inner {{
                padding: 0;
            }}
            .playoff-picture-header {{
                font-family: 'Fraunces', Georgia, serif;
                font-size: 18px;
                font-weight: 600;
                margin-bottom: 8px;
            }}
            .playoff-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 14px;
            }}
            .playoff-table th {{
                text-align: right;
                font-weight: 500;
                color: var(--navy-3);
                padding: 6px 8px;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.04em;
                border-bottom: 1px solid var(--navy-3);
            }}
            .playoff-table th.team-col {{
                text-align: left;
            }}
            .playoff-table td {{
                padding: 6px 8px;
                border-bottom: 1px solid rgba(0,0,0,0.06);
            }}
            .playoff-table td.team-cell {{
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .playoff-table td.prob-cell {{
                text-align: right;
                font-variant-numeric: tabular-nums;
                background-position: left center;
                background-repeat: no-repeat;
            }}
            .playoff-table tr.eliminated {{
                opacity: 0.5;
            }}
            .playoff-logo {{
                width: 22px;
                height: 22px;
                object-fit: contain;
                flex-shrink: 0;
            }}
            .playoff-team-name {{
                font-weight: 500;
            }}
            @media (max-width: 768px) {{
                .playoff-table {{
                    font-size: 13px;
                }}
                .playoff-table th, .playoff-table td {{
                    padding: 5px 6px;
                }}
                .playoff-picture {{
                    margin: 16px 0;
                }}
            }}

            /* ---------- Inline team playoff probability ---------- */
            .team-prob, .win-prob {{
                font-size: 0.72rem;
                color: var(--text-subtle);
                font-variant-numeric: tabular-nums;
                line-height: 1;
                font-family: var(--body);
            }}
            .team-prob {{ margin-top: 1px; }}
            .win-prob {{ margin-top: 3px; width: 100%; }}
            .final-score {{
                display: flex;
                align-items: center;
                gap: 6px;
                font-family: 'Albert Sans', system-ui, sans-serif;
                font-size: 14px;
                color: var(--navy);
                margin-top: 4px;
            }}
            .final-team {{
                color: var(--navy-3);
            }}
            .final-team.win {{
                color: var(--navy);
                font-weight: 600;
            }}
            .final-sep {{
                color: var(--navy-3);
            }}
            .final-tag {{
                margin-left: 4px;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                color: var(--navy-3);
                background: rgba(13, 27, 42, 0.06);
                padding: 1px 6px;
                border-radius: 3px;
            }}

            /* ---------- Controls ---------- */
            .controls {{
                background: var(--surface);
                border-bottom: 1px solid var(--line);
                position: sticky;
                top: 0;
                z-index: 20;
                transition: box-shadow 0.18s ease;
            }}
            .controls.is-stuck {{
                box-shadow: 0 4px 16px rgba(13, 27, 42, 0.10);
            }}
            .controls-inner {{
                max-width: 1100px;
                margin: 0 auto;
                padding: 14px 32px 16px;
                display: flex;
                flex-direction: column;
                gap: 10px;
            }}
            .filter-row {{
                display: flex;
                align-items: center;
                gap: 14px;
                flex-wrap: wrap;
            }}
            .filter-label {{
                font-size: 0.7rem;
                font-weight: 600;
                color: var(--text-subtle);
                white-space: nowrap;
                text-transform: uppercase;
                letter-spacing: 0.14em;
            }}
            .filter-group {{
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 0.9em;
            }}
            .filter-group select, .filter-group input[type="date"] {{
                padding: 6px 10px;
                border: 1px solid var(--line);
                border-radius: 4px;
                font-size: 0.86rem;
                cursor: pointer;
                background: var(--surface);
                font-family: var(--body);
                color: var(--text);
                font-feature-settings: 'tnum' on;
            }}
            .filter-group select:focus, .filter-group input[type="date"]:focus {{ outline: none; border-color: var(--navy); }}
            .date-arrow {{ color: var(--text-subtle); font-size: 0.9em; }}
            .pill-group {{ display: flex; gap: 6px; flex-wrap: wrap; }}
            .pill {{
                padding: 5px 12px;
                border: 1px solid var(--line);
                border-radius: 999px;
                font-size: 0.82rem;
                font-weight: 500;
                cursor: pointer;
                background: var(--surface);
                color: #555c66;
                font-family: var(--body);
                transition: color 0.12s, border-color 0.12s, background 0.12s;
            }}
            .pill:hover {{ border-color: var(--navy); color: var(--navy); }}
            .pill[aria-pressed="true"] {{ background: var(--navy); border-color: var(--navy); color: white; }}
            .preset-pill[aria-pressed="true"] {{ background: var(--orange); border-color: var(--orange); color: white; }}
            .sort-toggle {{ display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }}
            .sort-btn {{
                padding: 6px 14px;
                border: none;
                background: var(--surface);
                font-size: 0.84rem;
                font-weight: 500;
                cursor: pointer;
                color: #555c66;
                font-family: var(--body);
            }}
            .sort-btn + .sort-btn {{ border-left: 1px solid var(--line); }}
            .sort-btn[aria-pressed="true"] {{ background: var(--navy); color: white; }}
            .sort-wrap {{ margin-left: auto; }}

            /* ---------- Main layout ---------- */
            .content {{
                max-width: 1100px;
                margin: 0 auto;
                padding: 28px 16px 16px;
                width: 100%;
                flex: 1;
            }}

            /* ---------- Featured top pick ---------- */
            .featured-eyebrow {{
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 0.7rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.2em;
                color: var(--orange);
                margin-bottom: 10px;
            }}
            .featured-eyebrow::before {{
                content: '';
                width: 28px;
                height: 1.5px;
                background: var(--orange);
            }}
            .top-pick-badge {{
                display: inline-block;
                font-size: 0.62rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.18em;
                color: var(--orange);
                margin-bottom: 6px;
            }}
            .featured, .skeleton-featured {{
                background: var(--surface);
                border-radius: 10px;
                display: grid;
                grid-template-columns: 1fr auto;
                gap: 32px;
                align-items: center;
                margin-bottom: 32px;
            }}
            .featured {{
                padding: 28px 32px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 12px 28px -16px rgba(13, 27, 42, 0.18);
                border-left: 4px solid var(--orange);
                position: relative;
                overflow: hidden;
            }}
            .featured::after {{
                content: '';
                position: absolute;
                inset: 0;
                background: radial-gradient(circle at 100% 0%, rgba(255, 107, 0, 0.05), transparent 50%);
                pointer-events: none;
            }}
            .featured-meta {{
                font-size: 0.74rem;
                font-weight: 500;
                text-transform: uppercase;
                letter-spacing: 0.14em;
                color: var(--text-subtle);
                margin-bottom: 14px;
            }}
            .featured-teams {{
                display: flex;
                align-items: center;
                gap: 18px;
                font-family: var(--display);
                font-variation-settings: 'opsz' 110;
                font-weight: 600;
                font-size: clamp(1.4rem, 3.2vw, 2.1rem);
                line-height: 1.05;
                letter-spacing: -0.018em;
                flex-wrap: wrap;
                margin-bottom: 18px;
            }}
            .featured-teams .team {{ display: inline-flex; align-items: center; gap: 12px; }}
            .featured-teams .team-logo {{ width: 38px; height: 38px; object-fit: contain; }}
            .featured-teams .vs {{
                font-family: var(--body);
                font-size: 0.74rem;
                font-weight: 600;
                letter-spacing: 0.18em;
                text-transform: uppercase;
                color: var(--text-subtle);
                padding: 0 2px;
            }}
            .featured-bottom {{
                display: flex;
                gap: 28px;
                flex-wrap: wrap;
                align-items: center;
            }}
            .featured-stat {{
                display: inline-flex;
                flex-direction: column;
                gap: 2px;
            }}
            .featured-stat-label {{
                font-size: 0.66rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.16em;
                color: var(--text-subtle);
            }}
            .featured-stat-value {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 72;
                font-weight: 600;
                font-size: 1.05rem;
                color: var(--text);
                font-feature-settings: 'tnum' on;
            }}
            .featured-broadcaster {{
                display: inline-flex;
                align-items: center;
                gap: 6px;
                font-size: 0.84rem;
                color: var(--text-muted);
            }}
            .featured-broadcaster::before {{
                content: '';
                display: inline-block;
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: var(--orange);
            }}
            .featured-score {{
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 6px;
                position: relative;
                z-index: 1;
            }}
            .featured-score-num {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 144;
                font-weight: 700;
                font-size: clamp(3.6rem, 9vw, 5.6rem);
                line-height: 0.85;
                color: var(--orange);
                letter-spacing: -0.04em;
                font-feature-settings: 'tnum' on;
            }}
            .featured-score-label {{
                font-size: 0.66rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.2em;
                color: var(--text-subtle);
            }}

            /* ---------- Games table ---------- */
            .games-table {{
                width: 100%;
                border-collapse: collapse;
                background: var(--surface);
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
                font-feature-settings: 'tnum' on;
            }}
            .games-table thead {{ background: var(--navy); }}
            .games-table th {{
                padding: 14px 16px;
                text-align: left;
                font-weight: 600;
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.16em;
                color: #8a9bb0;
            }}
            .games-table td {{
                padding: 14px 16px;
                border-bottom: 1px solid var(--line-soft);
                font-size: 0.93rem;
                vertical-align: middle;
            }}
            .games-table tbody tr:last-child td {{ border-bottom: none; }}
            .games-table tbody tr:hover {{ background: #fbf9f3; }}
            .col-date {{ white-space: nowrap; color: var(--text-muted); }}
            .col-time {{ white-space: nowrap; color: var(--text-subtle); font-size: 0.86em; }}
            .score-cell {{ width: 1%; }}
            .score-num {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 96;
                font-weight: 700;
                font-size: 1.45rem;
                line-height: 1;
                letter-spacing: -0.025em;
                font-feature-settings: 'tnum' on;
            }}
            .score-num.high, .games-card-score.high {{ color: var(--orange); }}
            .score-num.medium, .games-card-score.medium {{ color: var(--orange-deep); }}
            .score-num.low, .games-card-score.low {{ color: var(--text-subtle); }}
            .score-num.empty, .games-card-score.empty {{ color: var(--text-subtle); opacity: 0.5; }}
            .matchup {{
                font-weight: 600;
                color: var(--navy);
                display: flex;
                align-items: center;
                gap: 10px;
                flex-wrap: wrap;
            }}
            .team {{ display: inline-flex; align-items: center; gap: 6px; }}
            .team-logo {{ width: 22px; height: 22px; object-fit: contain; flex-shrink: 0; }}
            .vs {{
                color: var(--text-subtle);
                font-weight: 500;
                font-size: 0.72rem;
                letter-spacing: 0.14em;
                text-transform: uppercase;
                padding: 0 2px;
            }}
            .broadcaster-badge {{
                display: inline-block;
                padding: 4px 10px;
                background: #f0ebe1;
                color: #4d5560;
                border-radius: 3px;
                font-size: 0.78rem;
                font-weight: 500;
                letter-spacing: 0.01em;
            }}

            .excitement-eyebrow {{
                display: none;
                font-family: var(--display);
                line-height: 1.1;
                white-space: nowrap;
            }}
            .excitement-eyebrow.close {{
                display: block;
                font-style: italic;
                font-size: 0.82rem;
                font-weight: 500;
                letter-spacing: -0.005em;
                color: var(--text-muted);
                margin-bottom: 4px;
            }}
            .excitement-eyebrow.thriller {{
                display: inline-flex;
                align-items: center;
                gap: 5px;
                font-style: normal;
                font-weight: 900;
                font-variation-settings: 'opsz' 144;
                font-size: 1rem;
                text-transform: uppercase;
                letter-spacing: 0.015em;
                color: var(--orange);
                transform: rotate(-3deg);
                transform-origin: left center;
                margin-bottom: 6px;
                text-shadow: 1.5px 1.5px 0 var(--orange-deep);
                line-height: 1;
            }}
            .excitement-eyebrow.thriller::before {{
                content: '✸';
                color: var(--orange);
                font-size: 0.95em;
                line-height: 1;
                transform: rotate(3deg);
                text-shadow: none;
            }}
            .score-stack {{
                display: flex;
                flex-direction: column;
                align-items: flex-start;
            }}

            /* ---------- Mini bars ---------- */
            .mini-bar-row {{
                display: grid;
                grid-template-columns: 80px 1fr 28px;
                align-items: center;
                gap: 8px;
                font-size: 0.74rem;
                margin-top: 6px;
            }}
            .mini-bar-label {{
                color: var(--text-muted);
                font-weight: 500;
                letter-spacing: 0.02em;
            }}
            .mini-bar-track {{
                height: 5px;
                background: var(--line-soft);
                border-radius: 999px;
                overflow: hidden;
            }}
            .mini-bar-fill {{
                display: block;
                height: 100%;
                border-radius: 999px;
            }}
            .mini-bar-fill.quality {{ background: linear-gradient(90deg, #ff6b00, #ff9540); }}
            .mini-bar-fill.importance {{ background: linear-gradient(90deg, #2b3a52, #5a6573); }}
            .mini-bar-num {{
                font-family: var(--display);
                font-weight: 600;
                font-size: 0.85rem;
                text-align: right;
                font-feature-settings: 'tnum' on;
                color: var(--text);
            }}
            .mini-bar-num.empty {{ color: var(--text-subtle); }}
            .mini-bar-compact {{
                display: grid;
                grid-template-columns: 1fr 26px;
                align-items: center;
                gap: 7px;
                font-size: 0.74rem;
                min-width: 100px;
            }}
            .mini-bar-compact .mini-bar-num {{ font-size: 0.74rem; }}

            /* ---------- Loading skeleton ---------- */
            .skeleton-bar {{
                background: linear-gradient(90deg, #ece8de 0%, #f5f1e6 50%, #ece8de 100%);
                background-size: 200% 100%;
                animation: shimmer 1.4s linear infinite;
                border-radius: 4px;
                display: block;
            }}
            @keyframes shimmer {{
                0% {{ background-position: 200% 0; }}
                100% {{ background-position: -200% 0; }}
            }}
            .skeleton-featured {{
                padding: 32px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
                border-left: 4px solid var(--line);
            }}
            .skeleton-table {{
                background: var(--surface);
                border-radius: 8px;
                padding: 16px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
            }}
            .skeleton-row {{
                display: flex;
                gap: 16px;
                align-items: center;
                padding: 14px 0;
                border-bottom: 1px solid var(--line-soft);
            }}
            .skeleton-row:last-child {{ border-bottom: none; }}

            /* ---------- Empty state ---------- */
            .empty-state {{
                text-align: center;
                padding: 64px 24px;
                background: var(--surface);
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
            }}
            .empty-state-icon {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 144;
                font-weight: 700;
                font-size: 3.5rem;
                line-height: 1;
                color: var(--line);
                letter-spacing: -0.04em;
                margin-bottom: 8px;
            }}
            .empty-state-title {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 96;
                font-weight: 600;
                font-size: 1.25rem;
                color: var(--navy);
                letter-spacing: -0.015em;
                margin-bottom: 6px;
            }}
            .empty-state-msg {{
                color: var(--text-muted);
                font-size: 0.92rem;
            }}

            .error {{
                background: #fff0f0;
                color: #c00;
                padding: 14px 16px;
                border-radius: 6px;
                border-left: 3px solid #c00;
                font-size: 0.92rem;
            }}

            /* ---------- Footer ---------- */
            #completed-section {{
                margin-top: 48px;
                border-top: 1px solid rgba(13, 27, 42, 0.08);
                padding-top: 24px;
            }}
            .completed-toggle {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                margin: 0 auto;
                padding: 10px 20px;
                background: transparent;
                border: 1px solid rgba(13, 27, 42, 0.2);
                border-radius: 999px;
                font-family: 'Albert Sans', system-ui, sans-serif;
                font-size: 14px;
                font-weight: 500;
                color: var(--navy);
                cursor: pointer;
            }}
            .completed-toggle:hover {{
                background: rgba(13, 27, 42, 0.04);
            }}
            .completed-toggle:focus-visible {{
                outline: 2px solid var(--orange);
                outline-offset: 2px;
            }}
            .completed-toggle-count {{
                color: var(--navy-3);
            }}
            .completed-toggle-chevron {{
                transition: transform 0.15s ease;
            }}
            .completed-toggle[aria-expanded="true"] .completed-toggle-chevron {{
                transform: rotate(180deg);
            }}
            .completed-heading {{
                font-family: 'Fraunces', Georgia, serif;
                font-weight: 600;
                font-size: 22px;
                color: var(--navy);
                margin: 24px 0 16px;
            }}
            .completed-heading-sub {{
                font-weight: 500;
                font-style: italic;
                color: var(--navy-3);
            }}
            .completed-header-row {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 8px;
            }}
            .completed-header-row .completed-heading {{ margin: 24px 0 16px; }}
            .footer {{
                text-align: center;
                padding: 28px 16px;
                color: var(--text-muted);
                font-size: 0.84rem;
                border-top: 1px solid var(--line);
                margin-top: 32px;
            }}
            .footer a {{ color: var(--text-muted); text-decoration: underline; }}
            .footer a:hover {{ color: var(--orange); }}
            .link-button {{
                background: none;
                border: none;
                padding: 0;
                color: var(--text-muted);
                text-decoration: underline;
                cursor: pointer;
                font-family: inherit;
                font-size: inherit;
            }}
            .link-button:hover {{ color: var(--orange); }}

            /* ---------- Modal ---------- */
            .modal-backdrop {{
                display: none;
                position: fixed;
                inset: 0;
                background: rgba(13, 27, 42, 0.55);
                backdrop-filter: blur(4px);
                -webkit-backdrop-filter: blur(4px);
                z-index: 100;
                align-items: flex-start;
                justify-content: center;
                padding: 56px 16px;
                overflow-y: auto;
            }}
            .modal-backdrop.open {{ display: flex; }}
            .modal {{
                background: var(--surface);
                max-width: 640px;
                width: 100%;
                border-radius: 10px;
                padding: 36px 38px;
                box-shadow: 0 24px 56px rgba(0, 0, 0, 0.25);
                position: relative;
            }}
            .modal h2 {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 144;
                font-weight: 700;
                font-size: 1.7rem;
                color: var(--navy);
                margin-bottom: 14px;
                letter-spacing: -0.022em;
            }}
            .modal h3 {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 72;
                font-weight: 600;
                font-size: 1.05rem;
                color: var(--navy);
                margin-top: 22px;
                margin-bottom: 6px;
                letter-spacing: -0.01em;
            }}
            .modal p {{ color: #3f4a58; line-height: 1.6; font-size: 0.95rem; }}
            .modal a {{ color: var(--orange); }}
            .modal .close {{
                position: absolute;
                top: 14px;
                right: 18px;
                background: none;
                border: none;
                font-size: 1.6em;
                cursor: pointer;
                color: var(--text-subtle);
                line-height: 1;
                padding: 4px 8px;
                border-radius: 4px;
            }}
            .modal .close:hover {{ color: var(--navy); }}

            /* ---------- Focus visible ---------- */
            button:focus-visible,
            .pill:focus-visible,
            .sort-btn:focus-visible,
            select:focus-visible,
            input[type="date"]:focus-visible,
            a:focus-visible {{
                outline: 2px solid var(--orange);
                outline-offset: 2px;
            }}
            .header-link:focus-visible {{ outline-color: var(--orange); }}

            /* ---------- Mobile cards ---------- */
            .games-cards {{ display: none; }}  /* Hidden on desktop; flipped on inside the mobile media query */
            .games-card {{
                background: var(--surface);
                border: 1px solid var(--line);
                border-radius: 6px;
                padding: 12px 12px;
                margin-bottom: 10px;
                display: grid;
                grid-template-columns: 50px 1fr;
                gap: 12px;
                align-items: stretch;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
            }}
            .games-card-score {{
                font-family: var(--display);
                font-variation-settings: 'opsz' 96;
                font-weight: 700;
                font-size: 1.7rem;
                line-height: 1;
                letter-spacing: -0.025em;
                font-feature-settings: 'tnum' on;
                text-align: center;
                align-self: center;
            }}
            .games-card-stack {{
                display: flex;
                flex-direction: column;
                gap: 2px;
                min-width: 0;  /* allow long team names to wrap inside the grid cell */
            }}
            .games-card-eyebrow {{
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 0.6rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.18em;
                color: var(--orange);
                margin-bottom: 4px;
            }}
            .games-card-eyebrow::before {{
                content: '';
                width: 14px;
                height: 1.5px;
                background: var(--orange);
            }}
            .games-card-matchup {{
                font-family: var(--display);
                font-weight: 600;
                font-size: 0.95rem;
                line-height: 1.2;
                color: var(--navy);
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .games-card-matchup .team {{ display: inline-flex; align-items: center; gap: 5px; }}
            .games-card-matchup .team-logo {{ width: 16px; height: 16px; object-fit: contain; flex-shrink: 0; }}
            .games-card-matchup .vs {{
                color: var(--text-subtle);
                font-style: italic;
                font-weight: 500;
                font-size: 0.85rem;
                letter-spacing: 0;
                text-transform: none;
                padding: 0 4px;
            }}
            .games-card-meta {{
                font-size: 0.72rem;
                color: var(--text-muted);
                margin-top: 2px;
            }}

            /* ---------- WP Chart ---------- */
            [data-espn-id] {{ cursor: pointer; }}
            .games-card[data-espn-id]:hover {{ border-color: var(--navy-3); }}
            .wp-chart-svg {{
                width: 100%;
                height: 150px;
                display: block;
                overflow: visible;
            }}

            /* ---------- Mobile ---------- */
            @media (max-width: 768px) {{
                .header {{ padding: 22px 18px 24px; }}
                .header-inner {{ align-items: flex-start; }}
                .controls-inner {{ padding: 12px 18px 14px; }}
                .content {{ padding: 22px 14px 12px; }}
                .featured {{
                    grid-template-columns: 1fr;
                    padding: 22px 22px;
                    gap: 18px;
                }}
                .featured-score {{
                    flex-direction: row;
                    align-items: baseline;
                    gap: 12px;
                }}
                .featured-score-num {{ font-size: 3.4rem; }}
                .games-table th, .games-table td {{ padding: 11px 10px; }}
                .games-table {{ font-size: 0.86rem; }}
                .score-num {{ font-size: 1.25rem; }}
                .hide-mobile {{ display: none; }}
                .modal {{ padding: 28px 22px; }}
                .modal h2 {{ font-size: 1.45rem; }}
                .sort-wrap {{ margin-left: 0; }}
                .games-table {{ display: none; }}
                .games-cards {{ display: block; }}
            }}

            @media (prefers-reduced-motion: reduce) {{
                .skeleton-bar {{ animation: none; }}
            }}
        </style>
    </head>
    <body>
        <header class="header">
            <div class="header-inner">
                <div class="header-text">
                    <h1 class="wordmark">WNBA Games to <em>Watch</em></h1>
                    <p class="tagline">Tonight&rsquo;s best matchups, ranked by team quality and playoff stakes.</p>
                </div>
                <button class="header-link" id="how-it-works-btn" type="button">How it works</button>
            </div>
        </header>

        <div class="playoff-picture" id="playoff-picture" style="display:none">
            <div class="playoff-picture-inner">
                <div class="playoff-picture-header">Playoff Picture &middot; Updated daily</div>
                <table class="playoff-table" id="playoff-table">
                    <thead>
                        <tr>
                            <th class="team-col">Team</th>
                            <th>Playoffs</th>
                            <th>Semis</th>
                            <th>Finals</th>
                            <th>Champ</th>
                        </tr>
                    </thead>
                    <tbody id="playoff-tbody"></tbody>
                </table>
            </div>
        </div>

        <div id="controls-sentinel" aria-hidden="true"></div>
        <div class="controls">
            <div class="controls-inner">
                <div class="filter-row">
                    <span class="filter-label">Networks</span>
                    <div class="pill-group" id="network-pills"></div>
                </div>
                <div class="filter-row">
                    <span class="filter-label">Date</span>
                    <div class="pill-group" id="preset-pills">
                        <button class="pill preset-pill" data-preset="today" type="button" aria-pressed="false">Today</button>
                        <button class="pill preset-pill" data-preset="7" type="button" aria-pressed="false">Next 7 days</button>
                        <button class="pill preset-pill" data-preset="30" type="button" aria-pressed="false">Next 30 days</button>
                        <button class="pill preset-pill" data-preset="all" type="button" aria-pressed="true">All</button>
                    </div>
                    <div class="filter-group">
                        <input type="date" id="from-date" aria-label="From date">
                        <span class="date-arrow" aria-hidden="true">&rarr;</span>
                        <input type="date" id="to-date" aria-label="To date">
                    </div>
                    <div class="filter-group">
                        <label for="team-filter" class="filter-label">Team</label>
                        <select id="team-filter"><option value="">All</option></select>
                    </div>
                    <div class="filter-group sort-wrap">
                        <span class="filter-label">Sort</span>
                        <div class="sort-toggle" role="group" aria-label="Sort games">
                            <button class="sort-btn" id="sort-date" type="button" aria-pressed="true">Date</button>
                            <button class="sort-btn" id="sort-score" type="button" aria-pressed="false">Overall</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <main class="content">
            <div id="featured-container"></div>
            <div id="games-container"></div>
            <section id="completed-section" aria-labelledby="completed-heading">
                <button type="button" id="completed-toggle"
                        class="completed-toggle" aria-expanded="false"
                        aria-controls="completed-content" hidden>
                    <span class="completed-toggle-text">Show completed games</span>
                    <span class="completed-toggle-count" id="completed-toggle-count"></span>
                    <span class="completed-toggle-chevron" aria-hidden="true">&#9662;</span>
                </button>
                <div id="completed-content" hidden>
                    <div class="completed-header-row">
                        <h2 id="completed-heading" class="completed-heading">
                            Completed games <span class="completed-heading-sub" id="completed-sort-label">&middot; Sorted by date</span>
                        </h2>
                        <div class="sort-toggle" role="group" aria-label="Sort completed games">
                            <button class="sort-btn" id="completed-sort-date" type="button" aria-pressed="true">Date</button>
                            <button class="sort-btn" id="completed-sort-excitement" type="button" aria-pressed="false">Excitement</button>
                        </div>
                    </div>
                    <div id="completed-games-container"></div>
                </div>
            </section>
        </main>

        <footer class="footer">
            Team strength from <a href="https://www.espn.com/wnba/bpi" target="_blank" rel="noopener">ESPN BPI</a>.
            Schedule and broadcasters from ESPN. Updated daily.
            &middot; <button type="button" class="link-button" id="how-it-works-footer">How it works</button>
        </footer>

        <div class="modal-backdrop" id="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="modal-title">
            <div class="modal">
                <button class="close" id="modal-close" aria-label="Close">&times;</button>
                <h2 id="modal-title">How it works</h2>
                <p>Every WNBA game gets a nightly score out of 100, weighing two things:</p>

                <h3>Quality (60%)</h3>
                <p>
                    How good is the matchup? Uses the harmonic mean of both teams&rsquo;
                    <a href="https://www.espn.com/wnba/bpi" target="_blank" rel="noopener">ESPN BPI</a>
                    ratings, which penalizes lopsided games. A +5 vs +5 matchup scores much higher
                    than a +5 vs -5 blowout waiting to happen.
                </p>

                <h3>Importance (40%)</h3>
                <p>
                    How much does this game matter for playoff seeding? A Monte Carlo simulation
                    runs the rest of the season 2,000 times per outcome, using Elo ratings with
                    home-court advantage and a margin-of-victory multiplier to decide each
                    simulated game. We sum how much every team&rsquo;s playoff odds swing
                    depending on who wins tonight &mdash; including bubble teams watching from
                    the outside, not just the two playing. Bigger swing &rarr; higher score.
                    Playoff odds and game importance come from the same simulation run, so the
                    two numbers are consistent with each other.
                </p>

                <h3>Notes</h3>
                <p>
                    Non-regular-season games are not simulated and show no importance score. Expansion teams
                    start at league-average strength until they have a real BPI.
                </p>
            </div>
        </div>

        <script>
            let allGames = [];
            let allCompleted = [];
            let selectedNetworks = new Set();
            let sortBy = 'date';
            let completedSortMode = 'date';

            const NETWORK_LABELS = {{
                'ESPN': 'ESPN', 'ABC': 'ABC', 'NBC': 'NBC/Peacock',
                'Prime Video': 'Prime Video', 'CBS': 'CBS/Paramount+',
                'ION': 'ION', 'USA Network': 'USA Network',
                'League Pass': 'League Pass', 'NBA TV': 'NBA TV',
            }};

            const EXCITEMENT_CLOSE = 4.0;
            const EXCITEMENT_THRILLER = 7.5;
            const EXCITEMENT_FUTURE_WEIGHT = 2.5;

            function addDaysISO(days) {{
                const d = new Date();
                d.setDate(d.getDate() + days);
                return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
            }}

            function setPreset(preset) {{
                document.querySelectorAll('.preset-pill').forEach(p => {{
                    p.setAttribute('aria-pressed', p.dataset.preset === preset ? 'true' : 'false');
                }});
                const fromInput = document.getElementById('from-date');
                const toInput = document.getElementById('to-date');
                if (preset === 'today') {{
                    fromInput.value = addDaysISO(0);
                    toInput.value = addDaysISO(0);
                }} else if (preset === '7') {{
                    fromInput.value = addDaysISO(0);
                    toInput.value = addDaysISO(7);
                }} else if (preset === '30') {{
                    fromInput.value = addDaysISO(0);
                    toInput.value = addDaysISO(30);
                }} else {{
                    fromInput.value = '';
                    toInput.value = '';
                }}
                applyFilters();
            }}

            function clearActivePreset() {{
                document.querySelectorAll('.preset-pill').forEach(p => p.setAttribute('aria-pressed', 'false'));
            }}

            function renderLoadingSkeleton() {{
                document.getElementById('featured-container').innerHTML = `
                    <div class="skeleton-featured">
                        <div>
                            <span class="skeleton-bar" style="width:120px;height:10px;margin-bottom:14px;"></span>
                            <span class="skeleton-bar" style="width:75%;height:28px;margin-bottom:16px;"></span>
                            <span class="skeleton-bar" style="width:40%;height:12px;"></span>
                        </div>
                        <span class="skeleton-bar" style="width:90px;height:64px;"></span>
                    </div>
                `;
                const rows = Array(4).fill(0).map(() => `
                    <div class="skeleton-row">
                        <span class="skeleton-bar" style="width:80px;height:12px;"></span>
                        <span class="skeleton-bar" style="width:36px;height:20px;"></span>
                        <span class="skeleton-bar" style="flex:1;height:14px;"></span>
                        <span class="skeleton-bar" style="width:80px;height:18px;"></span>
                    </div>
                `).join('');
                document.getElementById('games-container').innerHTML = `<div class="skeleton-table">${{rows}}</div>`;
            }}

            async function loadGames() {{
                renderLoadingSkeleton();
                try {{
                    const response = await fetch('/api/games/upcoming');
                    allGames = await response.json();
                    populateFilters();
                    applyFilters();
                    loadCompleted();
                    // Decoupled from the main payload: a slow ESPN scoreboard
                    // call shouldn't delay first paint. Merge in when it lands.
                    hydrateGameStatuses();
                }} catch (error) {{
                    document.getElementById('featured-container').innerHTML = '';
                    document.getElementById('games-container').innerHTML =
                        `<div class="error">Error loading games: ${{escapeHtml(error.message)}}</div>`;
                }}
            }}

            // Tracks which espn_ids appeared in the most recent live-status
            // response (i.e. "today's slate"). Lets us know when to keep
            // polling for tipoff transitions and when to stop.
            let todaysEspnIds = new Set();
            let statusRefreshInterval = null;

            // Backoff sequence (seconds) for live-status retries on 5xx. Doubles
            // until 5 min then holds, so a flaky ESPN scoreboard doesn't strand
            // the page on stale pregame WP forever.
            const STATUS_RETRY_BACKOFFS = [30, 60, 120, 300];
            let statusRetryStep = 0;
            let statusRetryTimer = null;

            async function hydrateGameStatuses() {{
                try {{
                    const resp = await fetch('/api/games/live-status');
                    if (!resp.ok) {{
                        scheduleStatusRetry();
                        return;
                    }}
                    const statuses = await resp.json();
                    if (!statuses || typeof statuses !== 'object') return;
                    statusRetryStep = 0;
                    if (statusRetryTimer) {{
                        clearTimeout(statusRetryTimer); statusRetryTimer = null;
                    }}
                    todaysEspnIds = new Set(Object.keys(statuses));
                    let changed = false;
                    for (const g of allGames) {{
                        const next = g.espn_id ? statuses[g.espn_id] : null;
                        if (next && g.game_status !== next) {{
                            g.game_status = next;
                            changed = true;
                        }}
                    }}
                    if (changed) {{
                        hydrateLiveWp();
                        armLiveWpPoll();
                    }}
                    armStatusRefreshPoll();
                }} catch (e) {{
                    scheduleStatusRetry();
                }}
            }}

            function scheduleStatusRetry() {{
                if (statusRetryTimer) return;  // already scheduled
                const delay = STATUS_RETRY_BACKOFFS[
                    Math.min(statusRetryStep, STATUS_RETRY_BACKOFFS.length - 1)
                ] * 1000;
                statusRetryStep++;
                statusRetryTimer = setTimeout(() => {{
                    statusRetryTimer = null;
                    hydrateGameStatuses();
                }}, delay);
            }}

            // Periodically re-fetch live-status until every game on today's
            // slate has tipped off (or otherwise left STATUS_SCHEDULED). Without
            // this, a page opened pre-tipoff would never start live-WP polling
            // because game_status would stay 'STATUS_SCHEDULED' forever.
            function hasPendingTippoff() {{
                if (todaysEspnIds.size === 0) return false;
                return allGames.some(g =>
                    g.espn_id && todaysEspnIds.has(g.espn_id) && g.game_status === 'STATUS_SCHEDULED'
                );
            }}

            function armStatusRefreshPoll() {{
                if (statusRefreshInterval) {{
                    clearInterval(statusRefreshInterval); statusRefreshInterval = null;
                }}
                if (!hasPendingTippoff()) return;
                statusRefreshInterval = setInterval(() => {{
                    if (!hasPendingTippoff()) {{
                        clearInterval(statusRefreshInterval); statusRefreshInterval = null;
                        return;
                    }}
                    hydrateGameStatuses();
                }}, 60000);
            }}

            async function fetchPlayoffOdds() {{
                try {{
                    const resp = await fetch('/api/playoff-odds');
                    if (!resp.ok) return;
                    const odds = await resp.json();
                    if (!odds || odds.length === 0) return;
                    renderPlayoffPicture(odds);
                }} catch (e) {{
                    // Non-fatal — page works without the section
                }}
            }}

            function renderPlayoffPicture(odds) {{
                const tbody = document.getElementById('playoff-tbody');
                const section = document.getElementById('playoff-picture');
                tbody.innerHTML = odds.map(t => {{
                    const mp = Math.round(t.make_playoffs_prob * 100);
                    const sf = Math.round(t.reach_semis_prob * 100);
                    const fn = Math.round(t.reach_finals_prob * 100);
                    const ch = Math.round(t.win_championship_prob * 100);
                    const eliminated = (mp === 0 && sf === 0 && fn === 0 && ch === 0);
                    const logoHtml = t.logo_url
                        ? `<img class="playoff-logo" src="${{escapeHtml(t.logo_url)}}" alt="" aria-hidden="true">`
                        : `<span class="playoff-logo"></span>`;
                    const cell = (pct, label) => {{
                        const fill = `linear-gradient(to right, rgba(255,107,0,0.20) ${{pct}}%, transparent ${{pct}}%)`;
                        return `<td class="prob-cell" style="background: ${{fill}}" aria-label="${{pct}}% ${{label}}">${{pct}}%</td>`;
                    }};
                    return `
                        <tr${{eliminated ? ' class="eliminated"' : ''}}>
                            <td class="team-cell">${{logoHtml}}<span class="playoff-team-name">${{escapeHtml(t.team)}}</span></td>
                            ${{cell(mp, 'chance to make the playoffs')}}
                            ${{cell(sf, 'chance to reach the semifinals')}}
                            ${{cell(fn, 'chance to reach the finals')}}
                            ${{cell(ch, 'chance to win the championship')}}
                        </tr>`;
                }}).join('');
                section.style.display = '';
            }}

            // Idempotent: sources options from the union of upcoming and
            // completed games and is called after each list loads, so a
            // team/broadcaster that only appears in the archive is still
            // selectable. Preserves prior selections across re-runs.
            function populateFilters() {{
                const lists = [allGames, allCompleted];
                const networks = [...new Set(
                    lists.flatMap(list => list.map(g => g.broadcaster).filter(Boolean))
                )].sort();
                const pillGroup = document.getElementById('network-pills');
                pillGroup.innerHTML = networks.map(n => {{
                    const pressed = selectedNetworks.has(n) ? 'true' : 'false';
                    return `<button class="pill" data-network="${{escapeHtml(n)}}" type="button" aria-pressed="${{pressed}}">${{escapeHtml(NETWORK_LABELS[n] || n)}}</button>`;
                }}).join('');
                pillGroup.querySelectorAll('.pill').forEach(btn => {{
                    btn.addEventListener('click', () => {{
                        const net = btn.dataset.network;
                        if (selectedNetworks.has(net)) {{
                            selectedNetworks.delete(net);
                            btn.setAttribute('aria-pressed', 'false');
                        }} else {{
                            selectedNetworks.add(net);
                            btn.setAttribute('aria-pressed', 'true');
                        }}
                        applyFilters();
                    }});
                }});

                const teams = [...new Set(
                    lists.flatMap(list => list.flatMap(g => [g.team_a, g.team_b]))
                )].sort();
                const teamSelect = document.getElementById('team-filter');
                const currentTeam = teamSelect.value;
                teamSelect.innerHTML = '<option value="">All</option>' + teams.map(t =>
                    `<option value="${{escapeHtml(t)}}">${{escapeHtml(t)}}</option>`
                ).join('');
                if (currentTeam && teams.includes(currentTeam)) {{
                    teamSelect.value = currentTeam;
                }}
            }}

            // Shared by `applyFilters` (upcoming list) and `renderCompleted`
            // (archive). Reads selectedNetworks from module scope.
            function matchesScope(game, team) {{
                if (selectedNetworks.size > 0 && !selectedNetworks.has(game.broadcaster)) return false;
                if (team && game.team_a !== team && game.team_b !== team) return false;
                return true;
            }}

            function applyFilters() {{
                const fromDate = document.getElementById('from-date').value;
                const toDate = document.getElementById('to-date').value;
                const team = document.getElementById('team-filter').value;
                const inScope = (g) => matchesScope(g, team);

                // Top pick is computed against the next 7 days regardless of the
                // user's date range, but suppressed below when it falls outside
                // the active filter window (otherwise a "Top pick · Next 7 days"
                // hero card can promote a game the table doesn't list).
                const today = addDaysISO(0);
                const weekOut = addDaysISO(7);
                const featuredCandidates = allGames.filter(g => {{
                    if (!inScope(g)) return false;
                    const d = localDateISO(g);
                    return d >= today && d <= weekOut;
                }});
                // Backend widens by one ET day for late-ET crossover viewers
                // (see /api/games/upcoming). Floor the list at today-local so
                // yesterday's games don't leak into "upcoming" once the
                // viewer's local calendar has rolled over. An explicit
                // fromDate (user-picked) overrides the floor.
                const lowerBound = fromDate || today;
                let featured = featuredCandidates.length === 0
                    ? null
                    : featuredCandidates.reduce((best, g) => g.overall_score > best.overall_score ? g : best);
                if (featured) {{
                    const fd = localDateISO(featured);
                    if (fd < lowerBound || (toDate && fd > toDate)) featured = null;
                }}
                renderFeatured(featured);

                const games = allGames.filter(game => {{
                    if (!inScope(game)) return false;
                    const d = localDateISO(game);
                    // Live games bypass the implicit today-floor so a late-ET
                    // game still in progress for a west-coast viewer past local
                    // midnight doesn't vanish from the page. An explicit
                    // user-picked fromDate still wins.
                    if (d < lowerBound && !(!fromDate && isLiveStatus(game.game_status))) return false;
                    if (toDate && d > toDate) return false;
                    return true;
                }});

                if (games.length === 0) {{
                    renderEmpty();
                    if (isCompletedExpanded()) renderCompleted();
                    renderedEspnIds = new Set();
                    clearLiveWpPoll();
                    return;
                }}

                let rest = games;
                if (sortBy === 'score') {{
                    rest.sort((a, b) => b.overall_score - a.overall_score);
                }} else {{
                    rest.sort((a, b) => localDateISO(a).localeCompare(localDateISO(b)) || timeKey(a) - timeKey(b));
                }}
                renderGames(rest, featured, 'games-container', SCORE_MODES.OVERALL);
                if (isCompletedExpanded()) renderCompleted();
                renderedEspnIds = new Set(
                    rest.concat(featured ? [featured] : [])
                        .filter(g => g.espn_id)
                        .map(g => g.espn_id)
                );
                hydrateLiveWp();
                armLiveWpPoll();
            }}

            async function loadCompleted() {{
                try {{
                    const resp = await fetch('/api/games/completed');
                    if (!resp.ok) return;
                    allCompleted = await resp.json();
                    // Re-populate so completed-only broadcasters/teams are
                    // selectable in the pills and dropdown.
                    populateFilters();
                    setupCompletedToggle();
                }} catch (e) {{
                    console.error('Failed to load completed games', e);
                }}
            }}

            function setupCompletedToggle() {{
                const btn = document.getElementById('completed-toggle');
                const content = document.getElementById('completed-content');
                const countEl = document.getElementById('completed-toggle-count');
                if (!btn || !content || !countEl) return;
                if (!allCompleted.length) return;
                btn.hidden = false;
                countEl.textContent = '(' + allCompleted.length + ')';
                if (btn.dataset.toggleReady) return;
                btn.dataset.toggleReady = '1';
                btn.addEventListener('click', () => {{
                    const next = btn.getAttribute('aria-expanded') !== 'true';
                    btn.setAttribute('aria-expanded', String(next));
                    content.hidden = !next;
                    btn.querySelector('.completed-toggle-text').textContent =
                        next ? 'Hide completed games' : 'Show completed games';
                    if (next) renderCompleted();
                }});
                const dateBtn = document.getElementById('completed-sort-date');
                const exciteBtn = document.getElementById('completed-sort-excitement');
                const label = document.getElementById('completed-sort-label');
                function setCompletedSort(mode) {{
                    if (mode === completedSortMode) return;
                    completedSortMode = mode;
                    dateBtn.setAttribute('aria-pressed', mode === 'date' ? 'true' : 'false');
                    exciteBtn.setAttribute('aria-pressed', mode === 'excitement' ? 'true' : 'false');
                    if (label) label.textContent = mode === 'date'
                        ? '· Sorted by date'
                        : '· Sorted by excitement';
                    if (isCompletedExpanded()) renderCompleted();
                }}
                dateBtn.addEventListener('click', () => setCompletedSort('date'));
                exciteBtn.addEventListener('click', () => setCompletedSort('excitement'));
            }}

            function isCompletedExpanded() {{
                const btn = document.getElementById('completed-toggle');
                return !!btn && btn.getAttribute('aria-expanded') === 'true';
            }}

            function applyExcitementClass(eyebrow, label) {{
                eyebrow.textContent = label || '';
                eyebrow.className = 'excitement-eyebrow'
                    + (label === 'Thriller' ? ' thriller' : label === 'Close game' ? ' close' : '');
            }}

            function excitementLabelFor(score) {{
                if (score == null) return '';
                if (score >= EXCITEMENT_THRILLER) return 'Thriller';
                if (score >= EXCITEMENT_CLOSE) return 'Close game';
                return '';
            }}

            function sortCompleted(games) {{
                const byDateDesc = (a, b) => {{
                    const da = localDateISO(a), db = localDateISO(b);
                    return da < db ? 1 : da > db ? -1 : 0;
                }};
                const byExciteDesc = (a, b) =>
                    (b.excitement_index ?? -Infinity) - (a.excitement_index ?? -Infinity);
                const cmp = completedSortMode === 'date'
                    ? (a, b) => byDateDesc(a, b) || byExciteDesc(a, b)
                    : (a, b) => byExciteDesc(a, b) || byDateDesc(a, b);
                return games.slice().sort(cmp);
            }}

            function renderCompleted() {{
                const container = document.getElementById('completed-games-container');
                if (!container) return;
                const team = document.getElementById('team-filter').value;
                const filtered = sortCompleted(allCompleted.filter(g => matchesScope(g, team)));
                renderGames(filtered, null, 'completed-games-container', SCORE_MODES.EXCITEMENT);
                filtered.forEach(g => {{
                    if (!g.espn_id) return;
                    const eyebrow = container.querySelector(`[data-espn-id="${{g.espn_id}}"] .excitement-eyebrow`);
                    if (eyebrow) applyExcitementClass(eyebrow, excitementLabelFor(g.excitement_index));
                }});
            }}

            function renderFeatured(game) {{
                const container = document.getElementById('featured-container');
                if (!game) {{ container.innerHTML = ''; return; }}

                const importance = game.importance_score == null ? '—' : game.importance_score.toFixed(0);
                const importanceTitle = game.importance_score == null
                    ? 'Not simulated'
                    : 'Playoff stakes from Monte Carlo';
                const wp = winProbText(game);
                const wpTag = game.espn_id ? ` data-row-wp-id="${{escapeHtml(game.espn_id)}}"` : '';
                const winProbStat = wp
                    ? `<span class="featured-stat"><span class="featured-stat-label">Win prob</span><span class="featured-stat-value"${{wpTag}}>${{wp}}</span></span>`
                    : '';

                container.innerHTML = `
                    <div class="featured-eyebrow">Top pick &middot; Next 7 days</div>
                    <article class="featured" aria-label="Top pick game">
                        <div>
                            <div class="featured-meta">${{formatLocalDate(game, {{ weekday: 'long', month: 'long', day: 'numeric' }})}} &middot; ${{escapeHtml(formatLocalTime(game.time_utc, game.time))}}</div>
                            <div class="featured-teams">
                                ${{renderTeam(game.team_a, game.team_a_logo)}}
                                <span class="vs">vs</span>
                                ${{renderTeam(game.team_b, game.team_b_logo)}}
                            </div>
                            <div class="featured-bottom">
                                <span class="featured-stat">
                                    <span class="featured-stat-label">Quality</span>
                                    <span class="featured-stat-value">${{game.quality_score.toFixed(0)}}</span>
                                </span>
                                <span class="featured-stat" title="${{escapeHtml(importanceTitle)}}">
                                    <span class="featured-stat-label">Importance</span>
                                    <span class="featured-stat-value">${{importance}}</span>
                                </span>
                                ${{winProbStat}}
                                <span class="featured-broadcaster">${{escapeHtml(game.broadcaster || 'TBD')}}</span>
                            </div>
                        </div>
                        <div class="featured-score">
                            <div class="featured-score-num">${{formatScore(game.overall_score)}}</div>
                            <div class="featured-score-label">Overall · /100</div>
                        </div>
                    </article>
                `;
            }}

            function renderEmpty() {{
                document.getElementById('games-container').innerHTML = `
                    <div class="empty-state" role="status">
                        <div class="empty-state-icon" aria-hidden="true">0</div>
                        <div class="empty-state-title">No games match</div>
                        <div class="empty-state-msg">Try clearing a filter or expanding the date window.</div>
                    </div>
                `;
            }}

            function renderGames(games, featured, containerId, scoreHeader) {{
                const container = document.getElementById(containerId || 'games-container');
                if (games.length === 0) {{ container.innerHTML = ''; return; }}
                container.innerHTML = `
                    <table class="games-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Time</th>
                                <th>${{scoreHeader}}</th>
                                <th>Matchup</th>
                                <th class="hide-mobile">Quality</th>
                                <th class="hide-mobile">Importance</th>
                                <th>Watch on</th>
                            </tr>
                        </thead>
                        <tbody>${{games.map(g => renderGameRow(g, g === featured, scoreHeader)).join('')}}</tbody>
                    </table>
                    <div class="games-cards">${{games.map(g => renderGameCard(g, g === featured, scoreHeader)).join('')}}</div>
                `;
            }}

            function formatLocalTime(timeUtc, fallback) {{
                if (!timeUtc) return fallback || 'TBD';
                const d = new Date(timeUtc);
                if (isNaN(d)) return fallback || 'TBD';
                return d.toLocaleTimeString(undefined, {{
                    hour: 'numeric',
                    minute: '2-digit',
                    timeZoneName: 'short',
                }});
            }}

            function timeKey(g) {{
                const t = g.time_utc ? Date.parse(g.time_utc) : NaN;
                return isNaN(t) ? Infinity : t;
            }}

            // Local-tz Date derived from time_utc, or null if unavailable.
            // Shared by localDateISO and formatLocalDate. Both fall back to
            // the ET schedule date when this returns null (transient during
            // the first daily-update cycle after deploy).
            function gameLocalDate(g) {{
                if (!g.time_utc) return null;
                const d = new Date(g.time_utc);
                return isNaN(d) ? null : d;
            }}

            function localDateISO(g) {{
                const d = gameLocalDate(g);
                if (!d) return g.date;
                return d.getFullYear() + '-' +
                    String(d.getMonth() + 1).padStart(2, '0') + '-' +
                    String(d.getDate()).padStart(2, '0');
            }}

            function formatLocalDate(g, opts) {{
                const d = gameLocalDate(g);
                if (!d) return formatDate(g.date, opts);
                return d.toLocaleDateString(
                    undefined, opts || {{ weekday: 'short', month: 'short', day: 'numeric' }}
                );
            }}

            function formatDate(dateStr, opts) {{
                const [year, month, day] = dateStr.split('-');
                return new Date(year, month - 1, day).toLocaleDateString(
                    'en-US', opts || {{ weekday: 'short', month: 'short', day: 'numeric' }}
                );
            }}

            function escapeHtml(s) {{
                return String(s).replace(/[&<>"']/g, c => ({{
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                }})[c]);
            }}

            function renderTeam(name, logo) {{
                const safeName = escapeHtml(name);
                if (logo) {{
                    return `<span class="team"><img class="team-logo" src="${{escapeHtml(logo)}}" alt="" loading="lazy">${{safeName}}</span>`;
                }}
                return `<span class="team">${{safeName}}</span>`;
            }}

            function getScoreClass(score) {{
                if (score == null) return 'empty';
                return score >= 40 ? 'high' : score >= 25 ? 'medium' : 'low';
            }}

            function formatScore(score) {{
                return score == null ? '—' : score.toFixed(0);
            }}

            // scoreHeader matches the column label so the rendered value matches the
            // column's scale: 'Excitement' renders excitement_index (~0-12, em dash when
            // missing — older games without espn_id can't be backfilled from PBP);
            // 'Overall' renders overall_score (0-100). Mixing scales in one column makes
            // the higher-scale value look like a "great" game when it's unrelated.
            // SCORE_MODES is the source of truth; an unknown scoreHeader is a caller
            // bug that surfaces as an em-dash + console.error rather than silently
            // mis-rendering against the wrong scale.
            const SCORE_MODES = {{ OVERALL: 'Overall', EXCITEMENT: 'Excitement' }};

            function primaryScore(game, scoreHeader) {{
                if (scoreHeader === SCORE_MODES.EXCITEMENT) {{
                    return game.excitement_index != null
                        ? game.excitement_index.toFixed(1)
                        : '—';
                }}
                if (scoreHeader === SCORE_MODES.OVERALL) {{
                    return formatScore(game.overall_score);
                }}
                console.error('primaryScore: unknown scoreHeader:', scoreHeader);
                return '—';
            }}

            function primaryScoreClass(game, scoreHeader) {{
                if (scoreHeader === SCORE_MODES.EXCITEMENT) {{
                    if (game.excitement_index == null) return 'empty';
                    if (game.excitement_index >= EXCITEMENT_THRILLER) return 'high';
                    if (game.excitement_index >= EXCITEMENT_CLOSE) return 'medium';
                    return 'low';
                }}
                if (scoreHeader === SCORE_MODES.OVERALL) {{
                    return getScoreClass(game.overall_score);
                }}
                return 'empty';
            }}

            // Pass homePctOverride (0..1) for live data; omit for pregame (uses game.win_prob_a).
            function winProbText(game, homePctOverride) {{
                const homePct = homePctOverride != null ? homePctOverride : game.win_prob_a;
                if (homePct == null) return '';
                const pctA = Math.round(homePct * 100);
                const pctB = 100 - pctA;
                const a = escapeHtml(game.team_a_abbr || game.team_a);
                const b = escapeHtml(game.team_b_abbr || game.team_b);
                return `${{a}} ${{pctA}}% · ${{b}} ${{pctB}}%`;
            }}

            function renderWinProb(game) {{
                const text = winProbText(game);
                if (!text) return '';
                const tag = game.espn_id ? ` data-row-wp-id="${{escapeHtml(game.espn_id)}}"` : '';
                return `<div class="win-prob"${{tag}}>${{text}}</div>`;
            }}

            function renderFinalScore(game) {{
                if (game.final_score_a == null || game.final_score_b == null) return '';
                const a = escapeHtml(game.team_a_abbr || game.team_a);
                const b = escapeHtml(game.team_b_abbr || game.team_b);
                const winA = game.final_score_a > game.final_score_b;
                const winB = game.final_score_b > game.final_score_a;
                const aCls = winA ? 'final-team win' : 'final-team';
                const bCls = winB ? 'final-team win' : 'final-team';
                return `<div class="final-score" aria-label="Final score">
                    <span class="${{aCls}}">${{a}} ${{game.final_score_a}}</span>
                    <span class="final-sep">·</span>
                    <span class="${{bCls}}">${{b}} ${{game.final_score_b}}</span>
                    <span class="final-tag">Final</span>
                </div>`;
            }}

            function renderScoreLine(game) {{
                if (game.final_score_a != null && game.final_score_b != null) {{
                    return renderFinalScore(game);
                }}
                return renderWinProb(game);
            }}

            function renderMiniBar(label, score, kind) {{
                if (score == null) {{
                    return `
                        <div class="mini-bar-row" role="img" aria-label="${{label}} not simulated"
                             title="Not simulated">
                            <span class="mini-bar-label">${{label}}</span>
                            <span class="mini-bar-track" aria-hidden="true"></span>
                            <span class="mini-bar-num empty">&mdash;</span>
                        </div>
                    `;
                }}
                const pct = Math.max(0, Math.min(100, score));
                const num = score.toFixed(0);
                return `
                    <div class="mini-bar-row" role="img" aria-label="${{label}} ${{num}} of 100">
                        <span class="mini-bar-label">${{label}}</span>
                        <span class="mini-bar-track" aria-hidden="true"><span class="mini-bar-fill ${{kind}}" style="width: ${{pct}}%"></span></span>
                        <span class="mini-bar-num">${{num}}</span>
                    </div>
                `;
            }}

            function renderMiniBarCompact(score, kind) {{
                if (score == null) {{
                    return `
                        <div class="mini-bar-compact" role="img" aria-label="${{kind}} not simulated">
                            <span class="mini-bar-track" aria-hidden="true"></span>
                            <span class="mini-bar-num empty">&mdash;</span>
                        </div>
                    `;
                }}
                const pct = Math.max(0, Math.min(100, score));
                const num = score.toFixed(0);
                return `
                    <div class="mini-bar-compact" role="img" aria-label="${{kind}} ${{num}} of 100">
                        <span class="mini-bar-track" aria-hidden="true"><span class="mini-bar-fill ${{kind}}" style="width: ${{pct}}%"></span></span>
                        <span class="mini-bar-num">${{num}}</span>
                    </div>
                `;
            }}

            function renderGameRow(game, isTopPick, scoreHeader) {{
                const cls = primaryScoreClass(game, scoreHeader);
                const impTitle = game.importance_score == null ? 'Not simulated' : '';
                const badge = isTopPick ? '<div class="top-pick-badge">Top pick</div>' : '';
                return `
                    <tr${{game.espn_id ? ` data-espn-id="${{escapeHtml(game.espn_id)}}" role="link" tabindex="0"` : ''}}>
                        <td class="col-date">${{formatLocalDate(game)}}</td>
                        <td class="col-time">${{escapeHtml(formatLocalTime(game.time_utc, game.time))}}</td>
                        <td class="score-cell"><div class="score-stack">${{game.espn_id ? `<span class="excitement-eyebrow" data-wp-id="${{escapeHtml(game.espn_id)}}"></span>` : ''}}<span class="score-num ${{cls}}">${{primaryScore(game, scoreHeader)}}</span></div></td>
                        <td>
                            ${{badge}}
                            <div class="matchup">
                                <div>
                                    ${{renderTeam(game.team_a, game.team_a_logo)}}
                                    ${{game.team_a_playoff_prob != null ? `<div class="team-prob">${{Math.round(game.team_a_playoff_prob * 100)}}% playoff odds</div>` : ''}}
                                </div>
                                <span class="vs">vs</span>
                                <div>
                                    ${{renderTeam(game.team_b, game.team_b_logo)}}
                                    ${{game.team_b_playoff_prob != null ? `<div class="team-prob">${{Math.round(game.team_b_playoff_prob * 100)}}% playoff odds</div>` : ''}}
                                </div>
                                ${{renderScoreLine(game)}}
                            </div>
                        </td>
                        <td class="hide-mobile">${{renderMiniBarCompact(game.quality_score, 'quality')}}</td>
                        <td class="hide-mobile" title="${{impTitle}}">${{renderMiniBarCompact(game.importance_score, 'importance')}}</td>
                        <td><span class="broadcaster-badge">${{escapeHtml(game.broadcaster || 'TBD')}}</span></td>
                    </tr>
                `;
            }}

            function renderGameCard(game, isTopPick, scoreHeader) {{
                const cls = primaryScoreClass(game, scoreHeader);
                const eyebrow = isTopPick ? '<div class="games-card-eyebrow">Top pick</div>' : '';
                const dateStr = formatLocalDate(game);
                const timeStr = escapeHtml(formatLocalTime(game.time_utc, game.time));
                const hasBroadcaster = game.broadcaster && game.broadcaster !== 'TBD';
                const broadcastSeg = hasBroadcaster ? ` &middot; ${{escapeHtml(game.broadcaster)}}` : '';
                const meta = `${{dateStr}} &middot; ${{timeStr}}${{broadcastSeg}}`;
                return `
                    <div class="games-card"${{game.espn_id ? ` data-espn-id="${{escapeHtml(game.espn_id)}}" role="link" tabindex="0"` : ''}}>
                        <div class="games-card-score ${{cls}}">${{primaryScore(game, scoreHeader)}}</div>
                        <div class="games-card-stack">
                            ${{game.espn_id ? `<span class="excitement-eyebrow" data-wp-id="${{escapeHtml(game.espn_id)}}"></span>` : ''}}
                            ${{eyebrow}}
                            <div class="games-card-matchup">
                                <div>
                                    ${{renderTeam(game.team_a, game.team_a_logo)}}
                                    ${{game.team_a_playoff_prob != null ? `<div class="team-prob">${{Math.round(game.team_a_playoff_prob * 100)}}% playoff odds</div>` : ''}}
                                </div>
                                <span class="vs">vs</span>
                                <div>
                                    ${{renderTeam(game.team_b, game.team_b_logo)}}
                                    ${{game.team_b_playoff_prob != null ? `<div class="team-prob">${{Math.round(game.team_b_playoff_prob * 100)}}% playoff odds</div>` : ''}}
                                </div>
                            </div>
                            <div class="games-card-meta">${{meta}}</div>
                            ${{renderScoreLine(game)}}
                            ${{renderMiniBar('Quality', game.quality_score, 'quality')}}
                            ${{renderMiniBar('Importance', game.importance_score, 'importance')}}
                        </div>
                    </div>
                `;
            }}

            // ESPN reports STATUS_HALFTIME between halves and STATUS_END_PERIOD
            // between quarters. Both are "live" for rendering and polling.
            function isLiveStatus(status) {{
                return status === 'STATUS_IN_PROGRESS'
                    || status === 'STATUS_HALFTIME'
                    || status === 'STATUS_END_PERIOD';
            }}

            // ---------- Live WP hydration (collapsed row) ----------
            // The main table/card shows the *pregame* Elo WP by default
            // (game.win_prob_a). For games currently in progress, fetch the
            // latest live home_pct from /api/live-wp and swap in the live
            // numbers so users don't see a stale pregame line.
            let liveWpInterval = null;
            // Tracks the espn_ids actually rendered after the most recent
            // applyFilters() — so the poller doesn't keep hitting ESPN for
            // games the user has filtered off-screen.
            let renderedEspnIds = new Set();

            function clearLiveWpPoll() {{
                if (liveWpInterval) {{ clearInterval(liveWpInterval); liveWpInterval = null; }}
            }}

            function liveGamesInList() {{
                return (allGames || []).filter(g =>
                    g.espn_id && isLiveStatus(g.game_status) && renderedEspnIds.has(g.espn_id)
                );
            }}

            async function hydrateOneLiveWp(game) {{
                try {{
                    const resp = await fetch(`/api/live-wp?espn_id=${{encodeURIComponent(game.espn_id)}}`);
                    if (!resp.ok) return;
                    const data = await resp.json();
                    // ESPN may have flipped the game to a terminal state since
                    // the last upcoming-list fetch; update local cache so the
                    // poll stops including it.
                    if (data.status) game.game_status = data.status;
                    const plays = data.plays || [];
                    if (plays.length === 0) return;
                    const last = plays[plays.length - 1];
                    const text = winProbText(game, last.home_pct);
                    const sel = `[data-row-wp-id="${{CSS.escape(game.espn_id)}}"]`;
                    document.querySelectorAll(sel).forEach(el => {{ el.innerHTML = text; }});
                    // Paint excitement eyebrow for this live game from play-by-play data.
                    const excitementLabel = computeExcitement(plays);
                    document.querySelectorAll(`[data-espn-id="${{CSS.escape(game.espn_id)}}"]`).forEach(row => {{
                        const eyebrow = row.querySelector('.excitement-eyebrow');
                        if (eyebrow) applyExcitementClass(eyebrow, excitementLabel);
                    }});
                }} catch (e) {{
                    console.warn('Live WP hydration failed:', e);
                }}
            }}

            async function hydrateLiveWp() {{
                const live = liveGamesInList();
                if (live.length === 0) return;
                await Promise.all(live.map(hydrateOneLiveWp));
            }}

            function armLiveWpPoll() {{
                clearLiveWpPoll();
                if (liveGamesInList().length === 0) return;
                liveWpInterval = setInterval(() => {{
                    if (liveGamesInList().length === 0) {{ clearLiveWpPoll(); return; }}
                    hydrateLiveWp();
                }}, 30000);
            }}

            // Elapsed game seconds for a play; regulation = 2400s (4×10 min), each OT = 300s.
            // Returns >2400 for OT plays, which correctly weights them as higher-leverage.
            // ESPN's clock is "M:SS" most of the time but switches to "S.S" (decimal seconds)
            // when under a minute remains in the period.
            function elapsedSeconds(play) {{
                const clock = play.clock || '';
                let remainingInPeriod;
                if (clock.indexOf(':') >= 0) {{
                    const [m, s] = clock.split(':');
                    remainingInPeriod = (parseFloat(m) || 0) * 60 + (parseFloat(s) || 0);
                }} else {{
                    remainingInPeriod = parseFloat(clock) || 0;
                }}
                const periodLength = play.period <= 4 ? 600 : 300;
                const elapsedInPeriod = periodLength - remainingInPeriod;
                let prior = 0;
                for (let q = 1; q < play.period; q++) prior += (q <= 4 ? 600 : 300);
                return prior + elapsedInPeriod;
            }}

            // Excitement = leverage-weighted past WP movement + expected future WP movement.
            //   past   = Σ |ΔWPᵢ| · Lᵢ                  where Lᵢ = elapsed_s / 2400
            //   future = γ · 2p(1−p) · L_now            where p = current WP
            // The future term naturally vanishes when the game is decided (p → 0 or 1),
            // and dominates for currently-tight late-game situations.
            function computeExcitement(plays) {{
                if (!plays || plays.length < 2) return null;
                const REGULATION_SECS = 2400;
                let past = 0;
                for (let i = 1; i < plays.length; i++) {{
                    const dWP = Math.abs(plays[i].home_pct - plays[i - 1].home_pct);
                    past += dWP * (elapsedSeconds(plays[i]) / REGULATION_SECS);
                }}
                const last = plays[plays.length - 1];
                const p = last.home_pct;
                const future = 2 * p * (1 - p) * (elapsedSeconds(last) / REGULATION_SECS);
                return excitementLabelFor(past + EXCITEMENT_FUTURE_WEIGHT * future);
            }}

            function setSortBy(mode) {{
                sortBy = mode;
                document.getElementById('sort-date').setAttribute('aria-pressed', mode === 'date' ? 'true' : 'false');
                document.getElementById('sort-score').setAttribute('aria-pressed', mode === 'score' ? 'true' : 'false');
                applyFilters();
            }}

            // ---------- Modal w/ focus trap ----------
            let lastFocused = null;

            function getFocusable(container) {{
                return Array.from(container.querySelectorAll(
                    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
                )).filter(el => el.offsetParent !== null || el === document.activeElement);
            }}

            function openModal() {{
                lastFocused = document.activeElement;
                const backdrop = document.getElementById('modal-backdrop');
                backdrop.classList.add('open');
                const focusable = getFocusable(backdrop);
                if (focusable.length) focusable[0].focus();
            }}

            function closeModal() {{
                document.getElementById('modal-backdrop').classList.remove('open');
                if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
            }}

            function handleModalKeydown(e) {{
                const backdrop = document.getElementById('modal-backdrop');
                if (!backdrop.classList.contains('open')) return;
                if (e.key === 'Escape') {{ closeModal(); return; }}
                if (e.key !== 'Tab') return;
                const focusable = getFocusable(backdrop);
                if (!focusable.length) {{ e.preventDefault(); return; }}
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (e.shiftKey && document.activeElement === first) {{
                    e.preventDefault();
                    last.focus();
                }} else if (!e.shiftKey && document.activeElement === last) {{
                    e.preventDefault();
                    first.focus();
                }}
            }}

            document.addEventListener('DOMContentLoaded', () => {{
                loadGames();
                fetchPlayoffOdds();
                document.getElementById('from-date').addEventListener('change', () => {{ clearActivePreset(); applyFilters(); }});
                document.getElementById('to-date').addEventListener('change', () => {{ clearActivePreset(); applyFilters(); }});
                document.getElementById('team-filter').addEventListener('change', applyFilters);
                document.getElementById('sort-date').addEventListener('click', () => setSortBy('date'));
                document.getElementById('sort-score').addEventListener('click', () => setSortBy('score'));

                document.querySelectorAll('.preset-pill').forEach(btn => {{
                    btn.addEventListener('click', () => setPreset(btn.dataset.preset));
                }});

                document.getElementById('how-it-works-btn').addEventListener('click', openModal);
                document.getElementById('how-it-works-footer').addEventListener('click', openModal);
                document.getElementById('modal-close').addEventListener('click', closeModal);
                document.getElementById('modal-backdrop').addEventListener('click', (e) => {{
                    if (e.target.id === 'modal-backdrop') closeModal();
                }});
                document.addEventListener('keydown', handleModalKeydown);

                const handleEspnRowClick = (e) => {{
                    const target = e.target.closest('[data-espn-id]');
                    if (!target || !target.dataset.espnId) return;
                    window.location.href = '/game/' + encodeURIComponent(target.dataset.espnId);
                }};
                document.getElementById('games-container').addEventListener('click', handleEspnRowClick);
                document.getElementById('completed-games-container').addEventListener('click', handleEspnRowClick);
                const handleEspnRowKey = (e) => {{
                    if (e.key !== 'Enter') return;
                    const target = e.target.closest('[data-espn-id]');
                    if (!target || !target.dataset.espnId) return;
                    window.location.href = '/game/' + encodeURIComponent(target.dataset.espnId);
                }};
                document.getElementById('games-container').addEventListener('keydown', handleEspnRowKey);
                document.getElementById('completed-games-container').addEventListener('keydown', handleEspnRowKey);

                // Add a shadow to the filter bar only once it pins to the top.
                const sentinel = document.getElementById('controls-sentinel');
                const controls = document.querySelector('.controls');
                if (sentinel && controls && 'IntersectionObserver' in window) {{
                    new IntersectionObserver(([entry]) => {{
                        controls.classList.toggle('is-stuck', !entry.isIntersecting);
                    }}).observe(sentinel);
                }}
            }});
        </script>
    </body>
    </html>
    """


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

    h2h = get_head_to_head(session, game.team_a_id, game.team_b_id, season_year=2026)

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
    """Uppercase eyebrow: DATE · TIME · BROADCASTER (broadcaster optional)."""
    parts = []
    if game.date:
        parts.append(escape_html(game.date))
    if game.time:
        parts.append(escape_html(game.time))
    broadcaster = (game.broadcaster or "").strip()
    if broadcaster and broadcaster.upper() != "TBD":
        parts.append(escape_html(broadcaster))
    return " · ".join(parts)


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

    return f"""
                <div class="breakdown-block">
                    <span class="mini-bar-label">Quality — {q_label} · 60% of score</span>
                    <span class="mini-bar-track" aria-hidden="true"><span class="mini-bar-fill quality" style="width: {q_pct:.1f}%"></span></span>
                    <p class="breakdown-text">{q_text}</p>
                </div>
                <div class="breakdown-block">
                    <span class="mini-bar-label">{escape_html(i_label)}</span>
                    {i_track}
                    <p class="breakdown-text">{i_text}</p>
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
                <h2 class="section-title">Head-to-head &middot; 2026</h2>
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

            function escapeHtml(s) {{
                return String(s).replace(/[&<>"']/g, c => ({{
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                }})[c]);
            }}

            function isLiveStatus(status) {{
                return status === 'STATUS_IN_PROGRESS'
                    || status === 'STATUS_HALFTIME'
                    || status === 'STATUS_END_PERIOD';
            }}

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
                    let status;
                    if (isLiveStatus(data.status)) {{
                        const last = data.plays[data.plays.length - 1];
                        const period = last && last.period ? `Q${{escapeHtml(String(last.period))}}` : '';
                        const clock = last && last.clock ? escapeHtml(String(last.clock)) : '';
                        status = [period, clock].filter(Boolean).join(' ');
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
