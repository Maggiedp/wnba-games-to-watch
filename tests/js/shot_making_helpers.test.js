const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { fmtSigned, dietBar, xppsMarker, vsLeagueBridge, bridgeGapText, bridgeDescriptor } = loadHelpers('shot_making_helpers.js');

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

const ANCHORS = { avg_xpps: 1.027, avg_pps: 1.037 };
const ROW = { expected_pps: 0.932, actual_pps: 1.144 };

test('bridgeGapText signs to 3 decimals with a unicode minus', () => {
  assert.strictEqual(bridgeGapText(0.107), '+0.107');
  assert.strictEqual(bridgeGapText(-0.095), '−0.095');
  assert.strictEqual(bridgeGapText(0), '+0.000');
});

test('bridgeDescriptor gates on the 0.03 near-zero band', () => {
  assert.strictEqual(bridgeDescriptor('diet', 0.05), 'takes easier shots than average');
  assert.strictEqual(bridgeDescriptor('diet', -0.05), 'takes harder shots than average');
  assert.strictEqual(bridgeDescriptor('diet', 0.02), 'takes about average-difficulty shots');
  assert.strictEqual(bridgeDescriptor('making', 0.05), 'converts above expectation');
  assert.strictEqual(bridgeDescriptor('making', -0.05), 'converts below expectation');
  assert.strictEqual(bridgeDescriptor('making', -0.02), 'converts about as expected');
});

test('vsLeagueBridge chains the diet segment into the making segment', () => {
  const html = vsLeagueBridge(ROW, ANCHORS, 0.285, null);
  // selection -0.095 -> ends at 50 - (0.095/0.285)*50 = 33.33%
  assert.match(html, /left:33\.33%/);
  // making runs from 33.33% to the total position 50 + (0.107/0.285)*50 = 68.77%
  assert.match(html, /width:35\.44%/);
  assert.match(html, /−0\.095/);
  assert.match(html, /\+0\.107/);
});

test('vsLeagueBridge draws the diet segment hollow and never signs its color', () => {
  const html = vsLeagueBridge(ROW, ANCHORS, 0.285, null);
  assert.match(html, /class="bridge-seg is-diet"/);
  assert.doesNotMatch(html, /is-diet is-(positive|negative)/);
});

test('vsLeagueBridge colors making and total by sign', () => {
  const good = vsLeagueBridge(ROW, ANCHORS, 0.285, null);
  assert.match(good, /bridge-seg is-making is-positive/);
  const bad = vsLeagueBridge({ expected_pps: 1.111, actual_pps: 0.762 }, ANCHORS, 0.285, null);
  assert.match(bad, /bridge-seg is-making is-negative/);
  assert.match(bad, /bridge-seg is-total is-negative/);
});

test('vsLeagueBridge appends the rank label only when given one', () => {
  assert.match(vsLeagueBridge(ROW, ANCHORS, 0.285, '#12 of 113 in points per shot'), /#12 of 113/);
  assert.doesNotMatch(vsLeagueBridge(ROW, ANCHORS, 0.285, null), /of 113/);
});

test('vsLeagueBridge returns empty string when it cannot be drawn', () => {
  assert.strictEqual(vsLeagueBridge(ROW, { avg_xpps: null, avg_pps: null }, 0.285, null), '');
  assert.strictEqual(vsLeagueBridge(ROW, ANCHORS, 0, null), '');
  assert.strictEqual(vsLeagueBridge(ROW, ANCHORS, null, null), '');
});
