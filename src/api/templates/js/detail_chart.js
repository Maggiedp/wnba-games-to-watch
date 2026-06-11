function buildWpSvg(plays, homeAbbr, awayAbbr) {
    if (!plays || plays.length < 2) return '';
    // The labels are dropped into innerHTML below, and team
    // abbreviations are external ESPN/DB data — escape them (via the
    // shared escapeHtml) so a poisoned value can't inject markup.
    const homeLbl = escapeHtml(homeAbbr);
    const awayLbl = escapeHtml(awayAbbr);
    const W = 500, H = 150;
    const padL = 36, padR = 8, padT = 8, padB = 8;
    const cW = W - padL - padR;
    const cH = H - padT - padB;
    const midY = padT + cH / 2;
    const N = plays.length;

    const pts = plays.map((p, i) => [
        padL + (i / (N - 1)) * cW,
        padT + (1 - p.home_pct) * cH
    ]);

    const periodBounds = [];
    for (let i = 1; i < plays.length; i++) {
        if (plays[i].period !== plays[i - 1].period) {
            periodBounds.push(pts[i][0].toFixed(1));
        }
    }

    const firstX = pts[0][0].toFixed(1);
    const lastX = pts[N - 1][0].toFixed(1);
    const botY = (H - padB).toFixed(1);

    // Home fill: always below the line (orange)
    const homePoly = [[firstX, botY],
        ...pts.map(p => [p[0].toFixed(1), p[1].toFixed(1)]),
        [lastX, botY]].map(p => `${p[0]},${p[1]}`).join(' ');

    const linePath = 'M ' + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L ');
    const [dotX, dotY] = pts[N - 1];
    const pBounds = periodBounds.map(x =>
        `<line x1="${x}" y1="${padT}" x2="${x}" y2="${H - padB}" stroke="#e7e2d8" stroke-width="1" stroke-dasharray="2,2"/>`
    ).join('');

    return `<svg class="wp-chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Win probability chart">
        <text x="${padL - 4}" y="${padT + 5}" text-anchor="end" font-size="9" fill="#8a929d">${awayLbl}</text>
        <text x="${padL - 4}" y="${midY + 3}" text-anchor="end" font-size="9" fill="#8a929d">50%</text>
        <text x="${padL - 4}" y="${H - padB}" text-anchor="end" font-size="9" fill="#8a929d">${homeLbl}</text>
        <polygon points="${homePoly}" fill="rgba(255,107,0,0.15)"/>
        <line x1="${padL}" y1="${midY.toFixed(1)}" x2="${W - padR}" y2="${midY.toFixed(1)}" stroke="#c8c2b8" stroke-width="1" stroke-dasharray="3,3"/>
        ${pBounds}
        <path d="${linePath}" fill="none" stroke="var(--orange)" stroke-width="1.5" stroke-linejoin="round"/>
        <circle cx="${dotX.toFixed(1)}" cy="${dotY.toFixed(1)}" r="3" fill="var(--orange)"/>
    </svg>`;
}

// Header above the curve. A LIVE game gets a hero win-probability
// readout (leading team + its current WP%, set in the page's
// Fraunces number idiom); a finished game keeps a compact score
// line — there's no "probability" once the game is decided.
function headerHtml(data, homeAbbr, awayAbbr) {
    const away = `${escapeHtml(awayAbbr)} ${escapeHtml(String(data.away_score))}`;
    const home = `${escapeHtml(homeAbbr)} ${escapeHtml(String(data.home_score))}`;
    const last = data.plays[data.plays.length - 1];
    const period = last && last.period ? last.period : 0;

    if (!isLiveStatus(data.status)) {
        return `<div class="wp-chart-header">${away} &nbsp; ${home} <span class="wp-chart-status">&middot; Final</span></div>`;
    }

    let status;
    if (data.status === 'STATUS_HALFTIME') {
        status = 'Halftime';
    } else if (data.status === 'STATUS_END_PERIOD') {
        status = period <= 4 ? `End Q${escapeHtml(String(period))}` : 'End OT';
    } else {
        const q = period <= 4 ? `Q${escapeHtml(String(period))}` : 'OT';
        const clock = last && last.clock ? ` ${escapeHtml(String(last.clock))}` : '';
        status = `${q}${clock}`;
    }
    const statusLine = `<div class="wp-live-status"><span class="live-dot" aria-hidden="true"></span>Live <span class="wp-chart-status">&middot; ${status} &middot; ${away} &ndash; ${home}</span></div>`;

    // Current WP = most recent play with a finite home win % in
    // [0,1]. ESPN can emit null/garbage here and the server passes
    // it through, so never synthesize a midpoint — a fake "50%"
    // reads as a real, confident call. Drop to the status line
    // alone when no usable value exists.
    let homePct = null;
    for (let i = data.plays.length - 1; i >= 0; i--) {
        const v = data.plays[i].home_pct;
        if (Number.isFinite(v) && v >= 0 && v <= 1) { homePct = v; break; }
    }
    if (homePct === null) {
        return `<div class="wp-chart-header">${statusLine}</div>`;
    }

    const homeLeads = homePct >= 0.5;
    const leadAbbr = escapeHtml(homeLeads ? homeAbbr : awayAbbr);
    const trailAbbr = escapeHtml(homeLeads ? awayAbbr : homeAbbr);
    const leadPct = Math.round((homeLeads ? homePct : 1 - homePct) * 100);
    const trailPct = 100 - leadPct;

    return `<div class="wp-chart-header">
        ${statusLine}
        <div class="wp-live-readout"><span class="wp-live-team">${leadAbbr}</span><span class="wp-live-pct">${leadPct}%</span></div>
        <div class="wp-live-label">win probability &middot; ${trailAbbr} ${trailPct}%</div>
    </div>`;
}
