// Pure helpers for the /playoff-odds page's Playoff Picture table. Moved out of
// homepage_helpers.js when the table moved off the homepage (2026-07-21). No
// module.exports — top-level `function` declarations become globals in the page's
// single <script> scope and in the node:vm test loader.

// Probability (0..1) → heatmap cell text: '' for 0, '<1%' for tiny-nonzero, else
// 'NN%'. Shared by the seed cells (buildSeedRow) and the Miss cell. Distinct from
// the round-cell formatter, which shows '0%' rather than blank for an exact zero.
function seedPctText(prob) {
    if (!(prob > 0)) return '';
    const pct = Math.round(prob * 100);
    return pct === 0 ? '<1%' : pct + '%';
}

// Probability (0..1) → the alpha for a heatmap cell's rgba() fill: '0' (transparent)
// for 0, else a 0.06–0.85 ramp. Shared by the orange seed cells and the greige Miss
// cell — only the rgba hue differs between them.
function heatAlpha(prob) {
    return prob > 0 ? Math.min(0.85, 0.06 + prob * 0.9).toFixed(3) : '0';
}

// Probability (0..1) → the alpha for the Rounds-view "Champ" (title) cell's orange
// fill. A steeper, capped ramp than heatAlpha because championship odds live in a
// small range (~0 to ~0.30 even for the favorite), so the flat seed ramp would read
// as barely-tinted. Capped at 0.5 so it stays a heat cue, not a solid block, and
// never out-shouts the Playoffs funnel bar. '0' (transparent) for 0/eliminated.
function champHeatAlpha(prob) {
    return prob > 0 ? Math.min(0.5, 0.1 + prob * 1.4).toFixed(3) : '0';
}

// Seed distribution → per-seed display cells for the Playoff Picture "Seeds" view.
// seedDistribution is the /api/playoff-odds field: {seed: prob} with string keys
// (JSON) summing to the team's make-playoffs prob; only seeds that occurred are
// present (absent seed = 0). Two distinct empty encodings (do not conflate — the
// Seeds-toggle gate depends on the difference): `{}` is a WRITTEN row for a team
// that reached the playoffs in zero sims (make_playoffs_prob == 0, i.e. eliminated
// — a complete row); `null` is a row a daily run hasn't written (legacy/pre-column).
// Returns { cells: [{seed, prob, display}] for seeds 1..8, hasData }.
//   display: '' for 0/absent, '<1%' for tiny-nonzero, 'NN%' otherwise (mirrors the
//            round-cell convention). hasData = any seed prob > 0.
function buildSeedRow(seedDistribution) {
    const sd = seedDistribution || {};
    let hasData = false;
    const cells = [];
    for (let seed = 1; seed <= 8; seed++) {
        const raw = sd[seed];  // numeric index coerces to the string JSON key
        const prob = (typeof raw === 'number' && raw > 0) ? raw : 0;
        if (prob > 0) hasData = true;
        cells.push({ seed, prob, display: seedPctText(prob) });
    }
    return { cells, hasData };
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
