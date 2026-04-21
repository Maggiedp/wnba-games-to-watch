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
    quality_score: float
    importance_score: float
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
                quality_score=ranking.quality_score,
                importance_score=ranking.importance_score,
                overall_score=ranking.overall_score,
                broadcaster=ranking.broadcaster,
            )
        )

    return results


_HOMEPAGE_HTML = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>WNBA Games to Watch</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: #f0f0f0;
                min-height: 100vh;
            }
            .header {
                background: #0d1b2a;
                color: white;
                padding: 24px 32px;
                border-bottom: 3px solid #ff6b00;
            }
            .header h1 {
                font-size: 1.6em;
                font-weight: 700;
                letter-spacing: -0.02em;
            }
            .header h1 span {
                color: #ff6b00;
            }
            .header p {
                font-size: 0.9em;
                color: #8a9bb0;
                margin-top: 4px;
            }
            .controls {
                background: white;
                padding: 14px 32px;
                border-bottom: 1px solid #e0e0e0;
                display: flex;
                gap: 20px;
                align-items: center;
            }
            .filter-group {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 0.9em;
            }
            .filter-group label {
                font-weight: 600;
                color: #444;
            }
            .filter-group select {
                padding: 6px 10px;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 0.95em;
                cursor: pointer;
                background: white;
            }
            .filter-group select:focus {
                outline: none;
                border-color: #ff6b00;
            }
            .content {
                max-width: 1000px;
                margin: 0 auto;
                padding: 24px 16px;
            }
            .games-table {
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 6px;
                overflow: hidden;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }
            .games-table thead {
                background: #0d1b2a;
            }
            .games-table th {
                padding: 12px 16px;
                text-align: left;
                font-weight: 600;
                font-size: 0.8em;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: #8a9bb0;
            }
            .games-table td {
                padding: 14px 16px;
                border-bottom: 1px solid #f0f0f0;
                font-size: 0.95em;
            }
            .games-table tbody tr:last-child td {
                border-bottom: none;
            }
            .games-table tbody tr:hover {
                background: #fafafa;
            }
            .score-badge {
                display: inline-block;
                padding: 4px 10px;
                border-radius: 3px;
                font-weight: 700;
                font-size: 0.9em;
                font-variant-numeric: tabular-nums;
            }
            .score-high {
                background: #ff6b00;
                color: white;
            }
            .score-medium {
                background: #ffe0cc;
                color: #a03c00;
            }
            .score-low {
                background: #f0f0f0;
                color: #666;
            }
            .matchup {
                font-weight: 600;
                color: #0d1b2a;
            }
            .broadcaster-badge {
                display: inline-block;
                padding: 3px 8px;
                background: #eef2f7;
                color: #444;
                border-radius: 3px;
                font-size: 0.82em;
                font-weight: 500;
            }
            .empty-state {
                text-align: center;
                padding: 60px 20px;
                color: #888;
                background: white;
                border-radius: 6px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            }
            .error {
                background: #fff0f0;
                color: #c00;
                padding: 14px;
                border-radius: 4px;
                border-left: 3px solid #c00;
            }
            @media (max-width: 768px) {
                .header { padding: 16px; }
                .controls { padding: 12px 16px; }
                .games-table th, .games-table td { padding: 10px 12px; }
                .games-table { font-size: 0.88em; }
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>WNBA <span>Games to Watch</span></h1>
            <p>Upcoming games ranked by quality &amp; playoff importance</p>
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
                <p>Loading...</p>
            </div>
        </div>

        <script>
            async function loadGames() {
                const broadcaster = document.getElementById('broadcaster-filter').value;
                const container = document.getElementById('games-container');

                try {
                    let url = '/api/games/upcoming';
                    if (broadcaster) {
                        url = `/api/games/filter?broadcaster=${encodeURIComponent(broadcaster)}`;
                    }

                    const response = await fetch(url);
                    const games = await response.json();

                    if (games.length === 0) {
                        container.innerHTML = '<div class="empty-state">No upcoming games found.</div>';
                        return;
                    }

                    const html = `
                        <table class="games-table">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Time</th>
                                    <th>Score</th>
                                    <th>Matchup</th>
                                    <th>Quality</th>
                                    <th>Importance</th>
                                    <th>Broadcaster</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${games.map(game => renderGameRow(game)).join('')}
                            </tbody>
                        </table>
                    `;
                    container.innerHTML = html;
                } catch (error) {
                    container.innerHTML = `<div class="error">Error loading games: ${error.message}</div>`;
                }
            }

            function formatDate(dateStr) {
                const [year, month, day] = dateStr.split('-');
                const d = new Date(year, month - 1, day);
                return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
            }

            function renderGameRow(game) {
                const scoreColor = game.overall_score >= 80 ? 'score-high' : game.overall_score >= 50 ? 'score-medium' : 'score-low';

                return `
                    <tr>
                        <td style="white-space: nowrap; color: #555;">${formatDate(game.date)}</td>
                        <td style="white-space: nowrap; color: #666; font-size: 0.88em;">${game.time || 'TBD'}</td>
                        <td>
                            <span class="score-badge ${scoreColor}">
                                ${game.overall_score.toFixed(0)}/100
                            </span>
                        </td>
                        <td class="matchup">${game.team_a} vs ${game.team_b}</td>
                        <td>${game.quality_score.toFixed(0)}</td>
                        <td>${game.importance_score.toFixed(0)}</td>
                        <td>
                            <span class="broadcaster-badge">${game.broadcaster || 'TBD'}</span>
                        </td>
                    </tr>
                `;
            }

            // Load games on page load and when filter changes
            document.addEventListener('DOMContentLoaded', loadGames);
            document.getElementById('broadcaster-filter').addEventListener('change', loadGames);
        </script>
    </body>
    </html>
    """


def render_homepage() -> str:
    return _HOMEPAGE_HTML
