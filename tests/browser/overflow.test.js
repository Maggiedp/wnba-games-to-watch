// Real-browser smoke walk: page-level horizontal overflow + inline-script
// syntax errors, across every user-facing page at each width where the layout
// changes configuration (see WIDTHS).
//
// CI: launches the runner's preinstalled Chrome (--headless --no-sandbox).
// Local (macOS): puppeteer.launch HANGS under the sandbox — never launch here.
// Launch Chrome manually and connect via CHROME_URL:
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//     --headless --disable-gpu --no-first-run \
//     --user-data-dir=/tmp/wnba-smoke-profile --remote-debugging-port=9222 about:blank &
//   CHROME_URL=http://127.0.0.1:9222 node --test 'tests/browser/**/*.test.js'
// Afterwards: pkill -f "remote-debugging-port=9222"

'use strict';

const assert = require('node:assert');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { after, before, test } = require('node:test');

const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.SMOKE_PORT || 8123);
const BASE = `http://127.0.0.1:${PORT}`;
// Six templates collapse their grids below 768px, so a phone-only walk can't
// see a multi-column layout at all — that blind spot shipped the PR #121 bridge
// bug (chart pushed into .shot-panel's narrow column at >=768px). 1200 loads
// every page in its widest layout. Phone widths are emulated as phones (touch +
// mobile viewport meta) and desktop widths are not, so a state that only exists
// under a max-width media query must declare `widths: PHONE_WIDTHS`.
// Every template that swaps a phone layout for a desktop one does it at 768px,
// so one constant classifies any width — including one added later that is in
// neither list below.
const CARD_BREAKPOINT = 768;

// Widths are the NARROWEST width of each layout configuration — the point where
// that configuration is tightest and fails first — not evenly spaced samples:
//   769  first width past the card breakpoint: tables render at their
//        narrowest and .shot-panel is already 2-col.
//   961  first width past `@media (max-width: 960px)`, so all 7 homepage table
//        columns return: 914px available against the completed table's 889px
//        min-content is the tightest margin anywhere in the layout.
// Mid-band widths (800, 820, 960) are deliberately omitted: each renders the
// same configuration as one of the above with strictly more room, so they cost
// a subtest per page and can only fail if the tighter width already has.
const PHONE_WIDTHS = [320, 360, 390, 430];
const DESKTOP_WIDTHS = [769, 961];
const WIDTHS = [...PHONE_WIDTHS, ...DESKTOP_WIDTHS];

// Sub-768 breakpoints are page-specific, so the pages that have one opt in
// rather than every page paying for it (same `widths` override HOMEPAGE_STATES
// already uses). 561 is the first width past the 560px query in player.html,
// shot_making.html and style.html, and past playoff_odds.html's 480px one:
// below it the vs-league chart stacks its labels into two rows, shot_making
// hides two table columns, .style-grid drops to one column and the playoff
// table condenses to team abbreviations. It sits UNDER CARD_BREAKPOINT, so it
// pairs the mobile card layout with the desktop label geometry — the narrowest
// track the unstacked vs-league labels ever render on, and the narrowest
// .style-grid 2-col. Walking it on the other six page-defs would break the
// mid-band rule above: none of them has a query between 430 and 769, so 561
// would render 430's configuration with more room.
const WIDTHS_WITH_561 = [...PHONE_WIDTHS, 561, ...DESKTOP_WIDTHS];

// /shot-making condenses its table up to 800px rather than the usual 768 (see
// the media query in shot_making.html), so 801 is the first width where the
// full-size desktop table renders — and it is the tightest margin anywhere in
// that page's layout: the wrapper is 712px against the table's 710px
// min-content, 2px of room. The narrowest-of-each-configuration rule makes this
// a walk, not a waiver.
const SHOT_MAKING_WIDTHS = [...WIDTHS_WITH_561, 801];

// Every `@media (max-width: N)` in the templates should have N+1 walked
// somewhere, or be listed here with the reason it isn't. Prose can't be checked
// against a template edit — assertBreakpointsAreWalkedOrWaived can, so a new
// breakpoint fails CI instead of silently becoming an unwalked band. Keyed by
// the walkable width (N+1), same shape as the nav-coverage test below.
const UNWALKED_BREAKPOINTS = new Map([
  [481, '/playoff-odds full team names return; same config as 561, 80px tighter'],
  [641, '/replay .shape-grid + /transparency .cal-layout 1col->2col; 769 is 2col'],
  [901, '.style-grid narrowest 3-col; 561 walks its narrowest 2-col, 961 a 3-col'],
]);

// The homepage is the ONLY page with a container wider than `.wrap`'s 920px cap
// (its .content is 1100px; the detail and player pages cap at 760px), so 1200 is
// a distinct layout here and a byte-identical repeat of 961 everywhere else —
// measured with per-element geometry fingerprints, not assumed.
const HOMEPAGE_WIDTHS = [...WIDTHS, 1200];

// Seeded by tests/browser/smoke_server.py; tests/test_smoke_seed.py guards
// that both ids exist and their surfaces are populated.
const UPCOMING_DETAIL_ID = '9990001';
const COMPLETED_DETAIL_ID = '9980001';

// Switch the /playoff-odds table to the Seeds heatmap view (asserts it took).
async function openPlayoffSeedsView(page) {
  await page.waitForSelector('#playoff-view-toggle:not([hidden])');
  await page.click('[data-playoff-view="seeds"]');
  await page.waitForFunction(
    () => document.getElementById('playoff-table').classList.contains('view-seeds'),
  );
}

// readySelector = client-rendered content that must exist before measuring
// (`load` fires long before a post-fetch render; see loadAt).
// widths = optional per-page override, defaulting to WIDTHS.
const PAGES = [
  { path: '/replay', readySelector: '#replay-grid .shape-card' },
  {
    path: '/rankings',
    readySelector: '#elo-chart svg',
    extraAssert: assertSeasonIsNamed('#elo-season'),
  },
  {
    path: '/transparency',
    readySelector: '#calibration-chart svg',
    extraAssert: assertSeasonIsNamed('#cal-season-head'),
  },
  { path: '/style', readySelector: '#style-grid svg', widths: WIDTHS_WITH_561 },
  {
    path: '/shot-making',
    readySelector: '#shots-tbody tr',
    widths: SHOT_MAKING_WIDTHS,
    // Guards the band in its measured form: collapsed, no panel open.
    extraAssert: assertLeaderboardFits,
  },
  {
    path: '/shot-making',
    readySelector: '#shots-tbody tr',
    widths: SHOT_MAKING_WIDTHS,
    apply: async (page) => {
      await page.waitForSelector('#shots-tbody tr.player-row');
      await page.click('#shots-tbody tr.player-row');
      await page.waitForSelector('.shot-panel .bridge-mark.is-actual');
      await page.waitForSelector('.shot-panel .shot-chart-svg, .shot-panel .status');
      await page.waitForFunction(
        () => !!document.querySelector('.shot-panel .shot-chart-svg'),
      );
      // Only shot marks carry a <title>; the court chrome (free-throw ring,
      // hoop, arc paths) carries none. Counting titles proves a real shot
      // painted AND counts off-scale chevrons, which are <path> not <circle>.
      // Don't revert to counting circles: the empty court already renders TWO
      // (ring + hoop), so a `circle.length > 1` gate passes on a blank chart.
      await page.waitForFunction(
        () => document.querySelectorAll('.shot-panel .shot-chart-svg title').length > 0,
      );
    },
    extraAssert: assertShotPanelLayout,
  },
  {
    path: '/player/smoke-shooter-0',
    readySelector: '#shot-chart svg',
    widths: WIDTHS_WITH_561,
    apply: async (page) => {
      await page.waitForSelector('.bridge-mark.is-actual');
    },
    // The seed puts this row's PPS mark at ~98% of track (see smoke_server.py),
    // so this page renders the vs-league chart's tightest label geometry at
    // every walked width — the case whose label escaped the track by 100.8px.
    extraAssert: assertBridgeLabels,
  },
  // NOT 'main' (server-rendered, resolves instantly): under `load` that would
  // measure the page before the WP chart hydrates. The chart is painted from
  // /api/live-wp, which smoke_server.py stubs to raise ESPNNotFoundError -> 404,
  // so the client's terminal path writes its "Chart unavailable." message INTO
  // #wp-chart. The server-rendered placeholder is a SIBLING of #wp-chart, so
  // this selector matches only the client-written one. If the stub ever starts
  // returning plays, the chart renders an <svg> instead and this times out —
  // loudly wrong, not silently early.
  { path: `/game/${UPCOMING_DETAIL_ID}`, readySelector: '#wp-chart .chart-placeholder' },
  { path: `/game/${COMPLETED_DETAIL_ID}`, readySelector: '#wp-chart .chart-placeholder' },
  { path: '/playoff-odds', readySelector: '#playoff-tbody tr', widths: WIDTHS_WITH_561 },
  {
    path: '/playoff-odds',
    readySelector: '#playoff-tbody tr',
    widths: WIDTHS_WITH_561,
    apply: openPlayoffSeedsView,
  },
];

let server;
let browser;

async function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) throw new Error(`server not ready: ${url}`);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

before(async () => {
  // Fail fast on a server already holding the port. `waitForServer` polls until
  // SOMETHING answers, so a leaked server from an earlier run gets silently
  // adopted — and templates are frozen at ITS import, so the whole walk then
  // measures stale HTML/CSS and passes. That is a false green, not a flake: it
  // hid a deliberate CSS break during this file's own verification.
  let stale;
  try {
    stale = await fetch(`${BASE}/api/games/upcoming`);
  } catch {
    stale = null;  // nothing listening — the good case
  }
  if (stale) {
    throw new Error(
      `port ${PORT} is already serving; a previous smoke server leaked. `
      + 'It would be adopted and the walk would measure ITS templates, not '
      + `yours. Kill it first: pkill -f "tests.browser.smoke_server"`,
    );
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wnba-smoke-'));
  const python = process.env.SMOKE_PYTHON
    || (fs.existsSync('venv/bin/python') ? 'venv/bin/python' : 'python');
  server = spawn(
    python,
    ['-m', 'tests.browser.smoke_server',
      '--port', String(PORT), '--db', path.join(tmp, 'smoke.db')],
    { stdio: 'inherit' },
  );
  await waitForServer(`${BASE}/api/games/upcoming`, 30_000);

  if (process.env.CHROME_URL) {
    browser = await puppeteer.connect({ browserURL: process.env.CHROME_URL });
  } else if (process.platform === 'darwin') {
    throw new Error(
      'Set CHROME_URL (e.g. http://127.0.0.1:9222): puppeteer.launch hangs '
      + 'under the macOS sandbox — launch Chrome manually, see file header.',
    );
  } else {
    browser = await puppeteer.launch({
      executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
      headless: true,
      args: ['--no-sandbox', '--disable-gpu'],
    });
  }
});

after(async () => {
  if (browser) {
    // disconnect (not close) when attached to a shared local Chrome — close
    // would kill it for subsequent runs.
    if (process.env.CHROME_URL) await browser.disconnect();
    else await browser.close();
  }
  if (server) server.kill();
});

function collectSyntaxErrors(page) {
  const errors = [];
  page.on('pageerror', (err) => {
    // Only parse failures block: a SyntaxError means a template shipped
    // inline JS the browser couldn't parse. Runtime errors are out of scope.
    if (/SyntaxError/.test(`${err.name}: ${err.message}`)) errors.push(err);
  });
  return errors;
}

async function loadAt(page, urlPath, width, readySelector) {
  await page.setViewport({
    width, height: 800, deviceScaleFactor: 1, isMobile: width <= CARD_BREAKPOINT,
  });
  // 'load', not 'networkidle0': measured on /replay, every request finished at
  // 67ms but networkidle0's idle window held `goto` open until 1043ms — ~975ms
  // of pure timer per subtest, and the walk is ~90% of CI wall clock. Dropping
  // it took the local walk 117s -> 67s with no assertion weakened.
  //
  // The two gates below are the actual correctness guarantees (7-45ms), and
  // that is not incidental: `load` does NOT imply fonts are ready. Measured,
  // document.fonts.status is still 'loading' at this line on 3-4 of the 10
  // walked pages (which ones varies run to run) — so the fonts.ready await is
  // what closes the font-load race behind the phantom featured-card sighting,
  // and it is verified to leave all 10 pages 'loaded' before anything is
  // measured. Order matters: readySelector first, so client-rendered content
  // has requested its faces before we await. Don't weaken this to
  // 'domcontentloaded' on the theory that fonts.ready covers everything —
  // unsized subresources shift layout and have no such gate.
  await page.goto(`${BASE}${urlPath}`, { waitUntil: 'load', timeout: 20_000 });
  if (readySelector) await page.waitForSelector(readySelector, { timeout: 10_000 });
  await page.evaluate(() => document.fonts.ready);
}

async function assertNoOverflow(page, label) {
  const m = await page.evaluate(() => ({
    scrollWidth: document.scrollingElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  assert.ok(
    m.scrollWidth <= m.innerWidth,
    `${label}: page overflows horizontally `
    + `(scrollWidth ${m.scrollWidth} > innerWidth ${m.innerWidth})`,
  );
}

// One walked subtest: fresh page → load → optional state build → parse-error
// and overflow asserts → optional scoped extra assert → close.
async function checkPage(label, width, { path: urlPath, readySelector, apply, extraAssert }) {
  const page = await browser.newPage();
  try {
    const syntaxErrors = collectSyntaxErrors(page);
    await loadAt(page, urlPath, width, readySelector);
    if (apply) await apply(page);
    assert.deepStrictEqual(syntaxErrors.map(String), []);
    await assertNoOverflow(page, label);
    if (extraAssert) await extraAssert(page, label, width);
  } finally {
    await page.close();
  }
}

// Both endpoints default to the newest POPULATED season, so through the
// offseason these pages show the season that just FINISHED. That is only honest
// because the page names the year it got — the fallback is otherwise
// indistinguishable from current data, which is exactly the mislabel
// /api/shot-making stays on the clock to avoid.
//
// The static markup ships the pre-fetch placeholder ("current" / "This
// season"); the loader overwrites it from `data.season`. So a typo in that one
// line fails silently and restores the mislabel, with the page still rendering,
// still parsing and still not overflowing — invisible to every other assert
// here. This one asserts the element ends up holding a four-digit year.
function assertSeasonIsNamed(selector) {
  return async (page, label) => {
    const text = await page.$eval(selector, (el) => el.textContent.trim());
    assert.match(
      text,
      /\b\d{4}\b/,
      `${label}: ${selector} should name the season it is showing, got "${text}" `
        + '(the loader failed to fill it from data.season, so a fallback season '
        + 'would render as if it were the current one)',
    );
  };
}

// Regression (PR #112): the date row's ~337px min-content used to truncate the
// "to" date input at 320px — invisible to the page-level scrollWidth assert
// (the collapsed/open panel CLIPS instead of scrolling the page), so the fixed
// inputs get a scoped bounding-rect assert in the filter-panel-open state.
async function assertDateInputsFit(page, label) {
  const m = await page.evaluate(() => {
    const rect = (id) => {
      const r = document.getElementById(id).getBoundingClientRect();
      return { id, left: r.left, right: r.right };
    };
    return {
      innerWidth: window.innerWidth,
      inputs: [rect('from-date'), rect('to-date')],
    };
  });
  for (const r of m.inputs) {
    assert.ok(
      r.right <= m.innerWidth && r.left >= 0,
      `${label}: #${r.id} [${r.left}, ${r.right}] outside viewport 0..${m.innerWidth}`,
    );
  }
}

// The vs-league chart positions its two value labels absolutely, so nothing
// about them shows up in `scrollWidth <= innerWidth` — they can print on top of
// each other, or on top of the sentence below, while the page assertion stays
// green. That is exactly what happened: an edge-clamp meant to stop a label
// leaving the track could put both labels on the same side, and at 320px the
// two glosses overlapped by 29px. Measure the rendered rects instead.
async function assertBridgeLabels(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('.bridge');
    if (!root) return null;
    const rect = (el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
    };
    const labs = [...root.querySelectorAll('.bridge-lab')].map(rect);
    const said = root.querySelector('.bridge-said');
    return { labs, axis: rect(root.querySelector('.bridge-axis')), said: said && rect(said) };
  });
  if (!m) return;   // the chart degrades to nothing without league anchors
  assert.strictEqual(m.labs.length, 2, `${label}: expected exactly 2 bridge labels`);
  const [a, b] = m.labs;
  const overlaps = (p, q) => !(p.right <= q.left || q.right <= p.left)
    && !(p.bottom <= q.top || q.bottom <= p.top);
  assert.ok(
    !overlaps(a, b),
    `${label}: the xPPS and PPS labels overlap by `
    + `${(Math.min(a.right, b.right) - Math.max(a.left, b.left)).toFixed(1)}px `
    + '— they must occupy disjoint boxes bounded by the track',
  );
  // A wrapped label must not run into the sentence under the chart, which
  // clears the labels by a hand-set margin rather than by layout.
  if (m.said) {
    for (const [i, l] of m.labs.entries()) {
      assert.ok(
        !overlaps(l, m.said),
        `${label}: bridge label ${i} collides with the sentence below it `
        + '(a narrow box wrapped the gloss past the .bridge-said clearance)',
      );
    }
  }
  // Neither box may escape the track it is measured against.
  for (const [i, l] of m.labs.entries()) {
    assert.ok(
      l.left >= m.axis.left - 1 && l.right <= m.axis.right + 1,
      `${label}: bridge label ${i} (${l.left.toFixed(0)}–${l.right.toFixed(0)}) `
      + `escapes the track (${m.axis.left.toFixed(0)}–${m.axis.right.toFixed(0)})`,
    );
  }
}

// Regression (2026-08-11): `.shots-table td { white-space: nowrap }` inherits
// into the expand panel — `.shot-panel-row > td` resets only padding and
// background — so the chart caption rendered as one 878px line and opening a
// panel gave the whole leaderboard horizontal scroll: 40px at desktop, and at
// 390px the table ballooned 548 -> 894, leaving most of the chart off-screen.
// The page-level assert cannot see it (`.shots-table-scroll { overflow-x: auto }`
// contains it) and neither can the geometry asserts above and below, which are
// measured relative to the panel and stay true while it is oversized.
//
// Relative (open vs closed), not `scroll <= client`, and that split is real
// rather than an inconsistency: at phone widths this table is SUPPOSED to
// scroll inside its wrapper (min-content 510px against a 316px viewport at
// 390px), so an absolute assert would fail on correct layout there. Above the
// card breakpoint the board is meant to fit, and assertLeaderboardFits says so
// absolutely. This one holds at every width because it asks a question that is
// width-independent: did opening a panel make the table wider than the
// leaderboard alone needs?
async function assertPanelDoesNotWidenTheTable(page, label) {
  const measure = () => page.evaluate(() => {
    const d = document.querySelector('.shots-table-scroll');
    return d && { client: d.clientWidth, scroll: d.scrollWidth };
  });
  const open = await measure();
  assert.ok(open, `${label}: no .shots-table-scroll rendered`);
  // Accordion: clicking the open row's own header closes it. Bounded well
  // under the 30s default: if the toggle ever stops closing, the default would
  // stall this CI-blocking walk for 30s x 7 widths — 3x the whole walk's budget.
  await page.click('#shots-tbody tr.player-row');
  await page.waitForFunction(
    () => !document.querySelector('.shot-panel'), { timeout: 5_000 },
  );
  const closed = await measure();
  assert.ok(
    open.scroll <= closed.scroll,
    `${label}: opening a shot panel widened the leaderboard from `
    + `${closed.scroll}px to ${open.scroll}px (visible ${open.client}px) — is the `
    + 'panel inheriting `white-space: nowrap` from .shots-table td?',
  );
}

// Regression (PR #121): .shot-panel is a `1.4fr 1fr` grid and the bridge is a
// THIRD child, so without `grid-column: 1 / -1` auto-placement puts it in
// column 1 — squeezing the chart into the narrow column and dropping the zones
// to row 2. The page-level scrollWidth assert CANNOT see this at any width (the
// panel re-flows, it never overflows), so the desktop layout gets a scoped
// geometry assert: the bridge spans the panel, and chart + zones share a row.
// Verified by deliberate break — deleting the grid-column rule fails this.
async function assertShotPanelLayout(page, label, width) {
  await assertBridgeLabels(page, label);
  // .shot-panel collapses to a single column at the card breakpoint, where the
  // zones legitimately sit below the chart — this invariant is desktop-only.
  // Below it, the panel's job is to fit the screen instead. Kept as an
  // either/or rather than running both everywhere so each failure message
  // names the cause that actually applies at that width.
  if (width > CARD_BREAKPOINT) {
    await assertPanelColumns(page, label);
  } else {
    await assertPanelFitsViewport(page, label);
    await assertPanelStaysInView(page, label);
  }
  // Self-gates to desktop widths; here it also covers the panel-open case.
  await assertLeaderboardFits(page, label, width);
  // LAST: this one collapses the panel to take its comparison measurement, so
  // nothing needing the panel open may run after it.
  await assertPanelDoesNotWidenTheTable(page, label);
}

// The ABSOLUTE half of the wrapper-scroll family: this wrapper is a
// contained-overflow safety net, so reaching it at all is the bug.
//
// Deliberately NOT unified with assertPanelDoesNotWidenTheTable, which is
// RELATIVE (open vs closed). That split is a real difference, not an
// implementation inconsistency — a phone-width table is SUPPOSED to scroll —
// so it stays visible in the names rather than becoming a mode flag.
async function assertWrapperDoesNotScroll(page, label, selector, hint) {
  const wraps = await page.evaluate(
    (sel) => [...document.querySelectorAll(sel)]
      .map((d) => ({ client: d.clientWidth, scroll: d.scrollWidth })),
    selector,
  );
  assert.ok(wraps.length > 0, `${label}: no ${selector} rendered`);
  wraps.forEach((w, i) => {
    const which = wraps.length > 1 ? ` [${i}]` : '';
    assert.ok(
      w.scroll <= w.client,
      `${label}: ${selector}${which} scrolls inside its wrapper by `
      + `${w.scroll - w.client}px (min-content ${w.scroll}px vs ${w.client}px `
      + `visible) — ${hint}`,
    );
  });
}

// Regression (item p): the condensed table styling ended at 768px while the
// full-size table's min-content stayed wider than the wrapper until 799px, so
// 769-798px side-scrolled the board. Numbers and derivation live on
// SHOT_MAKING_WIDTHS, which is what walks the tight end of that range.
//
// ⚠️ This assert is only as strong as the seed's widest Player cell: with the
// old uniform short names the seeded table's min-content was 680px and this
// passed at 769px on the very code that overflowed production by 30px. See
// _LONGEST_NAME in smoke_server.py before trusting a green run here.
//
// Desktop-only because below the breakpoint this table legitimately scrolls
// (see assertPanelDoesNotWidenTheTable). Self-gating so both call sites — the
// collapsed page def and assertShotPanelLayout — can call it unconditionally.
async function assertLeaderboardFits(page, label, width) {
  if (width <= CARD_BREAKPOINT) return;
  await assertWrapperDoesNotScroll(
    page, label, '.shots-table-scroll',
    'does the condensed table styling stop before the viewport is wide enough '
    + 'for the full-size table?',
  );
}

// Regression (item q): the panel renders in a `colspan` cell INSIDE
// .shots-table-scroll, so its width tracked the TABLE (549px) rather than the
// screen (316px at a 390px viewport) and the chart sat ~1.7x the visible
// region, needing a sideways scroll to read on a phone.
//
// Nothing already here could see it. The page-level scrollWidth assert can't
// (the wrapper contains it); assertPanelColumns is desktop-only and measures
// relative geometry; and assertPanelDoesNotWidenTheTable stays true while the
// panel is oversized, because a 549px panel inside a 549px table doesn't widen
// anything — it only ever asked whether the panel made the table WORSE, never
// whether the panel fits the screen.
async function assertPanelFitsViewport(page, label) {
  const m = await page.evaluate(() => {
    const d = document.querySelector('.shots-table-scroll');
    const p = document.querySelector('.shot-panel');
    return d && p && { client: d.clientWidth, panel: p.getBoundingClientRect().width };
  });
  assert.ok(m, `${label}: .shot-panel or .shots-table-scroll missing`);
  assert.ok(
    // +1 for sub-pixel rounding: cqw resolves against a fractional width.
    m.panel <= m.client + 1,
    `${label}: the shot panel is ${Math.round(m.panel)}px inside a ${m.client}px `
    + 'visible region, so the chart needs a sideways scroll — is `width: 100cqw` '
    + 'missing from .shot-panel, or did .shots-table-scroll lose '
    + '`container-type: inline-size`?',
  );
}

// The other half of the (q) fix, and a separate invariant from fitting: the
// panel sits in a cell still wider than the screen, so without `position:
// sticky` a correctly-SIZED panel still slides out of view the moment the
// reader scrolls the table's columns. Scroll to the far edge and require the
// panel to have followed, then put scrollLeft back so nothing downstream
// measures a scrolled table.
//
// Both rects are viewport-relative, so the visible region is the CONTAINER's
// rect — not `0..clientWidth`. The scroll container starts at the page's left
// padding (x=37 at phone widths), so comparing against 0 fails on correct
// layout: a perfectly stuck panel reads 37..283, which is exactly the
// container's 246px of visible width.
async function assertPanelStaysInView(page, label) {
  const stuck = await page.evaluate(() => {
    const d = document.querySelector('.shots-table-scroll');
    d.scrollLeft = d.scrollWidth;
    const p = document.querySelector('.shot-panel').getBoundingClientRect();
    const c = d.getBoundingClientRect();
    const out = {
      left: p.left, right: p.right, cLeft: c.left, cRight: c.right, scrolled: d.scrollLeft,
    };
    d.scrollLeft = 0;  // every subtest gets a fresh page, so 0 is where it was
    return out;
  });
  assert.ok(
    stuck.scrolled > 0,
    `${label}: the leaderboard did not scroll, so this assert proved nothing `
    + '— has the seeded table stopped being wider than the viewport?',
  );
  assert.ok(
    stuck.left >= stuck.cLeft - 1 && stuck.right <= stuck.cRight + 1,
    `${label}: after scrolling the table to its far edge the panel sits at `
    + `${Math.round(stuck.left)}..${Math.round(stuck.right)}px, outside the `
    + `${Math.round(stuck.cLeft)}..${Math.round(stuck.cRight)}px visible region `
    + '— did .shot-panel lose `position: sticky; left: 0`?',
  );
}

async function assertPanelColumns(page, label) {
  const m = await page.evaluate(() => {
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width,
      };
    };
    return {
      panel: box('.shot-panel'),
      bridge: box('.shot-panel .bridge'),
      chart: box('.shot-panel .chart-wrap'),
      zones: box('.shot-panel .shot-zones'),
    };
  });
  for (const [name, b] of Object.entries(m)) {
    assert.ok(b, `${label}: .shot-panel ${name} missing — panel did not render`);
  }
  // Grid gap + padding mean the bridge is a few px under the panel's own box.
  assert.ok(
    m.bridge.width >= m.panel.width - 40,
    `${label}: bridge spans ${m.bridge.width}px of a ${m.panel.width}px panel `
    + '(expected full width — is `.shot-panel .bridge { grid-column: 1 / -1 }` gone?)',
  );
  // Column placement, not just vertical overlap: zones must start to the RIGHT
  // of where the chart ends. Vertical overlap alone would also be satisfied by
  // zones overlapping or clipping the chart inside a single column.
  assert.ok(
    m.zones.left >= m.chart.right,
    `${label}: zones (left ${m.zones.left}) do not sit in a column right of the `
    + `chart (right ${m.chart.right}) — panel is not laid out side by side`,
  );
  assert.ok(
    m.zones.top < m.chart.bottom,
    `${label}: zones (top ${m.zones.top}) dropped below the chart `
    + `(bottom ${m.chart.bottom}) instead of sharing its row`,
  );
}

// The .games-table-scroll wrapper CONTAINS table overflow, which means the
// page-level assert can never fail on the games tables — a regression in the
// `@media (max-width: 960px)` column rule would silently side-scroll the table
// while the walk stayed green. Assert the wrapper isn't actually scrolling, so
// the column rule is measured rather than merely symptom-hidden. Desktop only:
// the phone layout renders cards, and no .games-table-scroll is reached.
async function assertGamesTablesFit(page, label, width) {
  if (width <= CARD_BREAKPOINT) return;
  await assertWrapperDoesNotScroll(
    page, label, '.games-table-scroll',
    'has the hide-mobile breakpoint moved below the width where the full '
    + 'table fits?',
  );
}

// Homepage states. Each apply() ASSERTS the toggle took effect (waits on the
// resulting DOM state) so a renamed id/class fails loudly instead of letting
// the walk pass vacuously against an untoggled page.
const HOMEPAGE_STATES = [
  { name: 'default', extraAssert: assertGamesTablesFit },
  {
    name: 'filter-panel-open',
    // Phone-only state: .mobile-filter-bar is display:none at base and only
    // shows inside @media (max-width: 768px), so there is no toggle to click
    // at a desktop width (the full controls are already visible there).
    widths: PHONE_WIDTHS,
    apply: async (page) => {
      await page.click('#mobile-filter-toggle');
      await page.waitForFunction(
        () => document.getElementById('filter-panel').classList.contains('open'),
      );
    },
    extraAssert: assertDateInputsFit,
  },
  {
    name: 'completed-open',
    apply: async (page) => {
      await page.waitForSelector('#completed-toggle:not([hidden])');
      await page.click('#completed-toggle');
      await page.waitForFunction(
        () => !document.getElementById('completed-content').hidden,
      );
      await page.waitForFunction(
        () => document.querySelectorAll('#completed-games-container tr').length > 0,
      );
    },
    // The completed table is the wider of the two (extra score columns), so it
    // is the one that constrains the 960px breakpoint.
    extraAssert: assertGamesTablesFit,
  },
];

test('inner pages: no horizontal overflow, no inline-script syntax errors', async (t) => {
  for (const pageDef of PAGES) {
    for (const width of pageDef.widths || WIDTHS) {
      const label = `${pageDef.path} @ ${width}px`;
      await t.test(label, () => checkPage(label, width, pageDef));
    }
  }
});

test('homepage states: no horizontal overflow, no inline-script syntax errors', async (t) => {
  for (const state of HOMEPAGE_STATES) {
    for (const width of state.widths || HOMEPAGE_WIDTHS) {
      const label = `/ [${state.name}] @ ${width}px`;
      await t.test(label, () => checkPage(label, width, {
        path: '/',
        readySelector: '#games-container table',
        apply: state.apply,
        extraAssert: state.extraAssert,
      }));
    }
  }
});

// The width lists above are hand-synced to the templates' media queries, and a
// prose comment can't notice a template gaining one. This reads the queries back
// out and requires each to be walked or explicitly waived, so a new breakpoint
// fails here instead of quietly becoming an unwalked band. No browser needed.
test('every template breakpoint is walked or waived', () => {
  const dir = path.join(__dirname, '..', '..', 'src', 'api', 'templates');
  const sources = fs.readdirSync(dir)
    .filter((f) => f.endsWith('.html'))
    .map((f) => fs.readFileSync(path.join(dir, f), 'utf8'))
    .concat(fs.readFileSync(
      path.join(__dirname, '..', '..', 'src', 'api', 'routes.py'), 'utf8'));

  // Mirrors the two walk loops exactly — same collections, same defaults (see
  // the `pageDef.widths || WIDTHS` and `state.widths || HOMEPAGE_WIDTHS` in the
  // walk above) — so the walked set is the walked set by construction, with no
  // constant hand-listed here. The hand-listed version could not see a NEW
  // per-page list: adding SHOT_MAKING_WIDTHS made this test report 801 as an
  // unwalked band while the walk was in fact loading it, a false failure in the
  // one test whose whole job is to be trustworthy about coverage. Restating the
  // defaults here rather than reusing them would leave that same failure one
  // level down, for the next page def that introduces a default.
  const walked = new Set([
    ...PAGES.flatMap((p) => p.widths || WIDTHS),
    ...HOMEPAGE_STATES.flatMap((s) => s.widths || HOMEPAGE_WIDTHS),
  ]);
  const found = new Set();
  for (const src of sources) {
    for (const m of src.matchAll(/@media\s*\(max-width:\s*(\d+)px\)/g)) {
      found.add(Number(m[1]) + 1);
    }
  }
  assert.ok(found.size >= 5, `expected to find media queries, got [${[...found]}]`);

  const gaps = [...found].filter((w) => !walked.has(w) && !UNWALKED_BREAKPOINTS.has(w));
  assert.deepStrictEqual(gaps, [],
    `template breakpoint(s) neither walked nor waived: ${gaps.join(', ')}. `
    + 'Add the width to a walk list, or to UNWALKED_BREAKPOINTS with a reason.');

  // A waiver for a width that IS walked is stale bookkeeping — drop it.
  const stale = [...UNWALKED_BREAKPOINTS.keys()].filter((w) => walked.has(w));
  assert.deepStrictEqual(stale, [],
    `UNWALKED_BREAKPOINTS lists width(s) the walk already covers: ${stale.join(', ')}`);
});

// The site nav is the page inventory (_SITE_NAV_ITEMS in src/api/routes.py),
// and the repo's convention is "new top-level page → new nav entry" — so every
// nav destination must be in the walk, or adding a page would silently leave
// it uncovered while the suite stays green.
test('every site-nav page is covered by the walk', async () => {
  const page = await browser.newPage();
  try {
    await loadAt(page, '/', 390, '#games-container table');
    const hrefs = await page.$$eval('.site-nav-link', (els) =>
      els.map((el) => el.getAttribute('href')));
    assert.ok(hrefs.length >= 5, `expected a populated site nav, got [${hrefs}]`);
    const walked = new Set(['/', ...PAGES.map((p) => p.path)]);
    for (const href of hrefs) {
      assert.ok(walked.has(href), `nav page ${href} is not in the walk's PAGES list`);
    }
  } finally {
    await page.close();
  }
});
