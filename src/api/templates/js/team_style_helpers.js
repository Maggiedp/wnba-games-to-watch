// Pure helpers for the /style fingerprint gallery. Shipped byte-identical into
// the page (no module syntax); node-tested via node:vm (tests/js/helpers.js
// exposes top-level function declarations as globals).

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Short spoke labels keyed by axis key (the full names live in the page's key).
const _AXIS_SHORT = {
  pace: 'Pace',
  three_pa_rate: '3PA',
  ft_rate: 'FT',
  oreb_pct: 'OREB',
  assist_rate: 'AST',
  def_pressure: 'Def',
  opp_3pa_rate: '3sAl',
};

// Draw an N-axis radar (N = axes.length). Each axis object has `norm` in
// [0,100] and a `key` (for the spoke label), in the fixed AXES order (clockwise
// from top). Returns an <svg> string: grid polygon, midline polygon, the data
// polygon, and a short label at each spoke so the shape is readable without
// cross-referencing.
function buildRadarSvg(axes, opts) {
  opts = opts || {};
  const w = opts.width || 200;
  const h = opts.height || 165;
  const cx = w / 2;
  const cy = h / 2;
  const R = Math.min(w, h) / 2 - 24; // leave a margin for spoke labels
  const n = axes.length;
  const ang = (i) => ((-90 + (360 / n) * i) * Math.PI) / 180;
  const pt = (i, r) => [cx + r * Math.cos(ang(i)), cy + r * Math.sin(ang(i))];
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
  let labels = '';
  for (let i = 0; i < n; i++) {
    const short = _AXIS_SHORT[(axes[i] && axes[i].key) || ''] || '';
    if (!short) continue;
    const c = Math.cos(ang(i));
    const anchor = c > 0.3 ? 'start' : c < -0.3 ? 'end' : 'middle';
    const lp = pt(i, R + 11);
    labels +=
      `<text class="radar-axis" x="${lp[0].toFixed(1)}" y="${lp[1].toFixed(1)}"` +
      ` text-anchor="${anchor}">${short}</text>`;
  }
  return (
    `<svg class="radar" viewBox="0 0 ${w} ${h}" role="img" aria-label="team style radar">` +
    `<polygon class="radar-grid" points="${poly(R)}"></polygon>` +
    `<polygon class="radar-grid radar-mid" points="${poly(R / 2)}"></polygon>` +
    `<polygon class="radar-shape" points="${dataStr}"></polygon>` +
    labels +
    `</svg>`
  );
}
