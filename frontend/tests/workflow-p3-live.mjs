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
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: '打开报告' }).click(),
  ]);
  await popup.waitForLoadState();
  const reportText = await popup.locator('body').innerText();
  for (const text of ['P3 UI report', 'ViennaPS', 'process window selected', '打印 / 保存为 PDF', 'metric_', 'measurement']) {
    assert.ok(reportText.includes(text), `report must contain ${text}`);
  }
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
