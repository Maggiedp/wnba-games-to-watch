"""WNBA aging curve via the delta method on box-implied impact across consecutive
ages. Box-only -> uses full history, immune to the 3-season stint cliff.

CAVEAT (do not oversell): survivorship bias inflates apparent late-career retention
(better players play longer), and WNBA's small population (~144 players) makes the
curve noisy. Reported as a soft prior on Kalman drift, not a precise truth."""

import pandas as pd


def age_curve(
    df: pd.DataFrame, age_col: str = "age", impact_col: str = "impact"
) -> pd.Series:
    """age -> mean year-over-year change in impact, from same-player consecutive ages."""
    df = df.sort_values(["athlete_id", age_col])
    df = df.assign(
        _next=df.groupby("athlete_id")[impact_col].shift(-1),
        _next_age=df.groupby("athlete_id")[age_col].shift(-1),
    )
    consec = df[df["_next_age"] == df[age_col] + 1].copy()
    consec["_delta"] = consec["_next"] - consec[impact_col]
    return consec.groupby(age_col)["_delta"].mean()
