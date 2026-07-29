// Real-browser smoke walk: page-level horizontal overflow + inline-script
// syntax errors, across every user-facing page at phone widths.
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
const WIDTHS = [320, 360, 390, 430];

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
// (networkidle alone can race the post-fetch render).
const PAGES = [
  { path: '/replay', readySelector: '#replay-grid .shape-card' },
  { path: '/rankings', readySelector: '#elo-chart svg' },
  { path: '/transparency', readySelector: '#calibration-chart svg' },
  { path: '/style', readySelector: '#style-grid svg' },
  { path: '/shot-making', readySelector: '#shots-tbody tr' },
  {
    path: '/shot-making',
    readySelector: '#shots-tbody tr',
    apply: async (page) => {
      await page.waitForSelector('#shots-tbody tr.player-row');
      await page.click('#shots-tbody tr.player-row');
      await page.waitForSelector('.shot-panel .shot-chart-svg, .shot-panel .status');
      await page.waitForFunction(
        () => !!document.querySelector('.shot-panel .shot-chart-svg'),
      );
      // The empty court renders one <circle> (the hoop); >1 proves a real shot
      // dot painted, so the walk covers a populated chart, not just the court.
      await page.waitForFunction(
        () => document.querySelectorAll('.shot-panel .shot-chart-svg circle').length > 1,
      );
    },
  },
  { path: `/game/${UPCOMING_DETAIL_ID}`, readySelector: 'main' },
  { path: `/game/${COMPLETED_DETAIL_ID}`, readySelector: 'main' },
  { path: '/playoff-odds', readySelector: '#playoff-tbody tr' },
  { path: '/playoff-odds', readySelector: '#playoff-tbody tr', apply: openPlayoffSeedsView },
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
  await page.setViewport({ width, height: 800, deviceScaleFactor: 1, isMobile: true });
  await page.goto(`${BASE}${urlPath}`, { waitUntil: 'networkidle0', timeout: 20_000 });
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
    if (extraAssert) await extraAssert(page, label);
  } finally {
    await page.close();
  }
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

// Homepage states. Each apply() ASSERTS the toggle took effect (waits on the
// resulting DOM state) so a renamed id/class fails loudly instead of letting
// the walk pass vacuously against an untoggled page.
const HOMEPAGE_STATES = [
  { name: 'default' },
  {
    name: 'filter-panel-open',
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
  },
];

test('inner pages: no horizontal overflow, no inline-script syntax errors', async (t) => {
  for (const pageDef of PAGES) {
    for (const width of WIDTHS) {
      const label = `${pageDef.path} @ ${width}px`;
      await t.test(label, () => checkPage(label, width, pageDef));
    }
  }
});

test('homepage states: no horizontal overflow, no inline-script syntax errors', async (t) => {
  for (const state of HOMEPAGE_STATES) {
    for (const width of WIDTHS) {
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
