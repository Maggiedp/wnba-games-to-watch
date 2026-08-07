const { test } = require('node:test');
const assert = require('node:assert');
const { loadHelpers } = require('./helpers');

const H = loadHelpers('shot_chart_helpers.js');

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

test('rim shots plot at the hoop, not on the baseline', () => {
  assert.ok(Math.abs(H.shotChartY(1) - 272.5) <= 10); // within a foot of the rim
});

test('dot tooltip distance matches ESPN play-text distance', () => {
  // ESPN texts this shot as a "24-foot three point jumper".
  const svg = H.buildShotChartSvg([{ x: 25, y: 24, made: true, pv: 3, added: 1 }]);
  assert.match(svg, /<title>24 ft three/);
});

test('a deep heave is clamped into the viewBox instead of plotting off the top', () => {
  const svg = H.buildShotChartSvg([{ x: 25, y: 40, made: false, pv: 3, added: -1 }]);
  const top = Number(svg.match(/viewBox="0 ([-\d.]+)/)[1]);
  const cy = Number(svg.match(/<circle cx="250" cy="([-\d.]+)" r="5"/)[1]);
  assert.ok(cy >= top, `heave plotted off-chart at cy=${cy}, viewBox top ${top}`);
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

test('buildShotChartSvg draws a court with no dots when empty', () => {
  const svg = H.buildShotChartSvg([]);
  assert.match(svg, /^<svg/);
  assert.match(svg, /<\/svg>$/);
  assert.strictEqual((svg.match(/<circle/g) || []).length >= 1, true); // hoop circle
});

test('buildShotChartSvg draws one dot per shot with a title tooltip', () => {
  const svg = H.buildShotChartSvg([
    { x: 25, y: 4, made: true, pv: 2, added: 0.5 },
    { x: 2, y: 2, made: false, pv: 3, added: -1.1 },
  ]);
  assert.strictEqual((svg.match(/<title>/g) || []).length, 2);
  assert.match(svg, /made/);
  assert.match(svg, /missed/);
});
