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
