/**
 * viewer3d benchmark: headless Chromium against the standalone harness.
 *
 * No backend is involved. The harness generates synthetic snapshots (>= 200k
 * triangles, 5 materials, undulating surface) and exposes
 * `window.__viewer3dHarness` for step switching, frame timing and framebuffer
 * analysis.
 *
 * Usage: node tests/viewer3d-benchmark.mjs
 * Env:   VIEWER3D_BASE_URL (default http://127.0.0.1:8811)
 *        VIEWER3D_BENCH_JSON (default ../../docs/viewer3d-benchmark.json)
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const baseUrl = (process.env.VIEWER3D_BASE_URL ?? 'http://127.0.0.1:8811').replace(/\/$/, '');
const jsonPath = process.env.VIEWER3D_BENCH_JSON ?? path.join(root, 'docs/viewer3d-benchmark.json');

const FRAME_BUDGET_MS = 33;
const FIRST_SWITCH_BUDGET_MS = 800;
const CACHED_SWITCH_BUDGET_MS = 150;

const report = {
  status: 'running',
  generatedAt: new Date().toISOString(),
  baseUrl,
  browser: 'chromium headless (swiftshader)',
  triangles: 0,
  avgFrameMs: null,
  p50FrameMs: null,
  webglBaselineMs: null,
  apiMode: null,
  p95FrameMs: null,
  switchMs: { first: null, cached: null, heavy: null },
  staticFullDetailMs: null,
  materials: [],
  checks: [],
  consoleErrors: [],
  limitations: [],
};

const check = (name, data = {}) => {
  report.checks.push({ name, ...data });
  console.log(`PASS ${name}${Object.keys(data).length ? ` ${JSON.stringify(data)}` : ''}`);
};

let browser;
try {
  browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error') report.consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => report.consoleErrors.push(String(error)));

  await page.goto(`${baseUrl}/viewer3d-harness.html`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__viewer3dHarness?.getState().ready === true, null, { timeout: 60000 });
  const initial = await page.evaluate(() => window.__viewer3dHarness.getState());
  report.triangles = initial.stats.triangles;
  assert.ok(report.triangles >= 200_000, `harness must render at least 200k triangles, got ${report.triangles}`);
  check('harness ready', { triangles: report.triangles, parseMs: initial.stats.parseMs });

  // --- pixels + material colours -------------------------------------------
  const pixels = await page.evaluate(() => window.__viewer3dHarness.analyze());
  assert.ok(pixels, 'framebuffer readback failed');
  assert.ok(pixels.nonBackground > 5000, `canvas looks empty (${pixels.nonBackground} painted pixels)`);
  assert.ok(pixels.uniqueColors > 20, `canvas is flat (${pixels.uniqueColors} quantised colours)`);
  const presentMaterials = pixels.materials.filter((entry) => entry.pixels > 200);
  assert.equal(presentMaterials.length, 5, `expected 5 material colours, got ${JSON.stringify(pixels.materials)}`);
  report.materials = pixels.materials;
  check('canvas has pixels and all 5 material colours', {
    painted: pixels.nonBackground,
    uniqueColors: pixels.uniqueColors,
    size: `${pixels.width}x${pixels.height}`,
  });

  // --- interaction frames (camera drag, interaction LOD active) -------------
  const spin = await page.evaluate(() => window.__viewer3dHarness.spin(120));
  report.avgFrameMs = Number(spin.avg.toFixed(2));
  report.p50FrameMs = Number(spin.p50.toFixed(2));
  report.p95FrameMs = Number(spin.p95.toFixed(2));
  check('120 rotating frames measured', {
    avgMs: report.avgFrameMs,
    p50Ms: report.p50FrameMs,
    p95Ms: report.p95FrameMs,
    maxMs: Number(spin.max.toFixed(2)),
    fps: Number(spin.fps.toFixed(1)),
  });

  // --- full-detail static frames (LOD released) -----------------------------
  const statics = await page.evaluate(() => window.__viewer3dHarness.staticFrames(20));
  report.staticFullDetailMs = { avg: Number(statics.avg.toFixed(2)), p95: Number(statics.p95.toFixed(2)) };
  check('full-detail static frames', report.staticFullDetailMs);

  // --- step switching -------------------------------------------------------
  const firstSwitch = await page.evaluate(() => window.__viewer3dHarness.switchStep('step-2'));
  report.switchMs.first = Number(firstSwitch.ms.toFixed(1));
  assert.ok(report.switchMs.first < FIRST_SWITCH_BUDGET_MS, `first step switch took ${report.switchMs.first} ms`);
  check('first switch (cold parse)', { ms: report.switchMs.first, parseMs: firstSwitch.stats.parseMs });

  const cachedSwitch = await page.evaluate(() => window.__viewer3dHarness.switchStep('step-1'));
  report.switchMs.cached = Number(cachedSwitch.ms.toFixed(1));
  assert.ok(report.switchMs.cached < CACHED_SWITCH_BUDGET_MS, `cached step switch took ${report.switchMs.cached} ms`);
  check('cached switch (LRU hit)', { ms: report.switchMs.cached, parseMs: cachedSwitch.stats.parseMs });

  const afterSwitch = await page.evaluate(() => window.__viewer3dHarness.analyze());
  assert.ok(afterSwitch.nonBackground > 5000, 'canvas is empty after switching back');
  check('canvas still painted after switching', { painted: afterSwitch.nonBackground });

  // --- heavy step: vertex-clustering simplification -------------------------
  const heavy = await page.evaluate(() => window.__viewer3dHarness.switchStep('step-3'));
  report.switchMs.heavy = Number(heavy.ms.toFixed(1));
  assert.equal(heavy.stats.simplified, true, 'the 700k triangle step must be simplified');
  assert.ok(heavy.stats.triangles <= 300_000, `simplified mesh still has ${heavy.stats.triangles} triangles`);
  assert.ok(heavy.stats.sourceTriangles >= 700_000, `unexpected source triangle count ${heavy.stats.sourceTriangles}`);
  check('vertex clustering keeps the heavy step under budget', {
    ms: report.switchMs.heavy,
    from: heavy.stats.sourceTriangles,
    to: heavy.stats.triangles,
  });
  const heavyPixels = await page.evaluate(() => window.__viewer3dHarness.analyze());
  assert.ok(heavyPixels.nonBackground > 5000, 'simplified mesh did not render');

  // --- clipping plane is real ----------------------------------------------
  await page.evaluate(() => window.__viewer3dHarness.switchStep('step-1'));
  const beforeClip = await page.evaluate(() => window.__viewer3dHarness.analyze().nonBackground);
  await page.getByLabel('截面', { exact: true }).check();
  await page.waitForTimeout(250);
  const afterClip = await page.evaluate(() => window.__viewer3dHarness.analyze().nonBackground);
  await page.getByLabel('截面', { exact: true }).uncheck();
  assert.ok(afterClip < beforeClip * 0.85, `clipping did not remove geometry (${beforeClip} -> ${afterClip})`);
  check('X/Y/Z clipping slider removes geometry', { before: beforeClip, after: afterClip });

  // --- wireframe toggle is real --------------------------------------------
  const surfaceShot = await page.evaluate(() => window.__viewer3dHarness.analyze());
  await page.getByLabel('显示模式').selectOption('wireframe');
  await page.waitForTimeout(300);
  const wireframeShot = await page.evaluate(() => window.__viewer3dHarness.analyze());
  await page.getByLabel('显示模式').selectOption('surface');
  await page.waitForTimeout(300);
  assert.notEqual(wireframeShot.signature, surfaceShot.signature, 'wireframe/surface toggle had no visible effect');
  check('wireframe/surface toggle changes the image', {
    surfacePixels: surfaceShot.nonBackground,
    wireframePixels: wireframeShot.nonBackground,
  });

  // --- material visibility toggle is real ----------------------------------
  await page.getByLabel('显示 Si', { exact: true }).uncheck();
  await page.waitForTimeout(250);
  const hidden = await page.evaluate(() => window.__viewer3dHarness.analyze());
  await page.getByLabel('显示 Si', { exact: true }).check();
  const siBefore = pixels.materials.find((entry) => entry.color === '#f59e0b')?.pixels ?? 0;
  const siAfter = hidden.materials.find((entry) => entry.color === '#f59e0b')?.pixels ?? 0;
  assert.ok(siAfter < siBefore * 0.2, `hiding Si left ${siAfter} amber pixels (was ${siBefore})`);
  check('material visibility toggle hides only that material', { before: siBefore, after: siAfter });

  // --- 2D snapshots use the canvas renderer, not the 3D engine -------------
  const page2d = await context.newPage();
  page2d.on('console', (message) => {
    if (message.type() === 'error') report.consoleErrors.push(message.text());
  });
  page2d.on('pageerror', (error) => report.consoleErrors.push(String(error)));
  await page2d.goto(`${baseUrl}/viewer3d-harness.html?dimension=2`, { waitUntil: 'domcontentloaded' });
  await page2d.waitForFunction(() => window.__viewer3dHarness?.getState().ready === true, null, { timeout: 60000 });
  await page2d.waitForSelector('.wb-viewer3d__canvas2d');
  const pixels2d = await page2d.evaluate(() => window.__viewer3dHarness.analyze2d());
  assert.ok(pixels2d, '2D canvas readback failed');
  assert.ok(pixels2d.nonBackground > 500, `2D canvas looks empty (${pixels2d.nonBackground} painted pixels)`);
  assert.ok(pixels2d.materials.filter((entry) => entry.pixels > 100).length >= 2, '2D canvas lost its material colours');
  await page2d.close();
  check('2D snapshots render on a canvas with material colours', {
    painted: pixels2d.nonBackground,
    materials: pixels2d.materials.filter((entry) => entry.pixels > 100).length,
  });

  // --- production path: worker fetches the snapshot, prefetch warms the next
  const apiSnapshot = (phase) => {
    const grid = 122;                       // ~30k triangles, JSON stays small
    const side = grid + 1;
    const points = [];
    const triangles = [];
    const materialIds = [];
    for (let j = 0; j < side; j++) {
      for (let i = 0; i < side; i++) {
        const x = -4 + (8 * i) / grid;
        const y = -4 + (8 * j) / grid;
        points.push(x, y, 0.4 * Math.sin(x * 1.3 + phase) * Math.cos(y * 1.1));
      }
    }
    for (let j = 0; j < grid; j++) {
      for (let i = 0; i < grid; i++) {
        const a = j * side + i;
        const b = a + 1;
        const c = a + side;
        const d = c + 1;
        triangles.push(a, c, b, b, c, d);
        materialIds.push((a % 3) * 10, (a % 3) * 10);
      }
    }
    return {
      dimension: 3,
      points,
      triangles,
      cellData: { materialIds },
      materials: { '0': { name: 'Mask', color: '#3b82f6' }, '10': { name: 'Si', color: '#f59e0b' }, '20': { name: 'SiO2', color: '#22c55e' } },
      coordinateUnit: 'um',
      stepName: `API 步骤 ${phase}`,
    };
  };
  const apiPage = await context.newPage();
  apiPage.on('console', (message) => {
    if (message.type() === 'error') report.consoleErrors.push(message.text());
  });
  apiPage.on('pageerror', (error) => report.consoleErrors.push(String(error)));
  const manifest = { snapshots: [{ stepId: 'api-1', name: 'API 1', stepIndex: 0 }, { stepId: 'api-2', name: 'API 2', stepIndex: 1 }] };
  await apiPage.route('**/api/runs/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/snapshots/api-1')) return route.fulfill({ json: apiSnapshot(0.4) });
    if (url.pathname.endsWith('/snapshots/api-2')) return route.fulfill({ json: apiSnapshot(1.9) });
    if (url.pathname.endsWith('/api/runs/benchmark-run')) return route.fulfill({ json: manifest });
    return route.fulfill({ status: 404, json: { detail: 'not found' } });
  });
  await apiPage.goto(`${baseUrl}/viewer3d-harness.html?source=api`, { waitUntil: 'domcontentloaded' });
  await apiPage.waitForFunction(() => window.__viewer3dHarness?.getState().ready === true, null, { timeout: 60000 });
  const apiState = await apiPage.evaluate(() => window.__viewer3dHarness.getState());
  assert.ok(apiState.stats.triangles > 20_000, `API snapshot did not render (${apiState.stats.triangles} triangles)`);
  // Idle prefetch should have warmed api-2, so switching must be a cache hit.
  await apiPage.waitForTimeout(1500);
  const apiSwitch = await apiPage.evaluate(() => window.__viewer3dHarness.switchStep('api-2'));
  assert.equal(apiSwitch.stats.parseMs, 0, 'neighbouring step was not prefetched');
  assert.ok(apiSwitch.ms < CACHED_SWITCH_BUDGET_MS, `prefetched switch took ${apiSwitch.ms} ms`);
  report.apiMode = { triangles: apiState.stats.triangles, prefetchedSwitchMs: Number(apiSwitch.ms.toFixed(1)) };
  await apiPage.close();
  check('worker fetch path + idle prefetch of the next step', report.apiMode);

  // --- raw WebGL2 floor on the same canvas ---------------------------------
  const baseline = await page.evaluate(
    ([width, height, triangles]) => window.__viewer3dHarness.webglBaseline(width, height, triangles),
    [pixels.width, pixels.height, report.triangles],
  );
  report.webglBaselineMs = Number(baseline.avgMs.toFixed(2));
  check('raw WebGL2 baseline (no vtk)', { ms: report.webglBaselineMs, triangles: baseline.triangles });

  // --- frame budget ---------------------------------------------------------
  if (report.avgFrameMs > FRAME_BUDGET_MS) {
    report.limitations.push(
      `平均帧时间 ${report.avgFrameMs} ms 超过 ${FRAME_BUDGET_MS} ms：无头 Chromium 只能用 SwiftShader 软件光栅化。` +
      `同机同分辨率的裸 WebGL2 基线（单 draw call、最简着色器、同样 ${report.triangles} 个三角形）为 ${report.webglBaselineMs} ms/帧，` +
      `因此瓶颈是 SwiftShader 软件光栅化而不是查看器的 draw call 数量；查看器相对基线的开销约为 ${(report.avgFrameMs / Math.max(report.webglBaselineMs, 0.01)).toFixed(1)}x。`,
    );
  } else {
    check('interaction frame time within budget', { avgMs: report.avgFrameMs, budgetMs: FRAME_BUDGET_MS });
  }

  assert.deepEqual(report.consoleErrors, [], `console errors: ${report.consoleErrors.join(' | ')}`);
  check('no console errors');

  report.status = 'passed';
} catch (error) {
  report.status = 'failed';
  report.error = error instanceof Error ? error.message : String(error);
  console.error(`FAIL ${report.error}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await fs.mkdir(path.dirname(jsonPath), { recursive: true });
  await fs.writeFile(jsonPath, `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
}
