// Excitement-index thresholds and weight. Canonical values live in
// src/scoring/excitement.py — test_constants_match_js asserts the sync.
const EXCITEMENT_CLOSE = 4.0;
const EXCITEMENT_THRILLER = 7.5;
const EXCITEMENT_FUTURE_WEIGHT = 2.5;

function excitementLabelFor(score) {
    if (score == null) return '';
    if (score >= EXCITEMENT_THRILLER) return 'Thriller';
    if (score >= EXCITEMENT_CLOSE) return 'Close game';
    return '';
}

// Pass homePctOverride (0..1) for live data; omit for pregame (uses game.win_prob_a).
function winProbText(game, homePctOverride) {
    const homePct = homePctOverride != null ? homePctOverride : game.win_prob_a;
    if (homePct == null) return '';
    const pctA = Math.round(homePct * 100);
    const pctB = 100 - pctA;
    const a = escapeHtml(game.team_a_abbr || game.team_a);
    const b = escapeHtml(game.team_b_abbr || game.team_b);
    return `${a} ${pctA}% · ${b} ${pctB}%`;
}

// Elapsed game seconds for a play; regulation = 2400s (4×10 min), each OT = 300s.
// Returns >2400 for OT plays, which correctly weights them as higher-leverage.
// ESPN's clock is "M:SS" most of the time but switches to "S.S" (decimal seconds)
// when under a minute remains in the period.
function elapsedSeconds(play) {
    const clock = play.clock || '';
    let remainingInPeriod;
    if (clock.indexOf(':') >= 0) {
        const [m, s] = clock.split(':');
        remainingInPeriod = (parseFloat(m) || 0) * 60 + (parseFloat(s) || 0);
    } else {
        remainingInPeriod = parseFloat(clock) || 0;
    }
    const periodLength = play.period <= 4 ? 600 : 300;
    const elapsedInPeriod = periodLength - remainingInPeriod;
    let prior = 0;
    for (let q = 1; q < play.period; q++) prior += (q <= 4 ? 600 : 300);
    return prior + elapsedInPeriod;
}

// Excitement = leverage-weighted past WP movement + expected future WP movement.
//   past   = Σ |ΔWPᵢ| · Lᵢ                  where Lᵢ = elapsed_s / 2400
//   future = γ · 2p(1−p) · L_now            where p = current WP
// The future term naturally vanishes when the game is decided (p → 0 or 1),
// and dominates for currently-tight late-game situations.
// Returns the numeric index, or null when there aren't enough plays.
function excitementScore(plays) {
    if (!plays || plays.length < 2) return null;
    const REGULATION_SECS = 2400;
    let past = 0;
    for (let i = 1; i < plays.length; i++) {
        const dWP = Math.abs(plays[i].home_pct - plays[i - 1].home_pct);
        past += dWP * (elapsedSeconds(plays[i]) / REGULATION_SECS);
    }
    const last = plays[plays.length - 1];
    const p = last.home_pct;
    const future = 2 * p * (1 - p) * (elapsedSeconds(last) / REGULATION_SECS);
    return past + EXCITEMENT_FUTURE_WEIGHT * future;
}

function computeExcitement(plays) {
    return excitementLabelFor(excitementScore(plays));
}

// Pure core of the homepage's rebucketIntoCompleted: builds the
// completed-shaped clone for a just-finalized game, or returns null when
// nothing should be spliced (missing game/espn_id/payload, already present
// in `completed`, or NaN scores from an incomplete payload).
//
// Clones (does not mutate the shared allGames entry): the upcoming objects
// must never gain score fields, or a future applyFilters change could leak
// a final into the upcoming table with a score showing.
// team_a == home (winProbText maps home_pct -> win_prob_a), so
// home_score -> final_score_a, away_score -> final_score_b.
function completedEntryFromLiveWp(game, wpData, completed) {
    if (!game || !game.espn_id || !wpData) return null;
    if (completed.some(g => g.espn_id === game.espn_id)) return null;
    const fa = parseInt(wpData.home_score, 10);
    const fb = parseInt(wpData.away_score, 10);
    if (Number.isNaN(fa) || Number.isNaN(fb)) return null;  // incomplete payload; leave to reload
    return {
        ...game,
        final_score_a: fa,
        final_score_b: fb,
        excitement_index: excitementScore(wpData.plays),
    };
}

// Seed distribution → per-seed display cells for the Playoff Picture "Seeds" view.
// seedDistribution is the /api/playoff-odds field: {seed: prob} with string keys
// (JSON) summing to the team's make-playoffs prob; only seeds that occurred are
// present (absent seed = 0), and it's null/{} for rows a daily run hasn't written.
// Returns { cells: [{seed, prob, display, isModal}] for seeds 1..8, hasData }.
//   display: '' for 0/absent, '<1%' for tiny-nonzero, 'NN%' otherwise (mirrors the
//            round-cell convention).
//   isModal: marks the single most-likely seed (argmax); nothing is flagged when
//            there's no data. hasData = any seed prob > 0.
function buildSeedRow(seedDistribution) {
    const sd = seedDistribution || {};
    let modalSeed = 0;
    let modalProb = 0;
    const cells = [];
    for (let seed = 1; seed <= 8; seed++) {
        const raw = sd[seed];  // numeric index coerces to the string JSON key
        const prob = (typeof raw === 'number' && raw > 0) ? raw : 0;
        if (prob > modalProb) { modalProb = prob; modalSeed = seed; }
        cells.push({ seed, prob });
    }
    for (const c of cells) {
        if (c.prob > 0) {
            const pct = Math.round(c.prob * 100);
            c.display = pct === 0 ? '<1%' : pct + '%';
        } else {
            c.display = '';
        }
        c.isModal = c.seed === modalSeed && modalProb > 0;
    }
    return { cells, hasData: modalProb > 0 };
}

// Gate for the Playoff Picture "Seeds" toggle. Offer it only when the snapshot is
// COMPLETE — every displayed team has a computed seed_distribution (populated, or
// {} for an eliminated team; both non-null). A null is a legacy / not-yet-written /
// malformed row, so a mixed or partial snapshot keeps the toggle hidden rather than
// rendering a team that still has real playoff odds as a blank, eliminated-looking
// row. (buildSeedRow's hasData can't gate this: it's false for a legitimately
// eliminated {} too, so `some(hasData)` would expose a mixed snapshot.)
function seedsViewAvailable(odds) {
    return Array.isArray(odds) && odds.length > 0
        && odds.every(t => t && t.seed_distribution != null);
}
