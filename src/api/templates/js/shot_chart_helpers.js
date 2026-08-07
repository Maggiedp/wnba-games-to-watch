// Pure helpers for the /shot-making per-player shot chart. Shipped
// byte-identical into the page (no module syntax); node-tested via node:vm.
// Coordinate frame: ESPN offense-normalized half-court, x in [0,50] (hoop at
// x=25), y = feet from THE BASKET (not the baseline — verified against ESPN's
// own play-text distances: hypot(x-25, y) matches them to 0.32 ft median).
// SVG viewBox is 500 wide with the hoop near the bottom (see SHOT_CHART_TOP).

// The drawn hoop's cy. The three-point path below is centered on it, so the
// dot transform must anchor here too or threes land inside the arc.
const SHOT_HOOP_Y = 272.5;
// Top of the viewBox. Negative so the court coords below stay as drawn while
// the chart gains headroom: ~2% of league shots are beyond 26 ft, and anchoring
// dots to the hoop moved them 4.75 ft up. This keeps 32 ft of range visible.
const SHOT_CHART_TOP = -50;

function shotChartX(cx) { return cx * 10; }
function shotChartY(cy) { return SHOT_HOOP_Y - cy * 10; }

function shotChartCourt() {
  return '<rect x="0" y="' + SHOT_CHART_TOP + '" width="500" height="' + (320 - SHOT_CHART_TOP)
    + '" fill="var(--surface,#fbfaf8)"/>'
    + '<g fill="none" stroke="var(--line,#c9c3ba)" stroke-width="2">'
    + '<line x1="0" y1="320" x2="500" y2="320"/>'
    + '<line x1="0" y1="0" x2="0" y2="320"/><line x1="500" y1="0" x2="500" y2="320"/>'
    + '<rect x="170" y="170" width="160" height="150"/>'
    + '<circle cx="250" cy="170" r="60"/>'
    + '<line x1="220" y1="282" x2="280" y2="282"/>'
    + '<path d="M226,272 A24,24 0 0 0 274,272"/>'
    + '<path d="M30,320 L30,246.8 A221.5,221.5 0 0 1 470,246.8 L470,320"/></g>'
    + '<circle cx="250" cy="272.5" r="7.5" fill="none" stroke="var(--navy,#1c2b3a)" stroke-width="2.5"/>';
}

// Diverging fill: added>=0 -> orange, added<0 -> cool grey; opacity scales with
// magnitude (clamped). Returns an SVG attribute fragment.
function shotDotFill(added) {
  const t = Math.max(-1, Math.min(1, Number(added) / 2.2));
  if (added >= 0) return 'fill="#e8641e" fill-opacity="' + (0.25 + 0.6 * t).toFixed(2) + '"';
  return 'fill="#5b6472" fill-opacity="' + (0.18 + 0.5 * Math.abs(t)).toFixed(2) + '"';
}

function buildShotChartSvg(shots) {
  let s = '<svg viewBox="0 ' + SHOT_CHART_TOP + ' 500 ' + (320 - SHOT_CHART_TOP)
    + '" class="shot-chart-svg" role="img" aria-label="Shot chart">';
  s += shotChartCourt();
  for (const sh of (shots || [])) {
    const px = shotChartX(sh.x);
    // Clamp a deep heave to the top edge rather than plotting it off-chart.
    const py = Math.max(SHOT_CHART_TOP + 6, shotChartY(sh.y));
    const dist = Math.round(Math.hypot(sh.x - 25, sh.y));
    const kind = sh.pv === 3 ? 'three' : (dist <= 4 ? 'rim' : 'jumper');
    const sign = sh.added >= 0 ? '+' : '−';
    const label = dist + ' ft ' + kind + ' · ' + (sh.made ? 'made' : 'missed')
      + ' · ' + sign + Math.abs(sh.added).toFixed(1) + ' pts';
    s += '<circle cx="' + px + '" cy="' + py + '" r="5" ' + shotDotFill(sh.added)
      + '><title>' + label + '</title></circle>';
  }
  s += '</svg>';
  return s;
}
