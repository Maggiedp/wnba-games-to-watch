const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { buildLineChartSvg } =
  loadHelpers('shared.js', 'line_chart.js');

const series = [{ label: 'Aces', abbr: 'LV', last: 0.9,
  points: [{ x: 0, y: 0.4 }, { x: 1, y: 0.9 }] }];

test('yFormat formats axis labels (percent)', () => {
  const svg = buildLineChartSvg(series, {
    width: 200, height: 120, xTicks: [],
    yMin: 0, yMax: 1, yFormat: v => Math.round(v * 100) + '%' });
  assert.match(svg, /100%/);
  assert.match(svg, /0%/);
});

test('default yFormat rounds (Elo unchanged)', () => {
  const svg = buildLineChartSvg([{ label: 'A', abbr: 'A', last: 1600,
    points: [{ x: 0, y: 1500 }, { x: 1, y: 1600 }] }],
    { width: 200, height: 120, xTicks: [] });
  assert.match(svg, /1500/);
  assert.doesNotMatch(svg, /%/);
});

test('fixed yMin/yMax domain is honored over data range', () => {
  // With yMax=1 but data max 0.9, the top gridline label is 100%, not 90%.
  const svg = buildLineChartSvg(series, {
    width: 200, height: 120, xTicks: [],
    yMin: 0, yMax: 1, yFormat: v => Math.round(v * 100) + '%' });
  assert.match(svg, /100%/);
});

test('emits trend-line / trend-hit / trend-label classes', () => {
  const svg = buildLineChartSvg(series, { width: 200, height: 120, xTicks: [] });
  assert.match(svg, /class="trend-line"/);
  assert.match(svg, /class="trend-hit"/);
  assert.match(svg, /class="trend-label"/);
});
