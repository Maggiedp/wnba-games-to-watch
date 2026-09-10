"""Live playoff-odds overlay: determinism primitives and override assembly.

A 10k Monte Carlo carries ~±0.5pp of sampling jitter. The daily path hides
this by seeding RNG from the last completed game's date, so scores are stable
until new results arrive. The live path cannot inherit that seed, and a
percentage that twitches while the ball is dead reads as broken — the more so
because the championship column's real per-game movement (0.4-1.3pp, measured
2026-09-09) is SMALLER than the jitter.

So: seed from a stable hash of the quantized inputs. Same game state, same
number, exactly.
"""

import hashlib
import json

# Live win probabilities are rounded to the nearest percentage point before
# both seeding and simulating. ESPN's home_pct ticks on every play, including
# non-scoring ones; hashing the raw value would reseed on every poll, and a
# 0.612 -> 0.614 nudge would produce a jitter jump larger than the real signal.
LIVE_WP_QUANTUM = 0.01


def quantize_win_prob(p: float) -> float:
    """Round a win probability to the nearest LIVE_WP_QUANTUM."""
    steps = round(p / LIVE_WP_QUANTUM)
    return round(steps * LIVE_WP_QUANTUM, 10)


def live_sim_seed(
    standings: dict[str, dict],
    remaining_games: list[tuple[str, str]],
    overrides: dict[int, float],
) -> int:
    """A deterministic RNG seed over the WHOLE sim input set.

    Hashing only the live game's probability would leave the published number
    unchanged across a real standings correction, so every input participates.

    Uses SHA-256, NOT the builtin hash(): hash() is salted per process, so the
    number would change on every Cloud Run container restart.
    """
    payload = json.dumps(
        {
            "standings": sorted(
                (name, s["wins"], s["losses"], round(s["elo"], 4))
                for name, s in standings.items()
            ),
            "games": [list(g) for g in remaining_games],
            "overrides": sorted(overrides.items()),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)
