"""Win-probability curve → game-shape metrics for the Replay Value archive.

`plays` is the list of {seq, period, clock, home_pct} dicts that
`fetch_live_win_probability` returns. Every metric is derived purely from the
home-win-probability series; `elapsed_seconds` converts ESPN period+clock to
game-time seconds. Mirrors excitement.py's "<2 plays → None" contract so an
insufficient feed is left absent and retried next run.

Live games on /replay compute their shape server-side through
`compute_live_shape` (Plan 3d, 2026-07-03) — reusing these same functions — so
there is intentionally NO JS metric mirror to keep in sync.
"""

from dataclasses import dataclass

from src.scoring.excitement import (
    REGULATION_SECONDS,
    compute_excitement,
    elapsed_seconds,
)

CURVE_POINTS = 100  # downsampled sparkline resolution

# Coverage gate for FINAL games only (compute_live_shape keeps the bare
# <2-plays contract — a live feed is legitimately partial). A feed that is
# individually-valid but collectively degenerate (ESPN's 2025-05-02 DAL@LV
# feed: 3 samples spanning 2 seconds, all 0.0) otherwise archives garbage
# metrics — a flat sliver at 0% scored comeback=0.5 and topped the /replay
# comeback sort. Healthy feeds have ~100+ samples spanning 2400-2700s, so
# both thresholds sit far below every legitimate row observed (768/768).
MIN_SHAPE_PLAYS = 20
MIN_SHAPE_SPAN_SECONDS = REGULATION_SECONDS * 0.75


def feed_span_seconds(plays: list[dict]) -> float:
    """Game-time seconds a time-sorted feed covers (0.0 if <2 plays). Public
    so rejection logging reports the same number the gate tested."""
    if len(plays) < 2:
        return 0.0
    return elapsed_seconds(plays[-1]) - elapsed_seconds(plays[0])


def _covers_game(plays: list[dict]) -> bool:
    """True when a time-sorted FINAL feed has enough samples and game-time
    span for the shape metrics + curve to honestly represent the game."""
    if len(plays) < MIN_SHAPE_PLAYS:
        return False
    return feed_span_seconds(plays) >= MIN_SHAPE_SPAN_SECONDS


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


def _winner_series(plays: list[dict], home_won: bool) -> list[float] | None:
    """The winner's WP series (q_i), or None if <2 plays. `home_won` is the
    ACTUAL result (from the final score), NOT inferred from the last WP sample —
    ESPN can mark a game FINAL before its WP feed crosses to the real winner, so
    inferring here would mis-score exactly the late comebacks this metric exists
    to catch. For the away winner, q = 1 - p."""
    if not plays or len(plays) < 2:
        return None
    return [(p["home_pct"] if home_won else 1.0 - p["home_pct"]) for p in plays]


def winner_low_wp(plays: list[dict], home_won: bool) -> float | None:
    """The winner's lowest win probability (min q_i). `home_won` is the actual
    result (see `_winner_series`)."""
    q = _winner_series(plays, home_won)
    return None if q is None else min(q)


def compute_comeback(plays: list[dict], home_won: bool) -> float | None:
    """How far below 50% the winner fell: max(0, 0.5 - min q_i). `home_won` is
    the actual result (see `_winner_series`)."""
    low = winner_low_wp(plays, home_won)
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


def compute_game_shape(plays: list[dict], home_won: bool) -> ShapeMetrics | None:
    """All archive metrics + the downsampled curve, or None if the feed can't
    honestly represent the game — fewer than MIN_SHAPE_PLAYS samples or less
    than MIN_SHAPE_SPAN_SECONDS of game-time covered (see _covers_game). The
    caller leaves the row absent and retries next run.

    `home_won` is the ACTUAL result from the final score (not inferred from the
    last WP sample) so the winner-dependent metrics stay consistent with the
    stored `winner` column even when ESPN's WP feed lags the final whistle."""
    if not _covers_game(plays):
        return None
    excitement = compute_excitement(plays, final=True)
    tension = compute_tension(plays)
    comeback = compute_comeback(plays, home_won)
    low = winner_low_wp(plays, home_won)
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


@dataclass
class LiveShape:
    tension: float
    excitement: float
    lead_changes: int
    curve: list[list[float]]


def compute_live_shape(plays: list[dict]) -> LiveShape | None:
    """Winner-independent shape bundle for a LIVE (unfinished) game: tension,
    live excitement (future term ON via final=False), lead changes, and the
    home-oriented downsampled curve. None if <2 usable plays (caller skips the
    game and retries next poll).

    Deliberately NO comeback / winner_low_wp / winner: those are winner-dependent
    and a live game has no winner — computing "against the current leader" would
    flip on every lead change. See the Replay Value Plan 3d design (2026-07-03).
    """
    tension = compute_tension(plays)
    excitement = compute_excitement(plays, final=False)
    lead_changes = compute_lead_changes(plays)
    if None in (tension, excitement, lead_changes):
        return None
    return LiveShape(
        tension=tension,
        excitement=excitement,
        lead_changes=lead_changes,
        curve=downsample_curve(plays),
    )
