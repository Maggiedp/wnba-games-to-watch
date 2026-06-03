from src.scoring.calibration import compute_calibration


def test_empty_predictions():
    r = compute_calibration([])
    assert r.n == 0
    assert r.buckets == []
    assert r.brier == 0.0


def test_perfect_predictor_brier_zero():
    preds = [(1.0, True), (1.0, True), (0.0, False), (0.0, False)]
    r = compute_calibration(preds)
    assert r.brier == 0.0
    assert r.n == 4


def test_always_half():
    preds = [(0.5, True), (0.5, False)]
    r = compute_calibration(preds)
    assert abs(r.brier - 0.25) < 1e-9


def test_bucket_actual_rate():
    # Four predictions in the 0.6-0.7 bucket; 3 of 4 won -> actual_rate 0.75.
    preds = [(0.65, True), (0.65, True), (0.65, True), (0.65, False)]
    r = compute_calibration(preds, n_buckets=10)
    bucket = next(b for b in r.buckets if b.lo <= 0.65 < b.hi)
    assert bucket.count == 4
    assert abs(bucket.actual_rate - 0.75) < 1e-9
    assert abs(bucket.predicted_mean - 0.65) < 1e-9


def test_top_bucket_is_inclusive_of_one():
    # 1.0 must land in the last bucket, not be dropped.
    r = compute_calibration([(1.0, True)], n_buckets=10)
    assert r.n == 1
    assert sum(b.count for b in r.buckets) == 1


def test_none_predictions_dropped():
    r = compute_calibration([(None, True), (0.5, False)])
    assert r.n == 1
