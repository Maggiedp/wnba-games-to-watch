"""API routes and response models for WNBA Games to Watch."""

from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.db.schema import DailyRanking
from src.db.queries import get_teams_by_ids
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
                time="",
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

        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: #f0f0f0;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
            }}
            .header {{
                background: #0d1b2a;
                color: white;
                padding: 24px 32px;
                border-bottom: 3px solid #ff6b00;
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                gap: 16px;
            }}
            .header-text h1 {{
                font-size: 1.6em;
                font-weight: 700;
                letter-spacing: -0.02em;
            }}
            .header-text h1 span {{ color: #ff6b00; }}
            .header-text p {{
                font-size: 0.9em;
                color: #8a9bb0;
                margin-top: 4px;
            }}
            .header-link {{
                background: transparent;
                color: #8a9bb0;
                border: 1px solid #2b3a52;
                padding: 6px 12px;
                border-radius: 4px;
                font-size: 0.85em;
                cursor: pointer;
                font-family: inherit;
                white-space: nowrap;
            }}
            .header-link:hover {{ color: #ff6b00; border-color: #ff6b00; }}
            .controls {{
                background: white;
                padding: 14px 32px;
                border-bottom: 1px solid #e0e0e0;
                display: flex;
                gap: 20px;
                align-items: center;
            }}
            .filter-group {{
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 0.9em;
            }}
            .filter-group label {{ font-weight: 600; color: #444; }}
            .filter-group select {{
                padding: 6px 10px;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 0.95em;
                cursor: pointer;
                background: white;
            }}
            .filter-group select:focus {{ outline: none; border-color: #ff6b00; }}
            .content {{
                max-width: 1000px;
                margin: 0 auto;
                padding: 24px 16px;
                width: 100%;
                flex: 1;
            }}
            .games-table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 6px;
                overflow: hidden;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }}
            .games-table thead {{ background: #0d1b2a; }}
            .games-table th {{
                padding: 12px 16px;
                text-align: left;
                font-weight: 600;
                font-size: 0.8em;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: #8a9bb0;
            }}
            .games-table td {{
                padding: 14px 16px;
                border-bottom: 1px solid #f0f0f0;
                font-size: 0.95em;
            }}
            .games-table tbody tr:last-child td {{ border-bottom: none; }}
            .games-table tbody tr:hover {{ background: #fafafa; }}
            .score-badge {{
                display: inline-block;
                padding: 4px 10px;
                border-radius: 3px;
                font-weight: 700;
                font-size: 0.9em;
                font-variant-numeric: tabular-nums;
            }}
            .score-high {{ background: #ff6b00; color: white; }}
            .score-medium {{ background: #ffe0cc; color: #a03c00; }}
            .score-low {{ background: #f0f0f0; color: #666; }}
            .matchup {{
                font-weight: 600;
                color: #0d1b2a;
                display: flex;
                align-items: center;
                gap: 10px;
                flex-wrap: wrap;
            }}
            .team {{ display: inline-flex; align-items: center; gap: 6px; }}
            .team-logo {{
                width: 22px;
                height: 22px;
                object-fit: contain;
                flex-shrink: 0;
            }}
            .vs {{ color: #999; font-weight: 400; font-size: 0.85em; }}
            .broadcaster-badge {{
                display: inline-block;
                padding: 3px 8px;
                background: #eef2f7;
                color: #444;
                border-radius: 3px;
                font-size: 0.82em;
                font-weight: 500;
            }}
            .empty-state {{
                text-align: center;
                padding: 60px 20px;
                color: #888;
                background: white;
                border-radius: 6px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }}
            .error {{
                background: #fff0f0;
                color: #c00;
                padding: 14px;
                border-radius: 4px;
                border-left: 3px solid #c00;
            }}
            .footer {{
                text-align: center;
                padding: 24px 16px;
                color: #888;
                font-size: 0.85em;
                border-top: 1px solid #e0e0e0;
                margin-top: 24px;
            }}
            .footer a {{ color: #666; text-decoration: underline; }}
            .footer a:hover {{ color: #ff6b00; }}

            .modal-backdrop {{
                display: none;
                position: fixed;
                inset: 0;
                background: rgba(13, 27, 42, 0.6);
                z-index: 100;
                align-items: flex-start;
                justify-content: center;
                padding: 60px 16px;
                overflow-y: auto;
            }}
            .modal-backdrop.open {{ display: flex; }}
            .modal {{
                background: white;
                max-width: 600px;
                width: 100%;
                border-radius: 8px;
                padding: 32px;
                box-shadow: 0 8px 24px rgba(0,0,0,0.2);
                position: relative;
            }}
            .modal h2 {{
                font-size: 1.3em;
                color: #0d1b2a;
                margin-bottom: 16px;
            }}
            .modal h3 {{
                font-size: 1em;
                color: #0d1b2a;
                margin-top: 20px;
                margin-bottom: 6px;
            }}
            .modal p {{ color: #444; line-height: 1.55; font-size: 0.95em; }}
            .modal a {{ color: #ff6b00; }}
            .modal .close {{
                position: absolute;
                top: 12px;
                right: 16px;
                background: none;
                border: none;
                font-size: 1.5em;
                cursor: pointer;
                color: #888;
                line-height: 1;
            }}
            .modal .close:hover {{ color: #0d1b2a; }}

            @media (max-width: 768px) {{
                .header {{ padding: 16px; }}
                .controls {{ padding: 12px 16px; }}
                .games-table th, .games-table td {{ padding: 10px 10px; }}
                .games-table {{ font-size: 0.88em; }}
                .hide-mobile {{ display: none; }}
                .modal {{ padding: 24px 20px; }}
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-text">
                <h1>WNBA <span>Games to Watch</span></h1>
                <p>Tonight&rsquo;s best matchups, ranked by quality and playoff stakes</p>
            </div>
            <button class="header-link" id="how-it-works-btn" type="button">How it works</button>
        </div>

        <div class="controls">
            <div class="filter-group">
                <label for="broadcaster-filter">Watch on:</label>
                <select id="broadcaster-filter">
                    <option value="">All Networks</option>
                    <option value="ESPN">ESPN</option>
                    <option value="NBC">NBC/Peacock</option>
                    <option value="Prime Video">Prime Video</option>
                    <option value="CBS">CBS/Paramount+</option>
                    <option value="League Pass">League Pass</option>
                    <option value="ION">ION</option>
                    <option value="USA Network">USA Network</option>
                    <option value="NBA TV">NBA TV</option>
                </select>
            </div>
        </div>

        <div class="content">
            <div id="games-container">
                <p>Loading&hellip;</p>
            </div>
        </div>

        <div class="footer">
            Team strength from <a href="https://www.espn.com/wnba/bpi" target="_blank" rel="noopener">ESPN BPI</a>.
            Schedule and broadcasters from ESPN. Updated daily.
            &middot; <a href="#" id="how-it-works-footer">How it works</a>
        </div>

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
                    runs the rest of the season 10,000 times, measuring how each team&rsquo;s playoff
                    odds swing depending on who wins tonight. Bigger swing &rarr; higher score.
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
            async function loadGames() {{
                const broadcaster = document.getElementById('broadcaster-filter').value;
                const container = document.getElementById('games-container');

                try {{
                    let url = '/api/games/upcoming';
                    if (broadcaster) {{
                        url = `/api/games/filter?broadcaster=${{encodeURIComponent(broadcaster)}}`;
                    }}

                    const response = await fetch(url);
                    const games = await response.json();

                    if (games.length === 0) {{
                        container.innerHTML = '<div class="empty-state">No upcoming games found.</div>';
                        return;
                    }}

                    const html = `
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
                            <tbody>
                                ${{games.map(game => renderGameRow(game)).join('')}}
                            </tbody>
                        </table>
                    `;
                    container.innerHTML = html;
                }} catch (error) {{
                    container.innerHTML = `<div class="error">Error loading games: ${{error.message}}</div>`;
                }}
            }}

            function formatDate(dateStr) {{
                const [year, month, day] = dateStr.split('-');
                const d = new Date(year, month - 1, day);
                return d.toLocaleDateString('en-US', {{ weekday: 'short', month: 'short', day: 'numeric' }});
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

            function renderGameRow(game) {{
                const scoreColor = game.overall_score >= 80 ? 'score-high' : game.overall_score >= 50 ? 'score-medium' : 'score-low';

                return `
                    <tr>
                        <td style="white-space: nowrap; color: #555;">${{formatDate(game.date)}}</td>
                        <td style="white-space: nowrap; color: #666; font-size: 0.88em;">${{escapeHtml(game.time || 'TBD')}}</td>
                        <td>
                            <span class="score-badge ${{scoreColor}}">
                                ${{game.overall_score.toFixed(0)}}/100
                            </span>
                        </td>
                        <td>
                            <div class="matchup">
                                ${{renderTeam(game.team_a, game.team_a_logo)}}
                                <span class="vs">vs</span>
                                ${{renderTeam(game.team_b, game.team_b_logo)}}
                            </div>
                        </td>
                        <td class="hide-mobile">${{game.quality_score.toFixed(0)}}</td>
                        <td class="hide-mobile" title="${{game.importance_score == null ? 'Not simulated — games more than 30 days out aren\\'t projected for playoff impact' : ''}}">${{game.importance_score == null ? '&mdash;' : game.importance_score.toFixed(0)}}</td>
                        <td>
                            <span class="broadcaster-badge">${{escapeHtml(game.broadcaster || 'TBD')}}</span>
                        </td>
                    </tr>
                `;
            }}

            function openModal() {{
                document.getElementById('modal-backdrop').classList.add('open');
            }}
            function closeModal() {{
                document.getElementById('modal-backdrop').classList.remove('open');
            }}

            document.addEventListener('DOMContentLoaded', () => {{
                loadGames();
                document.getElementById('broadcaster-filter').addEventListener('change', loadGames);
                document.getElementById('how-it-works-btn').addEventListener('click', openModal);
                document.getElementById('how-it-works-footer').addEventListener('click', (e) => {{
                    e.preventDefault();
                    openModal();
                }});
                document.getElementById('modal-close').addEventListener('click', closeModal);
                document.getElementById('modal-backdrop').addEventListener('click', (e) => {{
                    if (e.target.id === 'modal-backdrop') closeModal();
                }});
                document.addEventListener('keydown', (e) => {{
                    if (e.key === 'Escape') closeModal();
                }});
            }});
        </script>
    </body>
    </html>
    """


def render_homepage() -> str:
    return _HOMEPAGE_HTML
