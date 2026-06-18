"""Box-score -> pts/100 impact model. One fitted OLS; two uses:
  - player_prior: slow per-player aggregate (RAPM prior mean)
  - game_signal:  single-game box line -> daily Kalman observation
Same coefficients, different granularity (see types.py / the spec disambiguation)."""

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class BoxModel:
    stat_cols: list
    coef: np.ndarray
    intercept: float
    target_col: str


def fit(df: pd.DataFrame, stat_cols, target_col: str) -> BoxModel:
    X = df[stat_cols].to_numpy(dtype=float)
    y = df[target_col].to_numpy(dtype=float)
    Xa = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(Xa, y, rcond=None)
    return BoxModel(list(stat_cols), beta[1:], float(beta[0]), target_col)


def game_signal(model: BoxModel, box_row) -> float:
    x = np.array([float(box_row[s]) for s in model.stat_cols])
    return float(x @ model.coef + model.intercept)


def player_prior(model: BoxModel, df: pd.DataFrame) -> pd.Series:
    """Mean box-implied impact per athlete_id (the slow RAPM prior mean)."""
    pred = df[model.stat_cols].to_numpy(dtype=float) @ model.coef + model.intercept
    tmp = pd.DataFrame({"athlete_id": df["athlete_id"].to_numpy(), "pred": pred})
    return tmp.groupby("athlete_id")["pred"].mean()
