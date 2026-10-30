import { useEffect, useMemo, useRef, useState } from 'react';
import './workflow.css';

type Health = { status: string; viennaps?: string; viennals?: string; activeRunId?: string | null };
type GdsAsset = { assetId: string; fileName: string };
type ProjectStep = { id: string; name: string; model: string; operation?: string; enabled: boolean; duration?: number };
type CompileInfo = {
  flowId: string;
  bindingId: string;
  compileId: string;
  projectName: string;
  dimension: number;
  stepCount: number;
  compile: { project: { name: string; dimension: number; steps: ProjectStep[] }; sourceMap?: Array<{ stepId: string; authoredStepId: string }> };
};
type RoutePlan = {
  route: { routeId: string; name: string; headAttemptId: string };
  stepOrder: string[];
  attempts: Array<{ attemptId: string; status: string; compileId: string; runSeed: number }>;
  segments: Array<{
    segmentId: string;
    attemptId: string;
    status: string;
    runId: string | null;
    startAfterStepId: string | null;
    throughStepId: string;
    segmentProjectHash: string;
    endCheckpointRef: unknown;
  }>;
};
type Measurement = {
  measurementId: string;
  status: string;
  value: number | null;
  unit: string;
  reason: string | null;
  bindingStatus: string;
  equivalenceStatus: string;
  sourceRunId: string | null;
  sourceStepId: string | null;
  sourceStateId: string | null;
};
type TemplateSummary = { templateId: string; name: string; stepCount: number };
type Provenance = {
  measurementId: string;
  bindingStatus: string;
  verifications: Array<{ verificationId: string; status: string; createdAt: string; evidence: { methodVersion: string } }>;
};

const STEP_LABELS: Record<string, string> = {
  isotropic: '各向同性',
  singleParticle: '单粒子',
  multiParticle: '多粒子',
  directional: '定向',
  specialized: '专用模型',
  planarize: '平坦化',
  removeMaterial: '移除材料',
  boolean: '布尔几何',
};

function errorText(payload: unknown): string {
  if (typeof payload === 'string') return payload;
  if (payload && typeof payload === 'object') {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => typeof item === 'object' && item && 'message' in item ? String((item as { message: unknown }).message) : String(item)).join('; ');
  }
  return '请求失败';
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const text = await response.text();
  let payload: unknown = text;
  try { payload = text ? JSON.parse(text) : {}; } catch { /* keep text */ }
  if (!response.ok) throw new Error(errorText(payload));
  return payload as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export default function WorkflowApp({ onOpenLegacy }: { onOpenLegacy?: () => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [gds, setGds] = useState<GdsAsset | null>(null);
  const [project, setProject] = useState<{ name?: string; dimension?: number; steps?: ProjectStep[]; geometry?: { kind?: string; assetId?: string } } | null>(null);
  const [compile, setCompile] = useState<CompileInfo | null>(null);
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [segment, setSegment] = useState<RoutePlan['segments'][number] | null>(null);
  const [measurement, setMeasurement] = useState<Measurement | null>(null);
  const [experiments, setExperiments] = useState<Array<{ index: number; routeId: string; segmentId: string; status: string; runId: string | null }>>([]);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [templateId, setTemplateId] = useState('');
  const [recipeMethod, setRecipeMethod] = useState<'cd_at_height' | 'etch_depth'>('etch_depth');
  const [recipeMaterial, setRecipeMaterial] = useState('Si');
  const [recipeHeight, setRecipeHeight] = useState('0');
  const [recipeX, setRecipeX] = useState('0');
  const [recipeRef, setRecipeRef] = useState('0');
  const [metrics, setMetrics] = useState<Measurement[]>([]);
  const [branchDuration, setBranchDuration] = useState('0.02');
  const [activeRecipeId, setActiveRecipeId] = useState('');
  const [experimentDoc, setExperimentDoc] = useState<{ experimentId: string; name: string; status: string; variants: Array<{ ordinal: number; replicates: Array<{ status: string; value?: number | null; unit?: string; runId?: string }> }> } | null>(null);
  const [provenance, setProvenance] = useState<Provenance | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [zone, setZone] = useState<'setup' | 'flow' | 'run' | 'experiment' | 'analyze' | 'report'>('setup');
  const [error, setError] = useState('');
  const gdsInput = useRef<HTMLInputElement>(null);
  const projectInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void api<Health>('/api/health').then(setHealth).catch(() => setHealth(null));
    void api<{ templates: TemplateSummary[] }>('/api/v1/workflow/templates').then(data => setTemplates(data.templates)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!segment || segment.status !== 'running') return;
    const timer = window.setInterval(() => {
      void api<RoutePlan['segments'][number]>(`/api/v1/workflow/segments/${segment.segmentId}`)
        .then(next => {
          setSegment(next);
          setRoute(previous => previous ? { ...previous, segments: previous.segments.map(item => item.segmentId === next.segmentId ? next : item) } : previous);
        })
        .catch(() => undefined);
    }, 600);
    return () => window.clearInterval(timer);
  }, [segment]);

  const activeSteps = useMemo(() => (compile?.compile.project.steps ?? []).filter(step => step.enabled), [compile]);
  const canAdopt = !!project && !busy;
  const canRoute = !!compile && !busy;
  const canRun = !!segment && segment.status === 'planned' && !busy;
  const canMeasure = !!segment && segment.status === 'succeeded' && !!segment.runId && !busy;

  const run = async (name: string, task: () => Promise<void>) => {
    setBusy(name);
    setError('');
    try { await task(); } catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); } finally { setBusy(null); }
  };

  const uploadGds = async (file: File) => {
    setBusy('gds');
    setError('');
    try {
      const response = await fetch(`/api/gds-assets?file_name=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      });
      const text = await response.text();
      let payload: unknown = text;
      try { payload = text ? JSON.parse(text) : {}; } catch { /* text */ }
      if (!response.ok) throw new Error(errorText(payload));
      setGds(payload as GdsAsset);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const importProject = async (file: File) => {
    setError('');
    setCompile(null);
    setRoute(null);
    setSegment(null);
    setMeasurement(null);
    setProvenance(null);
    try {
      const parsed = JSON.parse(await file.text()) as typeof project;
      setProject(parsed);
    } catch {
      setError('项目 JSON 无法解析');
    }
  };

  const adopt = () => run('adopt', async () => {
    if (!project) throw new Error('先导入项目 JSON');
    const result = await postJson<CompileInfo>('/api/v1/workflow/flows/from-project', { project });
    setCompile(result);
    setRoute(null);
    setSegment(null);
  });

  const saveTemplate = () => run('template', async () => {
    if (!compile) throw new Error('先采纳工程');
    const templateId = `tpl_${Date.now().toString(36)}`;
    await postJson('/api/v1/workflow/templates', {
      schemaVersion: 1, templateId, name: `${compile.projectName} · template`, description: '',
      steps: compile.compile.project.steps, variables: [],
    });
    const data = await api<{ templates: TemplateSummary[] }>('/api/v1/workflow/templates');
    setTemplates(data.templates);
    setTemplateId(templateId);
  });

  const applyTemplate = () => run('template', async () => {
    if (!project || !templateId) throw new Error('先选择模板');
    const result = await postJson<CompileInfo>('/api/v1/workflow/flows/from-template', {
      project, templateId, variables: {},
    });
    setCompile(result); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
  });

  const createRoute = () => run('route', async () => {
    if (!compile) throw new Error('先采纳工程');
    const enabledSteps = compile.compile.project.steps.filter(step => step.enabled);
    const splitAfterStepId = enabledSteps.length > 1 ? enabledSteps[enabledSteps.length - 2].id : undefined;
    const result = await postJson<{ plan: RoutePlan }>('/api/v1/workflow/routes/from-compile', {
      compileId: compile.compileId,
      name: `${compile.projectName} · P1 route`,
      splitAfterStepId,
    });
    setRoute(result.plan);
    setSegment(result.plan.segments[0] ?? null);
    setMeasurement(null);
    setProvenance(null);
  });

  const execute = () => run('execute', async () => {
    if (!segment) throw new Error('先创建 Route');
    const result = await postJson<{ segment: RoutePlan['segments'][number]; runId: string }>(
      `/api/v1/workflow/segments/${segment.segmentId}/execute`, {},
    );
    setSegment(result.segment);
    setRoute(previous => previous ? { ...previous, segments: previous.segments.map(item => item.segmentId === result.segment.segmentId ? result.segment : item) } : previous);
  });

  const measure = () => run('measure', async () => {
    if (!segment?.runId) throw new Error('先完成运行');
    if (!compile) throw new Error('先采纳工程');
    const metricId = `metric_${compile.flowId}_${recipeMethod}`;
    const recipeId = `recipe_${compile.flowId}_${recipeMethod}`;
    const metricUnit = 'um';
    await postJson('/api/v1/workflow/metrics', {
      schemaVersion: 1, metricId, name: `${recipeMethod} metric`, unit: metricUnit, description: 'P1 configurable recipe',
    });
    const parameters = recipeMethod === 'cd_at_height'
      ? { kind: 'cd_at_height', height: Number(recipeHeight) || 0, pairSelection: 'outermost' }
      : { kind: 'etch_depth', x: Number(recipeX) || 0, referenceHeight: Number(recipeRef) || 0 };
    await postJson('/api/v1/workflow/metrology-recipes', {
      schemaVersion: 1, recipeId, version: 1, metricId,
      name: `${recipeMethod} @ ${recipeMaterial}`, dimension: compile.dimension,
      method: recipeMethod, parameters, material: recipeMaterial,
    });
    const result = await postJson<Measurement>('/api/v1/workflow/measurements/from-run', {
      recipeId, recipeVersion: 1, runId: segment.runId, stepId: segment.throughStepId,
    });
    setMeasurement(result);
    const bound = await postJson<Measurement>(`/api/v1/workflow/measurements/${result.measurementId}/bind`, {
      segmentId: segment.segmentId, runId: segment.runId, stepId: segment.throughStepId,
    });
    setMeasurement(bound);
    setProvenance(await api<Provenance>(`/api/v1/workflow/measurements/${result.measurementId}/provenance`));
    const listed = await api<{ measurements: Measurement[] }>(`/api/v1/workflow/measurements?run_id=${segment.runId}`);
    setMetrics(listed.measurements);
    setActiveRecipeId(recipeId);
  });


  const executeAll = () => run('execute', async () => {
    if (!route) throw new Error('先创建 Route');
    for (const item of route.segments.filter(segmentItem => segmentItem.status === 'planned')) {
      const started = await postJson<{ segment: RoutePlan['segments'][number]; runId: string }>(
        `/api/v1/workflow/segments/${item.segmentId}/execute`, {},
      );
      setSegment(started.segment);
      setRoute(previous => previous ? { ...previous, segments: previous.segments.map(row => row.segmentId === started.segment.segmentId ? started.segment : row) } : previous);
      await waitSegment(item.segmentId);
    }
  });

  const branchLastStep = () => run('branch', async () => {
    if (!project || !compile || !route) throw new Error('先完成前缀 Route');
    const enabledSteps = compile.compile.project.steps.filter(step => step.enabled);
    if (enabledSteps.length < 2) throw new Error('至少需要两步');
    const tail = enabledSteps[enabledSteps.length - 1];
    const prefix = enabledSteps[enabledSteps.length - 2];
    const authoredTail = compile.compile.sourceMap?.find(entry => entry.stepId === tail.id)?.authoredStepId ?? tail.id;
    const cloned = JSON.parse(JSON.stringify(project)) as typeof project & { steps?: Array<Record<string, unknown>> };
    const target = cloned.steps?.find(step => step.id === authoredTail);
    if (!target) throw new Error('找不到尾步骤');
    target.duration = Number(branchDuration) || 0.02;
    const adopted = await postJson<CompileInfo>('/api/v1/workflow/flows/from-project', { project: cloned, flowId: compile.flowId });
    setCompile(adopted);
    const result = await postJson<{ plan: RoutePlan }>(`/api/v1/workflow/routes/${route.route.routeId}/branches`, {
      compileId: adopted.compileId, startAfterStepId: prefix.id, changedStepIds: [tail.id],
    });
    setRoute(result.plan);
    const tailSegment = result.plan.segments[result.plan.segments.length - 1];
    setSegment(tailSegment);
    setMeasurement(null); setProvenance(null); setMetrics([]);
  });

  const createPersistentExperiment = () => run('experiment', async () => {
    if (!project || !compile || !activeRecipeId) throw new Error('先完成一次测量以固定配方');
    const tail = compile.compile.project.steps.filter(step => step.enabled).slice(-1)[0];
    const authored = compile.compile.sourceMap?.find(entry => entry.stepId === tail.id)?.authoredStepId ?? tail.id;
    const variants = Array.from({ length: 10 }, (_, index) => ({
      parameters: { [authored]: { duration: 0.01 + index * 0.001 } },
      replicates: 1,
      seed: 20261029 + index,
    }));
    const doc = await postJson<typeof experimentDoc>('/api/v1/workflow/experiments', {
      name: `${compile.projectName} · 10 variants`, project, flowId: compile.flowId,
      recipeId: activeRecipeId, recipeVersion: 1, variants,
    });
    setExperimentDoc(doc);
  });

  const runPersistentExperiment = () => run('experiment', async () => {
    if (!experimentDoc) throw new Error('先创建实验');
    await postJson(`/api/v1/workflow/experiments/${experimentDoc.experimentId}/run`, {});
    for (let index = 0; index < 600; index += 1) {
      const doc = await api<typeof experimentDoc>(`/api/v1/workflow/experiments/${experimentDoc.experimentId}`);
      setExperimentDoc(doc);
      if (doc && ['completed', 'failed'].includes(doc.status)) return;
      await new Promise(resolve => window.setTimeout(resolve, 500));
    }
  });

  const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

  const waitSegment = async (segmentId: string) => {
    for (let i = 0; i < 300; i += 1) {
      const current = await api<RoutePlan['segments'][number]>(`/api/v1/workflow/segments/${segmentId}`);
      setExperiments(previous => previous.map(item => item.segmentId === segmentId ? { ...item, status: current.status, runId: current.runId } : item));
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(current.status)) return current;
      await sleep(500);
    }
    throw new Error('experiment segment did not finish');
  };

  const downloadReport = () => {
    const lines = [
      '<!doctype html><meta charset="utf-8"><title>P0 Workflow Report</title>',
      '<style>body{font-family:system-ui;padding:32px;line-height:1.5}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}</style>',
      `<h1>${compile?.projectName ?? 'Workflow'} — P0 report</h1>`,
      `<p>Compile: ${compile?.compileId ?? '—'}<br>Route: ${route?.route.routeId ?? '—'}<br>Run: ${segment?.runId ?? '—'}</p>`,
      '<h2>Experiment variants</h2><table><tr><th>#</th><th>Status</th><th>Run</th></tr>',
      ...experiments.map(item => `<tr><td>${item.index}</td><td>${item.status}</td><td>${item.runId ?? '—'}</td></tr>`),
      '</table>',
      '<h2>Measurement</h2>',
      `<p>${measurement ? `${measurement.status}: ${measurement.value ?? '—'} ${measurement.unit} · binding ${measurement.bindingStatus} · equivalence ${measurement.equivalenceStatus}` : '—'}</p>`,
    ];
    const url = URL.createObjectURL(new Blob([lines.join('')], { type: 'text/html' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'p0-workflow-report.html';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="wf-shell">
      <header className="wf-topbar">
        <div>
          <div className="wf-kicker">VIENNAPS WORKBENCH · P0</div>
          <h1>Workflow Studio</h1>
        </div>
        <div className="wf-health">
          <span className={health?.status === 'ok' ? 'wf-dot ok' : 'wf-dot'} />
          {health?.status === 'ok' ? '本机服务已连接' : '服务未连接'}
          {health?.activeRunId && <code>{health.activeRunId.slice(0, 8)}</code>}
          {onOpenLegacy && <button type="button" className="wf-legacy" onClick={onOpenLegacy}>传统工作区</button>}
        </div>
      </header>

      {error && <div className="wf-error">{error}</div>}

      <nav className="wf-zone-nav" aria-label="Workflow zones">
        {[
          ['setup', '01 工程'], ['flow', '02 流程'], ['run', '03 运行'],
          ['experiment', '04 实验'], ['analyze', '05 分析'], ['report', '06 报告'],
        ].map(([id, label]) => <button key={id} className={zone === id ? 'active' : ''} onClick={() => setZone(id as typeof zone)}>{label}</button>)}
      </nav>

      <main className="wf-grid">
        <section className={`wf-card wf-setup ${zone === 'setup' ? '' : 'wf-hidden'}`}>
          <div className="wf-card-head"><span>01</span><h2>工程接入</h2></div>
          <p className="wf-muted">先上传 GDS，再导入工程 JSON。工程里的 assetId 必须与上传文件一致。</p>
          <input ref={gdsInput} type="file" accept=".gds,.gdsii" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void uploadGds(file); }} />
          <button className="wf-button ghost" disabled={!!busy} onClick={() => gdsInput.current?.click()}>
            {gds ? `GDS · ${gds.fileName}` : '上传 GDS'}
          </button>
          {gds && <div className="wf-mono">asset {gds.assetId.slice(0, 16)}…</div>}
          <input ref={projectInput} type="file" accept=".json" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void importProject(file); }} />
          <button className="wf-button ghost" disabled={!!busy} onClick={() => projectInput.current?.click()}>
            {project ? '更换项目 JSON' : '导入项目 JSON'}
          </button>
          {project && <div className="wf-summary"><strong>{project.name ?? '未命名工程'}</strong><span>{project.dimension ?? '?'}D · {project.steps?.length ?? 0} 步 · {project.geometry?.kind ?? 'geometry'}</span></div>}
          <button className="wf-button primary" disabled={!canAdopt} onClick={() => void adopt()}>
            {busy === 'adopt' ? '采纳中…' : '采纳为 Workflow'}
          </button>
          {compile && <div className="wf-summary success"><strong>Compile 已冻结</strong><span>{compile.stepCount} 步 · {compile.compileId.slice(0, 12)}…</span></div>}
        </section>

        <section className={`wf-card wf-flow ${zone === 'flow' ? '' : 'wf-hidden'}`}>
          <div className="wf-card-head"><span>02</span><h2>流程 / Route</h2></div>
          {!compile && <div className="wf-empty">采纳工程后显示编译后的步骤。</div>}
          {compile && <>
            <div className="wf-muted">{compile.projectName} · {compile.dimension}D · {activeSteps.length} 个启用步骤</div>
            <div className="wf-step-list">
              {compile.compile.project.steps.map((step, index) => (
                <div className={`wf-step ${step.enabled ? '' : 'off'}`} key={step.id}>
                  <span className="wf-step-index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="wf-step-name">{step.name}</span>
                  <span className="wf-step-model">{STEP_LABELS[step.model] ?? step.model}</span>
                </div>
              ))}
            </div>
            <button className="wf-button primary" disabled={!canRoute} onClick={() => void createRoute()}>
              {busy === 'route' ? '创建中…' : '创建首个 Route'}
            </button>
            <button className="wf-button ghost" disabled={!compile || !!busy} onClick={() => void saveTemplate()}>保存当前步骤为模板</button>
            <div className="wf-template-row">
              <select value={templateId} onChange={event => setTemplateId(event.target.value)}>
                <option value="">选择模板</option>
                {templates.map(item => <option key={item.templateId} value={item.templateId}>{item.name} · {item.stepCount} 步</option>)}
              </select>
              <button className="wf-button ghost" disabled={!project || !templateId || !!busy} onClick={() => void applyTemplate()}>应用</button>
            </div>
          </>}
          {route && <div className="wf-summary"><strong>{route.route.name}</strong><span>{route.segments.length} Segment · {route.stepOrder.length} Step · head {route.route.headAttemptId.slice(0, 10)}…</span></div>}
        </section>

        <section className={`wf-card wf-run ${zone === 'run' ? '' : 'wf-hidden'}`}>
          <div className="wf-card-head"><span>03</span><h2>运行 / 测量</h2></div>
          {!segment && <div className="wf-empty">创建 Route 后可执行 Segment。</div>}
          {segment && <>
            <div className="wf-status-row"><span className={`wf-status ${segment.status}`}>{segment.status}</span><code>{segment.runId ?? 'no run yet'}</code></div>
            {route && <div className="wf-route-tree">
              {route.attempts.map(attempt => <div key={attempt.attemptId} className="wf-attempt">
                <div className="wf-attempt-title"><strong>{attempt.attemptId.slice(0, 10)}…</strong><span>{attempt.status}</span></div>
                {route.segments.filter(item => item.attemptId === attempt.attemptId).map(item => <div key={item.segmentId} className="wf-segment-row"><span className={`wf-status ${item.status}`}>{item.status}</span><code>{item.startAfterStepId ? 'after ' + item.startAfterStepId.slice(0, 8) : 'start'} → {item.throughStepId.slice(0, 8)}</code></div>)}
              </div>)}
            </div>}
            <div className="wf-muted">steps {segment.startAfterStepId ?? 'start'} → {segment.throughStepId}</div>
            <button className="wf-button primary" disabled={!canRun} onClick={() => void execute()}>
              {busy === 'execute' ? '启动中…' : '执行 Segment'}
            </button>
            <button className="wf-button ghost" disabled={!route || !!busy} onClick={() => void executeAll()}>执行全部 Segment</button>
            <div className="wf-branch-row">
              <input aria-label="尾步时长" value={branchDuration} onChange={event => setBranchDuration(event.target.value)} />
              <button className="wf-button ghost" disabled={!route || !!busy} onClick={() => void branchLastStep()}>修改尾步并分支</button>
            </div>
            {segment.status === 'running' && <div className="wf-progress"><i /></div>}
            <div className="wf-recipe">
              <div className="wf-recipe-title">测量配方</div>
              <select value={recipeMethod} onChange={event => setRecipeMethod(event.target.value as typeof recipeMethod)}>
                <option value="etch_depth">深度</option>
                <option value="cd_at_height">指定高度 CD</option>
              </select>
              <input aria-label="测量材料" value={recipeMaterial} onChange={event => setRecipeMaterial(event.target.value)} placeholder="材料" />
              {recipeMethod === 'cd_at_height'
                ? <input aria-label="CD 高度" value={recipeHeight} onChange={event => setRecipeHeight(event.target.value)} placeholder="高度 µm" />
                : <>
                  <input aria-label="深度 X" value={recipeX} onChange={event => setRecipeX(event.target.value)} placeholder="X µm" />
                  <input aria-label="参考高度" value={recipeRef} onChange={event => setRecipeRef(event.target.value)} placeholder="参考高度 µm" />
                </>}
            </div>
            <button className="wf-button ghost" disabled={!canMeasure} onClick={() => void measure()}>
              {busy === 'measure' ? '测量中…' : '按配方测量并绑定'}
            </button>
            {segment.status === 'succeeded' && <div className="wf-muted">运行完成后可测量；2D 支持 CD/深度，3D 支持材料顶高深度。</div>}
          </>}
          {measurement && <div className="wf-summary">
            <strong>Measurement · {measurement.status}</strong>
            <span>{measurement.value === null ? measurement.reason ?? '无值' : `${measurement.value.toFixed(4)} ${measurement.unit}`}</span>
            <span>binding: {measurement.bindingStatus} · equivalence: {measurement.equivalenceStatus}</span>
          </div>}
          {metrics.length > 0 && <table className="wf-table"><thead><tr><th>Metric</th><th>Value</th><th>Binding</th></tr></thead><tbody>{metrics.map(item => <tr key={item.measurementId}><td>{item.status}</td><td>{item.value === null ? item.reason : `${item.value.toFixed(4)} ${item.unit}`}</td><td>{item.bindingStatus} / {item.equivalenceStatus}</td></tr>)}</tbody></table>}
          {provenance && <div className="wf-provenance">
            <div className="wf-muted">verification records: {provenance.verifications.length}</div>
            {provenance.verifications.map(item => <div className="wf-verify" key={item.verificationId}><span className={`wf-status ${item.status}`}>{item.status}</span><code>{item.verificationId.slice(0, 10)}…</code><span>{item.evidence.methodVersion}</span></div>)}
          </div>}
        </section>
        {zone === 'experiment' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>04</span><h2>Experiment</h2></div>
          <p className="wf-muted">P1 手工实验：保存 10 个可分别设置尾步时长的 Variant，可设重复数与种子，串行执行并使用当前配方测量。</p>
          <button className="wf-button primary" disabled={!compile || !activeRecipeId || !!busy} onClick={() => void createPersistentExperiment()}>创建 10 变体实验</button>
          <button className="wf-button ghost" disabled={!experimentDoc || !!busy} onClick={() => void runPersistentExperiment()}>{busy === 'experiment' ? '实验运行中…' : '运行持久化实验'}</button>
          {experimentDoc && <table className="wf-table"><thead><tr><th>#</th><th>Status</th><th>Value</th><th>Run</th></tr></thead><tbody>{experimentDoc.variants.map(variant => <tr key={variant.ordinal}><td>{variant.ordinal}</td><td>{variant.replicates[0]?.status}</td><td>{variant.replicates[0]?.value ?? '—'} {variant.replicates[0]?.unit ?? ''}</td><td><code>{variant.replicates[0]?.runId?.slice(0, 8) ?? '—'}</code></td></tr>)}</tbody></table>}
        </section>}
        {zone === 'analyze' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>05</span><h2>Analyze</h2></div>
          <p className="wf-muted">分析区当前展示 Measurement 与 Provenance；数值等价独立于绑定状态。</p>
          {measurement ? <div className="wf-summary"><strong>{measurement.status}</strong><span>{measurement.value === null ? measurement.reason : `${measurement.value.toFixed(4)} ${measurement.unit}`}</span><span>binding {measurement.bindingStatus} · equivalence {measurement.equivalenceStatus}</span></div> : <div className="wf-empty">完成一次运行与测量后显示指标。</div>}
          {experiments.length > 0 && <table className="wf-table"><thead><tr><th>Variant</th><th>Status</th><th>Run</th></tr></thead><tbody>{experiments.map(item => <tr key={item.segmentId}><td>{item.index}</td><td>{item.status}</td><td><code>{item.runId ?? '—'}</code></td></tr>)}</tbody></table>}
        </section>}
        {zone === 'report' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>06</span><h2>Report</h2></div>
          <p className="wf-muted">生成包含 Flow、Compile、Run、变体与 Measurement 的 HTML 报告；等价状态与绑定状态分开记录。</p>
          <button className="wf-button primary" onClick={downloadReport}>下载 HTML 报告</button>
          <div className="wf-summary"><strong>Workflow summary</strong><span>Flow {compile?.flowId ?? '—'}</span><span>Compile {compile?.compileId ?? '—'}</span><span>Run {segment?.runId ?? '—'}</span><span>Measurement {measurement?.measurementId ?? '—'}</span><span>Binding {measurement?.bindingStatus ?? '—'}</span></div>
        </section>}
      </main>

      <footer className="wf-footer">
        <span>Flow → Compile → Route → Run → Measure → Provenance</span>
        <span>{compile ? `compile ${compile.compileId.slice(0, 12)}…` : 'no compile'}</span>
      </footer>
    </div>
  );
}
