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

test('vsLeagueBridge places both marks and spans the arrow between them', () => {
  const html = vsLeagueBridge(ROW, ANCHORS, 0.285, null);
  // selection -0.095 -> the expected ring sits at 50 - (0.095/0.285)*50 = 33.33%
  assert.match(html, /class="bridge-mark is-expected"[^>]*left:33\.33%/);
  // total +0.107 -> the actual dot sits at 50 + (0.107/0.285)*50 = 68.77%
  assert.match(html, /class="bridge-mark is-actual"[^>]*left:68\.77%/);
  // the arrow must run mark to mark, so its span is exactly the difference
  assert.match(html, /class="bridge-arrow is-up"[^>]*left:33\.33%;width:35\.44%/);
  // making +0.202 and total +0.107 are both stated in the footer
  assert.match(html, /Shot-making <b>\+0\.202<\/b>/);
  assert.match(html, /Vs league <b>\+0\.107<\/b>/);
});

test('vsLeagueBridge names both marks and glosses them on the mark itself', () => {
  const html = vsLeagueBridge(ROW, ANCHORS, 0.285, null);
  assert.match(html, /xPPS<strong>0\.932<\/strong>/);
  assert.match(html, /PPS<strong>1\.144<\/strong>/);
  assert.match(html, /league average,<br>from her spots/);
  assert.match(html, /what she<br>actually scored/);
  // the tick is the average WNBA SHOT — it must not also claim the phrase
  // "league average", which belongs to the ring (an average SHOOTER)
  assert.match(html, /The average WNBA shot<em>1\.037<\/em>/);
});

test('vsLeagueBridge points the arrow by the sign of making', () => {
  assert.match(vsLeagueBridge(ROW, ANCHORS, 0.285, null), /bridge-arrow is-up/);
  const bad = vsLeagueBridge({ expected_pps: 1.111, actual_pps: 0.762 }, ANCHORS, 0.285, null);
  assert.match(bad, /bridge-arrow is-down/);
  assert.doesNotMatch(bad, /bridge-arrow is-up/);
});

test('vsLeagueBridge points each label away from the arrow so the two never collide', () => {
  // Both marks interior (33% / 69%), so the away-from-arrow rule governs and
  // the edge override stays out of it: making positive -> ring's label left,
  // dot's label right, i.e. each hangs on its outer side.
  const up = vsLeagueBridge(ROW, ANCHORS, 0.285, null);
  assert.match(up, /class="bridge-lab is-left"[^>]*>xPPS/);
  assert.match(up, /class="bridge-lab is-right"[^>]*>PPS/);
  // making negative with both marks still interior (70% / 35%): sides swap,
  // and each again points outward from the arrow that runs between them.
  const down = vsLeagueBridge({ expected_pps: 1.141, actual_pps: 0.9515 }, ANCHORS, 0.285, null);
  assert.match(down, /class="bridge-lab is-right"[^>]*>xPPS/);
  assert.match(down, /class="bridge-lab is-left"[^>]*>PPS/);
});

test('vsLeagueBridge omits the arrow when the two marks coincide', () => {
  // actual == expected + the league making residual -> the marks land together
  // and an arrow would be shorter than its own head. The ring nests around the
  // filled dot instead, which is the true picture of "scored what was expected".
  const html = vsLeagueBridge({ expected_pps: 1.027, actual_pps: 1.037 }, ANCHORS, 0.285, null);
  assert.doesNotMatch(html, /bridge-arrow/);
  // both marks still render, at the same position
  assert.match(html, /class="bridge-mark is-expected"[^>]*left:50\.00%/);
  assert.match(html, /class="bridge-mark is-actual"[^>]*left:50\.00%/);
});

test('vsLeagueBridge turns an edge label inward instead of hanging it off the track', () => {
  // bridge_scale is set by whichever board row is most extreme, so that row
  // genuinely lands at 100% of scale. The mark stays there; the label clamps to
  // 96% AND flips to is-left, or it would extend past the end of the track.
  const html = vsLeagueBridge({ expected_pps: 1.037, actual_pps: 1.322 }, ANCHORS, 0.285, null);
  assert.match(html, /class="bridge-mark is-actual"[^>]*left:100\.00%/);
  assert.match(html, /class="bridge-lab is-left"[^>]*left:96\.00%[^>]*>PPS/);
  // ...and the same at the low end, where the away-from-arrow rule would
  // otherwise point the label left off a mark sitting at 0%.
  const low = vsLeagueBridge({ expected_pps: 0.742, actual_pps: 1.037 }, ANCHORS, 0.285, null);
  assert.match(low, /class="bridge-mark is-expected"[^>]*left:0\.00%/);
  assert.match(low, /class="bridge-lab is-right"[^>]*left:4\.00%[^>]*>xPPS/);
});

test('vsLeagueBridge states both descriptors as one sentence', () => {
  assert.match(
    vsLeagueBridge(ROW, ANCHORS, 0.285, null),
    /She takes harder shots than average, and converts above expectation\./,
  );
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
