// Pure helpers for the /style fingerprint gallery. Shipped byte-identical into
// the page (no module syntax); node-tested via node:vm (tests/js/helpers.js
// exposes top-level function declarations as globals).

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Draw a 6-axis radar. `axes` is 6 objects with a `norm` in [0,100], in the
// fixed AXES order (clockwise from top). Returns an <svg> string: a grid
// hexagon, a midline hexagon, and the team's data polygon.
function buildRadarSvg(axes, opts) {
  opts = opts || {};
  const size = opts.size || 150;
  const cx = size / 2;
  const cy = size / 2;
  const R = size / 2 - 14; // padding for label breathing room
  const n = 6;
  const pt = (i, r) => {
    const ang = ((-90 + 60 * i) * Math.PI) / 180;
    return [cx + r * Math.cos(ang), cy + r * Math.sin(ang)];
  };
  const poly = (r) => {
    const pts = [];
    for (let i = 0; i < n; i++) pts.push(pt(i, r));
    return pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  };
  const dataPts = [];
  for (let i = 0; i < n; i++) {
    const norm = Math.max(0, Math.min(100, (axes[i] && axes[i].norm) || 0));
    dataPts.push(pt(i, (R * norm) / 100));
  }
  const dataStr = dataPts
    .map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`)
    .join(' ');
  return (
    `<svg class="radar" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-hidden="true">` +
    `<polygon class="radar-grid" points="${poly(R)}"></polygon>` +
    `<polygon class="radar-grid radar-mid" points="${poly(R / 2)}"></polygon>` +
    `<polygon class="radar-shape" points="${dataStr}"></polygon>` +
    `</svg>`
  );
}
