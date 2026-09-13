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
import logging

from src.scoring.elo import update_ratings
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

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
    # {remaining_games index: |final margin|}, in slate order. Feeds the Elo
    # replay below; None when ESPN reported a final without usable scores.
    settled_margins: dict[int, "int | None"] = field(default_factory=dict)


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
            home, away = remaining_games[index]
            if winner == home:
                result.overrides[index] = 1.0
            elif winner == away:
                result.overrides[index] = 0.0
            else:
                # Fail closed. The ESPN winner name matches neither participant
                # (canonicalization drift, a rename, a stale alias row). Recording
                # it as an away win would silently invert this game, so fall back
                # to the Elo probability instead — which is what the daily snapshot
                # already uses.
                logger.warning(
                    "live-odds: winner %r for event %s matches neither participant "
                    "(%r vs %r) — no override",
                    winner,
                    espn_id,
                    home,
                    away,
                )
                continue
            sa, sb = game.get("final_score_a"), game.get("final_score_b")
            result.settled_margins[index] = (
                abs(int(sa) - int(sb)) if sa is not None and sb is not None else None
            )
            result.settled_espn_ids.append(espn_id)
            continue

        home_pct = live_win_probs.get(espn_id)
        if home_pct is None:
            continue  # not started, or the WP fetch failed — fall back to Elo
        result.overrides[index] = quantize_win_prob(home_pct)
        result.live_espn_ids.append(espn_id)

    return result


def settled_record_deltas(
    live: LiveOverrides,
    remaining_games: list[tuple[str, str]],
    remaining_index_by_espn_id: dict[str, int],
) -> dict[str, list[int]]:
    """{team: [wins_delta, losses_delta]} for games final at ESPN but not in the DB.

    The odds fold these games in as certainty overrides, but the displayed W-L
    comes from the games table, which has no intra-day refresh. Without this the
    table publishes odds that already know tonight's result beside a record that
    does not — self-contradictory on screen until the 6 AM run.

    Derived from what the override map already decided (1.0 = home won, 0.0 =
    away won), so it cannot disagree with the simulation it accompanies. Only
    settled games count: an in-progress game has no result to add, and its
    override is a live probability, not a certainty.
    """
    deltas: dict[str, list[int]] = {}

    def bump(team: str, won: bool) -> None:
        d = deltas.setdefault(team, [0, 0])
        d[0 if won else 1] += 1

    for espn_id in live.settled_espn_ids:
        index = remaining_index_by_espn_id.get(espn_id)
        if index is None or index not in live.overrides:
            continue
        home, away = remaining_games[index]
        home_won = live.overrides[index] == 1.0
        bump(home, home_won)
        bump(away, not home_won)
    return deltas


def settled_elo_updates(
    live: LiveOverrides,
    remaining_games: list[tuple[str, str]],
    elo_by_team: dict[str, float],
) -> dict[str, float]:
    """Elo after replaying tonight's settled finals, as the 6 AM run will see it.

    A settled final is a FACT, not a simulated outcome — the daily Elo replay
    will consume it, so the live overlay must too. Without this the overlay
    publishes odds that already know tonight's result while still rating the
    teams as they were this morning; measured at up to ~4pp on a seed cell,
    which is 8x the Monte Carlo's own sampling noise.

    Simulated games deliberately do NOT move a rating: inside one simulation a
    future game is a hypothesis, and the sim holds strength fixed across its
    whole horizon. Only decided games are facts. That asymmetry is the point,
    not an inconsistency.

    Parameters mirror `replay_games` EXACTLY — k=DEFAULT_K, home_advantage=0.0
    (its default, and what daily_update's bare `replay_games(completed)` call
    uses), MOV on when scores are known. A different home_advantage here would
    reintroduce the very live-vs-daily disagreement this closes.
    """
    updated = dict(elo_by_team)
    # Insertion order is slate order, so a team playing twice compounds correctly.
    for index, margin in live.settled_margins.items():
        prob = live.overrides.get(index)
        if prob is None:
            continue
        home, away = remaining_games[index]
        if home not in updated or away not in updated:
            logger.warning(
                "live-odds: settled game %s/%s missing from Elo; not replayed",
                home,
                away,
            )
            continue
        updated[home], updated[away] = update_ratings(
            updated[home],
            updated[away],
            team_a_won=prob == 1.0,
            home_advantage=0.0,
            mov=margin,
        )
    return updated
