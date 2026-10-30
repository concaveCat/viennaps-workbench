/** P4 Chromium acceptance: modules/expressions, measurement decisions and shared library. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const baseUrl = (process.env.WORKBENCH_URL ?? 'http://127.0.0.1:8817').replace(/\/$/, '');
const projectPath = process.env.WORKFLOW_PROJECT ?? path.join(here, 'fixtures', 'workflow-2d-five.json');
const screenshot = process.env.WORKFLOW_SCREENSHOT ?? '/tmp/workflow-p4.png';

const moduleJson = JSON.stringify({
  schemaVersion: 1,
  moduleId: 'mod_p4_ui',
  name: 'P4 UI etch module',
  description: '',
  parameters: [{ name: 'duration', default: 0.02, unit: 's' }],
  steps: [{
    id: 'step_mod_etch_01', name: 'module etch', enabled: true,
    operation: 'etch', model: 'isotropic', rateMagnitude: 0, duration: '${duration}', rateMode: 'uniform',
  }],
}, null, 2);

const definitionJson = JSON.stringify({
  schemaVersion: 2,
  definitionId: 'def_p4_ui',
  flowId: 'flow_p4_ui',
  revision: 1,
  name: 'P4 UI modular flow',
  description: '',
  variables: [],
  nodes: [
    { kind: 'module-call', instanceId: 'call_one', moduleId: 'mod_p4_ui', parameters: { duration: 0.02 } },
    { kind: 'module-call', instanceId: 'call_two', moduleId: 'mod_p4_ui', parameters: { duration: 0.04 } },
    {
      kind: 'repeat', repeatId: 'loop_tail', loopVariable: 'i', count: 2,
      body: [{
        kind: 'step',
        step: {
          id: 'step_ui_tail_01', name: 'loop tail', enabled: true,
          operation: 'etch', model: 'isotropic', rateMagnitude: 0,
          duration: '${0.01 + i * 0.01}', rateMode: 'uniform',
        },
      }],
    },
  ],
  solver: {
    timeStepRatio: 0.25, raysPerPoint: 16, cpuThreads: 1,
    random: { mode: 'fixed', seed: 7 }, enginePreference: 'cpu', timeoutSeconds: 30,
  },
}, null, 2);

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

  // P4-1: module + definition + loop expansion + compile.
  await page.getByRole('button', { name: '02 流程' }).click();
  await page.getByLabel('模块 JSON').fill(moduleJson);
  await page.getByRole('button', { name: '保存模块' }).click();
  await page.getByLabel('流程定义 JSON').fill(definitionJson);
  await page.getByRole('button', { name: '保存定义' }).click();
  await page.getByRole('button', { name: '展开预览' }).click();
  await page.getByText(/4 步 · 深度 \d+/).first().waitFor();
  assert.match(await page.locator('.wf-summary').filter({ hasText: '展开预览' }).innerText(), /2 模块实例 · 2 循环迭代/);
  await page.getByRole('button', { name: '编译模块化流程' }).click();
  await page.getByText(/模块化流程已编译 · 4 步/).waitFor();

  // P4-2: split route, execute prefix, measure, decide, execute branch, replay.
  await page.getByRole('button', { name: '创建首个 Route' }).click();
  await page.getByRole('button', { name: '03 运行' }).click();
  await page.getByRole('button', { name: '执行 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 60000 });
  await page.getByRole('button', { name: '按配方测量并绑定' }).click();
  await page.getByText(/binding: verified/).first().waitFor();
  await page.getByLabel('决策阈值').fill('0.5');
  await page.getByRole('button', { name: '测量并生成分支' }).click();
  await page.locator('table[aria-label="测量决策列表"] tbody tr').first().waitFor();
  const decisionRow = page.locator('table[aria-label="测量决策列表"] tbody tr').first();
  assert.match(await decisionRow.innerText(), /wfr_/);
  assert.match(await decisionRow.innerText(), /false/);
  await page.getByRole('button', { name: '执行 Segment' }).click();
  await page.locator('.wf-status.succeeded').first().waitFor({ timeout: 60000 });
  await page.getByRole('button', { name: /重放决策/ }).first().click();
  await page.locator('table[aria-label="测量决策列表"] tbody tr').nth(1).waitFor();
  assert.match(await page.locator('table[aria-label="测量决策列表"] tbody tr').nth(1).innerText(), /replay of/);

  // P4-3: export a bundle, then clone-import it.
  await page.getByRole('button', { name: '02 流程' }).click();
  await page.getByLabel('共享库名称').fill('P4 UI bundle');
  await page.getByRole('button', { name: '导出共享库' }).click();
  await page.locator('table[aria-label="共享库列表"] tbody tr').first().waitFor();
  const bundleRow = page.locator('table[aria-label="共享库列表"] tbody tr').first();
  assert.match(await bundleRow.innerText(), /1 模块修订 · 1 定义/);
  await page.getByLabel('共享库 bundle 选择').selectOption({ index: 1 });
  await page.getByLabel('共享库导入策略').selectOption('clone');
  await page.getByRole('button', { name: '导入共享库', exact: true }).click();
  await page.getByLabel('共享库导入结果').waitFor();
  assert.match(await page.getByLabel('共享库导入结果').innerText(), /定义: def_p4_ui_/);

  await page.screenshot({ path: screenshot, fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'passed', baseUrl, screenshot, errors }));
} finally {
  await browser.close();
}
