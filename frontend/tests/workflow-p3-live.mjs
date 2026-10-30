/** P3 Chromium acceptance: synced candidate views, process window, calibration, report. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const baseUrl = (process.env.WORKBENCH_URL ?? 'http://127.0.0.1:8816').replace(/\/$/, '');
const projectPath = process.env.WORKFLOW_PROJECT ?? path.join(here, 'fixtures', 'workflow-2d-etch.json');
const calibrationPath = process.env.WORKFLOW_CALIBRATION ?? path.join(here, 'fixtures', 'calibration.csv');
const screenshot = process.env.WORKFLOW_SCREENSHOT ?? '/tmp/workflow-p3.png';

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1500 } });
page.setDefaultTimeout(60000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));

try {
  await page.goto(`${baseUrl}/`);
  await page.getByText('ViennaPS Workbench').first().waitFor();
  await page.locator('input[type=file]').nth(1).setInputFiles(projectPath);
  await page.getByRole('button', { name: '去 02 定义流程' }).click();
  await page.getByRole('button', { name: '编译流程' }).click();
  await page.getByText(/编译成功/).waitFor();
  await page.getByRole('button', { name: /^(创建 Route|去 03)$/ }).click();
  await page.getByRole('button', { name: '执行全部 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 60000 });
  await page.getByRole('button', { name: '测量', exact: true }).click();
  await page.getByText(/绑定 \(binding\)：已绑定/).first().waitFor();

  // 03 structure timeline: the initial structure and every executed step are
  // visible chips, not a hidden dropdown. Switching back to the initial
  // structure keeps the 2D canvas and the measurement marker alive.
  await page.locator('.wf-structure-timeline').waitFor();
  const stepChips = page.locator('.wf-step-chip');
  assert.ok(await stepChips.count() >= 2, 'timeline must show the initial structure plus at least one step');
  const initialChip = page.locator('.wf-step-chip[data-step-id="initial"]').first();
  await initialChip.waitFor();
  await initialChip.click();
  await page.locator('.wf-structure-canvas').waitFor();
  await page.locator('.wf-marker').first().waitFor();
  assert.equal(await initialChip.getAttribute('aria-selected'), 'true');
  assert.ok(await page.locator('.wf-structure-legend i').count() >= 1, '2D legend must stay visible after switching steps');

  // 2D structure readability: material fill under the interface line, a real
  // height axis with ticks, and no fake XY/XZ/YZ selector on a 2D snapshot.
  const structureSvg = page.locator('.wf-structure-canvas').first();
  assert.ok(await structureSvg.locator('polygon.wf-fill').count() >= 1, '2D structure must fill the material below the surface');
  const structureMarkup = await structureSvg.innerHTML();
  assert.ok(structureMarkup.includes('高度 (µm)'), '2D structure must label the height axis in µm');
  assert.ok(structureMarkup.includes('X (µm)'), '2D structure must label the X axis in µm');
  assert.ok((await structureSvg.locator('.wf-tick').count()) >= 3, '2D structure must draw at least 3 ticks');
  assert.match(structureMarkup, /表面高度|深度/, '2D structure must annotate the surface height or etched depth');
  assert.equal(await page.locator('select[aria-label="结构视图轴"]').count(), 0, '2D must not offer XY/XZ/YZ axes');
  assert.equal((await page.getByLabel('结构视图轴').innerText()).trim(), 'X – 高度');

  // The material fill must hang *below* the interface line: every fill vertex
  // is at or under the surface (larger SVG y) and the polygon reaches the
  // bottom of the view instead of closing at the top.
  const fillGeometry = await structureSvg.evaluate(svg => {
    const box = svg.viewBox.baseVal;
    const line = svg.querySelector('.wf-interface');
    const surfaceY = line
      ? Math.max(Number(line.getAttribute('y1')), Number(line.getAttribute('y2')))
      : null;
    // The plot area is smaller than the viewBox: titles, ticks and the scale
    // bar live in the margins (N7).
    const axisLines = Array.from(svg.querySelectorAll('.wf-axis-line line'));
    const plotBottom = axisLines[0] ? Number(axisLines[0].getAttribute('y1')) : box.y + box.height;
    const plotTop = axisLines[1] ? Number(axisLines[1].getAttribute('y2')) : box.y;
    const polygons = Array.from(svg.querySelectorAll('polygon.wf-fill')).map(node =>
      (node.getAttribute('points') ?? '').trim().split(/\s+/).map(pair => pair.split(',').map(Number)));
    return { top: box.y, height: box.height, surfaceY, plotTop, plotBottom, polygons };
  });
  assert.ok(fillGeometry.polygons.length >= 1, '2D fill polygon must exist');
  assert.ok(fillGeometry.surfaceY !== null, '2D interface line must exist');
  const plotHeight = fillGeometry.plotBottom - fillGeometry.plotTop;
  assert.ok(plotHeight > 0, '2D plot area must have a height');
  for (const polygon of fillGeometry.polygons) {
    const ys = polygon.map(([, y]) => y);
    assert.ok(Math.min(...ys) >= fillGeometry.surfaceY - 1e-6,
      `fill must start at the surface line (min y ${Math.min(...ys)} < surface ${fillGeometry.surfaceY})`);
    assert.ok(Math.min(...ys) > fillGeometry.plotTop + plotHeight * 0.1,
      'fill must not be drawn above the surface');
    assert.ok(Math.max(...ys) >= fillGeometry.plotBottom - plotHeight * 0.02,
      'fill must reach the bottom of the plot');
    assert.ok(Math.max(...ys) <= fillGeometry.plotBottom + 1e-6, 'fill must not extend past the plot bottom');
  }

  // ---- N7: axis titles, ticks and the scale bar never overlap ------------
  const structureLayout = await structureSvg.evaluate(svg => {
    const rect = selector => {
      const node = svg.querySelector(selector);
      if (!node) return null;
      const box = node.getBoundingClientRect();
      return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, text: node.textContent ?? '' };
    };
    const overlap = (a, b) => !!a && !!b
      && Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5
      && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
    const xTitle = rect('.wf-axis-title--x');
    const vTitle = rect('.wf-axis-title--v');
    const scaleLabel = rect('.wf-scale-label');
    const zeroTicks = Array.from(svg.querySelectorAll('.wf-tick-label')).filter(node => (node.textContent ?? '').trim() === '0');
    const vTicks = Array.from(svg.querySelectorAll('.wf-tick-label--v')).map(node => {
      const box = node.getBoundingClientRect();
      return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, text: node.textContent ?? '' };
    });
    const tickColor = getComputedStyle(svg.querySelector('.wf-tick-label')).fill;
    return {
      xTitle, vTitle, scaleLabel, vTicks, tickColor,
      zeroCount: zeroTicks.length,
      xTitleHitsScale: overlap(xTitle, scaleLabel),
      vTitleHitsTicks: vTicks.some(tick => overlap(vTitle, tick)),
      vTitleHitsXTitle: overlap(vTitle, xTitle),
    };
  });
  assert.ok(structureLayout.xTitle && structureLayout.scaleLabel, '2D axis title and scale bar must exist');
  assert.equal(structureLayout.xTitleHitsScale, false,
    `X (µm) must not overlap the scale bar (${JSON.stringify(structureLayout)})`);
  assert.equal(structureLayout.vTitleHitsTicks, false,
    `高度 (µm) must not overlap a tick label (${JSON.stringify(structureLayout)})`);
  assert.equal(structureLayout.vTitleHitsXTitle, false, 'the two axis titles must not overlap');
  assert.equal(structureLayout.zeroCount, 1, `the origin must be labelled once, got ${structureLayout.zeroCount}`);
  assert.equal(structureLayout.tickColor, 'rgb(38, 49, 58)', `tick text must stay dark, got ${structureLayout.tickColor}`);
  assert.equal(await structureSvg.locator('.wf-structure-note').count(), 1, 'the depth/surface note must stay visible');
  await page.screenshot({
    path: process.env.WORKFLOW_STRUCTURE_SCREENSHOT ?? '/tmp/workflow-p3-structure.png',
    fullPage: true,
  });

  await page.getByRole('button', { name: '04 实验', exact: true }).click();
  for (const index of [1, 2, 3]) {
    await page.getByLabel(`变体 ${index} replicates`).fill('2');
  }
  await page.getByRole('button', { name: '创建实验', exact: true }).click();
  await page.getByRole('button', { name: '运行实验', exact: true }).click();
  await page.getByText('实验状态：已完成').waitFor({ timeout: 180000 });
  assert.equal(await page.locator('.wf-result-table tbody tr').count(), 3);
  assert.equal(await page.locator('.wf-result-table .wf-repeat').count(), 6);

  // 05 loads the finished experiment's candidates on its own.
  await page.getByRole('button', { name: '05 分析', exact: true }).click();
  await page.locator('.wf-compare-canvas').first().waitFor({ timeout: 60000 });
  assert.equal(await page.locator('.wf-compare-grid figure').count(), 3);
  assert.equal(await page.locator('.wf-compare-canvas').count(), 3);
  const viewBoxes = await page.locator('.wf-compare-canvas').evaluateAll(nodes => nodes.map(node => node.getAttribute('viewBox')));
  assert.equal(new Set(viewBoxes).size, 1, 'all candidate views must share one synchronized viewBox');
  assert.equal(await page.locator('.wf-table').filter({ hasText: '最大点偏差' }).locator('tbody tr').count(), 3);
  assert.ok((await page.locator('.wf-compare-grid').innerText()).includes('µm'));
  assert.ok((await page.locator('.wf-compare-legend').first().innerText()).length > 0);
  await page.locator('.wf-compare-grid').screenshot({ path: process.env.WORKFLOW_COMPARE_SCREENSHOT ?? '/tmp/workflow-p3-compare.png' });

  await page.locator('.wf-analysis-advanced > summary').click();
  await page.getByLabel('窗口下限').fill('0.00015');
  await page.getByLabel('窗口上限').fill('0.0003');
  await page.getByRole('button', { name: '计算窗口' }).click();
  await page.locator('.wf-summary').filter({ hasText: '工艺窗口' }).waitFor();
  assert.match(await page.locator('.wf-summary').filter({ hasText: '工艺窗口' }).innerText(), /通过 \d+ · 超出 \d+/);

  await page.getByLabel('标定 CSV 文件').setInputFiles(calibrationPath);
  await page.getByRole('button', { name: '创建数据集' }).click();
  await page.getByText(/数据集 calibration/).waitFor();
  await page.getByRole('button', { name: '拟合' }).click();
  await page.getByText('已验证', { exact: true }).waitFor();
  assert.match(await page.locator('.wf-summary').filter({ hasText: 'scale_offset' }).innerText(), /a=1\.05/);

  await page.getByRole('button', { name: '06 报告', exact: true }).click();
  await page.getByLabel('报告标题').fill('P3 UI report');
  await page.getByLabel('报告结论').fill('process window selected');
  await page.getByRole('button', { name: '生成报告', exact: true }).click();
  await page.locator('.wf-report-result').filter({ hasText: 'P3 UI report' }).waitFor({ timeout: 60000 });
  assert.equal(await page.getByLabel('报告标题').isEditable(), true);
  assert.match(await page.locator('.wf-report-result').innerText(), /已生成/);
  assert.match(await page.locator('.wf-report-conclusions').innerText(), /process window selected/);
  const frame = page.frameLocator('iframe.wf-report-preview');
  await frame.locator('body').waitFor();
  assert.ok((await frame.locator('svg.report-svg').count()) >= 1, 'inline report preview must contain the structure view');
  // The preview grows with the report body so the structure section is on the
  // first screen instead of hidden behind a fixed 420 px frame.
  const structureHeading = frame.getByRole('heading', { name: '结构视图' });
  await structureHeading.waitFor();
  const headingBox = await structureHeading.boundingBox();
  const previewBox = await page.locator('iframe.wf-report-preview').boundingBox();
  assert.ok(headingBox && previewBox, 'report preview and structure heading must be laid out');
  assert.ok(headingBox.y + headingBox.height <= previewBox.y + previewBox.height + 1, 'structure heading must be visible inside the preview');
  assert.ok(previewBox.height > 520, `preview must grow with the report (${previewBox.height}px)`);
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: '打开报告' }).click(),
  ]);
  await popup.waitForLoadState();
  const reportText = await popup.locator('body').innerText();
  for (const text of ['P3 UI report', 'ViennaPS', 'process window selected', '打印 / 保存为 PDF', '结构视图', '结论']) {
    assert.ok(reportText.includes(text), `report must contain ${text}`);
  }
  assert.doesNotMatch(reportText, /\b(planned|running|succeeded|failed)\b/, 'report statuses must be Chinese');
  assert.ok((await popup.locator('svg.report-svg').count()) >= 1);
  await popup.close();

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 HTML' }).click();
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), 'P3 UI report.html');

  await page.screenshot({ path: screenshot, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', baseUrl, screenshot, errors, viewBox: viewBoxes[0] }));
} finally {
  await browser.close();
}
