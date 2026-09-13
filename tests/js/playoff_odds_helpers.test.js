const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

// Self-contained: buildSeedRow calls seedPctText in the same file; none use shared.js.
const {
  buildSeedRow, seedsViewAvailable, seedPctText, heatAlpha, champHeatAlpha,
  liveMarkerFor, playoffsColumnIsDead,
} = loadHelpers('playoff_odds_helpers.js');

// --- buildSeedRow (Playoff Picture "Seeds" view) ---

test('buildSeedRow: null/undefined/empty has no data and 8 all-blank cells', () => {
  for (const empty of [null, undefined, {}]) {
    const row = buildSeedRow(empty);
    assert.equal(row.hasData, false);
    assert.equal(row.cells.length, 8);
    // join to a string, not deepEqual: the vm loader puts cells in a separate
    // realm, so a cross-realm array fails deepStrictEqual's prototype check.
    assert.equal(row.cells.map(c => c.seed).join(','), '1,2,3,4,5,6,7,8');
    assert.ok(row.cells.every(c => c.display === '' && c.prob === 0));
  }
});

test('buildSeedRow: string-keyed distribution renders % and flags the modal seed', () => {
  // Sums to 1.00; seeds 6-8 absent → blank. Modal (argmax) = seed 1.
  const row = buildSeedRow({ '1': 0.45, '2': 0.30, '3': 0.15, '4': 0.07, '5': 0.03 });
  assert.equal(row.hasData, true);
  assert.equal(row.cells[0].display, '45%');
  assert.equal(row.cells[1].display, '30%');
  assert.equal(row.cells[4].display, '3%');
  assert.equal(row.cells[5].display, '');   // seed 6 absent
  assert.equal(row.cells[7].display, '');   // seed 8 absent
});

test('buildSeedRow: tiny-but-nonzero shows <1%, explicit zero shows blank', () => {
  const row = buildSeedRow({ '7': 0, '8': 0.003 });
  assert.equal(row.cells[7].display, '<1%');  // 0.003 rounds to 0% → "<1%"
  assert.equal(row.cells[6].display, '');     // explicit 0 → blank
});

test('seedPctText: blank for 0, <1% for tiny-nonzero, rounded % otherwise', () => {
  assert.equal(seedPctText(0), '');
  assert.equal(seedPctText(-0.1), '');   // guards float noise below zero
  assert.equal(seedPctText(0.003), '<1%');
  assert.equal(seedPctText(0.45), '45%');
  assert.equal(seedPctText(1), '100%');
});

test('heatAlpha: "0" for 0/negative, else a 0.06–0.85 opacity ramp', () => {
  assert.equal(heatAlpha(0), '0');
  assert.equal(heatAlpha(-0.1), '0');
  assert.equal(heatAlpha(0.5), '0.510');   // 0.06 + 0.5*0.9
  assert.equal(heatAlpha(1), '0.850');     // capped at 0.85
});

test('champHeatAlpha: "0" for 0/negative, else a steeper 0.1+ ramp capped at 0.5', () => {
  assert.equal(champHeatAlpha(0), '0');
  assert.equal(champHeatAlpha(-0.1), '0');
  assert.equal(champHeatAlpha(0.12), '0.268');  // 0.1 + 0.12*1.4 — a favorite's ~12%
  assert.equal(champHeatAlpha(0.5), '0.500');   // 0.1 + 0.5*1.4 = 0.8 → capped at 0.5
});

// --- seedsViewAvailable (Seeds toggle gate — must not show a mixed snapshot) ---

test('seedsViewAvailable: only when every displayed team has non-null seed_distribution', () => {
  const populated = { seed_distribution: { '1': 0.5, '2': 0.5 } };
  const eliminated = { seed_distribution: {} };   // valid computed empty (0% playoffs)
  const legacy = { seed_distribution: null };      // no data: legacy/unwritten/malformed
  // {} counts as data — an eliminated team is a complete row, not a missing one.
  assert.equal(seedsViewAvailable([populated, eliminated]), true);
  // Mixed snapshot (one legacy null) must keep the toggle hidden.
  assert.equal(seedsViewAvailable([populated, legacy]), false);
  assert.equal(seedsViewAvailable([legacy, legacy]), false);
  // Nothing to show.
  assert.equal(seedsViewAvailable([]), false);
  assert.equal(seedsViewAvailable(null), false);
});

// --- liveMarkerFor (the /playoff-odds live marker's decision logic) ---

test('liveMarkerFor: empty odds array is not visible', () => {
  const state = liveMarkerFor([]);
  assert.equal(state.visible, false);
});

test('liveMarkerFor: a non-live array is not visible', () => {
  const state = liveMarkerFor([{ live: false, live_state: null }]);
  assert.equal(state.visible, false);
});

test('liveMarkerFor: live_state "live" is visible with the dot', () => {
  const state = liveMarkerFor([{ live: true, live_state: 'live' }]);
  assert.equal(state.visible, true);
  assert.equal(state.showDot, true);
  assert.match(state.text, /Live/);
});

test('liveMarkerFor: live_state "settled" is visible without the dot', () => {
  const state = liveMarkerFor([{ live: true, live_state: 'settled' }]);
  assert.equal(state.visible, true);
  assert.equal(state.showDot, false);
  assert.match(state.text, /Updated/);
});

// --- playoffsColumnIsDead (hide the Playoffs column only once it can't move) ---

test('playoffsColumnIsDead: true only when every team is mathematically in or out', () => {
  const clinched = [
    { make_playoffs_prob: 1 }, { make_playoffs_prob: 1 },
    { make_playoffs_prob: 0 }, { make_playoffs_prob: 0 },
  ];
  assert.equal(playoffsColumnIsDead(clinched), true);
});

test('playoffsColumnIsDead: false mid-race — one team still on the bubble', () => {
  // The regression this guards: gating the column on `live` alone would hide
  // the page's primary probability in June, when it is the most informative
  // column on the table.
  const bubble = [
    { make_playoffs_prob: 1 }, { make_playoffs_prob: 0.62 },
    { make_playoffs_prob: 0 },
  ];
  assert.equal(playoffsColumnIsDead(bubble), false);
});

test('playoffsColumnIsDead: false for a near-certain but not certain team', () => {
  assert.equal(playoffsColumnIsDead([{ make_playoffs_prob: 0.999 }]), false);
  assert.equal(playoffsColumnIsDead([{ make_playoffs_prob: 0.001 }]), false);
});

test('playoffsColumnIsDead: false for empty/null/malformed input', () => {
  assert.equal(playoffsColumnIsDead([]), false);
  assert.equal(playoffsColumnIsDead(null), false);
  assert.equal(playoffsColumnIsDead(undefined), false);
  assert.equal(playoffsColumnIsDead([null]), false);
  assert.equal(playoffsColumnIsDead([{}]), false);
});
