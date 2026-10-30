/** Real Chromium acceptance of the redesigned Workflow Studio. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const baseUrl = (process.env.WORKBENCH_URL ?? 'http://127.0.0.1:8812').replace(/\/$/, '');
const projectPath = process.env.WORKFLOW_PROJECT ?? path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'workflow-2d-trench.json');
const screenshot = process.env.WORKFLOW_SCREENSHOT ?? '/tmp/workflow-studio.png';

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.setDefaultTimeout(30000);
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
  await page.locator('.wf-status.planned').first().waitFor();
  await page.getByRole('button', { name: '执行 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 30000 });

  await page.getByRole('button', { name: '按配方测量并绑定' }).click();
  await page.getByText('Measurement · measured').waitFor({ timeout: 30000 });
  await page.getByText(/2\.0000 um/).first().waitFor({ timeout: 30000 });
  await page.getByText(/binding: verified/).waitFor({ timeout: 30000 });
  await page.screenshot({ path: screenshot, fullPage: true });

  await page.getByRole('button', { name: '传统工作区' }).click();
  await page.locator('.activity-rail').waitFor({ timeout: 15000 });
  await page.getByRole('button', { name: '返回 Workflow Studio' }).click();
  await page.getByText('Workflow Studio').waitFor({ timeout: 15000 });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', baseUrl, screenshot, errors }));
} finally {
  await browser.close();
}
