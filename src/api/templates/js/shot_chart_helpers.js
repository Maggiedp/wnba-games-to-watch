// Pure helpers for the /shot-making per-player shot chart. Shipped
// byte-identical into the page (no module syntax); node-tested via node:vm.
// Coordinate frame: ESPN offense-normalized half-court, x in [0,50] (hoop at
// x=25), y = feet from THE BASKET (not the baseline — verified against ESPN's
// own play-text distances: hypot(x-25, y) matches them to 0.32 ft median).
// SVG viewBox is 500 wide with the hoop near the bottom (see SHOT_CHART_TOP).

// Scale is 10px per foot throughout, including the court path below.
// The drawn hoop's cy. The three-point path below is centered on it, so the
// dot transform must anchor here too or threes land inside the arc.
const SHOT_HOOP_Y = 272.5;
const SHOT_BASELINE_Y = 320;
// Top of the viewBox = 32.25 ft of range above the hoop. Negative so the court
// coords below stay as drawn while the chart gains headroom: ~2% of league
// shots are beyond 26 ft, and anchoring dots to the hoop moved them 4.75 ft up.
const SHOT_CHART_TOP = SHOT_HOOP_Y - 322.5;
const SHOT_CHART_H = SHOT_BASELINE_Y - SHOT_CHART_TOP;
// Off-scale threshold, and the chevron's anchor: its half-height (4) plus 2 of
// margin, so the marker sits fully inside the frame.
const SHOT_CHART_EDGE = SHOT_CHART_TOP + 6;

function shotChartX(cx) { return cx * 10; }
function shotChartY(cy) { return SHOT_HOOP_Y - cy * 10; }

function shotChartCourt() {
  return '<rect x="0" y="' + SHOT_CHART_TOP + '" width="500" height="' + SHOT_CHART_H
    + '" fill="var(--surface,#fbfaf8)"/>'
    + '<g fill="none" stroke="var(--line,#c9c3ba)" stroke-width="2">'
    + '<line x1="0" y1="320" x2="500" y2="320"/>'
    + '<line x1="0" y1="0" x2="0" y2="320"/><line x1="500" y1="0" x2="500" y2="320"/>'
    + '<rect x="170" y="170" width="160" height="150"/>'
    + '<circle cx="250" cy="170" r="60"/>'
    + '<line x1="220" y1="282" x2="280" y2="282"/>'
    + '<path d="M226,272 A24,24 0 0 0 274,272"/>'
    + '<path d="M30,320 L30,246.8 A221.5,221.5 0 0 1 470,246.8 L470,320"/></g>'
    + '<circle cx="250" cy="' + SHOT_HOOP_Y
    + '" r="7.5" fill="none" stroke="var(--navy,#1c2b3a)" stroke-width="2.5"/>';
}

// Diverging fill: added>=0 -> orange, added<0 -> cool grey; opacity scales with
// magnitude (clamped). Returns an SVG attribute fragment.
function shotDotFill(added) {
  const t = Math.max(-1, Math.min(1, Number(added) / 2.2));
  if (added >= 0) return 'fill="#e8641e" fill-opacity="' + (0.25 + 0.6 * t).toFixed(2) + '"';
  return 'fill="#5b6472" fill-opacity="' + (0.18 + 0.5 * Math.abs(t)).toFixed(2) + '"';
}

function buildShotChartSvg(shots) {
  let s = '<svg viewBox="0 ' + SHOT_CHART_TOP + ' 500 ' + SHOT_CHART_H
    + '" class="shot-chart-svg" role="img" aria-label="Shot chart">';
  s += shotChartCourt();
  for (const sh of (shots || [])) {
    const px = shotChartX(sh.x), py = shotChartY(sh.y);
    const dist = Math.round(Math.hypot(sh.x - 25, sh.y));
    const kind = sh.pv === 3 ? 'three' : (dist <= 4 ? 'rim' : 'jumper');
    const sign = sh.added >= 0 ? '+' : '−';
    const offScale = py < SHOT_CHART_EDGE;
    const label = dist + ' ft ' + kind + ' · ' + (sh.made ? 'made' : 'missed')
      + ' · ' + sign + Math.abs(sh.added).toFixed(1) + ' pts'
      + (offScale ? ' · beyond the chart' : '');
    if (offScale) {
      // A heave past the chart's range (~0.4% of shots). Drawn as a chevron
      // pinned to the top edge, NOT a clamped dot: a dot there would read as a
      // real 31-footer, and two heaves of different length would be
      // indistinguishable from each other AND from an in-range shot. The
      // chevron declares "off-scale, see the tooltip for the true distance".
      // Not sized per-player from the longest shot on purpose — the court is a
      // fixed frame so charts stay comparable across players.
      s += '<path d="M' + (px - 5) + ',' + (SHOT_CHART_EDGE + 4)
        + ' L' + px + ',' + (SHOT_CHART_EDGE - 4)
        + ' L' + (px + 5) + ',' + (SHOT_CHART_EDGE + 4) + ' Z" ' + shotDotFill(sh.added)
        + '><title>' + label + '</title></path>';
    } else {
      s += '<circle cx="' + px + '" cy="' + py + '" r="5" ' + shotDotFill(sh.added)
        + '><title>' + label + '</title></circle>';
    }
  }
  s += '</svg>';
  return s;
}
