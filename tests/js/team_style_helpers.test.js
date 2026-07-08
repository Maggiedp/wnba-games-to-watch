const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadHelpers } = require('./helpers');

const { buildRadarSvg, ordinal } = loadHelpers('team_style_helpers.js');

test('ordinal formats ranks', () => {
  assert.equal(ordinal(1), '1st');
  assert.equal(ordinal(2), '2nd');
  assert.equal(ordinal(3), '3rd');
  assert.equal(ordinal(4), '4th');
  assert.equal(ordinal(11), '11th');
  assert.equal(ordinal(12), '12th');
  assert.equal(ordinal(13), '13th');
  assert.equal(ordinal(22), '22nd');
});

test('buildRadarSvg emits an SVG with a 6-point polygon', () => {
  const axes = [
    { norm: 100 }, { norm: 50 }, { norm: 8 },
    { norm: 75 }, { norm: 25 }, { norm: 60 },
  ];
  const svg = buildRadarSvg(axes);
  assert.ok(svg.startsWith('<svg'));
  const data = svg.match(/class="radar-shape" points="([^"]+)"/);
  assert.ok(data, 'data polygon present');
  assert.equal(data[1].trim().split(/\s+/).length, 6);
});

test('buildRadarSvg pushes higher norms further from center', () => {
  const near = buildRadarSvg([{ norm: 8 }, { norm: 8 }, { norm: 8 },
    { norm: 8 }, { norm: 8 }, { norm: 8 }]);
  const far = buildRadarSvg([{ norm: 100 }, { norm: 100 }, { norm: 100 },
    { norm: 100 }, { norm: 100 }, { norm: 100 }]);
  const yTopNear = Number(near.match(/class="radar-shape" points="[\d.]+,([\d.]+)/)[1]);
  const yTopFar = Number(far.match(/class="radar-shape" points="[\d.]+,([\d.]+)/)[1]);
  // Top vertex (i=0, angle -90deg) sits higher (smaller y) when norm is larger.
  assert.ok(yTopFar < yTopNear);
});

test('buildRadarSvg labels each spoke from the axis key', () => {
  const axes = [
    { key: 'pace', norm: 50 }, { key: 'three_pa_rate', norm: 50 },
    { key: 'ft_rate', norm: 50 }, { key: 'oreb_pct', norm: 50 },
    { key: 'assist_rate', norm: 50 }, { key: 'def_pressure', norm: 50 },
  ];
  const svg = buildRadarSvg(axes);
  for (const label of ['Pace', '3PA', 'FT', 'OREB', 'AST', 'Def']) {
    assert.ok(svg.includes(`>${label}</text>`), `has ${label} label`);
  }
});
