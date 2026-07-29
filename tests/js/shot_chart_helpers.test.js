const { test } = require('node:test');
const assert = require('node:assert');
const { loadHelpers } = require('./helpers');

const H = loadHelpers('shot_chart_helpers.js');

test('shotChartY maps baseline to bottom, midcourt upward', () => {
  assert.strictEqual(H.shotChartY(0), 320);
  assert.strictEqual(H.shotChartY(20), 120);
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
