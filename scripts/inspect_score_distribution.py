"""Inspect the live score distribution to calibrate game-detail blurb bands.

Read-only. Pulls quality_score / importance_score / win_prob_a from the live
API and prints quantiles, so the band thresholds in `src/api/blurbs.py` can be
checked against reality (rather than guessed) before they're frozen.

Usage:
    python -m scripts.inspect_score_distribution

Re-run at season end (more data) to re-validate the cutoffs — the same way the
excitement thresholds were recalibrated against the completed-game distribution.
"""

import json
import ssl
import urllib.request

try:
    import certifi

    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover - certifi is in requirements
    _CTX = ssl.create_default_context()

_ENDPOINTS = {
    "upcoming": "https://wumbers.com/api/games/upcoming",
    "completed": "https://wumbers.com/api/games/completed",
}


def _fetch(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "score-calibration"})
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as resp:
        return json.load(resp)


def _quantiles(values: list[float | None]) -> str:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return "no data"
    n = len(xs)

    def q(p: float) -> float:
        return xs[min(n - 1, int(p * n))]

    return (
        f"n={n}  min={xs[0]:.2f}  p10={q(0.10):.2f}  p25={q(0.25):.2f}  "
        f"med={q(0.50):.2f}  p75={q(0.75):.2f}  p90={q(0.90):.2f}  max={xs[-1]:.2f}"
    )


def main() -> None:
    for label, url in _ENDPOINTS.items():
        try:
            games = _fetch(url)
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            print(f"{label}: fetch failed: {exc}")
            continue
        print(f"\n=== {label} (rows={len(games)}) ===")
        print("quality   :", _quantiles([g.get("quality_score") for g in games]))
        print("importance:", _quantiles([g.get("importance_score") for g in games]))
        print("win_prob_a:", _quantiles([g.get("win_prob_a") for g in games]))


if __name__ == "__main__":
    main()
