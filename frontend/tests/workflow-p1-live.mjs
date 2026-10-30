/** P1 Chromium acceptance: recipe version, template variable, branch rerun, 10-variant experiment. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const baseUrl = (process.env.WORKBENCH_URL ?? 'http://127.0.0.1:8814').replace(/\/$/, '');
const projectPath = process.env.WORKFLOW_PROJECT ?? path.join(here, 'fixtures', 'workflow-2d-five.json');
const screenshot = process.env.WORKFLOW_SCREENSHOT ?? '/tmp/workflow-p1.png';

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
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
  await page.getByLabel('参考高度').fill('0.1');
  await page.getByRole('button', { name: '按配方测量并绑定' }).click();
  await page.getByText(/recipe_/).first().waitFor();

  await page.getByRole('button', { name: '02 流程' }).click();
  await page.getByRole('button', { name: '保存当前步骤为模板' }).click();
  await page.getByLabel('模板变量值').fill('0.03');
  await page.getByRole('button', { name: '应用' }).click();
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: '比较上一版' }).click();
  await page.getByText('Compile diff').waitFor();
  await page.getByText(/0\.01 → 0\.03/).first().waitFor();

  await page.getByRole('button', { name: '创建首个 Route' }).click();
  await page.getByRole('button', { name: '03 运行' }).click();
  await page.getByRole('button', { name: '执行全部 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 60000 });
  await page.getByLabel('尾步时长').fill('0.04');
  await page.getByRole('button', { name: '修改尾步并分支' }).click();
  await page.getByText('reuse').waitFor({ timeout: 30000 });

  await page.getByRole('button', { name: '04 实验' }).click();
  await page.getByRole('button', { name: '创建 10 变体实验' }).click();
  await page.getByText('Experiment status: planned').waitFor();
  await page.getByRole('button', { name: '运行持久化实验' }).click();
  await page.getByText('Experiment status: completed').waitFor({ timeout: 120000 });
  await page.screenshot({ path: screenshot, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', baseUrl, screenshot, errors }));
} finally {
  await browser.close();
}
