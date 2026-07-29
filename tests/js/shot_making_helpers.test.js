const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { fmtSigned, dietBar } = loadHelpers('shot_making_helpers.js');

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
