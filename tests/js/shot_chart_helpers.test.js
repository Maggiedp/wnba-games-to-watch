const { test } = require('node:test');
const assert = require('node:assert');
const { loadHelpers } = require('./helpers');

const H = loadHelpers('shot_chart_helpers.js');

// Only shot marks carry a <title>; this skips the court's own paths.
const marksOf = (svg) => [...svg.matchAll(/<(circle|path)\b([^>]*)><title>([^<]*)<\/title>/g)]
  .map(([, tag, attrs, title]) => ({ tag, attrs, title }));

// ESPN's coord_y is feet from the BASKET, not from the baseline: over 101
// coord-bearing shots in one game, hypot(x-25, y) matches ESPN's own play-text
// distance to 0.32 ft median, while hypot(x-25, y-4.75) is off by 2.45 ft.
test('shotChartY puts coord_y=0 at the hoop, not the baseline', () => {
  assert.strictEqual(H.shotChartY(0), 272.5); // the drawn hoop's cy
  assert.strictEqual(H.shotChartY(20), 72.5);
});

// The regression this guards: with a baseline-origin transform, 36 of Julie
// Allemand's 67 plotted threes rendered INSIDE the drawn arc.
test('three-pointers plot outside the drawn three-point line', () => {
  // ESPN rounds coords to whole feet, so a genuine three can land a hair inside
  // a 22.15 ft arc. Measured league-wide (3,096 threes): worst inset 0.22 ft
  // after the fix vs 3.90 ft before, so 0.5 ft separates rounding from a bug.
  const HOOP_X = 250, HOOP_Y = 272.5, ARC_R = 221.5 - 5, CORNER_X = 220 - 5;
  // Real 2026 coords: the closest-in threes taken league-wide (corners at
  // |x-25|=22, plus above-the-break attempts near the arc).
  const threes = [[3, 1], [47, 2], [3, 8], [8, 16], [17, 22], [25, 22], [40, 19]];
  for (const [x, y] of threes) {
    const px = H.shotChartX(x), py = H.shotChartY(y);
    const outside = Math.hypot(px - HOOP_X, py - HOOP_Y) >= ARC_R
      || Math.abs(px - HOOP_X) >= CORNER_X;
    assert.ok(outside, `three at (${x},${y}) plotted inside the arc`);
  }
});

// The dot transform and the COURT PATH encode the hoop separately, and the
// bug PR #124 fixed was exactly those two disagreeing. Read the hoop back
// out of the drawn court rather than trusting a remembered constant.
test('the dot transform agrees with the hoop the court actually draws', () => {
  const court = H.shotChartCourt();
  const hoopCy = Number(court.match(/<circle cx="250" cy="([-\d.]+)" r="7.5"/)[1]);
  assert.strictEqual(hoopCy, H.shotChartY(0), 'drawn hoop drifted from the transform');

  // The three-point arc's center, recovered from its own path: endpoints at
  // (30,endY)/(470,endY) with radius r put the center at endY + sqrt(r^2-220^2).
  const [, r, endY] = court.match(/A([\d.]+),[\d.]+ 0 0 1 470,([\d.]+)/).map(Number);
  const arcCenterY = endY + Math.sqrt(r * r - 220 * 220);
  assert.ok(Math.abs(arcCenterY - H.shotChartY(0)) < 0.1,
    `arc centered at ${arcCenterY}, transform puts the hoop at ${H.shotChartY(0)}`);
});

// ---------------------------------------------------------------------------
// Cell aggregation. A mark is one per (3 ft cell × off-scale status), NOT one
// per shot — see shot_chart_helpers.js for why those are the only two things
// that may partition a bin. Drawing a dot per shot hid 26-75% of a player's
// attempts under other dots, and the surviving colour was an order-dependent
// alpha composite of whatever painted last.
// ---------------------------------------------------------------------------

// THE regression guard for that bug: if colour still depended on paint order,
// permuting the input would change the output.
test('output is identical however the shots are ordered', () => {
  const shots = [
    { x: 25, y: 2, made: true, pv: 2, added: 1.4 },
    { x: 26, y: 1, made: false, pv: 2, added: -0.9 },
    { x: 25, y: 1, made: true, pv: 2, added: 0.3 },
    { x: 26, y: 2, made: false, pv: 2, added: -1.2 },
    { x: 24, y: 3, made: true, pv: 2, added: 0.6 },
    { x: 8, y: 20, made: false, pv: 3, added: -1.1 },
  ];
  const forward = H.buildShotChartSvg(shots);
  const reversed = H.buildShotChartSvg([...shots].reverse());
  assert.strictEqual(forward, reversed, 'paint order still changes the chart');

  const rotated = H.buildShotChartSvg([...shots.slice(3), ...shots.slice(0, 3)]);
  assert.strictEqual(forward, rotated, 'paint order still changes the chart');
});

test('shots at one coordinate collapse into a single mark', () => {
  const stacked = Array.from({ length: 12 }, () => (
    { x: 25, y: 1, made: true, pv: 2, added: 0.5 }
  ));
  assert.strictEqual(marksOf(H.buildShotChartSvg(stacked)).length, 1);
});

test('shots within a cell merge; shots further apart stay separate', () => {
  // (25,1) and (26,2) share the 3 ft cell floor(x/3),floor(y/3) = (8,0).
  const together = [
    { x: 25, y: 1, made: true, pv: 2, added: 0.5 },
    { x: 26, y: 2, made: false, pv: 2, added: -0.5 },
  ];
  assert.strictEqual(marksOf(H.buildShotChartSvg(together)).length, 1);

  // (25,1) -> cell (8,0) and (25,4) -> cell (8,1): different rows.
  const apart = [
    { x: 25, y: 1, made: true, pv: 2, added: 0.5 },
    { x: 25, y: 4, made: false, pv: 2, added: -0.5 },
  ];
  assert.strictEqual(marksOf(H.buildShotChartSvg(apart)).length, 2);
});

// A long two and a corner three can share a quantized coordinate, and the
// chart has no visual channel that tells them apart -- both are a circle
// coloured by `added`. Splitting them therefore could not communicate
// anything; it only stacked two marks on one spot, where they alpha-blended
// into a colour meaning neither and the lower tooltip became unreachable
// (measured: 19 exact-geometry collisions across 30 of 121 players).
// Merging is sound because `added` is already baseline-relative -- actual
// minus expected, point value included -- so the mean is well defined.
test('a two and a three from the same spot merge into one mark', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 22, made: true, pv: 3, added: 1.0 },
    { x: 25, y: 22, made: false, pv: 2, added: -1.0 },
  ]);
  const marks = marksOf(svg);
  assert.strictEqual(marks.length, 1);
  assert.strictEqual(marks[0].title, '22 ft · 2 attempts · 1 made · +0.0 pts');
});

// The general invariant behind the merge above: two marks must never sit at
// the same place, because stacked marks are unreadable and only the top one
// is hoverable. Position and off-scale status are the whole key, so this
// holds by construction -- the test pins it against a future partition being
// added for a property the mark cannot show.
test('no two marks share the same rendered geometry', () => {
  const shots = [];
  for (const [x, y] of [[25, 22], [1, 2], [47, 1], [25, 4], [26, 2], [30, 20]]) {
    shots.push({ x: x, y: y, made: true, pv: 3, added: 1.0 });
    shots.push({ x: x, y: y, made: false, pv: 2, added: -1.0 });
  }
  const svg = H.buildShotChartSvg(shots);
  const geoms = marksOf(svg).map((m) => m.attrs.replace(/ fill[^ ]*="[^"]*"/g, '').trim());
  assert.strictEqual(new Set(geoms).size, geoms.length, 'two marks render on top of each other');
});

test('cell colour is the mean of its shots, not any one of them', () => {
  const fillOf = (svg) => marksOf(svg)[0].attrs.match(/fill="[^"]+" fill-opacity="[^"]+"/)[0];
  // +2 and -2 in one cell must render as the cell's mean (0), which is what a
  // genuinely neutral cell renders as. Before aggregation this rendered as
  // whichever shot painted last.
  const mixed = H.buildShotChartSvg([
    { x: 25, y: 1, made: true, pv: 2, added: 2 },
    { x: 25, y: 1, made: false, pv: 2, added: -2 },
  ]);
  const neutral = H.buildShotChartSvg([
    { x: 25, y: 1, made: true, pv: 2, added: 0 },
    { x: 25, y: 1, made: false, pv: 2, added: 0 },
  ]);
  assert.strictEqual(fillOf(mixed), fillOf(neutral));
});

test('mark radius grows with attempts and is capped', () => {
  const radiusFor = (n) => {
    const svg = H.buildShotChartSvg(Array.from({ length: n }, () => (
      { x: 25, y: 1, made: true, pv: 2, added: 0.5 }
    )));
    return Number(marksOf(svg)[0].attrs.match(/ r="([\d.]+)"/)[1]);
  };
  const r1 = radiusFor(1), r9 = radiusFor(9), r49 = radiusFor(49);
  assert.ok(r1 < r9 && r9 < r49, `radius did not grow: ${r1}, ${r9}, ${r49}`);
  // The cap keeps a busy cell from swallowing its neighbours. It cannot
  // prevent overlap outright -- marks sit at centroids, and 75% of measured
  // neighbouring pairs are closer than two capped radii -- so this pins the
  // ceiling, and drawing busiest-first is what keeps small marks visible.
  assert.ok(radiusFor(400) <= 12, 'radius exceeded the cap');
});

test('a mark sits at the centroid of its shots, not the cell corner', () => {
  const svg = H.buildShotChartSvg([
    { x: 24, y: 0, made: true, pv: 2, added: 0.5 },
    { x: 26, y: 2, made: true, pv: 2, added: 0.5 },
  ]);
  const { attrs } = marksOf(svg)[0];
  assert.strictEqual(Number(attrs.match(/cx="([-\d.]+)"/)[1]), H.shotChartX(25));
  assert.strictEqual(Number(attrs.match(/cy="([-\d.]+)"/)[1]), H.shotChartY(1));
});

test('the tooltip reports attempts, makes and total points for the cell', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 1, made: true, pv: 2, added: 0.5 },
    { x: 25, y: 1, made: false, pv: 2, added: -0.9 },
    { x: 26, y: 2, made: true, pv: 2, added: 0.2 },
  ]);
  assert.strictEqual(marksOf(svg)[0].title, '1 ft · 3 attempts · 2 made · −0.2 pts');
});

test('a single-shot cell reads in the singular', () => {
  const svg = H.buildShotChartSvg([{ x: 25, y: 10, made: true, pv: 2, added: 0.5 }]);
  assert.strictEqual(marksOf(svg)[0].title, '10 ft · 1 attempt · 1 made · +0.5 pts');
});

// Guards the coordinate frame against ESPN's own play text: it describes this
// shot as a "24-foot three point jumper".
test('tooltip distance matches ESPN play-text distance', () => {
  const svg = H.buildShotChartSvg([{ x: 25, y: 24, made: true, pv: 3, added: 1 }]);
  assert.match(svg, /<title>24 ft · /);
});

// ---------------------------------------------------------------------------
// Off-scale marks. Rationale for the chevron lives in the helper.
// ---------------------------------------------------------------------------

// A 3 ft cell spans the chart's range cutoff (y > 31.65 ft), so binning on
// position alone merged a heave into an in-range circle and averaged the
// distance -- reproducing exactly the lie the chevron exists to prevent, that
// an off-scale shot "would read as a real 31-footer". Live on 2 of 121 players
// when this was caught (Caitlin Clark: one cell holding y=31 and y=32).
test('a cell straddling the range cutoff does not merge off-scale with in-range', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 30, made: false, pv: 3, added: -1 },
    { x: 25, y: 32, made: false, pv: 3, added: -1 },
  ]);
  const marks = marksOf(svg);
  assert.strictEqual(marks.length, 2, 'the heave was absorbed into the in-range mark');
  assert.deepStrictEqual(marks.map((m) => m.tag).sort(), ['circle', 'path']);
  assert.ok(marks.some((m) => m.title === '30 ft · 1 attempt · 0 made · −1.0 pts'));
  assert.ok(marks.some((m) => m.title.includes('32 ft') && m.title.includes('beyond the chart')));
});

// The mirror of the above: an in-range shot must not be dragged off-scale by
// heaves it shares a cell with.
test('heaves do not drag an in-range shot into the off-scale marker', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 31, made: false, pv: 3, added: -1 },
    { x: 25, y: 32, made: false, pv: 3, added: -1 },
    { x: 25, y: 32, made: false, pv: 3, added: -1 },
    { x: 25, y: 32, made: false, pv: 3, added: -1 },
  ]);
  const marks = marksOf(svg);
  assert.strictEqual(marks.length, 2);
  assert.ok(marks.some((m) => m.tag === 'circle' && m.title.startsWith('31 ft · 1 attempt')));
  assert.ok(marks.some((m) => m.tag === 'path' && m.title.startsWith('32 ft · 3 attempts')));
});

test('a shot beyond the chart range is marked off-scale, not clamped to a dot', () => {
  const svg = H.buildShotChartSvg([{ x: 25, y: 40, made: false, pv: 3, added: -1 }]);
  const marks = marksOf(svg);
  assert.strictEqual(marks.length, 1);
  assert.strictEqual(marks[0].tag, 'path', 'drew a lying dot');
  assert.match(svg, /<path d="M245,[-\d.]+ L250,/, 'no off-scale marker');
  assert.strictEqual(marks[0].title,
    '40 ft · 1 attempt · 0 made · −1.0 pts · beyond the chart',
    'a lone heave must read as one distance, not a degenerate range');
});

// Off-scale marks are pinned to the top edge, so two heaves at the same x drew
// byte-identical paths and the second tooltip was unreachable (pre-existing:
// before aggregation every heave drew its own pinned chevron). They merge, and
// report a range rather than a centroid because the cell is unbounded in y.
test('heaves at the same x merge into one chevron reporting a distance range', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 33, made: true, pv: 3, added: 1.9 },
    { x: 25, y: 49, made: false, pv: 3, added: -1.1 },
  ]);
  const marks = marksOf(svg);
  assert.strictEqual(marks.length, 1);
  assert.strictEqual(marks[0].tag, 'path');
  assert.strictEqual(marks[0].title,
    '33–49 ft · 2 attempts · 1 made · +0.8 pts · beyond the chart');
});

test('an in-range shot stays a plain dot and is never marked off-scale', () => {
  const svg = H.buildShotChartSvg([{ x: 25, y: 24, made: true, pv: 3, added: 1 }]);
  assert.strictEqual(marksOf(svg)[0].tag, 'circle');
  assert.doesNotMatch(svg, /beyond the chart/);
});

// Sideline marks are centered on the true x and allowed to clip at the edge,
// for both mark types. Shifting them inward would move the shot — the same
// class of lie as clamping a heave onto the top edge as a dot.
test('a sideline shot is centered on its true x, not nudged inside the edge', () => {
  const dot = H.buildShotChartSvg([{ x: 0, y: 10, made: true, pv: 2, added: 1 }]);
  assert.match(dot, /<circle cx="0" /, 'in-range sideline dot was shifted');
  const left = H.buildShotChartSvg([{ x: 0, y: 40, made: false, pv: 3, added: -1 }]);
  assert.match(left, /<path d="M-5,[-\d.]+ L0,/, 'off-scale sideline mark was shifted');
  const right = H.buildShotChartSvg([{ x: 50, y: 40, made: false, pv: 3, added: -1 }]);
  assert.match(right, /<path d="M495,[-\d.]+ L500,/, 'off-scale sideline mark was shifted');
});

test('every mark stays inside the viewBox', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 40, made: false, pv: 3, added: -1 },
    { x: 2, y: 66, made: false, pv: 3, added: -1 },
    { x: 25, y: 0, made: true, pv: 2, added: 1 },
  ]);
  const top = Number(svg.match(/viewBox="0 ([-\d.]+)/)[1]);
  const marks = marksOf(svg);
  assert.strictEqual(marks.length, 3, 'expected one mark per occupied cell');
  const ys = [];
  for (const { tag, attrs } of marks) {
    if (tag === 'circle') {
      const cy = Number(attrs.match(/cy="([-\d.]+)"/)[1]);
      ys.push(cy - Number(attrs.match(/ r="([\d.]+)"/)[1]));
    } else {
      for (const m of attrs.matchAll(/[ML][-\d.]+,([-\d.]+)/g)) ys.push(Number(m[1]));
    }
  }
  for (const y of ys) assert.ok(y >= top, `geometry at y=${y} escapes viewBox top ${top}`);
});

// Anchoring dots to the hoop moved them 4.75 ft up, so the chart needs range
// past the arc: ~1.9% of league shots are beyond 26 ft from the basket.
test('the viewBox reaches past 30 ft so deep threes are not jammed on the edge', () => {
  const svg = H.buildShotChartSvg([]);
  const top = Number(svg.match(/viewBox="0 ([-\d.]+)/)[1]);
  assert.ok(H.shotChartY(30) > top + 6, 'a 30-footer should plot inside the viewBox');
});

test('shotDotFill: positive orange, negative grey, opacity by magnitude', () => {
  assert.match(H.shotDotFill(2), /e8641e/);
  assert.match(H.shotDotFill(-2), /5b6472/);
  const big = H.shotDotFill(3), small = H.shotDotFill(0.1);
  const op = (s) => Number(s.match(/fill-opacity="([\d.]+)"/)[1]);
  assert.ok(op(big) > op(small));
});

test('buildShotChartSvg draws a court with no marks when empty', () => {
  const svg = H.buildShotChartSvg([]);
  assert.match(svg, /^<svg/);
  assert.match(svg, /<\/svg>$/);
  assert.strictEqual(marksOf(svg).length, 0);
  assert.ok((svg.match(/<circle/g) || []).length >= 1); // the court's own circles
});
