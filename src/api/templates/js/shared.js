function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
}

// ESPN reports STATUS_HALFTIME between halves and STATUS_END_PERIOD
// between quarters. Both are "live" for rendering and polling.
function isLiveStatus(status) {
    return status === 'STATUS_IN_PROGRESS'
        || status === 'STATUS_HALFTIME'
        || status === 'STATUS_END_PERIOD';
}

// A finished game. Scoped to STATUS_FINAL only — postponed/canceled/
// suspended are deliberately NOT treated as "final" here.
function isFinalStatus(status) {
    return status === 'STATUS_FINAL';
}
