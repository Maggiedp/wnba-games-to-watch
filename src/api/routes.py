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
                flex-direction: column;
                gap: 10px;
            }}
            .filter-row {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
            .filter-label {{ font-size: 0.9em; font-weight: 600; color: #444; white-space: nowrap; }}
            .filter-group {{ display: flex; align-items: center; gap: 6px; font-size: 0.9em; }}
            .filter-group label {{ font-weight: 600; color: #444; }}
            .filter-group select, .filter-group input[type="date"] {{
                padding: 5px 8px;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 0.9em;
                cursor: pointer;
                background: white;
                font-family: inherit;
            }}
            .filter-group select:focus, .filter-group input[type="date"]:focus {{ outline: none; border-color: #ff6b00; }}
            .pill-group {{ display: flex; gap: 6px; flex-wrap: wrap; }}
            .pill {{
                padding: 4px 10px;
                border: 1px solid #ccc;
                border-radius: 20px;
                font-size: 0.82em;
                font-weight: 500;
                cursor: pointer;
                background: white;
                color: #555;
                font-family: inherit;
            }}
            .pill:hover {{ border-color: #ff6b00; color: #ff6b00; }}
            .pill.active {{ background: #ff6b00; border-color: #ff6b00; color: white; }}
            .sort-toggle {{ display: flex; border: 1px solid #ccc; border-radius: 4px; overflow: hidden; }}
            .sort-btn {{
                padding: 5px 12px;
                border: none;
                background: white;
                font-size: 0.85em;
                cursor: pointer;
                color: #555;
                font-family: inherit;
            }}
            .sort-btn + .sort-btn {{ border-left: 1px solid #ccc; }}
            .sort-btn.active {{ background: #0d1b2a; color: white; }}
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
            <div class="filter-row">
                <span class="filter-label">Networks</span>
                <div class="pill-group" id="network-pills"></div>
            </div>
            <div class="filter-row">
                <div class="filter-group">
                    <label for="from-date">From</label>
                    <input type="date" id="from-date">
                </div>
                <div class="filter-group">
                    <label for="to-date">To</label>
                    <input type="date" id="to-date">
                </div>
                <div class="filter-group">
                    <label for="team-filter">Team</label>
                    <select id="team-filter"><option value="">All teams</option></select>
                </div>
                <div class="filter-group">
                    <label>Sort</label>
                    <div class="sort-toggle">
                        <button class="sort-btn active" id="sort-date" type="button">Date</button>
                        <button class="sort-btn" id="sort-score" type="button">Score</button>
                    </div>
                </div>
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
                    runs the rest of the season 2,000 times per outcome, using Elo ratings with
                    home-court advantage and a margin-of-victory multiplier to decide each
                    simulated game. We measure how much each team&rsquo;s playoff odds swing
                    depending on who wins tonight. Bigger swing &rarr; higher score.
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

            async function loadGames() {{
                const container = document.getElementById('games-container');
                try {{
                    const response = await fetch('/api/games/upcoming');
                    allGames = await response.json();
                    populateFilters();
                    applyFilters();
                }} catch (error) {{
                    container.innerHTML = `<div class="error">Error loading games: ${{error.message}}</div>`;
                }}
            }}

            function populateFilters() {{
                const networks = [...new Set(allGames.map(g => g.broadcaster).filter(Boolean))].sort();
                const pillGroup = document.getElementById('network-pills');
                pillGroup.innerHTML = networks.map(n =>
                    `<button class="pill" data-network="${{escapeHtml(n)}}" type="button">${{escapeHtml(NETWORK_LABELS[n] || n)}}</button>`
                ).join('');
                pillGroup.querySelectorAll('.pill').forEach(btn => {{
                    btn.addEventListener('click', () => {{
                        const net = btn.dataset.network;
                        if (selectedNetworks.has(net)) {{
                            selectedNetworks.delete(net);
                            btn.classList.remove('active');
                        }} else {{
                            selectedNetworks.add(net);
                            btn.classList.add('active');
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

                let games = allGames.filter(game => {{
                    if (selectedNetworks.size > 0 && !selectedNetworks.has(game.broadcaster)) return false;
                    if (team && game.team_a !== team && game.team_b !== team) return false;
                    if (fromDate && game.date < fromDate) return false;
                    if (toDate && game.date > toDate) return false;
                    return true;
                }});

                if (sortBy === 'score') {{
                    games = [...games].sort((a, b) => b.overall_score - a.overall_score);
                }}

                renderGames(games);
            }}

            function renderGames(games) {{
                const container = document.getElementById('games-container');
                if (games.length === 0) {{
                    container.innerHTML = '<div class="empty-state">No games match your filters.</div>';
                    return;
                }}
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
                        <tbody>${{games.map(renderGameRow).join('')}}</tbody>
                    </table>
                `;
            }}

            function formatDate(dateStr) {{
                const [year, month, day] = dateStr.split('-');
                return new Date(year, month - 1, day).toLocaleDateString('en-US', {{ weekday: 'short', month: 'short', day: 'numeric' }});
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
                const scoreColor = game.overall_score >= 40 ? 'score-high' : game.overall_score >= 25 ? 'score-medium' : 'score-low';
                const impTitle = game.importance_score == null ? 'Not simulated — games more than 30 days out aren\\'t projected for playoff impact' : '';
                const impVal = game.importance_score == null ? '&mdash;' : game.importance_score.toFixed(0);
                return `
                    <tr>
                        <td style="white-space: nowrap; color: #555;">${{formatDate(game.date)}}</td>
                        <td style="white-space: nowrap; color: #666; font-size: 0.88em;">${{escapeHtml(game.time || 'TBD')}}</td>
                        <td><span class="score-badge ${{scoreColor}}">${{game.overall_score.toFixed(0)}}/100</span></td>
                        <td>
                            <div class="matchup">
                                ${{renderTeam(game.team_a, game.team_a_logo)}}
                                <span class="vs">vs</span>
                                ${{renderTeam(game.team_b, game.team_b_logo)}}
                            </div>
                        </td>
                        <td class="hide-mobile">${{game.quality_score.toFixed(0)}}</td>
                        <td class="hide-mobile" title="${{impTitle}}">${{impVal}}</td>
                        <td><span class="broadcaster-badge">${{escapeHtml(game.broadcaster || 'TBD')}}</span></td>
                    </tr>
                `;
            }}

            function setSortBy(mode) {{
                sortBy = mode;
                document.getElementById('sort-date').classList.toggle('active', mode === 'date');
                document.getElementById('sort-score').classList.toggle('active', mode === 'score');
                applyFilters();
            }}

            function openModal() {{ document.getElementById('modal-backdrop').classList.add('open'); }}
            function closeModal() {{ document.getElementById('modal-backdrop').classList.remove('open'); }}

            document.addEventListener('DOMContentLoaded', () => {{
                loadGames();
                document.getElementById('from-date').addEventListener('change', applyFilters);
                document.getElementById('to-date').addEventListener('change', applyFilters);
                document.getElementById('team-filter').addEventListener('change', applyFilters);
                document.getElementById('sort-date').addEventListener('click', () => setSortBy('date'));
                document.getElementById('sort-score').addEventListener('click', () => setSortBy('score'));
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
