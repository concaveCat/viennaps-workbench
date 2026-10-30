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
  await page.getByText('本机服务已连接').waitFor();
  await page.locator('input[type=file]').nth(1).setInputFiles(projectPath);
  await page.getByRole('button', { name: '采纳为 Workflow' }).click();
  await page.getByText('Compile 已冻结').waitFor();

  await page.getByRole('button', { name: '02 流程' }).click();
  await page.getByRole('button', { name: '创建首个 Route' }).click();
  await page.getByRole('button', { name: '03 运行' }).click();
  await page.getByRole('button', { name: '执行全部 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 60000 });
  await page.getByRole('button', { name: '按配方测量并绑定' }).click();
  await page.getByText(/binding: verified/).first().waitFor();

  await page.getByRole('button', { name: '04 实验' }).click();
  for (const index of [1, 2, 3]) {
    await page.getByLabel(`变体 ${index} replicates`).fill('2');
  }
  await page.getByRole('button', { name: '创建 10 变体实验' }).click();
  await page.getByRole('button', { name: '运行持久化实验' }).click();
  await page.getByText('Experiment status: completed').waitFor({ timeout: 180000 });

  await page.getByRole('button', { name: '05 分析' }).click();
  await page.getByRole('button', { name: '载入实验前 3 个候选' }).click();
  await page.getByRole('button', { name: '比较并加载视图' }).click();
  await page.locator('.wf-compare-canvas').first().waitFor({ timeout: 60000 });
  assert.equal(await page.locator('.wf-compare-grid figure').count(), 3);
  assert.equal(await page.locator('.wf-compare-canvas').count(), 3);
  const viewBoxes = await page.locator('.wf-compare-canvas').evaluateAll(nodes => nodes.map(node => node.getAttribute('viewBox')));
  assert.equal(new Set(viewBoxes).size, 1, 'all candidate views must share one synchronized viewBox');
  assert.equal(await page.locator('.wf-table').filter({ hasText: 'max point' }).locator('tbody tr').count(), 3);
  assert.ok((await page.locator('.wf-compare-grid').innerText()).includes('length_um'));

  await page.getByLabel('窗口下限').fill('0.00015');
  await page.getByLabel('窗口上限').fill('0.0003');
  await page.getByRole('button', { name: '计算窗口' }).click();
  await page.getByText(/^Window$/).waitFor();
  assert.match(await page.locator('.wf-summary').filter({ hasText: 'Window' }).innerText(), /pass \d+ · fail \d+/);

  await page.getByLabel('标定 CSV 文件').setInputFiles(calibrationPath);
  await page.getByRole('button', { name: '创建数据集' }).click();
  await page.getByText(/dataset calibration/).waitFor();
  await page.getByRole('button', { name: '拟合' }).click();
  await page.getByText('validated', { exact: true }).waitFor();
  assert.match(await page.locator('.wf-summary').filter({ hasText: 'scale_offset' }).innerText(), /a=1\.05/);

  await page.getByRole('button', { name: '06 报告' }).click();
  await page.getByLabel('报告标题').fill('P3 UI report');
  await page.getByLabel('报告结论').fill('process window selected');
  await page.getByRole('button', { name: '生成 HTML 报告' }).click();
  await page.getByText(/^report_[a-f0-9]{16}$/).waitFor({ timeout: 60000 });
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
  assert.match(download.suggestedFilename(), /^report_[a-f0-9]{16}\.html$/);

  await page.screenshot({ path: screenshot, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', baseUrl, screenshot, errors, viewBox: viewBoxes[0] }));
} finally {
  await browser.close();
}
