import numpy as np
import pandas as pd
from src.darko.aging import age_curve


def _synthetic_careers(seed=2):
    rng = np.random.default_rng(seed)
    # true aging: improve through 26, decline after. delta(age) = (26-age)*0.2
    rows = []
    for pid in range(1, 60):
        base = rng.normal(0, 1)
        for age in range(21, 33):
            base += (26 - age) * 0.2 + rng.normal(0, 0.05)
            rows.append({"athlete_id": pid, "age": age, "impact": base})
    return pd.DataFrame(rows)


def test_age_curve_is_increasing_then_decreasing():
    df = _synthetic_careers()
    curve = age_curve(df, age_col="age", impact_col="impact")
    # delta should be positive (improving) at 22, negative (declining) at 31
    assert curve.loc[22] > 0
    assert curve.loc[31] < 0
