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

// --- Vs-league chart: one axis, two marks, one arrow.
// Same shots, two shooters: the ring is what a league-average WNBA player
// would score from her spots and shot types (xPPS), the filled dot is what
// she actually scored (PPS), and the arrow between them is her shot-making.
// Replaces the three-row waterfall this used to draw — see docs/SHIPPED.md.
// Mirrored by _vs_league_bridge_html() in src/api/routes.py for the
// server-rendered /player page; tests/test_bridge_parity.py asserts the two emit
// byte-identical HTML. Change both together.

const BRIDGE_NEAR = 0.03;

// Below this separation (in % of track width) the two marks coincide. The
// arrow between them would be shorter than its own 12px head, so it is
// omitted entirely and the ring nests around the filled dot instead — which
// is the true picture of "she scored exactly what was expected".
const BRIDGE_COINCIDENT_PCT = 1.2;

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

// A label sits outside its mark and would clip at the track edges. Both ends
// genuinely reach 0%/100% of scale in live data — bridge_scale is set by the
// largest |selection| OR |total| on the board, so whoever sets it lands on an
// edge — so clamp before positioning.
function bridgeLabelPct(x) {
  return Math.min(96, Math.max(4, x));
}

// Labels sit BELOW the axis, so they never collide with the arrow — only with
// each other. Pointing each away from the arrow is what keeps them apart. Near
// a track edge that rule would hang the label off the end, so position wins
// there and the label turns inward instead.
function bridgeLabelSide(pct, away) {
  if (pct > 80) return 'is-left';
  if (pct < 20) return 'is-right';
  return away;
}

// Direction and sign are the same fact: the arrow points right exactly when
// making is positive, so one class carries both the geometry and the colour.
function bridgeArrow(fromPct, toPct) {
  const left = Math.min(fromPct, toPct);
  const width = Math.abs(toPct - fromPct);
  const dir = toPct >= fromPct ? 'is-up' : 'is-down';
  return `<i class="bridge-arrow ${dir}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></i>`;
}

function bridgeMark(cls, pct) {
  return `<i class="bridge-mark ${cls}" style="left:${pct.toFixed(2)}%"></i>`;
}

// The gloss is what makes the abbreviation legible without a caption, so it
// lives on the mark rather than under the chart.
function bridgeLabel(pct, side, kicker, value, gloss) {
  return `<span class="bridge-lab ${side}" style="left:${bridgeLabelPct(pct).toFixed(2)}%">` +
    `${kicker}<strong>${value}</strong>` +
    `<span class="bridge-gloss">${gloss}</span></span>`;
}

function vsLeagueBridge(row, anchors, scale, rankLabel) {
  const ax = anchors && anchors.avg_xpps;
  const ap = anchors && anchors.avg_pps;
  if (typeof ax !== 'number' || typeof ap !== 'number') return '';
  if (typeof scale !== 'number' || !isFinite(scale) || scale <= 0) return '';

  const selection = row.expected_pps - ax;
  const total = row.actual_pps - ap;
  const making = total - selection;   // exact: the arrow must span mark to mark

  const mid = bridgePct(0, scale);
  const xEnd = bridgePct(selection, scale);
  const aEnd = bridgePct(total, scale);
  // Each label points away from the arrow, so the two can never collide.
  const up = aEnd >= xEnd;
  const coincident = Math.abs(aEnd - xEnd) < BRIDGE_COINCIDENT_PCT;

  return `<div class="bridge">` +
    `<h3 class="bridge-head">How she scores</h3>` +
    `<div class="bridge-axis">` +
      `<i class="bridge-base"></i>` +
      `<i class="bridge-tick" style="left:${mid.toFixed(2)}%"></i>` +
      `<span class="bridge-ticklab" style="left:${mid.toFixed(2)}%">` +
        `The average WNBA shot<em>${ap.toFixed(3)}</em></span>` +
      (coincident ? '' : bridgeArrow(xEnd, aEnd)) +
      bridgeMark('is-expected', xEnd) +
      bridgeMark('is-actual', aEnd) +
      bridgeLabel(xEnd, bridgeLabelSide(xEnd, up ? 'is-left' : 'is-right'), 'xPPS',
        row.expected_pps.toFixed(3), 'league average,<br>from her spots') +
      bridgeLabel(aEnd, bridgeLabelSide(aEnd, up ? 'is-right' : 'is-left'), 'PPS',
        row.actual_pps.toFixed(3), 'what she<br>actually scored') +
    `</div>` +
    `<p class="bridge-said">She ${bridgeDescriptor('diet', selection)}, ` +
      `and ${bridgeDescriptor('making', making)}.</p>` +
    `<p class="bridge-foot">` +
      `<span>Shot-making <b>${bridgeGapText(making)}</b></span>` +
      `<span>Vs league <b>${bridgeGapText(total)}</b></span>` +
      (rankLabel ? `<span>${rankLabel}</span>` : '') +
    `</p>` +
    `</div>`;
}
