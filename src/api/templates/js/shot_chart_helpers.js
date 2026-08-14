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

// Shots are aggregated into 3 ft cells rather than drawn one dot per shot.
// Measured live 2026-08-12: one r=5 dot on a 10px-per-foot scale covers exactly
// one square foot, and ESPN quantizes coords to whole feet, so dots stacked
// perfectly — 26–75% of a player's attempts rendered underneath another dot
// (Reese: 397 shots at 101 distinct coordinates, densest cell 32 deep). What
// survived was an alpha composite of whatever painted last, so the colour
// depended on DB row order (reversing it flipped one of her cells from grey to
// orange) and its saturation confounded volume with magnitude — a cell whose
// mean was +0.003 rendered as saturated mud. Colour now means one defined
// thing, the cell's mean points added; volume is carried by radius.
const SHOT_CELL_FT = 3;
// Marks sit at centroids, not cell centres, so the cell pitch does NOT bound
// how close two marks get: measured over 21 players' 1,528 cells, the median
// nearest same-pv centroid gap is 23.5px and 75% of pairs are under 30px. Some
// overlap is therefore inherent (as in any volume-scaled scatter) and is
// handled by drawing busiest-first so small marks stay on top. The cap is set
// at 12 from that median — the point where a typical neighbouring pair stays
// countable in the paint without flattening the volume signal. Absolute, NOT
// normalized per player: scaling to a player's own busiest cell would break
// cross-player comparability, the same objection that ruled out a per-player
// viewBox in PR #124.
const SHOT_R_MIN = 3, SHOT_R_K = 1.6, SHOT_R_MAX = 12;

function shotRound(v) { return Math.round(v * 10) / 10; }

function shotCells(shots) {
  const cells = new Map();
  for (const sh of (shots || [])) {
    // Off-scale status is decided PER SHOT and partitions the cell, because a
    // 3 ft cell spans the range cutoff (y > 31.65 ft). Deriving it from the
    // merged centroid instead let a heave average into an in-range circle —
    // reproducing the exact lie the chevron exists to prevent, that an
    // off-scale shot reads as a real 31-footer — and let heaves drag an
    // in-range shot out of the court. Partitioning also makes the centroid
    // agree with the flag for free: a mean of values all past the cutoff is
    // itself past it.
    const offScale = shotRound(shotChartY(sh.y)) < SHOT_CHART_EDGE;
    // Split by point value as well as position: a cell straddling the arc
    // holds shots graded against two different baselines, and averaging them
    // would paint one blended colour over both.
    const key = sh.pv + ':' + Math.floor(sh.x / SHOT_CELL_FT)
      + ':' + Math.floor(sh.y / SHOT_CELL_FT) + ':' + (offScale ? 'o' : 'i');
    let c = cells.get(key);
    if (!c) {
      c = { key: key, offScale: offScale, n: 0, made: 0, sx: 0, sy: 0, added: 0 };
      cells.set(key, c);
    }
    c.n += 1;
    if (sh.made) c.made += 1;
    c.sx += sh.x; c.sy += sh.y;
    c.added += Number(sh.added) || 0;
  }
  // Deterministic emit order so the chart cannot depend on row order: busiest
  // first, so a small mark paints on top and stays visible; key breaks ties.
  return [...cells.values()].sort(function (a, b) {
    return b.n - a.n || (a.key < b.key ? -1 : 1);
  });
}

function buildShotChartSvg(shots) {
  let s = '<svg viewBox="0 ' + SHOT_CHART_TOP + ' 500 ' + SHOT_CHART_H
    + '" class="shot-chart-svg" role="img" aria-label="Shot chart">';
  s += shotChartCourt();
  for (const c of shotCells(shots)) {
    // The centroid of the cell's shots, not the cell's corner: positions stay
    // real, and the marks don't snap onto a visible lattice.
    const fx = c.sx / c.n, fy = c.sy / c.n;
    const px = shotRound(shotChartX(fx)), py = shotRound(shotChartY(fy));
    const dist = Math.round(Math.hypot(fx - 25, fy));
    const sign = c.added >= 0 ? '+' : '−';
    const offScale = c.offScale;
    // Total points ties to the zone table's +pts column; the colour below is
    // the per-attempt rate.
    const label = dist + ' ft · ' + c.n + (c.n === 1 ? ' attempt' : ' attempts')
      + ' · ' + c.made + ' made · ' + sign + Math.abs(c.added).toFixed(1) + ' pts'
      + (offScale ? ' · beyond the chart' : '');
    const fill = shotDotFill(c.added / c.n);
    if (offScale) {
      // A heave past the chart's range (~0.4% of shots). Drawn as a chevron
      // pinned to the top edge, NOT a clamped dot: a dot there would read as a
      // real 31-footer, and two heaves of different length would be
      // indistinguishable from each other AND from an in-range shot. The
      // chevron declares "off-scale, see the tooltip for the true distance".
      // Not sized per-player from the longest shot on purpose — the court is a
      // fixed frame so charts stay comparable across players. Fixed size (not
      // scaled by n) because these cells are ~always a single desperation
      // heave; the tooltip carries the count if they are not.
      s += '<path d="M' + (px - 5) + ',' + (SHOT_CHART_EDGE + 4)
        + ' L' + px + ',' + (SHOT_CHART_EDGE - 4)
        + ' L' + (px + 5) + ',' + (SHOT_CHART_EDGE + 4) + ' Z" ' + fill
        + '><title>' + label + '</title></path>';
    } else {
      // A mark may clip at the frame's edge rather than being nudged inward —
      // moving it would displace the shots, the same class of lie as clamping
      // a heave onto the top edge as a dot.
      const r = shotRound(Math.min(SHOT_R_MAX, SHOT_R_MIN + SHOT_R_K * Math.sqrt(c.n)));
      s += '<circle cx="' + px + '" cy="' + py + '" r="' + r + '" ' + fill
        + '><title>' + label + '</title></circle>';
    }
  }
  s += '</svg>';
  return s;
}
