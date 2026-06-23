"""Win-probability curve → game-shape metrics for the Replay Value archive.

`plays` is the list of {seq, period, clock, home_pct} dicts that
`fetch_live_win_probability` returns. Every metric is derived purely from the
home-win-probability series; `elapsed_seconds` converts ESPN period+clock to
game-time seconds. Mirrors excitement.py's "<2 plays → None" contract so an
insufficient feed is left absent and retried next run.

Constants here must stay in sync with the JS mirror added in a later plan
(a test will assert this once the mirror exists).
"""

from dataclasses import dataclass

from src.scoring.excitement import (
    REGULATION_SECONDS,
    compute_excitement,
    elapsed_seconds,
)

CURVE_POINTS = 100  # downsampled sparkline resolution


def compute_tension(plays: list[dict]) -> float | None:
    """Late-weighted time-average of in-doubt-ness, in [0, 1] (None if <2 plays).

    Per sample u = 4*p*(1-p) (1 at a coin-flip, 0 at certainty), weighted by
    late weight L = elapsed/2400 and time slice dt since the prior sample, then
    normalized by total weighted time so OT games are not inflated.
    """
    if not plays or len(plays) < 2:
        return None
    num = 0.0
    den = 0.0
    prev_t = elapsed_seconds(plays[0])
    for play in plays[1:]:
        t = elapsed_seconds(play)
        dt = t - prev_t
        prev_t = t
        if dt <= 0:
            continue
        p = play["home_pct"]
        weight = (t / REGULATION_SECONDS) * dt
        num += (4.0 * p * (1.0 - p)) * weight
        den += weight
    if den == 0:
        return None
    return num / den


def _winner_series(plays: list[dict]) -> list[float] | None:
    """The eventual winner's WP series (q_i), or None if <2 plays. Winner = home
    if the final home_pct > 0.5 else away (q = 1 - p for away)."""
    if not plays or len(plays) < 2:
        return None
    home_won = plays[-1]["home_pct"] > 0.5
    return [(p["home_pct"] if home_won else 1.0 - p["home_pct"]) for p in plays]


def winner_low_wp(plays: list[dict]) -> float | None:
    """The eventual winner's lowest win probability (min q_i)."""
    q = _winner_series(plays)
    return None if q is None else min(q)


def compute_comeback(plays: list[dict]) -> float | None:
    """How far below 50% the eventual winner fell: max(0, 0.5 - min q_i)."""
    low = winner_low_wp(plays)
    return None if low is None else max(0.0, 0.5 - low)


def compute_lead_changes(plays: list[dict]) -> int | None:
    """Count of win-probability favorite flips (sign changes of p - 0.5)."""
    if not plays or len(plays) < 2:
        return None
    changes = 0
    for i in range(1, len(plays)):
        if (plays[i - 1]["home_pct"] - 0.5) * (plays[i]["home_pct"] - 0.5) < 0:
            changes += 1
    return changes


def downsample_curve(
    plays: list[dict], points: int = CURVE_POINTS
) -> list[list[float]]:
    """[[elapsed_seconds, home_pct], ...] reduced to ~`points` evenly-indexed
    samples for the sparkline. Returns everything if already small; always keeps
    the first and last sample so the line spans the full game."""
    series = [[float(elapsed_seconds(p)), p["home_pct"]] for p in plays]
    if len(series) <= points:
        return series
    step = (len(series) - 1) / (points - 1)
    idx = sorted({round(i * step) for i in range(points)})
    return [series[i] for i in idx]


@dataclass
class ShapeMetrics:
    excitement: float
    tension: float
    comeback: float
    lead_changes: int
    winner_low_wp: float
    curve: list[list[float]]


def compute_game_shape(plays: list[dict]) -> ShapeMetrics | None:
    """All archive metrics + the downsampled curve, or None if the feed has
    <2 usable plays (caller leaves the row absent and retries next run)."""
    excitement = compute_excitement(plays, final=True)
    tension = compute_tension(plays)
    comeback = compute_comeback(plays)
    low = winner_low_wp(plays)
    lead_changes = compute_lead_changes(plays)
    if None in (excitement, tension, comeback, low, lead_changes):
        return None
    return ShapeMetrics(
        excitement=excitement,
        tension=tension,
        comeback=comeback,
        lead_changes=lead_changes,
        winner_low_wp=low,
        curve=downsample_curve(plays),
    )
