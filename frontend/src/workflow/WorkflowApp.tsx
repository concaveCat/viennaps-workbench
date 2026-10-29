import { useEffect, useMemo, useRef, useState } from 'react';
import './workflow.css';

type Health = { status: string; viennaps?: string; viennals?: string; activeRunId?: string | null };
type GdsAsset = { assetId: string; fileName: string };
type ProjectStep = { id: string; name: string; model: string; operation?: string; enabled: boolean };
type CompileInfo = {
  flowId: string;
  bindingId: string;
  compileId: string;
  projectName: string;
  dimension: number;
  stepCount: number;
  compile: { project: { name: string; dimension: number; steps: ProjectStep[] } };
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

export default function WorkflowApp() {
  const [health, setHealth] = useState<Health | null>(null);
  const [gds, setGds] = useState<GdsAsset | null>(null);
  const [project, setProject] = useState<{ name?: string; dimension?: number; steps?: ProjectStep[]; geometry?: { kind?: string; assetId?: string } } | null>(null);
  const [compile, setCompile] = useState<CompileInfo | null>(null);
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [segment, setSegment] = useState<RoutePlan['segments'][number] | null>(null);
  const [measurement, setMeasurement] = useState<Measurement | null>(null);
  const [provenance, setProvenance] = useState<Provenance | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const gdsInput = useRef<HTMLInputElement>(null);
  const projectInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void api<Health>('/api/health').then(setHealth).catch(() => setHealth(null));
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

  const createRoute = () => run('route', async () => {
    if (!compile) throw new Error('先采纳工程');
    const result = await postJson<{ plan: RoutePlan }>('/api/v1/workflow/routes/from-compile', {
      compileId: compile.compileId,
      name: `${compile.projectName} · P0 route`,
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
    const stamp = Date.now().toString(36);
    const metricId = `metric_cd_${stamp}`;
    const recipeId = `recipe_cd_${stamp}`;
    await postJson('/api/v1/workflow/metrics', {
      schemaVersion: 1, metricId, name: 'P0 CD / depth', unit: 'um', description: 'workflow UI default recipe',
    });
    await postJson('/api/v1/workflow/metrology-recipes', {
      schemaVersion: 1, recipeId, version: 1, metricId, name: 'P0 depth at x=0', dimension: 2,
      method: 'etch_depth', parameters: { kind: 'etch_depth', x: 0, referenceHeight: 0 },
      material: 'Si',
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
  });

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
        </div>
      </header>

      {error && <div className="wf-error">{error}</div>}

      <main className="wf-grid">
        <section className="wf-card wf-setup">
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

        <section className="wf-card wf-flow">
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
          </>}
          {route && <div className="wf-summary"><strong>{route.route.name}</strong><span>{route.segments.length} Segment · {route.stepOrder.length} Step · head {route.route.headAttemptId.slice(0, 10)}…</span></div>}
        </section>

        <section className="wf-card wf-run">
          <div className="wf-card-head"><span>03</span><h2>运行 / 测量</h2></div>
          {!segment && <div className="wf-empty">创建 Route 后可执行 Segment。</div>}
          {segment && <>
            <div className="wf-status-row"><span className={`wf-status ${segment.status}`}>{segment.status}</span><code>{segment.runId ?? 'no run yet'}</code></div>
            <div className="wf-muted">steps {segment.startAfterStepId ?? 'start'} → {segment.throughStepId}</div>
            <button className="wf-button primary" disabled={!canRun} onClick={() => void execute()}>
              {busy === 'execute' ? '启动中…' : '执行 Segment'}
            </button>
            {segment.status === 'running' && <div className="wf-progress"><i /></div>}
            <button className="wf-button ghost" disabled={!canMeasure} onClick={() => void measure()}>
              {busy === 'measure' ? '测量中…' : '测量并绑定 Provenance'}
            </button>
            {segment.status === 'succeeded' && <div className="wf-muted">运行完成后可测量；P0-3 计量当前只支持 2D 快照。</div>}
          </>}
          {measurement && <div className="wf-summary">
            <strong>Measurement · {measurement.status}</strong>
            <span>{measurement.value === null ? measurement.reason ?? '无值' : `${measurement.value.toFixed(4)} ${measurement.unit}`}</span>
            <span>binding: {measurement.bindingStatus} · equivalence: {measurement.equivalenceStatus}</span>
          </div>}
          {provenance && <div className="wf-provenance">
            <div className="wf-muted">verification records: {provenance.verifications.length}</div>
            {provenance.verifications.map(item => <div className="wf-verify" key={item.verificationId}><span className={`wf-status ${item.status}`}>{item.status}</span><code>{item.verificationId.slice(0, 10)}…</code><span>{item.evidence.methodVersion}</span></div>)}
          </div>}
        </section>
      </main>

      <footer className="wf-footer">
        <span>Flow → Compile → Route → Run → Measure → Provenance</span>
        <span>{compile ? `compile ${compile.compileId.slice(0, 12)}…` : 'no compile'}</span>
      </footer>
    </div>
  );
}
