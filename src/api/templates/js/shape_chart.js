// Winner-oriented win-probability "fever line" for the Replay archive. `curve`
// is the stored [[t_sec, home_pct], ...]; we plot the WINNER's WP
// (q = home_pct if home won else 1 - home_pct) so every shape rises toward the
// top-right and the silhouette is the story. Pure/numeric (no external strings,
// so no escaping needed); colored via an inline CSS-var stroke like
// detail_chart.js. `opts`: {width, height, accent (CSS color string),
// emphasis: 'excitement'|'tension'|'comeback'}.
function buildShapeSvg(curve, winner, opts) {
  opts = opts || {};
  if (!curve || curve.length < 2) return '';
  const W = opts.width || 320, H = opts.height || 90;
  const padL = 4, padR = 4, padT = 6, padB = 6;
  const cW = W - padL - padR;
  const cH = H - padT - padB;
  const accent = opts.accent || 'var(--navy-3)';
  const emphasis = opts.emphasis || 'excitement';
  const homeWon = winner === 'home';
  const tmax = curve[curve.length - 1][0] || 1;
  const q = curve.map(c => homeWon ? c[1] : 1 - c[1]);  // winner-oriented
  const sx = t => padL + (tmax ? t / tmax : 0) * cW;
  const sy = v => padT + (1 - v) * cH;                  // q = 1 at the top
  const pts = curve.map((c, i) => [sx(c[0]), sy(q[i])]);
  const firstX = pts[0][0].toFixed(1);
  const lastX = pts[pts.length - 1][0].toFixed(1);
  const botY = (H - padB).toFixed(1);
  const fill = [[firstX, botY],
      ...pts.map(p => [p[0].toFixed(1), p[1].toFixed(1)]),
      [lastX, botY]].map(p => `${p[0]},${p[1]}`).join(' ');
  const line = 'M ' + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L ');
  const midY = sy(0.5).toFixed(1);

  let extra = '';
  if (emphasis === 'tension') {
    // Shade the in-doubt band (q within 0.5 +/- 0.15) so the eye reads how long
    // the game spent near a coin-flip.
    const top = sy(0.65), bot = sy(0.35);
    extra = `<rect class="shape-doubt" x="${padL}" y="${top.toFixed(1)}" width="${cW}" height="${(bot - top).toFixed(1)}"/>`;
  } else if (emphasis === 'comeback') {
    // Mark the winner's nadir (min q) — the bottom of the hole they climbed out of.
    let lo = 0;
    for (let i = 1; i < q.length; i++) if (q[i] < q[lo]) lo = i;
    extra = `<circle class="shape-nadir" cx="${pts[lo][0].toFixed(1)}" cy="${pts[lo][1].toFixed(1)}" r="3.5"/>`;
  }

  return `<svg class="shape-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Win probability shape">`
    + `<line class="shape-mid" x1="${padL}" y1="${midY}" x2="${W - padR}" y2="${midY}"/>`
    + extra
    + `<polygon class="shape-fill" points="${fill}" fill="${accent}" fill-opacity="0.12"/>`
    + `<path class="shape-line" d="${line}" fill="none" stroke="${accent}"/>`
    + `</svg>`;
}
