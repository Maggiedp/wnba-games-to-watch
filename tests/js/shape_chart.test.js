const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { buildShapeSvg } = loadHelpers('shape_chart.js');

const curve = [[0, 0.5], [1200, 0.2], [2400, 0.9]];

test('returns empty string for fewer than 2 points', () => {
  assert.equal(buildShapeSvg([[0, 0.5]], 'home', {}), '');
  assert.equal(buildShapeSvg([], 'home', {}), '');
});

test('emits fever line + fill + midline', () => {
  const svg = buildShapeSvg(curve, 'home', {});
  assert.match(svg, /class="shape-line"/);
  assert.match(svg, /class="shape-fill"/);
  assert.match(svg, /class="shape-mid"/);
});

test('tension emphasis adds the in-doubt band', () => {
  const svg = buildShapeSvg(curve, 'home', { emphasis: 'tension' });
  assert.match(svg, /class="shape-doubt"/);
});

test('comeback emphasis drops the nadir dot', () => {
  const svg = buildShapeSvg(curve, 'home', { emphasis: 'comeback' });
  assert.match(svg, /class="shape-nadir"/);
});

test('accent stroke is applied to the line', () => {
  const svg = buildShapeSvg(curve, 'home', { accent: 'var(--orange)' });
  assert.match(svg, /class="shape-line"[^>]*stroke="var\(--orange\)"/);
});

test('away winner orients the line upward at the end (q high -> small y)', () => {
  // away won: q = 1 - home_pct; final home_pct 0.1 -> q 0.9, near the top.
  const svg = buildShapeSvg([[0, 0.5], [2400, 0.1]], 'away', { height: 100 });
  const d = svg.match(/class="shape-line" d="M ([^"]+)"/)[1];
  const lastY = parseFloat(d.split(' L ').pop().split(',')[1]);
  assert.ok(lastY < 50, `expected winner to end in the top half, got y=${lastY}`);
});

test('midLabel true prints a 50% label on the midline', () => {
  const svg = buildShapeSvg(curve, 'home', { midLabel: true });
  assert.match(svg, /class="shape-mid-label"/);
  assert.match(svg, />50%</);
});

test('midLabel defaults off (no label)', () => {
  const svg = buildShapeSvg(curve, 'home', {});
  assert.doesNotMatch(svg, /shape-mid-label/);
});
