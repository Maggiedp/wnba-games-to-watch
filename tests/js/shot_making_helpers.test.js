const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { fmtSigned, dietBar, xppsMarker } = loadHelpers('shot_making_helpers.js');

test('fmtSigned adds sign and unicode minus', () => {
  assert.strictEqual(fmtSigned(12.4), '+12.4');
  assert.strictEqual(fmtSigned(-3.1), '−3.1');
  assert.strictEqual(fmtSigned(0), '+0.0');
});

test('dietBar renders a segment per family with a share', () => {
  const html = dietBar({ rim: 0.5, three: 0.5 });
  assert.match(html, /diet-rim/);
  assert.match(html, /diet-three/);
});

test('dietBar omits zero/absent families and follows rim/floater/mid/three/other order', () => {
  const html = dietBar({ rim: 0.2, floater: 0, mid: 0.3, three: 0.5, other: 0 });
  assert.doesNotMatch(html, /diet-floater/);
  assert.doesNotMatch(html, /diet-other/);
  const rimIdx = html.indexOf('diet-rim');
  const midIdx = html.indexOf('diet-mid');
  const threeIdx = html.indexOf('diet-three');
  assert.ok(rimIdx < midIdx && midIdx < threeIdx, 'segments appear in rim/mid/three order');
});

test('xppsMarker matches the displayed 3-decimal comparison', () => {
  assert.strictEqual(xppsMarker(1.10, 1.05), '▲');
  assert.strictEqual(xppsMarker(1.00, 1.05), '▽');
  assert.strictEqual(xppsMarker(1.055, 1.05), '▲');   // 0.005 above reads above, not neutral
  assert.strictEqual(xppsMarker(1.045, 1.05), '▽');   // 0.005 below reads below, not neutral
  assert.strictEqual(xppsMarker(1.051, 1.05), '▲');   // any 3-dp difference shows a direction
  assert.strictEqual(xppsMarker(1.05, 1.05), '–');    // identical as displayed → neutral
});

test('xppsMarker uses displayed precision, not a rounded raw subtraction', () => {
  // 1.0045 displays as "1.004" and 1.005 as "1.005" — different, so above.
  // Math.round((1.005 - 1.0045) * 1000) neutralizes this half-thousandth gap.
  assert.strictEqual(xppsMarker(1.005, 1.0045), '▲');
});

test('xppsMarker returns empty string when leagueAvg is missing', () => {
  assert.strictEqual(xppsMarker(1.10, null), '');
  assert.strictEqual(xppsMarker(1.10, undefined), '');
});
