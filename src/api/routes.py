"""API routes and response models for WNBA Games to Watch."""

import logging

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from src.data.espn_api import today_et
from src.db.queries import (
    get_game_fields,
    get_playoff_probabilities,
    get_teams_by_ids,
)
from src.db.schema import DailyRanking

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
    """Per-team playoff probability for the standings section."""

    team: str
    abbreviation: str
    logo_url: str
    probability: float  # 0.0–1.0


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
                team_a_playoff_prob=prob_by_team_id.get(ranking.team_a_id),
                team_b_playoff_prob=prob_by_team_id.get(ranking.team_b_id),
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
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;0,9..144,700;0,9..144,900;1,9..144,500&family=Albert+Sans:wght@400;500;600&display=swap" rel="stylesheet">

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

            /* ---------- Playoff Picture ---------- */
            .playoff-picture {{
                background: var(--surface);
                border-bottom: 1px solid var(--line);
                padding: 20px 32px;
            }}
            .playoff-picture-inner {{
                max-width: 1100px;
                margin: 0 auto;
            }}
            .playoff-picture-header {{
                font-family: var(--body);
                font-size: 0.75rem;
                font-weight: 600;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: var(--text-subtle);
                margin-bottom: 14px;
            }}
            .playoff-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 6px 32px;
            }}
            .playoff-row {{
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 0.82rem;
            }}
            .playoff-logo {{
                width: 20px;
                height: 20px;
                object-fit: contain;
                flex-shrink: 0;
            }}
            .playoff-team-name {{
                flex: 1;
                min-width: 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                color: var(--text);
                font-weight: 500;
            }}
            .playoff-bar-track {{
                width: 80px;
                height: 4px;
                background: var(--line);
                border-radius: 2px;
                flex-shrink: 0;
            }}
            .playoff-bar-fill {{
                height: 100%;
                border-radius: 2px;
                background: var(--orange);
                transition: width 0.3s ease;
            }}
            .playoff-pct {{
                font-size: 0.78rem;
                font-variant-numeric: tabular-nums;
                color: var(--text-muted);
                width: 30px;
                text-align: right;
                flex-shrink: 0;
            }}
            @media (max-width: 768px) {{
                .playoff-grid {{
                    grid-template-columns: 1fr;
                }}
                .playoff-picture {{
                    padding: 16px 20px;
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

            /* ---------- WP Chart Panel ---------- */
            [data-espn-id] {{ cursor: pointer; }}
            .games-card[data-espn-id]:hover {{ border-color: var(--navy-3); }}
            .wp-panel-row:hover {{ background: transparent !important; }}
            .wp-panel {{
                padding: 16px 20px;
                background: var(--surface);
                border-top: 1px solid var(--line-soft);
                animation: wpFadeIn 0.15s ease;
            }}
            @keyframes wpFadeIn {{
                from {{ opacity: 0; transform: translateY(-4px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            .wp-panel-header {{
                font-size: 0.82rem;
                color: var(--text-muted);
                margin-bottom: 10px;
                font-weight: 500;
            }}
            .wp-panel-msg {{
                font-size: 0.88rem;
                color: var(--text-subtle);
                font-style: italic;
                padding: 4px 0;
            }}
            .wp-swatch {{
                display: inline-block;
                width: 8px;
                height: 8px;
                border-radius: 50%;
                margin-right: 4px;
                vertical-align: middle;
                position: relative;
                top: -1px;
            }}
            .wp-swatch-home {{ background: var(--orange); }}
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
                <div class="playoff-grid" id="playoff-grid"></div>
            </div>
        </div>

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
                    <h2 id="completed-heading" class="completed-heading">
                        Completed games <span class="completed-heading-sub">&middot; Sorted by excitement</span>
                    </h2>
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

            const NETWORK_LABELS = {{
                'ESPN': 'ESPN', 'ABC': 'ABC', 'NBC': 'NBC/Peacock',
                'Prime Video': 'Prime Video', 'CBS': 'CBS/Paramount+',
                'ION': 'ION', 'USA Network': 'USA Network',
                'League Pass': 'League Pass', 'NBA TV': 'NBA TV',
            }};

            const EXCITEMENT_CLOSE = 4.0;
            const EXCITEMENT_THRILLER = 6.0;
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
                }} catch (error) {{
                    document.getElementById('featured-container').innerHTML = '';
                    document.getElementById('games-container').innerHTML =
                        `<div class="error">Error loading games: ${{escapeHtml(error.message)}}</div>`;
                }}
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
                const grid = document.getElementById('playoff-grid');
                const section = document.getElementById('playoff-picture');
                grid.innerHTML = odds.map(t => {{
                    const pct = Math.round(t.probability * 100);
                    const logoHtml = t.logo_url
                        ? `<img class="playoff-logo" src="${{escapeHtml(t.logo_url)}}" alt="" aria-hidden="true">`
                        : `<span class="playoff-logo"></span>`;
                    return `
                        <div class="playoff-row">
                            ${{logoHtml}}
                            <span class="playoff-team-name">${{escapeHtml(t.team)}}</span>
                            <div class="playoff-bar-track" aria-hidden="true">
                                <div class="playoff-bar-fill" style="width:${{pct}}%"></div>
                            </div>
                            <span class="playoff-pct" aria-label="${{pct}}% playoff probability">${{pct}}%</span>
                        </div>`;
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
                collapsePanel();
                const fromDate = document.getElementById('from-date').value;
                const toDate = document.getElementById('to-date').value;
                const team = document.getElementById('team-filter').value;
                const inScope = (g) => matchesScope(g, team);

                // Top pick is fixed to the next 7 days regardless of the user's date range.
                const today = addDaysISO(0);
                const weekOut = addDaysISO(7);
                const featuredCandidates = allGames.filter(g =>
                    inScope(g) && g.date >= today && g.date <= weekOut
                );
                const featured = featuredCandidates.length === 0
                    ? null
                    : featuredCandidates.reduce((best, g) => g.overall_score > best.overall_score ? g : best);
                renderFeatured(featured);

                const games = allGames.filter(game => {{
                    if (!inScope(game)) return false;
                    if (fromDate && game.date < fromDate) return false;
                    if (toDate && game.date > toDate) return false;
                    return true;
                }});

                if (games.length === 0) {{
                    renderEmpty();
                    if (isCompletedExpanded()) renderCompleted();
                    return;
                }}

                let rest = games;
                if (sortBy === 'score') {{
                    rest.sort((a, b) => b.overall_score - a.overall_score);
                }} else {{
                    rest.sort((a, b) => a.date.localeCompare(b.date) || timeToMinutes(a.time) - timeToMinutes(b.time));
                }}
                renderGames(rest, featured, 'games-container', 'Overall');
                if (isCompletedExpanded()) renderCompleted();
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

            function renderCompleted() {{
                const container = document.getElementById('completed-games-container');
                if (!container) return;
                const team = document.getElementById('team-filter').value;
                const filtered = allCompleted.filter(g => matchesScope(g, team));
                renderGames(filtered, null, 'completed-games-container', 'Excitement');
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
                const winProbStat = wp
                    ? `<span class="featured-stat"><span class="featured-stat-label">Win prob</span><span class="featured-stat-value">${{wp}}</span></span>`
                    : '';

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
                        <tbody>${{games.map(g => renderGameRow(g, g === featured)).join('')}}</tbody>
                    </table>
                    <div class="games-cards">${{games.map(g => renderGameCard(g, g === featured)).join('')}}</div>
                `;
            }}

            function timeToMinutes(t) {{
                if (!t || t === 'TBD') return Infinity;
                const m = t.match(/(\d+):(\d+)\s*(AM|PM)/i);
                if (!m) return Infinity;
                let h = parseInt(m[1], 10);
                const min = parseInt(m[2], 10);
                if (m[3].toUpperCase() === 'PM' && h !== 12) h += 12;
                if (m[3].toUpperCase() === 'AM' && h === 12) h = 0;
                return h * 60 + min;
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

            // Show excitement_index so the visible Score matches the archive's sort key.
            function primaryScore(game) {{
                if (game.excitement_index != null) return game.excitement_index.toFixed(1);
                return formatScore(game.overall_score);
            }}

            function primaryScoreClass(game) {{
                if (game.excitement_index != null) {{
                    if (game.excitement_index >= EXCITEMENT_THRILLER) return 'high';
                    if (game.excitement_index >= EXCITEMENT_CLOSE) return 'medium';
                    return 'low';
                }}
                return getScoreClass(game.overall_score);
            }}

            function winProbText(game) {{
                if (game.win_prob_a == null) return '';
                const pctA = Math.round(game.win_prob_a * 100);
                const pctB = 100 - pctA;
                const a = escapeHtml(game.team_a_abbr || game.team_a);
                const b = escapeHtml(game.team_b_abbr || game.team_b);
                return `${{a}} ${{pctA}}% · ${{b}} ${{pctB}}%`;
            }}

            function renderWinProb(game) {{
                const text = winProbText(game);
                return text ? `<div class="win-prob">${{text}}</div>` : '';
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

            function renderGameRow(game, isTopPick) {{
                const cls = primaryScoreClass(game);
                const impTitle = game.importance_score == null ? 'Not simulated' : '';
                const badge = isTopPick ? '<div class="top-pick-badge">Top pick</div>' : '';
                return `
                    <tr${{game.espn_id ? ` data-espn-id="${{escapeHtml(game.espn_id)}}"` : ''}}>
                        <td class="col-date">${{formatDate(game.date)}}</td>
                        <td class="col-time">${{escapeHtml(game.time || 'TBD')}}</td>
                        <td class="score-cell"><div class="score-stack">${{game.espn_id ? `<span class="excitement-eyebrow" data-wp-id="${{escapeHtml(game.espn_id)}}"></span>` : ''}}<span class="score-num ${{cls}}">${{primaryScore(game)}}</span></div></td>
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

            function renderGameCard(game, isTopPick) {{
                const cls = primaryScoreClass(game);
                const eyebrow = isTopPick ? '<div class="games-card-eyebrow">Top pick</div>' : '';
                const dateStr = formatDate(game.date);
                const timeStr = escapeHtml(game.time || 'TBD');
                const hasBroadcaster = game.broadcaster && game.broadcaster !== 'TBD';
                const broadcastSeg = hasBroadcaster ? ` &middot; ${{escapeHtml(game.broadcaster)}}` : '';
                const meta = `${{dateStr}} &middot; ${{timeStr}}${{broadcastSeg}}`;
                return `
                    <div class="games-card"${{game.espn_id ? ` data-espn-id="${{escapeHtml(game.espn_id)}}"` : ''}}>
                        <div class="games-card-score ${{cls}}">${{primaryScore(game)}}</div>
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

            // ---------- WP Chart ----------
            let openEspnId = null;
            let pollInterval = null;

            // ESPN reports STATUS_HALFTIME between halves and STATUS_END_PERIOD
            // between quarters. Both are "live" for rendering and polling.
            function isLiveStatus(status) {{
                return status === 'STATUS_IN_PROGRESS'
                    || status === 'STATUS_HALFTIME'
                    || status === 'STATUS_END_PERIOD';
            }}

            function collapsePanel() {{
                if (pollInterval) {{ clearInterval(pollInterval); pollInterval = null; }}
                document.querySelectorAll('.wp-panel-row').forEach(el => el.remove());
                document.querySelectorAll('.wp-panel-card').forEach(el => el.remove());
                openEspnId = null;
            }}

            async function expandPanel(espnId, game) {{
                collapsePanel();
                openEspnId = espnId;
                document.querySelectorAll(`[data-espn-id="${{espnId}}"]`).forEach(el => {{
                    const panelHtml = `<div class="wp-panel">
                        <div class="wp-panel-header" data-wp-id="${{espnId}}">Loading…</div>
                        <div class="wp-chart-content" data-wp-id="${{espnId}}"></div>
                    </div>`;
                    if (el.tagName === 'TR') {{
                        const cols = el.querySelectorAll('td').length;
                        el.insertAdjacentHTML('afterend', `<tr class="wp-panel-row"><td colspan="${{cols}}">${{panelHtml}}</td></tr>`);
                    }} else {{
                        el.insertAdjacentHTML('afterend', `<div class="wp-panel-card">${{panelHtml}}</div>`);
                    }}
                }});
                await fetchAndRenderChart(espnId, game);
            }}

            async function fetchAndRenderChart(espnId, game) {{
                try {{
                    const resp = await fetch(`/api/live-wp?espn_id=${{encodeURIComponent(espnId)}}`);
                    if (!resp.ok) {{
                        const msg = resp.status === 404 ? 'Game not found.' : 'Chart unavailable.';
                        setWpContent(espnId, msg, `<div class="wp-panel-msg">${{msg}}</div>`);
                        return;
                    }}
                    const data = await resp.json();
                    renderChartData(espnId, data, game);
                    if (isLiveStatus(data.status) && openEspnId === espnId) {{
                        if (pollInterval) clearInterval(pollInterval);
                        pollInterval = setInterval(async () => {{
                            if (openEspnId !== espnId) {{ clearInterval(pollInterval); pollInterval = null; return; }}
                            try {{
                                const r = await fetch(`/api/live-wp?espn_id=${{encodeURIComponent(espnId)}}`);
                                if (!r.ok) return;
                                const d = await r.json();
                                renderChartData(espnId, d, game);
                                if (!isLiveStatus(d.status)) {{ clearInterval(pollInterval); pollInterval = null; }}
                            }} catch (e) {{ console.warn('WP poll failed:', e); }}
                        }}, 30000);
                    }}
                }} catch (e) {{
                    setWpContent(espnId, 'Chart unavailable.', '<div class="wp-panel-msg">Chart unavailable.</div>');
                }}
            }}

            function renderChartData(espnId, data, game) {{
                const plays = data.plays || [];
                const homeAbbr = escapeHtml(game.team_a_abbr || game.team_a);
                const awayAbbr = escapeHtml(game.team_b_abbr || game.team_b);
                let header = '';
                let chart = '';
                const homeSwatch = `<span class="wp-swatch wp-swatch-home"></span>`;
                if (plays.length === 0) {{
                    const msg = (data.status === 'STATUS_SCHEDULED' || !data.status || data.status === 'STATUS_UNKNOWN')
                        ? "Game hasn't started yet."
                        : 'No chart data available.';
                    header = msg;
                    chart = `<div class="wp-panel-msg">${{msg}}</div>`;
                }} else {{
                    const last = plays[plays.length - 1];
                    const hp = Math.round(last.home_pct * 100);
                    const ap = 100 - hp;
                    const homeLabel = `${{homeSwatch}}${{homeAbbr}} ${{hp}}%`;
                    const awayLabel = `${{awayAbbr}} ${{ap}}%`;
                    const wpLabel = `${{homeLabel}} &middot; ${{awayLabel}} win probability`;
                    const hs = data.home_score != null && data.home_score !== '' ? escapeHtml(String(data.home_score)) : null;
                    const as_ = data.away_score != null && data.away_score !== '' ? escapeHtml(String(data.away_score)) : null;
                    const scoreStr = (hs && as_) ? `${{homeAbbr}} ${{hs}}&ndash;${{as_}} ${{awayAbbr}}` : '';
                    if (data.status === 'STATUS_FINAL') {{
                        header = scoreStr ? `${{scoreStr}} &mdash; Final &middot; ${{wpLabel}}` : `Final &middot; ${{wpLabel}}`;
                    }} else if (isLiveStatus(data.status)) {{
                        let gameLabel;
                        if (data.status === 'STATUS_HALFTIME') {{
                            gameLabel = 'Halftime';
                        }} else if (data.status === 'STATUS_END_PERIOD') {{
                            gameLabel = last.period <= 4 ? `End Q${{last.period}}` : `End OT`;
                        }} else {{
                            const q = last.period <= 4 ? `Q${{last.period}}` : `OT`;
                            const clk = last.clock ? ` ${{escapeHtml(last.clock)}}` : '';
                            gameLabel = `${{q}}${{clk}}`;
                        }}
                        const gameState = scoreStr ? `${{scoreStr}} &middot; ${{gameLabel}}` : gameLabel;
                        header = `${{gameState}} &mdash; ${{wpLabel}}`;
                    }} else {{
                        header = wpLabel;
                    }}
                    chart = buildWpSvg(plays, homeAbbr, awayAbbr);
                }}
                setWpContent(espnId, header, chart);

                const excitementLabel = computeExcitement(plays);
                document.querySelectorAll(`[data-espn-id="${{espnId}}"]`).forEach(row => {{
                    const eyebrow = row.querySelector('.excitement-eyebrow');
                    if (eyebrow) applyExcitementClass(eyebrow, excitementLabel);
                }});
            }}

            function setWpContent(espnId, header, chart) {{
                document.querySelectorAll(`.wp-panel-header[data-wp-id="${{espnId}}"]`).forEach(el => {{
                    el.innerHTML = header;
                }});
                document.querySelectorAll(`.wp-chart-content[data-wp-id="${{espnId}}"]`).forEach(el => {{
                    el.innerHTML = chart || '';
                }});
            }}

            function buildWpSvg(plays, homeAbbr, awayAbbr) {{
                if (!plays || plays.length < 2) return '';
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
                for (let i = 1; i < plays.length; i++) {{
                    if (plays[i].period !== plays[i - 1].period) {{
                        periodBounds.push(pts[i][0].toFixed(1));
                    }}
                }}

                const firstX = pts[0][0].toFixed(1);
                const lastX = pts[N - 1][0].toFixed(1);
                const botY = (H - padB).toFixed(1);

                // Home fill: always below the line (orange)
                const homePoly = [[firstX, botY],
                    ...pts.map(p => [p[0].toFixed(1), p[1].toFixed(1)]),
                    [lastX, botY]].map(p => `${{p[0]}},${{p[1]}}`).join(' ');

                const linePath = 'M ' + pts.map(p => `${{p[0].toFixed(1)}},${{p[1].toFixed(1)}}`).join(' L ');
                const [dotX, dotY] = pts[N - 1];
                const pBounds = periodBounds.map(x =>
                    `<line x1="${{x}}" y1="${{padT}}" x2="${{x}}" y2="${{H - padB}}" stroke="#e7e2d8" stroke-width="1" stroke-dasharray="2,2"/>`
                ).join('');

                return `<svg class="wp-chart-svg" viewBox="0 0 ${{W}} ${{H}}" role="img" aria-label="Win probability chart">
                    <text x="${{padL - 4}}" y="${{padT + 5}}" text-anchor="end" font-size="9" fill="#8a929d">${{awayAbbr}}</text>
                    <text x="${{padL - 4}}" y="${{midY + 3}}" text-anchor="end" font-size="9" fill="#8a929d">50%</text>
                    <text x="${{padL - 4}}" y="${{H - padB}}" text-anchor="end" font-size="9" fill="#8a929d">${{homeAbbr}}</text>
                    <polygon points="${{homePoly}}" fill="rgba(255,107,0,0.15)"/>
                    <line x1="${{padL}}" y1="${{midY.toFixed(1)}}" x2="${{W - padR}}" y2="${{midY.toFixed(1)}}" stroke="#c8c2b8" stroke-width="1" stroke-dasharray="3,3"/>
                    ${{pBounds}}
                    <path d="${{linePath}}" fill="none" stroke="var(--orange)" stroke-width="1.5" stroke-linejoin="round"/>
                    <circle cx="${{dotX.toFixed(1)}}" cy="${{dotY.toFixed(1)}}" r="3" fill="var(--orange)"/>
                </svg>`;
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
                    const espnId = target.dataset.espnId;
                    if (openEspnId === espnId) {{ collapsePanel(); return; }}
                    const game = allGames.find(g => g.espn_id === espnId)
                        || allCompleted.find(g => g.espn_id === espnId);
                    if (!game) return;
                    expandPanel(espnId, game);
                }};
                document.getElementById('games-container').addEventListener('click', handleEspnRowClick);
                document.getElementById('completed-games-container').addEventListener('click', handleEspnRowClick);
            }});
        </script>
    </body>
    </html>
    """


def render_homepage() -> str:
    return _HOMEPAGE_HTML
