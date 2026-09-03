const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

// shared.js first: winProbText depends on escapeHtml.
const {
  excitementLabelFor, winProbText, elapsedSeconds,
  excitementScore, computeExcitement, completedEntryFromLiveWp,
  curveFromPlays, sortCompleted, liveExcitementLabel,
  addDaysToISO, nextPlayableDate, nextSlateAnchor, emptyStateFor,
} = loadHelpers('shared.js', 'homepage_helpers.js');

// A Q4 dogfight (alternating ~0.05↔0.95 WP swings) that banks Close-level
// cumulative excitement; the final play sets the CURRENT win prob.
function q4Dogfight(finalHomePct) {
  return [
    { period: 4, clock: '10:00', home_pct: 0.5 },
    { period: 4, clock: '8:00', home_pct: 0.95 },
    { period: 4, clock: '6:00', home_pct: 0.05 },
    { period: 4, clock: '4:00', home_pct: 0.95 },
    { period: 4, clock: '2:00', home_pct: 0.05 },
    { period: 4, clock: '1:00', home_pct: 0.95 },
    { period: 4, clock: '0:30', home_pct: 0.05 },
    { period: 4, clock: '0:00', home_pct: finalHomePct },
  ];
}

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

// --- curveFromPlays (live "building" fever-line curve) ---

test('curveFromPlays maps plays to [elapsed, home_pct] pairs', () => {
  const plays = [
    { period: 1, clock: '10:00', home_pct: 0.5 },  // 600 - 600 = 0 elapsed
    { period: 2, clock: '0:00', home_pct: 0.7 },   // full 1200
    { period: 4, clock: '0:00', home_pct: 0.9 },   // full 2400
  ];
  // Assert field-wise (not deepEqual): the array is built in the vm realm, so
  // its Array.prototype differs from the test realm's and deepStrictEqual would
  // fail on the prototype check (same reason completedEntryFromLiveWp below is
  // asserted per-field).
  const curve = curveFromPlays(plays);
  assert.equal(curve.length, 3);
  assert.equal(curve[0][0], 0);     assert.equal(curve[0][1], 0.5);
  assert.equal(curve[1][0], 1200);  assert.equal(curve[1][1], 0.7);
  assert.equal(curve[2][0], 2400);  assert.equal(curve[2][1], 0.9);
});

test('curveFromPlays returns [] for null/empty input', () => {
  assert.equal(curveFromPlays(null).length, 0);
  assert.equal(curveFromPlays([]).length, 0);
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

// --- liveExcitementLabel (current-WP gate over the cumulative label) ---

test('liveExcitementLabel suppresses the label when the game is currently lopsided', () => {
  const blowoutNow = q4Dogfight(0.95);
  // The cumulative label WOULD show (excitement was banked earlier)...
  assert.notEqual(computeExcitement(blowoutNow), '');
  // ...but the game is decided right now, so the live label is suppressed.
  assert.equal(liveExcitementLabel(blowoutNow), '');
});

test('liveExcitementLabel keeps the label when the game is currently close', () => {
  const closeNow = q4Dogfight(0.5);
  const label = computeExcitement(closeNow);
  assert.notEqual(label, '');
  assert.equal(liveExcitementLabel(closeNow), label);
});

test('liveExcitementLabel returns empty for a below-Close game (unchanged)', () => {
  // Close now, but nothing exciting happened → no label either way.
  const dull = [
    { period: 1, clock: '10:00', home_pct: 0.5 },
    { period: 4, clock: '0:00', home_pct: 0.5 },
  ];
  assert.equal(computeExcitement(dull), '');
  assert.equal(liveExcitementLabel(dull), '');
});

test('liveExcitementLabel returns empty for too-few plays without throwing', () => {
  assert.equal(liveExcitementLabel([]), '');
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

// --- sortCompleted (completed-section sort) ---

// Mid-day-UTC timestamps: the local calendar day matches the UTC day in any
// timezone from UTC-8 to UTC+3, so localDateISO's grouping is deterministic
// on both CI (UTC) and dev machines without pinning process.env.TZ.
function completedGame(espnId, dateIso, hourUtc, excitement) {
  return {
    espn_id: espnId,
    date: dateIso,
    time_utc: `${dateIso}T${String(hourUtc).padStart(2, '0')}:00:00Z`,
    excitement_index: excitement,
  };
}

test('sortCompleted date mode: within a day, most recent tip first (not excitement)', () => {
  const early = completedGame('e1', '2026-07-10', 16, 9.9);  // most exciting, earliest tip
  const mid = completedGame('e2', '2026-07-10', 18, 5.0);
  const late = completedGame('e3', '2026-07-10', 20, 1.0);   // least exciting, latest tip
  const sorted = sortCompleted([early, late, mid], 'date');
  // Strictly reverse-chronological: excitement must not reorder a day's games.
  assert.deepEqual(sorted.map(g => g.espn_id), ['e3', 'e2', 'e1']);
});

test('sortCompleted date mode: newer days come first', () => {
  const older = completedGame('e1', '2026-07-08', 18, 9.9);
  const newer = completedGame('e2', '2026-07-10', 16, 1.0);
  const sorted = sortCompleted([older, newer], 'date');
  assert.deepEqual(sorted.map(g => g.espn_id), ['e2', 'e1']);
});

test('sortCompleted excitement mode: excitement desc, date desc tiebreak', () => {
  const thriller = completedGame('e1', '2026-07-08', 18, 9.9);
  const dudNewer = completedGame('e2', '2026-07-10', 16, 1.0);
  const dudOlder = completedGame('e3', '2026-07-09', 16, 1.0);
  const sorted = sortCompleted([dudOlder, dudNewer, thriller], 'excitement');
  assert.deepEqual(sorted.map(g => g.espn_id), ['e1', 'e2', 'e3']);
});

test('sortCompleted date mode: missing time_utc sorts last within its day', () => {
  // A real repo state, not hypothetical: ESPN withdrawing a tip time clears
  // time_utc (upsert_game's combined TBD signal), and the column is nullable.
  const early = completedGame('e1', '2026-07-10', 16, 1.0);
  const tbd = { espn_id: 'e2', date: '2026-07-10', time_utc: null, excitement_index: 9.9 };
  const late = completedGame('e3', '2026-07-10', 20, 5.0);
  const sorted = sortCompleted([tbd, early, late], 'date');
  // Known times win: desc within the day, unknown-time rows sink to its end.
  assert.deepEqual(sorted.map(g => g.espn_id), ['e3', 'e1', 'e2']);
});

// --- Break / offseason empty state -------------------------------------
// The league takes multi-week breaks mid-season (international tournaments)
// and a ~7-month offseason, during which the default "next 7 days" window is
// empty while the payload still holds a full future slate. These helpers let
// the page tell "the league isn't playing" apart from "your filter is narrow".

// A game on `date` with no time_utc -> localDateISO falls back to the ET date.
function g(date, extra) {
  return Object.assign({ date, espn_id: 'x' + date }, extra || {});
}

test('addDaysToISO adds days to an ISO date', () => {
  assert.equal(addDaysToISO('2026-09-02', 7), '2026-09-09');
  assert.equal(addDaysToISO('2026-09-17', 0), '2026-09-17');
});

test('addDaysToISO rolls over months and years', () => {
  assert.equal(addDaysToISO('2026-09-28', 7), '2026-10-05');
  assert.equal(addDaysToISO('2026-12-30', 7), '2027-01-06');
  // 2028 is a leap year: Feb 29 exists.
  assert.equal(addDaysToISO('2028-02-27', 2), '2028-02-29');
});

test('addDaysToISO keeps the calendar day across a DST boundary', () => {
  // US DST starts 2026-03-08. A naive +7*86400e3 on a local Date would land
  // on 2026-03-13T23:00 and format as the 13th.
  assert.equal(addDaysToISO('2026-03-07', 7), '2026-03-14');
  // ...and ends 2026-11-01.
  assert.equal(addDaysToISO('2026-10-28', 7), '2026-11-04');
});

test('nextPlayableDate returns the earliest date on or after the floor', () => {
  const games = [g('2026-09-24'), g('2026-09-17'), g('2026-09-18')];
  assert.equal(nextPlayableDate(games, '2026-09-02'), '2026-09-17');
});

test('nextPlayableDate ignores dates before the floor, and includes the floor itself', () => {
  const games = [g('2026-08-30'), g('2026-09-17')];
  assert.equal(nextPlayableDate(games, '2026-09-17'), '2026-09-17');
  assert.equal(nextPlayableDate(games, '2026-09-18'), null);
});

test('nextPlayableDate returns null for no games', () => {
  assert.equal(nextPlayableDate([], '2026-09-02'), null);
  assert.equal(nextPlayableDate(null, '2026-09-02'), null);
});

test('nextPlayableDate uses the local date, not the raw ET date field', () => {
  // 2026-09-18T00:30Z tips 2026-09-17 8:30pm ET. A west-coast viewer sees the
  // 17th too; the row must not be advertised as the 18th.
  const games = [{ date: '2026-09-18', time_utc: '2026-09-18T00:30:00Z' }];
  const expected = localDateISOFor(games[0]);
  assert.equal(nextPlayableDate(games, '2026-09-02'), expected);
});

// Mirrors the helper's own localDateISO so the assertion above is tz-independent.
function localDateISOFor(game) {
  const d = new Date(game.time_utc);
  return d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0');
}

test('nextSlateAnchor returns null when the default week already has games', () => {
  const games = [g('2026-09-04'), g('2026-09-17')];
  assert.equal(nextSlateAnchor(games, '2026-09-02'), null);
});

test('nextSlateAnchor treats the 7th day out as inside the window', () => {
  // Boundary: the default window is [today, today+7] inclusive, matching
  // setPreset('7'). A game exactly on the edge must not trigger an advance.
  assert.equal(nextSlateAnchor([g('2026-09-09')], '2026-09-02'), null);
  assert.equal(nextSlateAnchor([g('2026-09-10')], '2026-09-02'), '2026-09-10');
});

test('nextSlateAnchor returns the next slate date across a real break', () => {
  // The 2026 mid-September break: last game 08-30, next 09-17.
  const games = [g('2026-09-17'), g('2026-09-18'), g('2026-09-24')];
  assert.equal(nextSlateAnchor(games, '2026-09-02'), '2026-09-17');
});

test('nextSlateAnchor returns null when nothing is scheduled at all', () => {
  assert.equal(nextSlateAnchor([], '2026-09-02'), null);
});

// Field-wise, not deepEqual: the result object is built in the vm realm, so
// its prototype fails deepStrictEqual's realm check (see curveFromPlays above).
test('emptyStateFor reports the archive branch only when nothing is scheduled', () => {
  const state = emptyStateFor([], [], '2026-09-02');
  assert.equal(state.mode, 'archive');
  assert.equal(state.date, null);
});

test('emptyStateFor explains with the next date when games exist later', () => {
  const games = [g('2026-09-17')];
  const state = emptyStateFor(games, games, '2026-09-02');
  assert.equal(state.mode, 'explain');
  assert.equal(state.date, '2026-09-17');
});

test('emptyStateFor explains from the floor being shown, not from today', () => {
  // A reader who picks an October window after the season ends must not be told
  // "the next games are Sep 17" — a date BEFORE the range they asked about.
  // The floor is the caller's lowerBound (fromDate || today), so searching from
  // inside their window correctly finds nothing and falls back to the generic
  // "expand the window" message.
  const games = [g('2026-09-17'), g('2026-09-24')];
  const inRange = emptyStateFor(games, games, '2026-09-10');
  assert.equal(inRange.date, '2026-09-17', 'a floor before the slate still names it');
  const afterSeason = emptyStateFor(games, games, '2026-10-01');
  assert.equal(afterSeason.mode, 'explain');
  assert.equal(
    afterSeason.date, null,
    'a floor past every game must not point backwards out of the range',
  );
});

test('emptyStateFor does not call a filtered-out team the offseason', () => {
  // A team filter that matches no remaining games is a narrow filter, not an
  // empty league: the archive branch would tell the reader the season is over.
  const state = emptyStateFor([g('2026-09-17')], [], '2026-09-02');
  assert.equal(state.mode, 'explain');
  assert.equal(state.date, null);
});
