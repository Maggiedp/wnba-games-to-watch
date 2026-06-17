import numpy as np
import pandas as pd
from src.darko.box_prior import fit, player_prior, game_signal

STATS = ["pts", "reb", "ast", "stl", "blk", "tov"]


def _synthetic_box(n=400, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, size=(n, len(STATS)))
    true_coef = np.array([1.0, 0.3, 0.5, 0.8, 0.7, -0.6])
    y = X @ true_coef + rng.normal(0, 0.1, size=n)  # plus-minus per 100
    df = pd.DataFrame(X, columns=STATS)
    df["plus_minus_per100"] = y
    df["athlete_id"] = rng.integers(1, 30, size=n)
    return df, true_coef


def test_fit_recovers_box_coefficients():
    df, true_coef = _synthetic_box()
    model = fit(df, stat_cols=STATS, target_col="plus_minus_per100")
    assert np.allclose(model.coef, true_coef, atol=0.15)


def test_game_signal_applies_same_coefficients():
    df, _ = _synthetic_box()
    model = fit(df, stat_cols=STATS, target_col="plus_minus_per100")
    one_game = df.iloc[0]
    expected = float(
        np.array([one_game[s] for s in STATS]) @ model.coef + model.intercept
    )
    assert abs(game_signal(model, one_game) - expected) < 1e-9


def test_player_prior_averages_per_player():
    df, _ = _synthetic_box()
    model = fit(df, stat_cols=STATS, target_col="plus_minus_per100")
    priors = player_prior(model, df)
    assert set(priors.index) <= set(df["athlete_id"].unique())
    assert priors.notna().all()
