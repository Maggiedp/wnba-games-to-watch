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
from dataclasses import dataclass, field

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


@dataclass
class LiveOverrides:
    """Per-game probability overrides for today's slate, plus what drove them."""

    overrides: dict[int, float] = field(default_factory=dict)
    live_espn_ids: list[str] = field(default_factory=list)
    settled_espn_ids: list[str] = field(default_factory=list)
    has_postseason: bool = False


def build_live_overrides(
    today_games: list[dict],
    live_win_probs: dict[str, float],
    remaining_index_by_espn_id: dict[str, int],
    remaining_games: list[tuple[str, str]],
) -> LiveOverrides:
    """Turn today's ESPN slate into a per-game-index override map.

    Three game states, one mechanism:

    * final at ESPN but still unrecorded in the DB -> 1.0 or 0.0 (certainty)
    * in progress with a usable WP -> quantized home_pct
    * anything else -> no entry, the sim uses Elo

    Folding finals in as certainty overrides is what keeps the live number from
    being WORSE than the morning snapshot: the games table has no intra-day
    refresh, so a game that ended at 9pm is invisible to standings until 6 AM.
    A game the daily run HAS already ingested is absent from remaining_games and
    therefore has no index, so it contributes nothing here and is counted in
    standings instead — exactly once, either way.
    """
    result = LiveOverrides()

    for game in today_games:
        if game.get("season_type") == 3:
            result.has_postseason = True

        espn_id = game.get("event_id", "")
        index = remaining_index_by_espn_id.get(espn_id)
        if index is None:
            continue  # already ingested, or not a remaining regular-season game

        status = game.get("status", "")
        if status == "STATUS_FINAL":
            winner = game.get("winner_team")
            if winner is None:
                continue  # tie or unparsed final — don't invent a result
            home = remaining_games[index][0]
            result.overrides[index] = 1.0 if winner == home else 0.0
            result.settled_espn_ids.append(espn_id)
            continue

        home_pct = live_win_probs.get(espn_id)
        if home_pct is None:
            continue  # not started, or the WP fetch failed — fall back to Elo
        result.overrides[index] = quantize_win_prob(home_pct)
        result.live_espn_ids.append(espn_id)

    return result
