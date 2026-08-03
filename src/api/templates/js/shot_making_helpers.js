// Pure helpers for the /shot-making leaderboard. Shipped byte-identical into
// the page (no module syntax); node-tested via node:vm (tests/js/helpers.js
// exposes top-level function declarations as globals).

// Signed one-decimal formatter for a points-added value, using a real Unicode
// minus (not the ASCII hyphen) to match the rest of the site's number styling.
function fmtSigned(n) {
  const v = Number(n) || 0;
  const s = Math.abs(v).toFixed(1);
  return v < 0 ? '−' + s : '+' + s;
}

// Renders a shot-diet bar: one flex-weighted <span> per family with a
// positive share, in a fixed rim/floater/mid/three/other reading order (not
// sorted by size) so every player's bar reads the same left-to-right.
function dietBar(diet) {
  const order = ['rim', 'floater', 'mid', 'three', 'other'];
  return order
    .filter((k) => (diet[k] || 0) > 0)
    .map((k) => `<span class="diet-seg diet-${k}" style="flex:${diet[k]}" title="${k}"></span>`)
    .join('');
}

// Neutral above/below-league-average marker for a player's xPPS. Position-only
// (▲/▽/–) in a muted tone — deliberately NOT green/red, because xPPS is shot
// SELECTION (diet value), not making; coloring it would collide with the
// Points-added semantics. Empty string when there's no league reference yet.
// Compares each operand rounded to the 3-decimal string the UI displays (via
// toFixed), NOT a rounded raw subtraction — the latter can neutralize a
// half-thousandth gap that the two displayed values actually show as different.
// So the glyph never contradicts the numbers in the cell; '–' = identical as
// displayed.
function xppsMarker(xpps, leagueAvg) {
  if (typeof leagueAvg !== 'number' || !isFinite(leagueAvg)) return '';
  if (typeof xpps !== 'number' || !isFinite(xpps)) return '';
  const a = Number(xpps.toFixed(3));
  const b = Number(leagueAvg.toFixed(3));
  if (a > b) return '▲';
  if (a < b) return '▽';
  return '–';
}

// --- Bridge (waterfall): league average -> shot diet -> shot-making -> scoring.
// Mirrored by _vs_league_bridge_html() in src/api/routes.py for the
// server-rendered /player page; tests/test_bridge_parity.py asserts the two emit
// byte-identical HTML. Change both together.

const BRIDGE_NEAR = 0.03;

// Signed 3-decimal gap, Unicode minus to match the rest of the site's numbers.
function bridgeGapText(n) {
  const v = Number(n) || 0;
  const s = Math.abs(v).toFixed(3);
  return v < 0 ? '−' + s : '+' + s;
}

// Literal, defensible phrasing in the manner of team_style._PHRASES: describe
// what the number says, never infer intent. The diet axis in particular tracks
// role (rim rate) far more than judgment, so it is never called good or bad.
function bridgeDescriptor(axis, gap) {
  const near = Math.abs(gap) < BRIDGE_NEAR;
  if (axis === 'diet') {
    if (near) return 'takes about average-difficulty shots';
    return gap > 0 ? 'takes easier shots than average' : 'takes harder shots than average';
  }
  if (near) return 'converts about as expected';
  return gap > 0 ? 'converts above expectation' : 'converts below expectation';
}

function bridgePct(v, scale) {
  return 50 + (v / scale) * 50;
}

function bridgeSeg(fromPct, toPct, cls) {
  const left = Math.min(fromPct, toPct);
  const width = Math.abs(toPct - fromPct);
  return `<i class="bridge-seg ${cls}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></i>`;
}

function bridgeRow(label, abs, segHtml, gap, note) {
  return `<div class="bridge-row">` +
    `<span class="bridge-label">${label}</span>` +
    `<span class="bridge-abs">${abs}</span>` +
    `<span class="bridge-track">${segHtml}</span>` +
    `<span class="bridge-gap">${bridgeGapText(gap)}</span>` +
    (note ? `<span class="bridge-note">${note}</span>` : '') +
    `</div>`;
}

function vsLeagueBridge(row, anchors, scale, rankLabel) {
  const ax = anchors && anchors.avg_xpps;
  const ap = anchors && anchors.avg_pps;
  if (typeof ax !== 'number' || typeof ap !== 'number') return '';
  if (typeof scale !== 'number' || !isFinite(scale) || scale <= 0) return '';

  const selection = row.expected_pps - ax;
  const total = row.actual_pps - ap;
  const making = total - selection;   // exact: the segments must chain seamlessly

  const mid = bridgePct(0, scale);
  const selEnd = bridgePct(selection, scale);
  const totEnd = bridgePct(total, scale);
  const sign = (v) => (v >= 0 ? 'is-positive' : 'is-negative');

  return `<div class="bridge">` +
    `<h3 class="bridge-head">How she scores</h3>` +
    bridgeRow('Shot diet', row.expected_pps.toFixed(3),
      bridgeSeg(mid, selEnd, 'is-diet'), selection, bridgeDescriptor('diet', selection)) +
    bridgeRow('Shot-making', '',
      `<i class="bridge-drop" style="left:${selEnd.toFixed(2)}%"></i>` +
      bridgeSeg(selEnd, totEnd, 'is-making ' + sign(making)), making,
      bridgeDescriptor('making', making)) +
    bridgeRow('Points per shot', row.actual_pps.toFixed(3),
      bridgeSeg(mid, totEnd, 'is-total ' + sign(total)), total,
      rankLabel || '') +
    `<p class="bridge-key">Shot diet describes what she shoots. It tracks role — mostly rim rate — more than judgment, so it isn't graded.</p>` +
    `</div>`;
}
