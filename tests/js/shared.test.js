const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { escapeHtml, isLiveStatus, isFinalStatus } = loadHelpers('shared.js');

test('escapeHtml neutralizes all five HTML metacharacters', () => {
  assert.equal(escapeHtml(`<img src=x onerror="alert('&')">`),
    '&lt;img src=x onerror=&quot;alert(&#39;&amp;&#39;)&quot;&gt;');
});

test('escapeHtml coerces non-strings', () => {
  assert.equal(escapeHtml(42), '42');
});

test('isLiveStatus covers in-progress, halftime, end-of-period', () => {
  for (const s of ['STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_END_PERIOD']) {
    assert.equal(isLiveStatus(s), true, s);
  }
});

test('isLiveStatus is false for final/scheduled/postponed', () => {
  for (const s of ['STATUS_FINAL', 'STATUS_SCHEDULED', 'STATUS_POSTPONED', undefined]) {
    assert.equal(isLiveStatus(s), false, String(s));
  }
});

test('isFinalStatus is STATUS_FINAL only', () => {
  assert.equal(isFinalStatus('STATUS_FINAL'), true);
  for (const s of ['STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_SUSPENDED', 'STATUS_IN_PROGRESS']) {
    assert.equal(isFinalStatus(s), false, s);
  }
});
