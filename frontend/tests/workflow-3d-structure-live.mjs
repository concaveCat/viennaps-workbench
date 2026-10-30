/**
 * 3D structure timeline acceptance (03 run).
 *
 * Covers the integration of the fast viewer3d into 03:
 *  - importing a 3D project, compiling, routing and executing it produces a
 *    visible "initial structure → step" timeline;
 *  - the 3D viewer renders the raw run snapshot (vtk.js canvas) with no console
 *    errors, and the canvas is not a flat single colour;
 *  - switching to the initial structure keeps the same viewer mounted;
 *  - the 02 step table's "结构" button jumps to 03 and selects that step;
 *  - the 03 segment row's "查看结构" button selects the segment's last step.
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const baseUrl = (process.env.WORKBENCH_URL ?? 'http://127.0.0.1:8861').replace(/\/$/, '');
const projectPath = process.env.WORKFLOW_PROJECT ?? path.join(here, 'fixtures', 'workflow-3d-plane.json');
const screenshot = process.env.WORKFLOW_SCREENSHOT ?? '/tmp/workflow-3d-structure.png';

/** Minimal 8-bit PNG decoder (Playwright screenshots are non-interlaced RGBA). */
function decodePng(buffer) {
  assert.equal(buffer.readUInt32BE(0), 0x89504e47, 'not a PNG');
  let offset = 8;
  let width = 0;
  let height = 0;
  let colorType = 0;
  const idat = [];
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString('ascii', offset + 4, offset + 8);
    const data = buffer.subarray(offset + 8, offset + 8 + length);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      assert.equal(data[8], 8, 'expected 8-bit PNG');
      assert.equal(data[12], 0, 'interlaced PNG is not supported');
      colorType = data[9];
    } else if (type === 'IDAT') {
      idat.push(data);
    } else if (type === 'IEND') {
      break;
    }
    offset += 12 + length;
  }
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : colorType === 0 ? 1 : 0;
  assert.ok(channels > 0, `unsupported PNG colour type ${colorType}`);
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const pixels = Buffer.alloc(width * height * channels);
  let position = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[position];
    position += 1;
    const line = raw.subarray(position, position + stride);
    position += stride;
    const previous = y > 0 ? pixels.subarray((y - 1) * stride, y * stride) : null;
    const current = pixels.subarray(y * stride, (y + 1) * stride);
    for (let x = 0; x < stride; x += 1) {
      const left = x >= channels ? current[x - channels] : 0;
      const up = previous ? previous[x] : 0;
      const upLeft = previous && x >= channels ? previous[x - channels] : 0;
      let value = line[x];
      if (filter === 1) value += left;
      else if (filter === 2) value += up;
      else if (filter === 3) value += Math.floor((left + up) / 2);
      else if (filter === 4) {
        const estimate = left + up - upLeft;
        const pa = Math.abs(estimate - left);
        const pb = Math.abs(estimate - up);
        const pc = Math.abs(estimate - upLeft);
        value += pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
      }
      current[x] = value & 0xff;
    }
  }
  return { width, height, channels, pixels };
}

function analyzePng(buffer) {
  const { width, height, channels, pixels } = decodePng(buffer);
  const counts = new Map();
  let background = 0;
  let backgroundKey = '';
  for (let i = 0; i < width * height; i += 1) {
    const r = pixels[i * channels];
    const g = pixels[i * channels + 1];
    const b = pixels[i * channels + 2];
    const key = `${r},${g},${b}`;
    const count = (counts.get(key) ?? 0) + 1;
    counts.set(key, count);
    if (count > background) {
      background = count;
      backgroundKey = key;
    }
  }
  const [br, bg, bb] = backgroundKey.split(',').map(Number);
  let painted = 0;
  const quantized = new Set();
  for (let i = 0; i < width * height; i += 1) {
    const r = pixels[i * channels];
    const g = pixels[i * channels + 1];
    const b = pixels[i * channels + 2];
    if (Math.abs(r - br) + Math.abs(g - bg) + Math.abs(b - bb) > 18) painted += 1;
    quantized.add(`${r >> 4},${g >> 4},${b >> 4}`);
  }
  return { width, height, painted, uniqueColors: quantized.size, backgroundKey };
}

const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1300 } });
page.setDefaultTimeout(90000);
const pageErrors = [];
const consoleErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') consoleErrors.push(message.text());
});

try {
  await page.goto(`${baseUrl}/`);
  await page.getByText('ViennaPS Workbench').first().waitFor();
  await page.locator('input[type=file]').nth(1).setInputFiles(projectPath);
  await page.getByRole('button', { name: '去 02 定义流程' }).click();
  await page.getByRole('button', { name: '编译流程' }).click();
  await page.getByText(/编译成功/).waitFor();
  await page.getByRole('button', { name: /^(创建 Route|去 03)$/ }).click();
  await page.getByRole('button', { name: '执行全部 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 90000 });

  // ---- 03 timeline + 3D canvas -------------------------------------------
  await page.locator('.wf-structure-timeline').waitFor();
  const chips = page.locator('.wf-step-chip');
  assert.ok(await chips.count() >= 2, 'timeline must contain the initial structure and the executed step');
  await page.locator('.wf-step-chip[data-step-id="initial"]').first().waitFor();
  await page.locator('.wb-viewer3d canvas').first().waitFor({ timeout: 90000 });
  await page.waitForFunction(() => {
    const el = document.querySelector('.wf-structure-3d-stats');
    return !!el && /三角形/.test(el.textContent ?? '');
  });
  const statsText = await page.locator('.wf-structure-3d-stats').innerText();
  const triangles = Number((statsText.match(/三角形\s*([\d,]+)/)?.[1] ?? '0').replace(/,/g, ''));
  assert.ok(triangles > 0, `3D viewer reported ${triangles} triangles`);
  assert.equal(await page.locator('.wf-structure-3d-stats').getByText('简化').count(), 0, 'a small plane must not be simplified');

  const stepChipId = await page.locator('.wf-step-chip.active').first().getAttribute('data-step-id');
  assert.ok(stepChipId && stepChipId !== 'initial', `default selection should be the executed step, got ${stepChipId}`);

  await page.locator('.wb-viewer3d__stage').screenshot({ path: screenshot.replace(/\.png$/, '-canvas.png') });
  const canvasPng = await page.locator('.wb-viewer3d__stage').screenshot();
  const canvasPixels = analyzePng(canvasPng);
  assert.ok(canvasPixels.painted > 500, `3D canvas looks blank (${canvasPixels.painted} painted pixels)`);
  assert.ok(canvasPixels.uniqueColors > 8, `3D canvas is flat (${canvasPixels.uniqueColors} quantized colours)`);

  // ---- initial structure keeps the viewer mounted -------------------------
  const initialChip = page.locator('.wf-step-chip[data-step-id="initial"]').first();
  await initialChip.click();
  await page.waitForFunction(() => document.querySelector('.wf-step-chip[data-step-id="initial"]')?.getAttribute('aria-selected') === 'true');
  await page.locator('.wb-viewer3d canvas').first().waitFor();
  await page.waitForFunction(() => !document.querySelector('.wb-viewer3d__overlay'));
  assert.match(await page.locator('.wf-structure-3d-stats').innerText(), /三角形/);
  assert.equal(await page.locator('.wf-structure-3d').count(), 1, 'the viewer must not be remounted when switching steps');

  // ---- 02 step row "结构" jumps back to the same step ---------------------
  await page.getByRole('button', { name: '02 流程', exact: true }).click();
  const stepRow = page.locator('table[aria-label="流程步骤"] tbody tr').filter({ hasText: 'etch' }).first();
  const structureButton = stepRow.getByRole('button', { name: '结构' });
  await structureButton.waitFor();
  assert.equal(await structureButton.isDisabled(), false, 'structure button must be enabled for an executed step');
  await page.screenshot({ path: screenshot.replace(/\.png$/, '-02.png'), fullPage: true });
  await structureButton.click();
  await page.locator('.wf-structure-timeline').waitFor();
  await page.waitForFunction(id => document.querySelector('.wf-step-chip.active')?.getAttribute('data-step-id') === id, stepChipId);

  // ---- 03 segment row "查看结构" selects the segment's through step -------
  await page.getByRole('button', { name: '02 流程', exact: true }).click();
  await page.getByRole('button', { name: '03 运行', exact: true }).click();
  await initialChip.click();
  const segmentButton = page.locator('.wf-segment-structure').first();
  await segmentButton.waitFor();
  assert.equal(await segmentButton.isDisabled(), false, 'segment structure button must be enabled after execution');
  await segmentButton.click();
  await page.waitForFunction(id => document.querySelector('.wf-step-chip.active')?.getAttribute('data-step-id') === id, stepChipId);
  assert.match(await page.locator('.wf-summary.success').filter({ hasText: 'Route 已创建' }).innerText(), /结构：/);

  await page.screenshot({ path: screenshot, fullPage: true });
  const benign = /favicon|Download the React DevTools/i;
  const consoleProblems = consoleErrors.filter(message => !benign.test(message));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleProblems, []);
  console.log(JSON.stringify({
    status: 'passed',
    baseUrl,
    screenshot,
    chips: await chips.count(),
    stepChipId,
    triangles,
    statsText,
    canvasPixels,
    consoleErrors: consoleProblems,
  }));
} finally {
  await browser.close();
}
