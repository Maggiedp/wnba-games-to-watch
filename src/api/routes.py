"""API routes and response models for WNBA Games to Watch."""

from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.db.schema import DailyRanking
from src.db.queries import get_game_times, get_teams_by_ids
import logging

logger = logging.getLogger(__name__)


class GameResponse(BaseModel):
    """Response model for a game."""

    date: str
    time: str
    team_a: str
    team_b: str
    team_a_abbr: str = ""
    team_b_abbr: str = ""
    team_a_logo: str = ""
    team_b_logo: str = ""
    quality_score: float
    # None when the game is outside the Monte Carlo window (currently 30 days out)
    # so we haven't simulated its playoff impact.
    importance_score: float | None = None
    overall_score: float
    broadcaster: str

    class Config:
        from_attributes = True


def format_games_response(
    rankings: list[DailyRanking], session: Session
) -> list[GameResponse]:
    """Format DailyRanking objects into GameResponse objects."""
    if not rankings:
        return []

    team_ids = {r.team_a_id for r in rankings} | {r.team_b_id for r in rankings}
    teams = get_teams_by_ids(session, team_ids)
    times = get_game_times(
        session, [(r.date, r.team_a_id, r.team_b_id) for r in rankings]
    )

    results = []
    for ranking in rankings:
        team_a = teams.get(ranking.team_a_id)
        team_b = teams.get(ranking.team_b_id)

        if not team_a or not team_b:
            logger.warning(
                f"Team not found for ranking: {ranking.team_a_id}, {ranking.team_b_id}"
            )
            continue

        results.append(
            GameResponse(
                date=ranking.date,
                time=times.get(
                    (ranking.date, ranking.team_a_id, ranking.team_b_id), ""
                ),
                team_a=team_a.name,
                team_b=team_b.name,
                team_a_abbr=team_a.abbreviation or "",
                team_b_abbr=team_b.abbreviation or "",
                team_a_logo=team_a.logo_url or "",
                team_b_logo=team_b.logo_url or "",
                quality_score=ranking.quality_score,
                importance_score=ranking.importance_score,
                overall_score=ranking.overall_score,
                broadcaster=ranking.broadcaster,
            )
        )

    return results


_SITE_URL = "https://wnba-games-to-watch-1068218371131.us-central1.run.app"
_SITE_TITLE = "WNBA Games to Watch"
_SITE_DESCRIPTION = (
    "A nightly ranking of the best WNBA matchups, weighing team quality and "
    "playoff stakes. Filter by where you can watch."
)

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

        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;0,9..144,700;1,9..144,500&family=Albert+Sans:wght@400;500;600&display=swap" rel="stylesheet">

        <style>
            :root {{
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
            }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            html {{ -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }}
            body {{
                font-family: var(--body);
                background: var(--bg);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                font-size: 16px;
                line-height: 1.45;
            }}

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

            /* ---------- Controls ---------- */
            .controls {{
                background: var(--surface);
                border-bottom: 1px solid var(--line);
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
            .score-num.high {{ color: var(--orange); }}
            .score-num.medium {{ color: var(--orange-deep); }}
            .score-num.low {{ color: var(--text-subtle); }}
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
            .col-num {{ font-feature-settings: 'tnum' on; color: var(--text-muted); font-weight: 500; }}
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

        <div class="controls">
            <div class="controls-inner">
                <div class="filter-row">
                    <span class="filter-label">Networks</span>
                    <div class="pill-group" id="network-pills"></div>
                </div>
                <div class="filter-row">
                    <span class="filter-label">Window</span>
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
                            <button class="sort-btn" id="sort-score" type="button" aria-pressed="false">Score</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <main class="content">
            <div id="featured-container"></div>
            <div id="games-container"></div>
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
                </p>

                <h3>Notes</h3>
                <p>
                    Importance is only computed for games within the next 30 days &mdash; further out,
                    standings move too much for the signal to be meaningful. Expansion teams start
                    at league-average strength until they have a real BPI.
                </p>
            </div>
        </div>

        <script>
            let allGames = [];
            let selectedNetworks = new Set();
            let sortBy = 'date';

            const NETWORK_LABELS = {{
                'ESPN': 'ESPN', 'ABC': 'ABC', 'NBC': 'NBC/Peacock',
                'Prime Video': 'Prime Video', 'CBS': 'CBS/Paramount+',
                'ION': 'ION', 'USA Network': 'USA Network',
                'League Pass': 'League Pass', 'NBA TV': 'NBA TV',
            }};

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
                }} catch (error) {{
                    document.getElementById('featured-container').innerHTML = '';
                    document.getElementById('games-container').innerHTML =
                        `<div class="error">Error loading games: ${{escapeHtml(error.message)}}</div>`;
                }}
            }}

            function populateFilters() {{
                const networks = [...new Set(allGames.map(g => g.broadcaster).filter(Boolean))].sort();
                const pillGroup = document.getElementById('network-pills');
                pillGroup.innerHTML = networks.map(n =>
                    `<button class="pill" data-network="${{escapeHtml(n)}}" type="button" aria-pressed="false">${{escapeHtml(NETWORK_LABELS[n] || n)}}</button>`
                ).join('');
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

                const teams = [...new Set(allGames.flatMap(g => [g.team_a, g.team_b]))].sort();
                const teamSelect = document.getElementById('team-filter');
                teams.forEach(t => {{
                    const opt = document.createElement('option');
                    opt.value = t;
                    opt.textContent = t;
                    teamSelect.appendChild(opt);
                }});
            }}

            function applyFilters() {{
                const fromDate = document.getElementById('from-date').value;
                const toDate = document.getElementById('to-date').value;
                const team = document.getElementById('team-filter').value;

                const matchesScope = (game) => {{
                    if (selectedNetworks.size > 0 && !selectedNetworks.has(game.broadcaster)) return false;
                    if (team && game.team_a !== team && game.team_b !== team) return false;
                    return true;
                }};

                // Top pick is fixed to the next 7 days regardless of the user's date range.
                const today = addDaysISO(0);
                const weekOut = addDaysISO(7);
                const featuredCandidates = allGames.filter(g =>
                    matchesScope(g) && g.date >= today && g.date <= weekOut
                );
                const featured = featuredCandidates.length === 0
                    ? null
                    : featuredCandidates.reduce((best, g) => g.overall_score > best.overall_score ? g : best);
                renderFeatured(featured);

                const games = allGames.filter(game => {{
                    if (!matchesScope(game)) return false;
                    if (fromDate && game.date < fromDate) return false;
                    if (toDate && game.date > toDate) return false;
                    return true;
                }});

                if (games.length === 0) {{
                    renderEmpty();
                    return;
                }}

                let rest = featured ? games.filter(g => g !== featured) : games;
                if (sortBy === 'score') {{
                    rest.sort((a, b) => b.overall_score - a.overall_score);
                }} else {{
                    rest.sort((a, b) => a.date.localeCompare(b.date) || (a.time || '').localeCompare(b.time || ''));
                }}
                renderGames(rest, featured);
            }}

            function renderFeatured(game) {{
                const container = document.getElementById('featured-container');
                if (!game) {{ container.innerHTML = ''; return; }}

                const importance = game.importance_score == null ? '—' : game.importance_score.toFixed(0);
                const importanceTitle = game.importance_score == null
                    ? 'Outside the 30-day Monte Carlo window'
                    : 'Playoff stakes from Monte Carlo';

                container.innerHTML = `
                    <div class="featured-eyebrow">Top pick &middot; Next 7 days</div>
                    <article class="featured" aria-label="Top pick game">
                        <div>
                            <div class="featured-meta">${{formatDate(game.date, {{ weekday: 'long', month: 'long', day: 'numeric' }})}} &middot; ${{escapeHtml(game.time || 'TBD')}}</div>
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
                                <span class="featured-broadcaster">${{escapeHtml(game.broadcaster || 'TBD')}}</span>
                            </div>
                        </div>
                        <div class="featured-score">
                            <div class="featured-score-num">${{game.overall_score.toFixed(0)}}</div>
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

            function renderGames(games, featured) {{
                const container = document.getElementById('games-container');
                if (games.length === 0) {{ container.innerHTML = ''; return; }}
                container.innerHTML = `
                    <table class="games-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Time</th>
                                <th>Score</th>
                                <th>Matchup</th>
                                <th class="hide-mobile">Quality</th>
                                <th class="hide-mobile">Importance</th>
                                <th>Watch on</th>
                            </tr>
                        </thead>
                        <tbody>${{games.map(g => renderGameRow(g, g === featured)).join('')}}</tbody>
                    </table>
                `;
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

            function renderGameRow(game, isTopPick) {{
                const cls = game.overall_score >= 40 ? 'high' : game.overall_score >= 25 ? 'medium' : 'low';
                const impTitle = game.importance_score == null ? 'Not simulated — games more than 30 days out aren\\'t projected for playoff impact' : '';
                const impVal = game.importance_score == null ? '&mdash;' : game.importance_score.toFixed(0);
                const badge = isTopPick ? '<div class="top-pick-badge">Top pick</div>' : '';
                return `
                    <tr>
                        <td class="col-date">${{formatDate(game.date)}}</td>
                        <td class="col-time">${{escapeHtml(game.time || 'TBD')}}</td>
                        <td class="score-cell"><span class="score-num ${{cls}}">${{game.overall_score.toFixed(0)}}</span></td>
                        <td>
                            ${{badge}}
                            <div class="matchup">
                                ${{renderTeam(game.team_a, game.team_a_logo)}}
                                <span class="vs">vs</span>
                                ${{renderTeam(game.team_b, game.team_b_logo)}}
                            </div>
                        </td>
                        <td class="hide-mobile col-num">${{game.quality_score.toFixed(0)}}</td>
                        <td class="hide-mobile col-num" title="${{impTitle}}">${{impVal}}</td>
                        <td><span class="broadcaster-badge">${{escapeHtml(game.broadcaster || 'TBD')}}</span></td>
                    </tr>
                `;
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
            }});
        </script>
    </body>
    </html>
    """


def render_homepage() -> str:
    return _HOMEPAGE_HTML
