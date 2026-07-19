// Real-browser smoke walk: page-level horizontal overflow + inline-script
// syntax errors, across every user-facing page at phone widths.
//
// CI: launches the runner's preinstalled Chrome (--headless --no-sandbox).
// Local (macOS): puppeteer.launch HANGS under the sandbox — never launch here.
// Launch Chrome manually and connect via CHROME_URL:
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//     --headless --disable-gpu --no-first-run \
//     --user-data-dir=/tmp/wnba-smoke-profile --remote-debugging-port=9222 about:blank &
//   CHROME_URL=http://127.0.0.1:9222 node --test tests/browser/
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

// readySelector = client-rendered content that must exist before measuring
// (networkidle alone can race the post-fetch render).
const PAGES = [
  { path: '/replay', readySelector: '#replay-grid .shape-card' },
  { path: '/rankings', readySelector: '#elo-chart svg' },
  { path: '/transparency', readySelector: '#calibration-chart svg' },
  { path: '/style', readySelector: '#style-grid svg' },
  { path: `/game/${UPCOMING_DETAIL_ID}`, readySelector: 'main' },
  { path: `/game/${COMPLETED_DETAIL_ID}`, readySelector: 'main' },
];

let server;
let browser;
let usingConnect = false;

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
    usingConnect = true;
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
    if (usingConnect) await browser.disconnect();
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

test('inner pages: no horizontal overflow, no inline-script syntax errors', async (t) => {
  for (const { path: urlPath, readySelector } of PAGES) {
    for (const width of WIDTHS) {
      await t.test(`${urlPath} @ ${width}px`, async () => {
        const page = await browser.newPage();
        try {
          const syntaxErrors = collectSyntaxErrors(page);
          await loadAt(page, urlPath, width, readySelector);
          assert.deepStrictEqual(syntaxErrors.map(String), []);
          await assertNoOverflow(page, `${urlPath} @ ${width}px`);
        } finally {
          await page.close();
        }
      });
    }
  }
});

// Homepage states. Each apply() ASSERTS the toggle took effect (waits on the
// resulting DOM state) so a renamed id/class fails loudly instead of letting
// the walk pass vacuously against an untoggled page.
async function openPlayoffPicture(page) {
  await page.waitForSelector('#playoff-toggle:not([hidden])');
  await page.click('#playoff-toggle');
  await page.waitForFunction(() => !document.getElementById('playoff-content').hidden);
  await page.waitForFunction(
    () => document.querySelectorAll('#playoff-tbody tr').length > 0,
  );
}

const HOMEPAGE_STATES = [
  { name: 'default', apply: async () => {} },
  {
    name: 'filter-panel-open',
    apply: async (page) => {
      await page.click('#mobile-filter-toggle');
      await page.waitForFunction(
        () => document.getElementById('filter-panel').classList.contains('open'),
      );
    },
  },
  { name: 'playoff-rounds', apply: openPlayoffPicture },
  {
    name: 'playoff-seeds',
    apply: async (page) => {
      await openPlayoffPicture(page);
      await page.waitForSelector('#playoff-view-toggle:not([hidden])');
      await page.click('[data-playoff-view="seeds"]');
      await page.waitForFunction(
        () => document.getElementById('playoff-table').classList.contains('view-seeds'),
      );
    },
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

test('homepage states: no horizontal overflow, no inline-script syntax errors', async (t) => {
  for (const state of HOMEPAGE_STATES) {
    for (const width of WIDTHS) {
      await t.test(`/ [${state.name}] @ ${width}px`, async () => {
        const page = await browser.newPage();
        try {
          const syntaxErrors = collectSyntaxErrors(page);
          await loadAt(page, '/', width, '#games-container table');
          await state.apply(page);
          assert.deepStrictEqual(syntaxErrors.map(String), []);
          await assertNoOverflow(page, `/ [${state.name}] @ ${width}px`);
        } finally {
          await page.close();
        }
      });
    }
  }
});

// Regression: at 320px the filter date row overflowed its clipped container
// (right edge ~337px), truncating the "to" date input — invisible to the
// page-level scrollWidth walk above (the panel clips instead of scrolling
// the page), so the fixed elements get a scoped bounding-rect assert.
test('filter date inputs fit the viewport when the panel is open', async (t) => {
  for (const width of WIDTHS) {
    await t.test(`/ [filter-panel-open] date inputs @ ${width}px`, async () => {
      const page = await browser.newPage();
      try {
        await loadAt(page, '/', width, '#games-container table');
        await page.click('#mobile-filter-toggle');
        await page.waitForFunction(
          () => document.getElementById('filter-panel').classList.contains('open'),
        );
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
            `#${r.id} @ ${width}px: [${r.left}, ${r.right}] outside viewport 0..${m.innerWidth}`,
          );
        }
      } finally {
        await page.close();
      }
    });
  }
});
