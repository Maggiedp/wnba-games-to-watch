"""API routes and response models for WNBA Games to Watch."""

from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.db.schema import DailyRanking
from src.db.queries import get_team_by_id
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
    results = []

    for ranking in rankings:
        team_a = get_team_by_id(session, ranking.team_a_id)
        team_b = get_team_by_id(session, ranking.team_b_id)

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


def render_homepage() -> str:
    """Render the homepage HTML."""
    html = """
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
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 1000px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
                overflow: hidden;
            }
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }
            .header h1 {
                font-size: 2.5em;
                margin-bottom: 10px;
            }
            .header p {
                font-size: 1.1em;
                opacity: 0.9;
            }
            .controls {
                padding: 20px 30px;
                border-bottom: 1px solid #eee;
                display: flex;
                gap: 20px;
                align-items: center;
                flex-wrap: wrap;
            }
            .filter-group {
                display: flex;
                align-items: center;
                gap: 10px;
            }
            .filter-group label {
                font-weight: 600;
                color: #333;
            }
            .filter-group select {
                padding: 8px 12px;
                border: 1px solid #ddd;
                border-radius: 6px;
                font-size: 1em;
                cursor: pointer;
            }
            .content {
                padding: 30px;
            }
            .games-table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 20px;
            }
            .games-table thead {
                background: #f5f5f5;
            }
            .games-table th {
                padding: 15px;
                text-align: left;
                font-weight: 600;
                color: #333;
                border-bottom: 2px solid #ddd;
            }
            .games-table td {
                padding: 15px;
                border-bottom: 1px solid #eee;
            }
            .games-table tbody tr:hover {
                background: #f9f9f9;
            }
            .score-badge {
                display: inline-block;
                padding: 6px 12px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 0.95em;
            }
            .score-high {
                background: #e8f5e9;
                color: #2e7d32;
            }
            .score-medium {
                background: #fff3e0;
                color: #e65100;
            }
            .score-low {
                background: #f3e5f5;
                color: #6a1b9a;
            }
            .matchup {
                font-weight: 500;
            }
            .broadcaster-badge {
                display: inline-block;
                padding: 4px 8px;
                background: #e3f2fd;
                color: #1565c0;
                border-radius: 4px;
                font-size: 0.85em;
                font-weight: 500;
            }
            .loading {
                text-align: center;
                padding: 40px;
                color: #666;
            }
            .error {
                background: #ffebee;
                color: #c62828;
                padding: 15px;
                border-radius: 6px;
                margin: 20px 0;
            }
            @media (max-width: 768px) {
                .controls {
                    flex-direction: column;
                    align-items: stretch;
                }
                .games-table {
                    font-size: 0.9em;
                }
                .games-table th,
                .games-table td {
                    padding: 10px;
                }
                .header h1 {
                    font-size: 1.8em;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🏀 WNBA Games to Watch</h1>
                <p>Find the most exciting WNBA games ranked by quality & playoff importance</p>
            </div>

            <div class="controls">
                <div class="filter-group">
                    <label for="broadcaster-filter">Filter by broadcaster:</label>
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
                <div id="games-container" class="loading">
                    <p>Loading games...</p>
                </div>
            </div>
        </div>

        <script>
            async function loadGames() {
                const broadcaster = document.getElementById('broadcaster-filter').value;
                const container = document.getElementById('games-container');

                try {
                    let url = '/api/games/today';
                    if (broadcaster) {
                        url = `/api/games/filter?broadcaster=${encodeURIComponent(broadcaster)}`;
                    }

                    const response = await fetch(url);
                    const games = await response.json();

                    if (games.length === 0) {
                        container.innerHTML = '<p style="text-align: center; padding: 40px;">No games found for today.</p>';
                        return;
                    }

                    const html = `
                        <table class="games-table">
                            <thead>
                                <tr>
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

            function renderGameRow(game) {
                const scoreClass = getScoreClass(game.overall_score);
                const scoreColor = game.overall_score >= 80 ? 'score-high' : game.overall_score >= 50 ? 'score-medium' : 'score-low';

                return `
                    <tr>
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

            function getScoreClass(score) {
                if (score >= 80) return 'score-high';
                if (score >= 50) return 'score-medium';
                return 'score-low';
            }

            // Load games on page load and when filter changes
            document.addEventListener('DOMContentLoaded', loadGames);
            document.getElementById('broadcaster-filter').addEventListener('change', loadGames);
        </script>
    </body>
    </html>
    """
    return html
