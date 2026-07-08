"""Pure play-style-fingerprint logic for the /style gallery.

Consumes raw per-team season metrics (from the team_style table) and produces
the league-relative view: each metric normalized 0-100 by percentile rank, a
rank + "of N" per axis, the two most distinctive axes (chips), an auto style
descriptor, and its single nearest stylistic neighbor (when clearly closest).
No I/O — the endpoint and tests both call compute_style_view().
"""

from __future__ import annotations

import math

# Minimum games before a team's style is trustworthy. Below it, a team is shown
# muted and excluded from the league normalization / neighbor computation so a
# 2-game sample can't distort the scale or be called someone's twin.
MIN_STYLE_GAMES = 5

# Radar order (clockwise from top): 5 offense + 2 defense. All are "higher raw
# value -> more of the trait" so higher percentile == further out.
AXES: list[tuple[str, str]] = [
    ("pace", "Pace"),
    ("three_pa_rate", "3PA rate"),
    ("ft_rate", "FT rate"),
    ("oreb_pct", "OREB%"),
    ("assist_rate", "Assist rate"),
    ("def_pressure", "Def. pressure"),
    ("opp_3pa_rate", "3s allowed"),
]

# (high-extreme phrase, low-extreme phrase) per axis. Each phrase describes ONLY
# what its axis measures (high, low) — no inferred intent. A low 3PA rate is
# "few threes" (a high 2P share), NOT "scores in the paint". The two defensive
# axes are style not quality: "Low-pressure D" (forces few TOs) and "Limits 3s"
# (opponents attempt few threes) describe scheme, not a good/bad defense.
_PHRASES: dict[str, tuple[str, str]] = {
    "pace": ("Up-tempo", "Deliberate"),
    "three_pa_rate": ("Three-heavy", "Two-point heavy"),
    "ft_rate": ("Gets to the line", "Rarely to the line"),
    "oreb_pct": ("Crashes the glass", "Rarely crashes glass"),
    "assist_rate": ("Ball-movement", "Self-created"),
    "def_pressure": ("Forces turnovers", "Low-pressure D"),
    "opp_3pa_rate": ("Concedes 3s", "Limits 3s"),
}


def _percentile_norms(values: list[float]) -> list[float]:
    """Map each value to 0-100 by average rank (ties share the mean position),
    floored at 8 so a last-place vertex keeps a small non-zero radius (a
    zero-radius vertex collapses the polygon to the center)."""
    m = len(values)
    if m == 1:
        return [50.0]
    order = sorted(values)
    out = []
    for v in values:
        lo = order.index(v)
        hi = m - 1 - order[::-1].index(v)
        ri = (lo + hi) / 2.0  # average rank index, 0 = lowest
        out.append(8.0 + 92.0 * ri / (m - 1))
    return out


def _ranks(values: list[float]) -> list[int]:
    """1-based rank per value, 1 = highest raw (ties share the rounded mean)."""
    m = len(values)
    desc = sorted(values, reverse=True)
    out = []
    for v in values:
        lo = desc.index(v)
        hi = m - 1 - desc[::-1].index(v)
        out.append(int(round((lo + hi) / 2.0)) + 1)
    return out


def _descriptor(ranks: dict[str, int], m: int) -> list[str]:
    """Up to 3 phrases from the team's most extreme axes (top-3 or bottom-3 of
    the league), ordered by extremity. 'Balanced' when nothing is extreme."""
    mid = (m + 1) / 2.0
    picks = []
    for key, _label in AXES:
        r = ranks[key]
        if r <= 3:
            picks.append((abs(r - mid), _PHRASES[key][0]))
        elif r >= m - 2:
            picks.append((abs(r - mid), _PHRASES[key][1]))
    picks.sort(key=lambda p: -p[0])
    phrases = [p[1] for p in picks[:3]]
    return phrases or ["Balanced"]


def _chips(ranks: dict[str, int], m: int) -> list[dict]:
    """The 2 axes whose rank is furthest from the league middle (most
    distinctive). Ties broken by AXES order."""
    mid = (m + 1) / 2.0
    ordered = sorted(AXES, key=lambda kl: (-abs(ranks[kl[0]] - mid), AXES.index(kl)))
    return [{"label": label, "rank": ranks[key], "of": m} for key, label in ordered[:2]]


# A team's nearest neighbor is shown as its "plays like" twin only when it's a
# clear match — at least this fraction closer than the runner-up. Otherwise the
# single match isn't defensible (at n~15 the 2nd/3rd nearest are a toss-up).
_NEIGHBOR_MARGIN = 0.10


def _neighbors(
    name: str, vecs: dict[str, list[float]], abbrevs: dict[str, str]
) -> list[dict]:
    """The single nearest team by Euclidean distance in the normalized style
    space, but only when it's a clear twin (>= _NEIGHBOR_MARGIN closer than the
    runner-up); else [] ("no clear stylistic twin"). Showing two neighbors was
    unstable at n~15 — the 2nd slot flipped on sub-rank noise."""
    here = vecs[name]
    dists = sorted(
        (math.dist(here, vec), other) for other, vec in vecs.items() if other != name
    )
    if not dists:
        return []
    d1, n1 = dists[0]
    if len(dists) >= 2:
        d2 = dists[1][0]
        if d2 <= 0 or (d2 - d1) / d2 < _NEIGHBOR_MARGIN:
            return []
    return [{"abbr": abbrevs.get(n1, ""), "team": n1}]


def _base(row: dict) -> dict:
    return {
        "team_id": row["team_id"],
        "team": row["team"],
        "abbr": row["abbr"],
        "games_played": row["games_played"],
    }


def compute_style_view(
    rows: list[dict], min_games: int = MIN_STYLE_GAMES
) -> list[dict]:
    """Enrich raw team rows into the /style view. `rows` items carry
    team_id/team/abbr/games_played + the 7 raw metric keys in AXES. Returns one
    dict per input team (order preserved); the endpoint attaches logo/record and
    sorts. Low-confidence teams (< min_games) are returned muted."""
    qualifying = [r for r in rows if r["games_played"] >= min_games]
    if len(qualifying) < 2:
        return [
            {
                **_base(r),
                "low_confidence": True,
                "axes": [],
                "chips": [],
                "descriptor": [],
                "plays_like": [],
            }
            for r in rows
        ]

    q_names = [r["team"] for r in qualifying]
    abbrevs = {r["team"]: r["abbr"] for r in rows}
    norm_map: dict[str, dict[str, float]] = {}
    rank_map: dict[str, dict[str, int]] = {}
    for key, _label in AXES:
        vals = [r[key] for r in qualifying]
        norm_map[key] = dict(zip(q_names, _percentile_norms(vals)))
        rank_map[key] = dict(zip(q_names, _ranks(vals)))
    m = len(qualifying)
    vecs = {n: [norm_map[k][n] for k, _ in AXES] for n in q_names}

    out = []
    for r in rows:
        name = r["team"]
        if r["games_played"] < min_games:
            out.append(
                {
                    **_base(r),
                    "low_confidence": True,
                    "axes": [],
                    "chips": [],
                    "descriptor": [],
                    "plays_like": [],
                }
            )
            continue
        ranks_here = {k: rank_map[k][name] for k, _ in AXES}
        axes = [
            {
                "key": k,
                "label": lbl,
                "value": round(r[k], 3),
                "norm": round(norm_map[k][name], 1),
                "rank": rank_map[k][name],
                "of": m,
            }
            for k, lbl in AXES
        ]
        out.append(
            {
                **_base(r),
                "low_confidence": False,
                "axes": axes,
                "chips": _chips(ranks_here, m),
                "descriptor": _descriptor(ranks_here, m),
                "plays_like": _neighbors(name, vecs, abbrevs),
            }
        )
    return out
