"""Python port of the JS excitement-index formula.

Mirrors the live in-browser computation in src/api/routes.py
(computeExcitement, elapsedSeconds). Used at daily-update time to store
a final excitement score per completed game so the archive endpoint
can sort by it cheaply.

Formula:
    past   = Σ |ΔWPᵢ| · Lᵢ                    where Lᵢ = elapsedSecondsᵢ / 2400
    future = γ · 2·p·(1−p) · L_now            where p = last play's home_pct
    score  = past + future

The future term naturally vanishes for finished games (p → 0 or 1),
so a stored score is dominated by the realized past-movement signal.

Constants must stay in sync with the JS template literals in
src/api/routes.py — tests/test_excitement.py asserts this.
"""

EXCITEMENT_CLOSE = 4.0
EXCITEMENT_THRILLER = 7.5
EXCITEMENT_FUTURE_WEIGHT = 2.5

# Current-win-probability band for the LIVE excitement label. The index is
# cumulative (it never decays), so a game that banked drama early can still
# clear the Close/Thriller thresholds while it's now a blowout. The live label
# (homepage badge + "tune in" alert) is suppressed when the *current* home win
# prob falls outside this band — the label should read "is it close now?", not
# "was it ever close?". Mirrored in js/homepage_helpers.js (test_constants_match_js).
EXCITEMENT_LOPSIDED_LOW = 0.15
EXCITEMENT_LOPSIDED_HIGH = 0.85

REGULATION_SECONDS = 2400  # 4 × 10 min
OT_PERIOD_SECONDS = 300


def elapsed_seconds(play: dict) -> float:
    """Total elapsed game-time seconds at this play.

    ESPN's clock is "M:SS" most of the time, but switches to decimal
    seconds like "48.7" when under a minute remains in the period.
    """
    clock = play.get("clock", "") or ""
    if ":" in clock:
        m, s = clock.split(":", 1)
        remaining = (float(m) if m else 0.0) * 60 + (float(s) if s else 0.0)
    else:
        try:
            remaining = float(clock)
        except ValueError:
            remaining = 0.0
    period = int(play.get("period", 1))
    period_length = 600 if period <= 4 else OT_PERIOD_SECONDS
    elapsed_in_period = period_length - remaining
    prior = sum(600 if q <= 4 else OT_PERIOD_SECONDS for q in range(1, period))
    return prior + elapsed_in_period


def compute_excitement(plays: list[dict], final: bool = False) -> float | None:
    """Return the raw excitement score, or None if there are fewer than
    2 plays.

    `final=False` (default, live games): includes γ·2p(1−p)·L_now to
    capture expected residual movement when the game is still close.

    `final=True` (completed games): omits the future term. ESPN's last
    recorded WP sample for a finished game often stops at e.g. 0.92
    instead of snapping to 1.0, so the live formula would otherwise
    persist a phantom future-swing component in the archive's stored
    score.

    None signals "no usable data" so callers leave `excitement_index`
    NULL and retry on the next run.
    """
    if not plays or len(plays) < 2:
        return None
    past = 0.0
    for i in range(1, len(plays)):
        d_wp = abs(plays[i]["home_pct"] - plays[i - 1]["home_pct"])
        past += d_wp * (elapsed_seconds(plays[i]) / REGULATION_SECONDS)
    if final:
        return past
    last = plays[-1]
    p = last["home_pct"]
    future = 2 * p * (1 - p) * (elapsed_seconds(last) / REGULATION_SECONDS)
    return past + EXCITEMENT_FUTURE_WEIGHT * future
