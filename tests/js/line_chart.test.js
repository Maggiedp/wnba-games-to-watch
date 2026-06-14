const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { buildLineChartSvg, isoMinusDays, computeRankDeltas, deltaHtml } =
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

test('isoMinusDays subtracts calendar days in UTC', () => {
  assert.equal(isoMinusDays('2026-06-13', 7), '2026-06-06');
  assert.equal(isoMinusDays('2026-06-03', 7), '2026-05-27');  // month boundary
});

test('computeRankDeltas: climbed / dropped / flat', () => {
  const data = { teams: {
    A: [{ date: '2026-06-01', value: 1500 }, { date: '2026-06-13', value: 1600 }],
    B: [{ date: '2026-06-01', value: 1550 }, { date: '2026-06-13', value: 1500 }],
    C: [{ date: '2026-06-01', value: 1520 }, { date: '2026-06-13', value: 1520 }],
  } };
  // week-ago ranks: B(1550)=1, C(1520)=2, A(1500)=3
  // current ranks:  A(1600)=1, C(1520)=2, B(1500)=3
  const d = computeRankDeltas(data);
  assert.equal(d.A, 2);   // 3 -> 1, climbed 2
  assert.equal(d.B, -2);  // 1 -> 3, dropped 2
  assert.equal(d.C, 0);   // unchanged
});

test('computeRankDeltas: team with no week-ago point is null', () => {
  const data = { teams: {
    A: [{ date: '2026-06-01', value: 1500 }, { date: '2026-06-13', value: 1600 }],
    B: [{ date: '2026-06-01', value: 1550 }, { date: '2026-06-13', value: 1500 }],
    D: [{ date: '2026-06-10', value: 1490 }],  // started after the cutoff
  } };
  const d = computeRankDeltas(data);
  assert.equal(d.D, null);
});

test('computeRankDeltas: exact value ties are flat (0)', () => {
  const data = { teams: {
    A: [{ date: '2026-06-01', value: 1500 }, { date: '2026-06-13', value: 1500 }],
    B: [{ date: '2026-06-01', value: 1500 }, { date: '2026-06-13', value: 1500 }],
  } };
  const d = computeRankDeltas(data);
  assert.equal(d.A, 0);
  assert.equal(d.B, 0);
});

test('computeRankDeltas: all points within the window are null', () => {
  const data = { teams: {
    A: [{ date: '2026-06-10', value: 1500 }, { date: '2026-06-13', value: 1600 }],
    B: [{ date: '2026-06-11', value: 1550 }, { date: '2026-06-13', value: 1500 }],
  } };
  const d = computeRankDeltas(data);
  assert.equal(d.A, null);
  assert.equal(d.B, null);
});
