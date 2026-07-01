const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

// shared.js first: winProbText depends on escapeHtml.
const {
  excitementLabelFor, winProbText, elapsedSeconds,
  excitementScore, computeExcitement, completedEntryFromLiveWp,
  buildSeedRow, seedsViewAvailable,
} = loadHelpers('shared.js', 'homepage_helpers.js');

// --- elapsedSeconds (mirrors tests/test_excitement.py for the Python port) ---

test('elapsedSeconds handles M:SS clock format', () => {
  // Period 1, 5:30 remaining → 600 - 330 = 270 elapsed, 0 prior.
  assert.equal(elapsedSeconds({ period: 1, clock: '5:30' }), 270);
  // Period 2, 0:00 remaining → full 1200 (2 quarters).
  assert.equal(elapsedSeconds({ period: 2, clock: '0:00' }), 1200);
});

test('elapsedSeconds handles decimal-seconds clock format', () => {
  // Period 4, 48.7s remaining → 1800 prior + (600 - 48.7) = 2351.3
  assert.ok(Math.abs(elapsedSeconds({ period: 4, clock: '48.7' }) - 2351.3) < 1e-9);
});

test('elapsedSeconds treats OT periods as 300s', () => {
  // First OT, 5:00 remaining = 0 elapsed in OT → 2400 prior.
  assert.equal(elapsedSeconds({ period: 5, clock: '5:00' }), 2400);
  assert.equal(elapsedSeconds({ period: 5, clock: '0:00' }), 2700);
});

// --- excitementScore / computeExcitement ---

test('excitementScore returns null below 2 plays', () => {
  assert.equal(excitementScore(null), null);
  assert.equal(excitementScore([]), null);
  assert.equal(excitementScore([{ period: 1, clock: '10:00', home_pct: 0.5 }]), null);
});

test('excitementScore scores a blowout low', () => {
  const plays = [
    { period: 1, clock: '10:00', home_pct: 0.5 },
    { period: 2, clock: '0:00', home_pct: 0.95 },
    { period: 4, clock: '0:00', home_pct: 1.0 },
  ];
  // past = 0.45·0.5 + 0.05·1.0 = 0.275; future = 2·1·0·1 = 0.
  assert.ok(Math.abs(excitementScore(plays) - 0.275) < 1e-9);
});

test('excitementScore registers a big late swing strongly', () => {
  const plays = [
    { period: 1, clock: '10:00', home_pct: 0.5 },
    { period: 4, clock: '0:30', home_pct: 0.7 },
    { period: 4, clock: '0:00', home_pct: 0.05 },
  ];
  assert.ok(excitementScore(plays) > 1.0);
});

test('computeExcitement maps score to label via thresholds', () => {
  assert.equal(excitementLabelFor(null), '');
  assert.equal(excitementLabelFor(3.9), '');
  assert.equal(excitementLabelFor(4.0), 'Close game');
  assert.equal(excitementLabelFor(7.5), 'Thriller');
  // End-to-end: too few plays → null score → empty label.
  assert.equal(computeExcitement([]), '');
});

// --- winProbText ---

test('winProbText uses pregame win_prob_a and sums to 100%', () => {
  const game = { team_a_abbr: 'NYL', team_b_abbr: 'LVA', win_prob_a: 0.615 };
  assert.equal(winProbText(game), 'NYL 62% · LVA 38%');
});

test('winProbText prefers the live homePctOverride', () => {
  const game = { team_a_abbr: 'NYL', team_b_abbr: 'LVA', win_prob_a: 0.615 };
  assert.equal(winProbText(game, 0.10), 'NYL 10% · LVA 90%');
});

test('winProbText returns empty string with no probability', () => {
  assert.equal(winProbText({ team_a: 'A', team_b: 'B' }), '');
});

test('winProbText falls back to full team names and escapes them', () => {
  const game = { team_a: 'A & B', team_b: '<X>', win_prob_a: 0.5 };
  assert.equal(winProbText(game), 'A &amp; B 50% · &lt;X&gt; 50%');
});

// --- completedEntryFromLiveWp (pure core of rebucketIntoCompleted) ---

const finalWp = {
  home_score: '88', away_score: '85',
  plays: [
    { period: 1, clock: '10:00', home_pct: 0.5 },
    { period: 4, clock: '0:00', home_pct: 1.0 },
  ],
};

test('completedEntryFromLiveWp builds a completed-shaped clone (team_a == home)', () => {
  const game = { espn_id: '401', team_a: 'Home', team_b: 'Away' };
  const entry = completedEntryFromLiveWp(game, finalWp, []);
  assert.equal(entry.final_score_a, 88);   // home_score → final_score_a
  assert.equal(entry.final_score_b, 85);   // away_score → final_score_b
  assert.equal(typeof entry.excitement_index, 'number');
  assert.notEqual(entry, game);            // clone, not the shared allGames entry
  assert.equal(game.final_score_a, undefined);  // source object not mutated
});

test('completedEntryFromLiveWp dedups by espn_id', () => {
  const game = { espn_id: '401', team_a: 'Home', team_b: 'Away' };
  assert.equal(completedEntryFromLiveWp(game, finalWp, [{ espn_id: '401' }]), null);
});

test('completedEntryFromLiveWp rejects missing game/espn_id/payload', () => {
  assert.equal(completedEntryFromLiveWp(null, finalWp, []), null);
  assert.equal(completedEntryFromLiveWp({ team_a: 'Home' }, finalWp, []), null);
  assert.equal(completedEntryFromLiveWp({ espn_id: '401' }, null, []), null);
});

test('completedEntryFromLiveWp rejects NaN scores (incomplete payload)', () => {
  const game = { espn_id: '401' };
  assert.equal(completedEntryFromLiveWp(game, { ...finalWp, home_score: null }, []), null);
  assert.equal(completedEntryFromLiveWp(game, { ...finalWp, away_score: undefined }, []), null);
});

test('completedEntryFromLiveWp nulls excitement_index when plays are too thin', () => {
  const entry = completedEntryFromLiveWp({ espn_id: '401' }, { ...finalWp, plays: [] }, []);
  assert.equal(entry.excitement_index, null);
});

// --- buildSeedRow (Playoff Picture "Seeds" view) ---

test('buildSeedRow: null/undefined/empty has no data and 8 all-blank cells', () => {
  for (const empty of [null, undefined, {}]) {
    const row = buildSeedRow(empty);
    assert.equal(row.hasData, false);
    assert.equal(row.cells.length, 8);
    // join to a string, not deepEqual: the vm loader puts cells in a separate
    // realm, so a cross-realm array fails deepStrictEqual's prototype check.
    assert.equal(row.cells.map(c => c.seed).join(','), '1,2,3,4,5,6,7,8');
    assert.ok(row.cells.every(c => c.display === '' && c.prob === 0 && !c.isModal));
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
  assert.equal(row.cells[0].isModal, true);
  assert.equal(row.cells.filter(c => c.isModal).length, 1);
});

test('buildSeedRow: tiny-but-nonzero shows <1%, explicit zero shows blank', () => {
  const row = buildSeedRow({ '7': 0, '8': 0.003 });
  assert.equal(row.cells[7].display, '<1%');  // 0.003 rounds to 0% → "<1%"
  assert.equal(row.cells[6].display, '');     // explicit 0 → blank
  assert.equal(row.cells[7].isModal, true);   // the only nonzero seed is modal
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
