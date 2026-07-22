const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

// Self-contained: buildSeedRow calls seedPctText in the same file; none use shared.js.
const {
  buildSeedRow, seedsViewAvailable, seedPctText, heatAlpha,
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
