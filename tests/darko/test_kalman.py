import numpy as np
from src.darko.kalman import KalmanFilter


def test_tracks_a_known_trajectory_better_than_naive():
    rng = np.random.default_rng(3)
    true = 5.0
    kf = KalmanFilter(x0=0.0, p0=4.0, q=0.05, r=1.0)
    errs_kf, errs_naive = [], []
    last_obs = 0.0
    for _ in range(60):
        z = true + rng.normal(0, 1.0)
        pred = kf.predict(drift=0.0)
        errs_kf.append((pred - true) ** 2)
        errs_naive.append((last_obs - true) ** 2)
        kf.update(z)
        last_obs = z
    assert np.mean(errs_kf[10:]) < np.mean(errs_naive[10:])


def test_intervals_are_calibrated():
    rng = np.random.default_rng(4)
    covered = 0
    trials = 300
    for _ in range(trials):
        true = rng.normal(0, 3)
        kf = KalmanFilter(x0=0.0, p0=9.0, q=0.0, r=1.0)
        for _ in range(20):
            kf.update(true + rng.normal(0, 1.0))
        lo, hi = kf.interval(0.80)
        if lo <= true <= hi:
            covered += 1
    frac = covered / trials
    assert 0.70 <= frac <= 0.90  # ~80% nominal
