"""Tests for the numpy IRLS logistic regression helper."""

import numpy as np

from scripts._logistic import logistic_fit


def test_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    n = 5000
    x = rng.normal(size=(n, 1))
    true_b0, true_b1 = -0.5, 1.5
    logits = true_b0 + true_b1 * x[:, 0]
    p = 1 / (1 + np.exp(-logits))
    y = (rng.uniform(size=n) < p).astype(float)

    res = logistic_fit(x, y)
    # Intercept + 1 slope.
    assert abs(res.coef[0] - true_b0) < 0.1
    assert abs(res.coef[1] - true_b1) < 0.15
    assert res.stderr[1] > 0
    # Strong real effect -> tiny p-value.
    assert res.pvalues[1] < 1e-10


def test_null_effect_is_not_significant():
    rng = np.random.default_rng(1)
    n = 4000
    x = rng.normal(size=(n, 1))
    y = (rng.uniform(size=n) < 0.5).astype(float)  # independent of x
    res = logistic_fit(x, y)
    assert res.pvalues[1] > 0.05
