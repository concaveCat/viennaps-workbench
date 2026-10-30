/** IA batch 3 Chromium acceptance: 04 experiments, 05 analysis, 06 report.
 *
 * Review items covered here:
 *  - 04/05/06 empty states without a project, with real next-step buttons and no
 *    full input tables / disabled-looking primary flows;
 *  - a 3-variant experiment with 2 replicates per variant, result-first layout,
 *    every replicate visible plus mean/std and Chinese status;
 *  - 05 auto-loads the finished experiment's candidates (condition labels),
 *    Chinese headers, axes/scale/legend per candidate view;
 *  - 06 blocks generation without a run/measurement with a visible reason, then
 *    generates a report containing the structure SVG and result tables, with the
 *    title/conclusions form above the generate button.
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const baseUrl = (process.env.WORKBENCH_URL ?? 'http://127.0.0.1:8812').replace(/\/$/, '');
const projectPath = process.env.WORKFLOW_PROJECT ?? path.join(here, 'fixtures', 'workflow-2d-five.json');
const screenshot = process.env.WORKFLOW_SCREENSHOT ?? '/tmp/workflow-ia-batch3.png';

const getJson = async endpoint => {
  const response = await fetch(`${baseUrl}${endpoint}`);
  assert.ok(response.ok, `${endpoint} -> ${response.status}`);
  return response.json();
};

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
page.setDefaultTimeout(60000);
const errors = [];
page.on('pageerror', error => errors.push(error.message));

try {
  await page.goto(`${baseUrl}/`);
  await page.getByText('ViennaPS Workbench').first().waitFor();

  // ---- Empty states: no project yet, so 04/05/06 must not render their forms.
  await page.getByRole('button', { name: '04 实验', exact: true }).click();
  await page.locator('[aria-label="实验空态"]').waitFor();
  assert.match(await page.locator('[aria-label="实验空态"]').innerText(), /还不能创建实验/);
  assert.equal(await page.getByLabel('变体 1 duration').count(), 0, 'no project must not render the 10-row variant table');
  assert.equal(await page.locator('[aria-label="变体输入表"]').count(), 0);
  await page.getByRole('button', { name: '去 01 新建工程' }).waitFor();

  await page.getByRole('button', { name: '05 分析', exact: true }).click();
  await page.locator('[aria-label="分析空态"]').waitFor();
  assert.equal(await page.locator('.wf-analysis-advanced').count(), 0, 'empty analysis must not render process window / calibration');
  assert.equal(await page.getByLabel('窗口下限').count(), 0);
  await page.getByRole('button', { name: '去 04 创建实验' }).waitFor();

  await page.getByRole('button', { name: '06 报告', exact: true }).click();
  await page.locator('[aria-label="报告禁用原因"]').waitFor();
  assert.match(await page.locator('[aria-label="报告禁用原因"]').innerText(), /先完成 03 运行与测量/);
  assert.equal(await page.getByRole('button', { name: '生成报告', exact: true }).isDisabled(), true);
  assert.equal(await page.locator('iframe.wf-report-preview').count(), 0, 'no empty report preview');

  // ---- Load a project, compile and route, but do not run: 06 stays blocked.
  await page.getByRole('button', { name: '01 工程', exact: true }).click();
  await page.locator('input[type=file]').nth(1).setInputFiles(projectPath);
  await page.getByRole('button', { name: '去 02 定义流程' }).click();
  await page.getByRole('button', { name: '编译流程' }).click();
  await page.getByText(/编译成功/).waitFor();
  await page.getByRole('button', { name: '创建 Route', exact: true }).click();
  await page.getByRole('button', { name: '06 报告', exact: true }).click();
  await page.locator('[aria-label="报告禁用原因"]').waitFor();
  assert.equal(await page.getByRole('button', { name: '生成报告', exact: true }).isDisabled(), true, 'no run yet => report disabled');

  // ---- Run and measure so 04 has a pinned recipe.
  await page.getByRole('button', { name: '03 运行', exact: true }).click();
  await page.getByRole('button', { name: '执行全部 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 120000 });
  await page.getByRole('button', { name: '测量', exact: true }).click();
  await page.getByText(/绑定 \(binding\)：已绑定/).first().waitFor();

  // ---- 04: named 3-variant experiment, 2 replicates each.
  await page.getByRole('button', { name: '04 实验', exact: true }).click();
  await page.getByLabel('实验名称').fill('IA3 三变体实验');
  assert.equal(await page.locator('[aria-label="变体输入表"] tbody tr').count(), 3, 'default variant count is 3');
  for (const index of [1, 2, 3]) {
    await page.getByLabel(`变体 ${index} replicates`).fill('2');
  }
  const conditionSummary = await page.locator('[aria-label="变体输入表"] tbody tr').first().innerText();
  assert.match(conditionSummary, /V1 · 0\.01 s · seed 20261029 · ×2/);
  await page.getByRole('button', { name: '创建实验', exact: true }).click();
  await page.getByText('实验状态：待执行').waitFor();
  assert.equal(await page.locator('.wf-result-table tbody tr').count(), 3);
  await page.getByRole('button', { name: '运行实验', exact: true }).click();
  await page.getByText('实验状态：已完成').waitFor({ timeout: 240000 });

  // Results first: 3 rows, 2 repeats each, Chinese headers/statuses, sane precision.
  assert.equal(await page.locator('.wf-result-table tbody tr').count(), 3);
  assert.equal(await page.locator('.wf-result-table .wf-repeat').count(), 6, 'every replicate must be shown');
  const resultHeaders = await page.locator('.wf-result-table thead').innerText();
  for (const header of ['条件', '重复结果', '均值', '标准差', '状态']) {
    assert.ok(resultHeaders.includes(header), `result header must be Chinese: ${header}`);
  }
  const resultText = await page.locator('.wf-result-table').innerText();
  assert.doesNotMatch(resultText, /succeeded|planned|running/, 'statuses must be Chinese');
  assert.doesNotMatch(resultText, /\d\.\d{8,}/, 'numbers must not leak float noise');
  assert.match(resultText, /成功/);

  const experimentSummaries = (await getJson('/api/v1/workflow/experiments')).experiments;
  const experiment = await getJson(`/api/v1/workflow/experiments/${experimentSummaries[0].experimentId}`);
  assert.equal(experiment.variants.length, 3);
  assert.equal(experiment.variants[0].replicates.length, 2);
  assert.ok(experiment.variants.every(variant => variant.replicates.every(replicate => replicate.runId)));

  // ---- 05: candidates arrive automatically with condition labels.
  await page.getByRole('button', { name: '在 05 分析' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.wf-compare-canvas').length === 3, null, { timeout: 120000 });
  assert.equal(await page.locator('.wf-compare-grid figure').count(), 3);
  const candidateCount = await page.locator('.wf-compare-grid figure').count();
  const caption = await page.locator('.wf-compare-grid figcaption').first().innerText();
  assert.match(caption, /V1 · 0\.01 s · seed \d+/);
  const conditionChips = await page.locator('.wf-condition-chips').innerText();
  assert.equal(await page.locator('.wf-condition-chip').count(), 3);
  assert.match(conditionChips, /V2 · 0\.02 s · seed/);
  const compareHeaders = await page.locator('[aria-label="候选对比表"] thead').innerText();
  for (const header of ['对比', '状态', '最大点偏差', '边界偏差', '材料偏差', '原因']) {
    assert.ok(compareHeaders.includes(header), `compare header must be Chinese: ${header}`);
  }
  const compareText = await page.locator('[aria-label="候选对比表"]').innerText();
  assert.doesNotMatch(compareText, /WITHIN_TOLERANCE|OUTSIDE_TOLERANCE|NOT_EVALUATED/);
  assert.ok((await page.locator('.wf-compare-canvas .wf-axes text').count()) > 0, 'each candidate view must draw axes/scale');
  const legend = await page.locator('.wf-compare-legend').first().innerText();
  assert.match(legend, /Si|材料/);
  assert.equal(await page.locator('.wf-analysis-advanced[open]').count(), 0, 'process window / calibration stay folded by default');

  // ---- 06: form above the button, then a report with structure + results.
  await page.getByRole('button', { name: '06 报告', exact: true }).click();
  const titleBox = await page.getByLabel('报告标题').boundingBox();
  const conclusionsBox = await page.getByLabel('报告结论').boundingBox();
  const generateBox = await page.getByRole('button', { name: '生成报告', exact: true }).boundingBox();
  assert.ok(titleBox && conclusionsBox && generateBox, 'report form must be visible');
  assert.ok(titleBox.y < generateBox.y && conclusionsBox.y < generateBox.y, 'title/conclusions must sit above the generate button');

  await page.getByLabel('报告标题').fill('IA3 报告');
  await page.getByLabel('报告结论').fill('选择 V2 · 0.02 s，CD 在公差内。');
  await page.getByRole('button', { name: '生成报告', exact: true }).click();
  await page.locator('.wf-report-result').filter({ hasText: 'IA3 报告' }).waitFor({ timeout: 120000 });
  assert.match(await page.locator('.wf-report-result').innerText(), /已生成/);
  assert.match(await page.locator('.wf-report-conclusions').innerText(), /选择 V2/);

  const summaryText = await page.locator('[aria-label="报告内容摘要"]').innerText();
  for (const text of ['P1 2D five-step fixture', 'Route', '测量', '实验']) {
    assert.ok(summaryText.includes(text), `report summary must contain ${text}`);
  }
  const frame = page.frameLocator('iframe.wf-report-preview');
  await frame.locator('body').waitFor();
  const reportText = await frame.locator('body').innerText();
  for (const text of ['IA3 报告', 'IA3 三变体实验', '选择 V2', '结论', '视图']) {
    assert.ok(reportText.includes(text), `report must contain ${text}`);
  }
  assert.ok((await frame.locator('svg.report-svg').count()) >= 1, 'report must embed the structure view');
  assert.match(reportText, /均值|mean/, 'report must contain the experiment result table');

  const reportRow = page.locator('.spx-tree-item').filter({ hasText: '报告' }).first();
  const treeText = await reportRow.innerText();
  assert.match(treeText, /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
  assert.equal((treeText.match(/已生成/g) ?? []).length, 1, 'report status appears once, in the badge');
  assert.doesNotMatch(treeText, /report_[a-f0-9]{8,}/, 'left tree must not lead with a report id');

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 HTML' }).click();
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), 'IA3 报告.html');

  await page.screenshot({ path: screenshot, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    status: 'passed', baseUrl, screenshot, errors,
    experiment: { variants: experiment.variants.length, replicates: experiment.variants[0].replicates.length },
    candidates: candidateCount,
  }));
} finally {
  await browser.close();
}
