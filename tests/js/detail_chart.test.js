const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

// Load shared first (escapeHtml/isLiveStatus), then the chart helpers — same
// order/scope as the browser concatenation on the detail page.
const { buildWpSvg, headerHtml } = loadHelpers('shared.js', 'detail_chart.js');

function play(home_pct, period, clock) {
  return { home_pct, period, clock };
}

// --- buildWpSvg ---
test('buildWpSvg returns empty string for fewer than 2 plays', () => {
  assert.equal(buildWpSvg([], 'LV', 'PHX'), '');
  assert.equal(buildWpSvg([play(0.5, 1)], 'LV', 'PHX'), '');
});

test('buildWpSvg renders an svg with escaped axis labels', () => {
  const svg = buildWpSvg([play(0.4, 1), play(0.6, 1)], 'LV', 'PHX');
  assert.match(svg, /^<svg class="wp-chart-svg"/);
  assert.ok(svg.includes('>LV<') && svg.includes('>PHX<'));
});

test('buildWpSvg escapes a malicious abbreviation', () => {
  const svg = buildWpSvg([play(0.4, 1), play(0.6, 1)], '<img>', 'PHX');
  assert.ok(!svg.includes('<img>'));
  assert.ok(svg.includes('&lt;img&gt;'));
});

// --- headerHtml: final game ---
test('headerHtml on a final game shows the score line, no readout', () => {
  const html = headerHtml(
    { status: 'STATUS_FINAL', home_score: 88, away_score: 80, plays: [play(0.9, 4)] },
    'LV', 'PHX');
  assert.ok(html.includes('&middot; Final'));
  assert.ok(!html.includes('wp-live-readout'));
});

// --- headerHtml: the near-miss — never synthesize a midpoint ---
test('headerHtml drops to the status line when no play has a usable home_pct', () => {
  const html = headerHtml(
    { status: 'STATUS_IN_PROGRESS', home_score: 50, away_score: 50,
      plays: [play(null, 2, '5:00'), play(NaN, 2, '4:30'), play(1.5, 2, '4:00')] },
    'LV', 'PHX');
  assert.ok(!html.includes('wp-live-readout'), 'must not render a readout');
  assert.ok(!html.includes('50%'), 'must not synthesize a midpoint');
  assert.ok(!html.includes('NaN'));
  assert.ok(html.includes('wp-live-status'));
});

test('headerHtml selects the LATEST finite home_pct in [0,1], skipping junk', () => {
  // latest usable value is 0.30 (home), even though later plays are garbage
  const html = headerHtml(
    { status: 'STATUS_IN_PROGRESS', home_score: 40, away_score: 55,
      plays: [play(0.8, 3, '2:00'), play(0.30, 4, '1:00'), play(null, 4, '0:30'), play(2.0, 4, '0:10')] },
    'LV', 'PHX');
  // home_pct 0.30 -> away (PHX) leads at 70%
  assert.ok(html.includes('wp-live-readout'));
  assert.ok(html.includes('>PHX<') && html.includes('70%'));
});

test('headerHtml rounds and shows the leading team when home leads', () => {
  const html = headerHtml(
    { status: 'STATUS_IN_PROGRESS', home_score: 60, away_score: 52,
      plays: [play(0.732, 4, '1:00')] },
    'LV', 'PHX');
  assert.ok(html.includes('>LV<') && html.includes('73%'));
  assert.ok(html.includes('win probability'));
});
