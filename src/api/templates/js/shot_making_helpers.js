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
