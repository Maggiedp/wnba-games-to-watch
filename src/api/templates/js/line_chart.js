// Shared multi-team trend line chart (Elo ratings, playoff odds, ...). Lines
// are styled/highlighted via CSS (.trend-line[.hi]); each path + end-label
// carries data-team = the series index. Depends on escapeHtml (shared.js).
function buildLineChartSvg(series, opts) {
  // series: [{label, abbr, last, points:[{x,y}]}]
  // opts: {width, height, xTicks, yMin?, yMax?, yFormat?}
  const W = opts.width, H = opts.height;
  const PL = 40, PR = 48, PT = 16, PB = 28;  // extra right pad for end-labels
  const fmt = opts.yFormat || Math.round;
  const xs = series.flatMap(s => s.points.map(p => p.x));
  const ys = series.flatMap(s => s.points.map(p => p.y));
  if (!xs.length) return '<p class="empty">No data yet.</p>';
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = opts.yMin != null ? opts.yMin : Math.min(...ys);
  const ymax = opts.yMax != null ? opts.yMax : Math.max(...ys);
  const sx = x => PL + (xmax === xmin ? 0 : (x - xmin) / (xmax - xmin)) * (W - PL - PR);
  const sy = y => H - PB - (ymax === ymin ? 0 : (y - ymin) / (ymax - ymin)) * (H - PT - PB);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Team trends over time">`;
  for (let i = 0; i <= 4; i++) {
    const val = ymin + (ymax - ymin) * i / 4;
    const y = sy(val);
    svg += `<line x1="${PL}" y1="${y}" x2="${W-PR}" y2="${y}" stroke="#ece6da"/>`;
    svg += `<text x="${PL-8}" y="${y+3}" text-anchor="end" font-size="10" fill="#8a929d">${fmt(val)}</text>`;
  }
  for (const t of (opts.xTicks || [])) {
    const x = sx(t.x);
    svg += `<line x1="${x}" y1="${PT}" x2="${x}" y2="${H-PB}" stroke="#f2ede3"/>`;
    svg += `<text x="${x}" y="${H-PB+15}" text-anchor="middle" font-size="10" fill="#8a929d">${t.label}</text>`;
  }
  const paths = series.map(s => s.points.length
    ? s.points.map((p, k) => `${k ? 'L' : 'M'}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ')
    : '');
  for (let i = 0; i < series.length; i++) {
    if (paths[i]) svg += `<path class="trend-line" data-team="${i}" d="${paths[i]}"/>`;
  }
  const ends = [];
  for (let i = 0; i < series.length; i++) {
    const pts = series[i].points;
    if (!pts.length) continue;
    ends.push({ i, abbr: series[i].abbr, y: sy(pts[pts.length - 1].y) });
  }
  ends.sort((a, b) => a.y - b.y);
  const gap = 11;
  for (let k = 1; k < ends.length; k++) {
    if (ends[k].y - ends[k - 1].y < gap) ends[k].y = ends[k - 1].y + gap;
  }
  const overflow = ends.length ? ends[ends.length - 1].y - (H - PB) : 0;
  if (overflow > 0) for (const e of ends) e.y -= overflow;
  for (const e of ends) {
    svg += `<text class="trend-label" data-team="${e.i}" x="${W-PR+5}" y="${e.y.toFixed(1)}" dominant-baseline="middle">${escapeHtml(e.abbr)}</text>`;
  }
  for (let i = 0; i < series.length; i++) {
    if (paths[i]) svg += `<path class="trend-hit" data-team="${i}" d="${paths[i]}"/>`;
  }
  svg += '</svg>';
  return svg;
}

// Renders a multi-team trend chart + ranked legend into the given mount/legend
// elements. `data` = {teams:{name:[{date,value}]}, abbrevs:{name:abbr}}.
// opts: {width, height, yMin?, yMax?, yFormat?}. The legend doubles as a
// standings list (sorted by current value desc).
function renderTeamTrendChart(mountEl, legendEl, data, opts = {}) {
  const fmt = opts.yFormat || Math.round;
  const names = Object.keys(data.teams || {});
  if (!names.length) { mountEl.innerHTML = '<p class="empty">Not enough history yet.</p>'; return; }
  const allDates = [...new Set(names.flatMap(n => data.teams[n].map(p => p.date)))].sort();
  if (allDates.length < 2) { mountEl.innerHTML = '<p class="empty">Not enough history yet.</p>'; return; }
  const dayIndex = Object.fromEntries(allDates.map((d, i) => [d, i]));
  const abbrevs = data.abbrevs || {};
  const series = names.map(n => {
    const pts = data.teams[n];
    return { label: n, abbr: abbrevs[n] || n.slice(0, 3).toUpperCase(),
             last: pts[pts.length - 1].value,
             points: pts.map(p => ({ x: dayIndex[p.date], y: p.value })) };
  });
  series.sort((a, b) => b.last - a.last);  // legend doubles as a standings list
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const seen = new Set();
  const xTicks = [];
  for (const d of allDates) {
    const ym = d.slice(0, 7);
    if (seen.has(ym)) continue;
    seen.add(ym);
    xTicks.push({ x: dayIndex[d], label: MONTHS[parseInt(d.slice(5, 7), 10) - 1] });
  }
  mountEl.innerHTML = buildLineChartSvg(series, {
    width: opts.width || 860, height: opts.height || 360, xTicks,
    yMin: opts.yMin, yMax: opts.yMax, yFormat: fmt });
  const svg = mountEl.querySelector('svg');
  legendEl.innerHTML = series.map((s, i) =>
    `<button class="legend-row" type="button" data-team="${i}">` +
    `<span class="rank">${i + 1}</span>${escapeHtml(s.label)}` +
    `<span class="value">${fmt(s.last)}</span></button>`).join('');
  const setHi = (i, on) => {
    const path = svg && svg.querySelector(`.trend-line[data-team="${i}"]`);
    const label = svg && svg.querySelector(`.trend-label[data-team="${i}"]`);
    const row = legendEl.querySelector(`.legend-row[data-team="${i}"]`);
    if (path) { path.classList.toggle('hi', on); if (on) path.parentNode.appendChild(path); }
    if (label) { label.classList.toggle('hi', on); if (on) label.parentNode.appendChild(label); }
    if (row) row.classList.toggle('active', on);
  };
  legendEl.querySelectorAll('.legend-row').forEach(row => {
    const i = row.dataset.team;
    row.addEventListener('mouseenter', () => setHi(i, true));
    row.addEventListener('mouseleave', () => setHi(i, false));
    row.addEventListener('focus', () => setHi(i, true));
    row.addEventListener('blur', () => setHi(i, false));
  });
  (svg ? svg.querySelectorAll('.trend-hit, .trend-label') : []).forEach(el => {
    const i = el.dataset.team;
    el.addEventListener('mouseenter', () => setHi(i, true));
    el.addEventListener('mouseleave', () => setHi(i, false));
  });
}

// Subtract `days` calendar days from an ISO YYYY-MM-DD string (UTC, TZ-safe).
function isoMinusDays(iso, days) {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

// Week-over-week rank movement per team. data = {teams:{name:[{date,value}]}}.
// Returns {name: delta|null} where delta = (rank `windowDays` ago) - (rank now);
// positive = climbed. null = no data point on-or-before the cutoff (expansion
// team, or season younger than the window). Ranks are computed over the set of
// teams that have a week-ago value, so a stable mid-season slate is true
// week-over-week movement. Points are assumed ascending by date (same contract
// the renderer relies on for `last`).
function computeRankDeltas(data, windowDays = 7) {
  const teams = data.teams || {};
  const names = Object.keys(teams);
  const result = {};
  let maxDate = '';
  for (const n of names) {
    const pts = teams[n];
    if (pts.length && pts[pts.length - 1].date > maxDate) maxDate = pts[pts.length - 1].date;
  }
  if (!maxDate) return result;
  const cutoff = isoMinusDays(maxDate, windowDays);
  const current = {}, weekAgo = {};
  for (const n of names) {
    const pts = teams[n];
    if (!pts.length) continue;
    current[n] = pts[pts.length - 1].value;
    let wa = null;
    for (const p of pts) { if (p.date <= cutoff) wa = p.value; }
    weekAgo[n] = wa;
  }
  const eligible = names.filter(n => weekAgo[n] != null && current[n] != null);
  const curRank = {}, waRank = {};
  [...eligible].sort((a, b) => current[b] - current[a]).forEach((n, i) => curRank[n] = i + 1);
  [...eligible].sort((a, b) => weekAgo[b] - weekAgo[a]).forEach((n, i) => waRank[n] = i + 1);
  for (const n of names) {
    result[n] = eligible.includes(n) ? (waRank[n] - curRank[n]) : null;
  }
  return result;
}
