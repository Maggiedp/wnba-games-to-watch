"""Plain-language score explanations for the game detail page.

Deterministic templates keyed off score bands — no LLM, no I/O. Thresholds
are named constants so they can be recalibrated against the live score
distribution in one place (see scripts/inspect_score_distribution.py).

Calibrated 2026-05-31 against the live API (271 upcoming + 62 completed games):
- Importance HIGH=65 / MID=35 splits upcoming games ~35% high / ~45% mid / ~20%
  low (upcoming p25=40, med=58, p75=72) — a healthy, non-degenerate spread; kept.
- Quality BPI cutoffs sit sensibly on the documented ±8 BPI normalization range
  (quality_score spans the full 0–100, med≈53); kept.
- Win-prob coin-flip margin tightened 0.04→0.03: at 0.04 (46–54%) ~22% of games
  read as "dead even," which overstates evenness; 0.03 (47–53%) earns the label.
Re-run the inspect script at season end (more data) to re-validate.
"""

# Quality bands (quality_score is 0-100). Lopsided matchups are detected
# from the BPI gap directly, since the harmonic mean already drags the
# score down for them but doesn't tell us *which* team is stronger.
QUALITY_STRONG_BPI = 3.0  # both teams above this = "contenders"
QUALITY_LOPSIDED_BPI_GAP = 5.0  # |bpi_a - bpi_b| above this = "lopsided"
QUALITY_LOW_BPI = 0.0  # both below this = "rebuilding"

# Importance bands (importance_score is 0-100; None = not simulated).
IMPORTANCE_HIGH = 65.0
IMPORTANCE_MID = 35.0

# win_prob_a is a 0–1 fraction (Elo probability). |win_prob_a - 0.5|
# below this margin = "coin flip" (47–53%; see calibration note above).
WIN_PROB_COIN_FLIP_MARGIN = 0.03


def quality_blurb(
    quality_score: float, bpi_a: float, bpi_b: float, team_a: str, team_b: str
) -> str:
    """One sentence on matchup quality, citing each team's BPI."""
    gap = abs(bpi_a - bpi_b)
    stronger, weaker = (team_a, team_b) if bpi_a >= bpi_b else (team_b, team_a)
    hi, lo = (bpi_a, bpi_b) if bpi_a >= bpi_b else (bpi_b, bpi_a)
    if gap >= QUALITY_LOPSIDED_BPI_GAP:
        return (
            f"Lopsided on paper: {stronger} (BPI {hi:.1f}) is a clear step "
            f"above {weaker} (BPI {lo:.1f})."
        )
    if bpi_a >= QUALITY_STRONG_BPI and bpi_b >= QUALITY_STRONG_BPI:
        return (
            f"Two of the league's best. {team_a} (BPI {bpi_a:.1f}) and "
            f"{team_b} (BPI {bpi_b:.1f}) are both genuine contenders."
        )
    if bpi_a < QUALITY_LOW_BPI and bpi_b < QUALITY_LOW_BPI:
        return (
            f"Two rebuilding teams (BPI {bpi_a:.1f}, {bpi_b:.1f}) — watchable, "
            f"but not a heavyweight bout."
        )
    return (
        f"A solid matchup — {stronger} (BPI {hi:.1f}) against a capable "
        f"{weaker} (BPI {lo:.1f})."
    )


def importance_blurb(importance_score: float | None) -> str:
    """One sentence on playoff stakes from the normalized importance score."""
    if importance_score is None:
        return "Not simulated, so there's no importance score for this game."
    if importance_score >= IMPORTANCE_HIGH:
        return "High stakes — this game meaningfully moves the playoff race."
    if importance_score >= IMPORTANCE_MID:
        return "Some playoff implications for seeding."
    return "Low playoff stakes at this point in the season."


def win_prob_blurb(win_prob_a: float | None, team_a: str, team_b: str) -> str:
    """One sentence on the win-probability split. `win_prob_a` is a 0–1
    fraction (Elo probability team A wins). Empty string when unknown."""
    if win_prob_a is None:
        return ""
    if abs(win_prob_a - 0.5) < WIN_PROB_COIN_FLIP_MARGIN:
        return f"A coin flip — {team_a} and {team_b} are dead even."
    if win_prob_a >= 0.5:
        return f"{team_a} favored at {win_prob_a * 100:.0f}% — Elo, with home court."
    return (
        f"{team_b} favored at {(1.0 - win_prob_a) * 100:.0f}% — Elo, with home court."
    )
