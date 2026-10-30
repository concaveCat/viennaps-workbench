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
  authoredSteps?: ProjectStep[];
  compile: { project: { name: string; dimension: number; steps: ProjectStep[] }; sourceMap?: Array<{ stepId: string; authoredStepId: string }> };
};
type RoutePlan = {
  route: { routeId: string; name: string; headAttemptId: string };
  stepOrder: string[];
  reuse: Array<{ reuseId: string; attemptId: string; reusedFromSegmentId: string; fromStepId: string; toStepId: string }>;
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
  recipeId: string;
  recipeVersion: number;
  method: string;
  material: string;
  sourceSnapshotHash: string;
  intersections: number[];
  uncertainty: number | null;
  uncertaintyStatus: string;
  bindingEvidence: { methodVersion: string; verifiedAt: string } | null;
};
type Recipe = {
  recipeId: string;
  version: number;
  metricId: string;
  name: string;
  dimension: number;
  method: string;
  material: string;
  parameters: Record<string, unknown>;
};
type TemplateSummary = { templateId: string; name: string; stepCount: number };
type LayoutStructure = {
  name: string;
  layers: number[];
  layerDatatypes: number[][];
  maskLayerDatatypes: number[][];
  references: string[];
  bounds: { x0: number; y0: number; x1: number; y1: number } | null;
};
type LayoutDetail = {
  layoutId: string;
  assetId: string;
  fileName: string;
  sizeBytes: number;
  structures: LayoutStructure[];
  createdAt: string;
};
type ClipPolygon = { layer: number; datatype: number; points: number[][] };
type ClipCanvas = {
  structure: string;
  bbox: { x0: number; y0: number; x1: number; y1: number };
  width: number;
  height: number;
  polygons: ClipPolygon[];
  polygonCount: number;
  pointCount: number;
  contentBounds: { x0: number; y0: number; x1: number; y1: number };
};
type Clip = ClipCanvas & {
  clipId: string;
  name: string;
  layoutId: string;
  layers: Array<{ layer: number; datatype: number | null; material: string }>;
  annotations: Array<{ annotationId: string; text: string; x: number; y: number }>;
  clipHash: string;
  gdsAssetId: string;
  createdAt: string;
};
type ExperimentDoc = {
  experimentId: string;
  name: string;
  status: string;
  kind?: string;
  recipeVersion: number;
  design?: { conditionCount: number; logicalRunCount: number; replicates: number };
  queue?: { total: number; pending: number; running: number; succeeded: number; failed: number; retries: number; estimatedSecondsRemaining: number; estimatedCostUsd: number };
  aggregation?: { rows: Array<{ ordinal: number; n: number; mean: number | null; std: number | null; ci95: number | null; unit: string; statisticalStatus: string }>; ciMethod?: string };
  retention?: { mode: string };
  baseCompileId?: string | null;
  variants: Array<{ ordinal: number; replicates: Array<{ status: string; value?: number | null; unit?: string; runId?: string; seed?: number; attemptCount?: number; statisticalSample?: boolean }> }>;
  project?: { steps: Array<{ id: string }> };
};
type SnapshotSummary = {
  runId: string;
  stepId: string;
  dimension: number;
  topology: string;
  digest: string;
  pointCount: number;
  lineCount: number;
  triangleCount: number;
  bounds: number[];
  materialMeasures: Record<string, number>;
  measureKind: string;
};
type CompareResult = {
  candidates: Array<{ runId: string; stepId: string; label: string | null; summary: SnapshotSummary }>;
  pairwise: Array<{
    left: string; right: string; status: string; leftDigest: string; rightDigest: string;
    maxPointDelta: number | null; boundsDelta: number; materialDelta: Record<string, number>;
    reasons: string[]; tolerance: number;
  }>;
};
type CandidateView = {
  dimension: number;
  topology: string;
  points: number[][];
  lines: number[][];
  triangles: number[][];
  materialIds: number[];
  lineMaterialIds: number[];
  triangleMaterialIds: number[];
  materials: Record<string, { name?: string; color?: string }>;
  pointCount: number;
  lineCount: number;
  triangleCount: number;
  bounds: number[];
  sampledPointCount: number;
};
type ProcessWindowResult = {
  summary: { total: number; pass: number; fail: number; uncertain: number; notEvaluated: number };
  factorWindows: Array<{ stepId: string; parameter: string; min: number | null; max: number | null }>;
  candidates: Array<{ ordinal: number; mean: number | null; ci95: number | null; status: string; margin: number | null; parameters: Record<string, Record<string, number>> }>;
  rows: Array<{ ordinal: number; mean: number | null; ci95: number | null; status: string }>;
  lower: number;
  upper: number;
};
type CalibrationDataset = { datasetId: string; name: string; trainCount: number; validationCount: number; sha256: string };
type CalibrationFit = {
  fitId: string;
  model: string;
  method: string;
  parameterNames: string[];
  parameters: number[];
  iterations: number;
  validationStatus: string;
  trainResiduals: { n: number; rmse: number | null; mae: number | null; maxAbs: number | null };
  validationResiduals: { n: number; rmse: number | null; mae: number | null; maxAbs: number | null };
};
type ReportDocument = { reportId: string; title: string; html: string; sha256: string; createdAt: string };
type ExperimentTable = {
  experimentId: string;
  status: string;
  unit: string;
  queue?: ExperimentDoc['queue'];
  rows: Array<{ ordinal: number; n: number; mean: number | null; std: number | null; ci95: number | null; statisticalStatus: string; parameters: Record<string, Record<string, number>> }>;
};
type Provenance = {
  measurementId: string;
  bindingStatus: string;
  verifications: Array<{ verificationId: string; status: string; createdAt: string; evidence: { methodVersion: string } }>;
};

const LAYER_COLORS = ['#64d2ff', '#ffd166', '#7ee787', '#ff7b72', '#d2a8ff'];

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
  const [activeRecipeVersion, setActiveRecipeVersion] = useState(1);
  const [metricDetail, setMetricDetail] = useState<{ measurement: Measurement; recipe: Recipe; provenance: Provenance } | null>(null);
  const [experimentDoc, setExperimentDoc] = useState<ExperimentDoc | null>(null);
  const [experimentTable, setExperimentTable] = useState<ExperimentTable | null>(null);
  const [layouts, setLayouts] = useState<Array<{ layoutId: string; fileName: string; structureCount: number }>>([]);
  const [activeLayout, setActiveLayout] = useState<LayoutDetail | null>(null);
  const [layoutStructure, setLayoutStructure] = useState('');
  const [selectedPairs, setSelectedPairs] = useState<string[]>([]);
  const [clipBounds, setClipBounds] = useState({ x0: 0, y0: 0, x1: 2, y1: 2 });
  const [clipName, setClipName] = useState('clip');
  const [canvas, setCanvas] = useState<ClipCanvas | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [activeClip, setActiveClip] = useState<Clip | null>(null);
  const [annotationText, setAnnotationText] = useState('');
  const [annotationX, setAnnotationX] = useState('0');
  const [annotationY, setAnnotationY] = useState('0');
  const [doeFactors, setDoeFactors] = useState([
    { stepId: '', parameter: 'rateMagnitude', values: '0.01,0.02,0.03' },
    { stepId: '', parameter: 'duration', values: '0.02,0.04,0.06' },
    { stepId: '', parameter: 'duration', values: '0.01,0.02,0.03' },
  ]);
  const [doeReplicates, setDoeReplicates] = useState(3);
  const [doeSeed, setDoeSeed] = useState('20261029');
  const [doeCost, setDoeCost] = useState('0');
  const [tableMin, setTableMin] = useState('');
  const [tableMax, setTableMax] = useState('');
  const [tableSort, setTableSort] = useState('ordinal');
  const [retentionInfo, setRetentionInfo] = useState<{ releasable: string[]; protected: Array<{ runId: string; reasons: string[] }> } | null>(null);
  const [compareCandidates, setCompareCandidates] = useState<Array<{ runId: string; stepId: string; label: string }>>([]);
  const [comparison, setComparison] = useState<CompareResult | null>(null);
  const [candidateViews, setCandidateViews] = useState<Record<string, CandidateView>>({});
  const [syncZoom, setSyncZoom] = useState(1);
  const [syncAxis, setSyncAxis] = useState<'xy' | 'xz' | 'yz'>('xy');
  const [windowLower, setWindowLower] = useState('');
  const [windowUpper, setWindowUpper] = useState('');
  const [windowResult, setWindowResult] = useState<ProcessWindowResult | null>(null);
  const [reportTitle, setReportTitle] = useState('Workflow report');
  const [reportConclusions, setReportConclusions] = useState('');
  const [reportDocument, setReportDocument] = useState<ReportDocument | null>(null);
  const [calibrationCsv, setCalibrationCsv] = useState('');
  const [calibrationName, setCalibrationName] = useState('calibration');
  const [calibrationModel, setCalibrationModel] = useState('scale_offset');
  const [calibrationMethod, setCalibrationMethod] = useState<'least-squares' | 'nelder-mead'>('least-squares');
  const [calibrationDataset, setCalibrationDataset] = useState<CalibrationDataset | null>(null);
  const [calibrationFit, setCalibrationFit] = useState<CalibrationFit | null>(null);
  const [previousCompile, setPreviousCompile] = useState<CompileInfo | null>(null);
  const [compileDiff, setCompileDiff] = useState<{ addedStepIds: string[]; removedStepIds: string[]; movedStepIds: string[]; changedStepIds: string[]; affectedStepIds: string[]; details?: Array<{ stepId: string; before: Record<string, unknown>; after: Record<string, unknown> }> } | null>(null);
  const [templateVariable, setTemplateVariable] = useState('0.02');
  const [experimentRows, setExperimentRows] = useState(Array.from({ length: 10 }, (_, index) => ({
    duration: (0.01 + index * 0.001).toFixed(4), replicates: index === 0 ? 2 : 1, seed: 20261029 + index,
  })));
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
    const steps = (project?.steps ?? []).filter(step => step.enabled);
    if (!steps.length) return;
    setDoeFactors(previous => previous.map((row, index) => row.stepId
      ? row
      : { ...row, stepId: steps[Math.min(index, steps.length - 1)]?.id ?? '' }));
  }, [project]);

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
    if (compile) setPreviousCompile(compile);
    setCompile(result);
    setRoute(null);
    setSegment(null);
  });

  const saveTemplate = () => run('template', async () => {
    if (!compile || !project) throw new Error('先采纳工程');
    const templateId = `tpl_${Date.now().toString(36)}`;
    const sourceSteps = (project.steps ?? []) as Array<Record<string, unknown>>;
    const enabled = sourceSteps.filter(step => step.enabled);
    const variableIds = new Set(enabled.slice(-2).map(step => step.id));
    const steps = sourceSteps.map(step => variableIds.has(step.id) ? { ...step, duration: '${duration}' } : step);
    await postJson('/api/v1/workflow/templates', {
      schemaVersion: 1, templateId, name: `${compile.projectName} · duration variable`, description: '',
      steps, variables: [{ name: 'duration', value: Number(templateVariable) || 0.02, unit: 's' }],
    });
    const data = await api<{ templates: TemplateSummary[] }>('/api/v1/workflow/templates');
    setTemplates(data.templates);
    setTemplateId(templateId);
  });

  const compareCompile = () => run('diff', async () => {
    if (!compile || !previousCompile) throw new Error('没有上一版 Compile');
    const result = await api<typeof compileDiff>(`/api/v1/workflow/compiles/${previousCompile.compileId}/diff/${compile.compileId}`);
    setCompileDiff(result);
  });

  const applyTemplate = (mode: 'replace' | 'append') => run('template', async () => {
    if (!project || !templateId) throw new Error('先选择模板');
    const value = Number(templateVariable) || 0.02;
    const result = await postJson<CompileInfo>('/api/v1/workflow/flows/from-template', {
      project, templateId, flowId: compile?.flowId, variables: { duration: value }, mode,
    });
    if (compile) setPreviousCompile(compile);
    setCompile(result); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setProject(previous => previous ? { ...previous, steps: result.authoredSteps ?? previous.steps } : previous);
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
    let recipeVersion = 1;
    let recipeSaved = false;
    while (!recipeSaved && recipeVersion <= 20) {
      try {
        await postJson('/api/v1/workflow/metrology-recipes', {
          schemaVersion: 1, recipeId, version: recipeVersion, metricId,
          name: `${recipeMethod} @ ${recipeMaterial}`, dimension: compile.dimension,
          method: recipeMethod, parameters, material: recipeMaterial,
        });
        recipeSaved = true;
      } catch (exc) {
        if (exc instanceof Error && exc.message.includes('already exists')) recipeVersion += 1;
        else throw exc;
      }
    }
    if (!recipeSaved) throw new Error('配方版本无法保存');
    const result = await postJson<Measurement>('/api/v1/workflow/measurements/from-run', {
      recipeId, recipeVersion, runId: segment.runId, stepId: segment.throughStepId,
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
    setActiveRecipeVersion(recipeVersion);
  });


  const executeAll = () => run('execute', async () => {
    if (!route) throw new Error('先创建 Route');
    for (const item of route.segments.filter(segmentItem => segmentItem.status === 'planned')) {
      const started = await postJson<{ segment: RoutePlan['segments'][number]; runId: string }>(
        `/api/v1/workflow/segments/${item.segmentId}/execute`, {},
      );
      setSegment(started.segment);
      setRoute(previous => previous ? { ...previous, segments: previous.segments.map(row => row.segmentId === started.segment.segmentId ? started.segment : row) } : previous);
      const done = await waitSegment(item.segmentId);
      setSegment(done);
      setRoute(previous => previous ? { ...previous, segments: previous.segments.map(row => row.segmentId === done.segmentId ? done : row) } : previous);
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
    const variants = experimentRows.map(row => ({
      parameters: { [authored]: { duration: Number(row.duration) || 0.01 } },
      replicates: row.replicates,
      seed: row.seed,
    }));
    const doc = await postJson<ExperimentDoc>('/api/v1/workflow/experiments', {
      name: `${compile.projectName} · 10 variants`, project, flowId: compile.flowId,
      compileId: compile.compileId,
      recipeId: activeRecipeId, recipeVersion: activeRecipeVersion, variants,
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

  const refreshLayouts = async () => {
    const data = await api<{ layouts: Array<{ layoutId: string; fileName: string; structureCount: number }> }>('/api/v1/workflow/layouts');
    setLayouts(data.layouts);
    return data.layouts;
  };

  const selectStructure = (layout: LayoutDetail, name: string) => {
    const structure = layout.structures.find(item => item.name === name);
    setLayoutStructure(name);
    setSelectedPairs((structure?.layerDatatypes ?? []).map(([layer, datatype]) => `${layer}:${datatype}`));
    if (structure?.bounds) {
      setClipBounds({ ...structure.bounds });
      setCanvas(null);
    }
  };

  const registerLayout = () => run('layout', async () => {
    if (!gds) throw new Error('先上传 GDS');
    const layout = await postJson<LayoutDetail>('/api/v1/workflow/layouts', {
      assetId: gds.assetId, fileName: gds.fileName,
    });
    setActiveLayout(layout);
    const first = layout.structures[0];
    if (first) selectStructure(layout, first.name);
    await refreshLayouts();
    const listed = await api<{ clips: Clip[] }>(`/api/v1/workflow/clips?layout_id=${layout.layoutId}`);
    setClips(listed.clips);
  });

  const chooseLayout = async (layoutId: string) => {
    if (!layoutId) { setActiveLayout(null); return; }
    const layout = await api<LayoutDetail>(`/api/v1/workflow/layouts/${layoutId}`);
    setActiveLayout(layout);
    const first = layout.structures[0];
    if (first) selectStructure(layout, first.name);
    const listed = await api<{ clips: Clip[] }>(`/api/v1/workflow/clips?layout_id=${layoutId}`);
    setClips(listed.clips);
  };

  const previewClip = () => run('preview', async () => {
    if (!activeLayout) throw new Error('先注册 Layout');
    const layers = selectedPairs.map(key => {
      const [layer, datatype] = key.split(':').map(Number);
      return { layer, datatype, material: 'Si' };
    });
    if (!layers.length) throw new Error('至少选择一个层');
    const preview = await postJson<ClipCanvas>(`/api/v1/workflow/layouts/${activeLayout.layoutId}/preview`, {
      structure: layoutStructure, layers, bbox: clipBounds,
    });
    setCanvas(preview);
    setActiveClip(null);
  });

  const saveClip = () => run('clip', async () => {
    if (!activeLayout) throw new Error('先注册 Layout');
    const layers = selectedPairs.map(key => {
      const [layer, datatype] = key.split(':').map(Number);
      return { layer, datatype, material: 'Si' };
    });
    const annotations = annotationText.trim()
      ? [{ text: annotationText.trim(), x: Number(annotationX) || 0, y: Number(annotationY) || 0 }]
      : [];
    const clip = await postJson<Clip>('/api/v1/workflow/clips', {
      layoutId: activeLayout.layoutId, name: clipName || `clip ${clips.length + 1}`,
      structure: layoutStructure, layers, bbox: clipBounds, annotations,
    });
    setClips(previous => [clip, ...previous]);
    setActiveClip(clip);
    setCanvas(clip);
    setAnnotationText('');
  });

  const selectClip = (clip: Clip) => {
    setActiveClip(clip);
    setCanvas(clip);
    setClipName(clip.name);
    if (activeLayout) selectStructure(activeLayout, clip.structure);
    setClipBounds({ ...clip.bbox });
  };

  const addAnnotation = () => run('annotation', async () => {
    if (!activeClip) throw new Error('先选择 Clip');
    if (!annotationText.trim()) throw new Error('填写标注文本');
    const updated = await postJson<Clip>(`/api/v1/workflow/clips/${activeClip.clipId}/annotations`, {
      text: annotationText.trim(), x: Number(annotationX) || 0, y: Number(annotationY) || 0,
    });
    setActiveClip(updated);
    setCanvas(updated);
    setClips(previous => previous.map(item => item.clipId === updated.clipId ? updated : item));
    setAnnotationText('');
  });

  const compileClip = () => run('clip-compile', async () => {
    if (!activeClip) throw new Error('先保存或选择 Clip');
    if (!project) throw new Error('先导入工程 JSON');
    const result = await postJson<CompileInfo>(`/api/v1/workflow/clips/${activeClip.clipId}/compile`, {
      project, flowId: compile?.flowId,
    });
    if (compile) setPreviousCompile(compile);
    setCompile(result); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
  });

  const createDoe = () => run('doe', async () => {
    if (!project || !compile || !activeRecipeId) throw new Error('先完成一次测量以固定配方');
    const factors = doeFactors
      .map(row => ({
        stepId: row.stepId,
        parameter: row.parameter,
        values: row.values.split(',').map(value => Number(value.trim())).filter(value => Number.isFinite(value)),
      }))
      .filter(row => row.stepId && row.values.length > 0);
    if (!factors.length) throw new Error('至少配置一个因子');
    const document = await postJson<ExperimentDoc>('/api/v1/workflow/experiments/doe', {
      name: `${compile.projectName} · DOE`, project, flowId: compile.flowId,
      compileId: compile.compileId,
      recipeId: activeRecipeId, recipeVersion: activeRecipeVersion, factors,
      replicates: doeReplicates, seed: Number(doeSeed) || 20261029,
      maxAttempts: 2, costPerRunUsd: Number(doeCost) || 0,
    });
    setExperimentDoc(document);
    setExperimentTable(null);
  });

  const previewRetention = () => run('retention', async () => {
    if (!experimentDoc) throw new Error('先创建实验');
    const info = await postJson<{ releasable: string[]; protected: Array<{ runId: string; reasons: string[] }> }>(
      `/api/v1/workflow/experiments/${experimentDoc.experimentId}/retention/preview`, { mode: 'release-runs' },
    );
    setRetentionInfo(info);
  });

  const applyRetention = (mode: 'keep-all' | 'release-runs') => run('retention', async () => {
    if (!experimentDoc) throw new Error('先创建实验');
    await postJson(`/api/v1/workflow/experiments/${experimentDoc.experimentId}/retention`, { mode });
    setExperimentDoc(await api<ExperimentDoc>(`/api/v1/workflow/experiments/${experimentDoc.experimentId}`));
    setRetentionInfo(await postJson<{ releasable: string[]; protected: Array<{ runId: string; reasons: string[] }> }>(
      `/api/v1/workflow/experiments/${experimentDoc.experimentId}/retention/preview`, { mode: 'release-runs' },
    ));
  });

  const loadExperimentTable = () => run('table', async () => {
    if (!experimentDoc) throw new Error('先创建实验');
    const query = new URLSearchParams();
    if (tableMin.trim()) query.set('min_value', tableMin.trim());
    if (tableMax.trim()) query.set('max_value', tableMax.trim());
    query.set('sort', tableSort);
    query.set('order', tableSort === 'ordinal' ? 'asc' : 'desc');
    setExperimentTable(await api<ExperimentTable>(`/api/v1/workflow/experiments/${experimentDoc.experimentId}/table?${query.toString()}`));
  });

  const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

  const waitSegment = async (segmentId: string) => {
    for (let i = 0; i < 300; i += 1) {
      const current = await api<RoutePlan['segments'][number]>(`/api/v1/workflow/segments/${segmentId}`);
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(current.status)) return current;
      await sleep(500);
    }
    throw new Error('experiment segment did not finish');
  };

  const showMetricDetail = (item: Measurement) => run('metric-detail', async () => {
    const [recipe, provenance] = await Promise.all([
      api<Recipe>(`/api/v1/workflow/metrology-recipes/${item.recipeId}?version=${item.recipeVersion}`),
      api<Provenance>(`/api/v1/workflow/measurements/${item.measurementId}/provenance`),
    ]);
    setMetricDetail({ measurement: item, recipe, provenance });
  });

  const loadExperimentCandidates = () => {
    if (!experimentDoc) return;
    const steps = (compile?.compile.project.steps ?? []).filter(step => step.enabled);
    const stepId = steps[steps.length - 1]?.id ?? '';
    const rows = experimentDoc.variants.slice(0, 3).map(variant => {
      const replicate = variant.replicates.find(item => item.runId) ?? variant.replicates[0];
      return { runId: replicate?.runId ?? '', stepId, label: `V${variant.ordinal}` };
    }).filter(row => row.runId && row.stepId);
    setCompareCandidates(rows);
  };

  const addCurrentCandidate = () => {
    if (!segment?.runId) return;
    setCompareCandidates(previous => [
      ...previous.filter(item => item.runId !== segment.runId),
      { runId: segment.runId!, stepId: segment.throughStepId, label: 'current' },
    ].slice(0, 3));
  };

  const runComparison = () => run('compare', async () => {
    const candidates = compareCandidates.filter(item => item.runId && item.stepId);
    if (candidates.length < 2) throw new Error('至少需要两个候选');
    const result = await postJson<CompareResult>('/api/v1/workflow/analysis/compare', {
      candidates, tolerance: 1e-4,
    });
    setComparison(result);
    const views = await Promise.all(candidates.map(item => api<CandidateView>(
      `/api/v1/workflow/analysis/candidates/${item.runId}/${item.stepId}/view`,
    )));
    setCandidateViews(Object.fromEntries(candidates.map((item, index) => [`${item.runId}:${item.stepId}`, views[index]])));
  });

  const computeProcessWindow = () => run('window', async () => {
    if (!experimentDoc) throw new Error('先创建实验');
    const lower = Number(windowLower);
    const upper = Number(windowUpper);
    if (!Number.isFinite(lower) || !Number.isFinite(upper) || lower >= upper) throw new Error('填写有效的窗口上下限');
    setWindowResult(await postJson<ProcessWindowResult>('/api/v1/workflow/analysis/process-window', {
      experimentId: experimentDoc.experimentId, lower, upper,
    }));
  });

  const generateReport = () => run('report', async () => {
    const lower = Number(windowLower);
    const upper = Number(windowUpper);
    const payload: Record<string, unknown> = {
      title: reportTitle || `${compile?.projectName ?? 'Workflow'} report`,
      conclusions: reportConclusions,
    };
    if (experimentDoc) payload.experimentId = experimentDoc.experimentId;
    if (measurement) payload.measurementId = measurement.measurementId;
    if (segment?.runId) { payload.runId = segment.runId; payload.stepId = segment.throughStepId; }
    if (Number.isFinite(lower) && Number.isFinite(upper) && lower < upper) { payload.lower = lower; payload.upper = upper; }
    setReportDocument(await postJson<ReportDocument>('/api/v1/workflow/analysis/report', payload));
  });

  const openReport = () => {
    if (!reportDocument) return;
    const url = URL.createObjectURL(new Blob([reportDocument.html], { type: 'text/html' }));
    window.open(url, '_blank');
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const downloadReportHtml = () => {
    if (!reportDocument) return;
    const url = URL.createObjectURL(new Blob([reportDocument.html], { type: 'text/html' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${reportDocument.reportId}.html`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const loadCalibrationFile = async (file: File) => {
    setCalibrationCsv(await file.text());
    setCalibrationName(file.name.replace(/\.[^.]+$/, ''));
    setCalibrationDataset(null);
    setCalibrationFit(null);
  };

  const createCalibrationDataset = () => run('calibration', async () => {
    if (!calibrationCsv.trim()) throw new Error('先导入 CSV');
    const dataset = await postJson<CalibrationDataset>('/api/v1/workflow/calibration/datasets', {
      name: calibrationName || 'calibration', csv: calibrationCsv,
    });
    setCalibrationDataset(dataset);
    setCalibrationFit(null);
  });

  const fitCalibration = () => run('calibration', async () => {
    if (!calibrationDataset) throw new Error('先创建数据集');
    setCalibrationFit(await postJson<CalibrationFit>('/api/v1/workflow/calibration/fit', {
      datasetId: calibrationDataset.datasetId, model: calibrationModel, method: calibrationMethod,
    }));
  });

  const analysisViewBox = useMemo(() => {
    const views = Object.values(candidateViews);
    if (!views.length) return '0 0 1 1';
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const view of views) {
      for (const point of view.points) {
        const x = syncAxis === 'yz' ? point[1] : point[0];
        const y = syncAxis === 'xy' ? point[1] : point[2];
        x0 = Math.min(x0, x); x1 = Math.max(x1, x);
        y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
    }
    if (!Number.isFinite(x0) || !Number.isFinite(y0)) return '0 0 1 1';
    const padding = Math.max((x1 - x0) * 0.08, (y1 - y0) * 0.08, 1e-6);
    const width = Math.max(x1 - x0 + padding * 2, 1e-6);
    const height = Math.max(y1 - y0 + padding * 2, 1e-6);
    const zoom = Math.max(0.25, syncZoom);
    return `${x0 - padding + width * (1 - 1 / zoom) / 2} ${-(y1 + padding) + height * (1 - 1 / zoom) / 2} ${width / zoom} ${height / zoom}`;
  }, [candidateViews, syncAxis, syncZoom]);

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
          {gds && <div className="wf-clip-editor">
            <div className="wf-card-subhead">版图 / Clip</div>
            <div className="wf-template-row">
              <button className="wf-button ghost" disabled={!!busy} onClick={() => void registerLayout()}>注册为 Layout</button>
              <select aria-label="版图选择" value={activeLayout?.layoutId ?? ''} onChange={event => void chooseLayout(event.target.value)}>
                <option value="">选择 Layout</option>
                {layouts.map(item => <option key={item.layoutId} value={item.layoutId}>{item.fileName} · {item.structureCount} 结构</option>)}
              </select>
            </div>
            {activeLayout && <>
              <div className="wf-template-row">
                <select aria-label="版图结构" value={layoutStructure} onChange={event => selectStructure(activeLayout, event.target.value)}>
                  {activeLayout.structures.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
                </select>
                <input aria-label="Clip 名称" value={clipName} onChange={event => setClipName(event.target.value)} placeholder="Clip 名称" />
              </div>
              <div className="wf-layer-picks">
                {(activeLayout.structures.find(item => item.name === layoutStructure)?.layerDatatypes ?? []).map(([layer, datatype]) => {
                  const key = `${layer}:${datatype}`;
                  return <label key={key}><input type="checkbox" checked={selectedPairs.includes(key)} onChange={event => setSelectedPairs(previous => event.target.checked ? [...previous, key] : previous.filter(item => item !== key))} /> L{layer}/{datatype}</label>;
                })}
              </div>
              <div className="wf-bounds">
                {(['x0', 'y0', 'x1', 'y1'] as const).map(key => <input key={key} aria-label={`Clip ${key}`} value={String(clipBounds[key])} onChange={event => setClipBounds(previous => ({ ...previous, [key]: Number(event.target.value) }))} />)}
                <button className="wf-button ghost" disabled={!!busy} onClick={() => void previewClip()}>预览</button>
                <button className="wf-button ghost" disabled={!!busy} onClick={() => void saveClip()}>保存 Clip</button>
              </div>
            </>}
            {canvas && <div className="wf-canvas-wrap">
              <svg viewBox={`0 0 ${canvas.width} ${canvas.height}`} className="wf-clip-canvas" role="img" aria-label="版图 Clip 画布">
                {canvas.polygons.map((polygon, index) => <polygon
                  key={`${polygon.layer}-${index}`}
                  points={polygon.points.map(([x, y]) => `${x},${canvas.height - y}`).join(' ')}
                  fill={LAYER_COLORS[polygon.layer % LAYER_COLORS.length]}
                  fillOpacity="0.45"
                  stroke={LAYER_COLORS[polygon.layer % LAYER_COLORS.length]}
                  strokeWidth={Math.max(canvas.width, canvas.height) / 500}
                />)}
                {activeClip?.annotations.map(annotation => <g key={annotation.annotationId}>
                  <circle cx={annotation.x} cy={activeClip.height - annotation.y} r={Math.max(canvas.width, canvas.height) / 80} className="wf-annotation" />
                  <text x={annotation.x} y={activeClip.height - annotation.y} className="wf-annotation-text">{annotation.text}</text>
                </g>)}
              </svg>
              <div className="wf-muted">{canvas.polygonCount} 多边形 · {canvas.pointCount} 点 · {canvas.width.toFixed(3)} × {canvas.height.toFixed(3)} µm</div>
            </div>}
            {activeClip && <div className="wf-summary"><strong>{activeClip.name}</strong><span>{activeClip.polygonCount} 多边形</span><span><code>{activeClip.clipHash.slice(0, 12)}…</code></span><span>{activeClip.annotations.length} 标注</span></div>}
            {activeClip && <div className="wf-template-row">
              <input aria-label="标注文本" value={annotationText} onChange={event => setAnnotationText(event.target.value)} placeholder="标注文本" />
              <input aria-label="标注 X" value={annotationX} onChange={event => setAnnotationX(event.target.value)} />
              <input aria-label="标注 Y" value={annotationY} onChange={event => setAnnotationY(event.target.value)} />
              <button className="wf-button ghost" disabled={!!busy} onClick={() => void addAnnotation()}>添加标注</button>
            </div>}
            {clips.length > 0 && <div className="wf-clip-list">
              {clips.map(clip => <button key={clip.clipId} type="button" className={activeClip?.clipId === clip.clipId ? 'active' : ''} onClick={() => selectClip(clip)}>{clip.name} · {clip.polygonCount}</button>)}
            </div>}
            {activeClip && <button className="wf-button primary" disabled={!project || !!busy} onClick={() => void compileClip()}>
              {busy === 'clip-compile' ? '编译中…' : '用此 Clip 编译'}
            </button>}
          </div>}
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
              <select aria-label="模板选择" value={templateId} onChange={event => setTemplateId(event.target.value)}>
                <option value="">选择模板</option>
                {templates.map(item => <option key={item.templateId} value={item.templateId}>{item.name} · {item.stepCount} 步</option>)}
              </select>
              <button className="wf-button ghost" disabled={!project || !templateId || !!busy} onClick={() => void applyTemplate('replace')}>替换流程</button>
              <button className="wf-button ghost" disabled={!project || !templateId || !!busy} onClick={() => void applyTemplate('append')}>插入模板</button>
            </div>
            <div className="wf-template-row">
              <input aria-label="模板变量值" value={templateVariable} onChange={event => setTemplateVariable(event.target.value)} />
              <button className="wf-button ghost" disabled={!compile || !!busy} onClick={() => void compareCompile()}>比较上一版</button>
            </div>
            {compileDiff && <div className="wf-summary"><strong>Compile diff</strong><span>+{compileDiff.addedStepIds.length} / -{compileDiff.removedStepIds.length} / moved {compileDiff.movedStepIds.length} / changed {compileDiff.changedStepIds.length}</span><span>affected: {compileDiff.affectedStepIds.map(id => id.slice(0, 8)).join(', ') || '—'}</span>{compileDiff.details?.slice(0, 3).map(item => <span key={item.stepId}>{item.stepId.slice(0, 8)}: {String(item.before.duration ?? '—')} → {String(item.after.duration ?? '—')}</span>)}</div>}
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
                {route.reuse.filter(item => item.attemptId === attempt.attemptId).map(item => <div key={item.reuseId} className="wf-segment-row"><span className="wf-status verified">reuse</span><code>{item.reusedFromSegmentId.slice(0, 8)} · {item.fromStepId.slice(0, 6)} → {item.toStepId.slice(0, 6)}</code></div>)}
                {route.segments.filter(item => item.attemptId === attempt.attemptId).map(item => <div key={item.segmentId} className="wf-segment-row"><span className={`wf-status ${item.status}`}>{item.status}</span><code>{item.startAfterStepId ? 'after ' + item.startAfterStepId.slice(0, 8) : 'start'} → {item.throughStepId.slice(0, 8)}</code>{item.status === 'planned' && <span className="wf-stale">待重跑</span>}</div>)}
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
          {metrics.length > 0 && <table className="wf-table"><thead><tr><th>Status</th><th>Value</th><th>Recipe / Step</th><th>Binding</th><th /></tr></thead><tbody>{metrics.map(item => <tr key={item.measurementId}><td>{item.status}</td><td>{item.value === null ? item.reason : `${item.value.toFixed(4)} ${item.unit}`}</td><td>{item.recipeId} v{item.recipeVersion} · {item.method} · {item.material} · {item.sourceStepId?.slice(0, 8)}</td><td>{item.bindingStatus} / {item.equivalenceStatus}</td><td><button className="wf-link" aria-label={`查看 ${item.measurementId} 证据`} onClick={() => void showMetricDetail(item)}>详情</button></td></tr>)}</tbody></table>}
          {metricDetail && <div className="wf-evidence">
            <div className="wf-evidence-head"><strong>{metricDetail.measurement.measurementId}</strong><span className={`wf-status ${metricDetail.measurement.status}`}>{metricDetail.measurement.status}</span></div>
            <div className="wf-evidence-grid">
              <span>method</span><code>{metricDetail.recipe.method}</code>
              <span>material</span><code>{metricDetail.recipe.material}</code>
              <span>recipe</span><code>{metricDetail.recipe.recipeId} v{metricDetail.recipe.version}</code>
              <span>parameters</span><code>{JSON.stringify(metricDetail.recipe.parameters)}</code>
              <span>snapshot</span><code>{metricDetail.measurement.sourceSnapshotHash.slice(0, 16)}…</code>
              <span>intersections</span><code>{metricDetail.measurement.intersections.join(', ') || '—'}</code>
              <span>uncertainty</span><code>{metricDetail.measurement.uncertainty ?? '—'} ({metricDetail.measurement.uncertaintyStatus})</code>
              <span>binding</span><code>{metricDetail.measurement.bindingStatus}{metricDetail.measurement.bindingEvidence ? ` · ${metricDetail.measurement.bindingEvidence.methodVersion}` : ''}</code>
              <span>equivalence</span><code>{metricDetail.measurement.equivalenceStatus}</code>
              <span>evidence records</span><code>{metricDetail.provenance.verifications.map(row => `${row.status}/${row.evidence.methodVersion}`).join(', ') || '—'}</code>
            </div>
          </div>}
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
          <table className="wf-table"><thead><tr><th>#</th><th>Duration</th><th>Reps</th><th>Seed</th></tr></thead><tbody>{experimentRows.map((row, index) => <tr key={index}><td>{index + 1}</td><td><input aria-label={`变体 ${index + 1} duration`} value={row.duration} onChange={event => setExperimentRows(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, duration: event.target.value } : item))} /></td><td><input aria-label={`变体 ${index + 1} replicates`} type="number" min={1} max={8} value={row.replicates} onChange={event => setExperimentRows(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, replicates: Number(event.target.value) || 1 } : item))} /></td><td><input aria-label={`变体 ${index + 1} seed`} type="number" value={row.seed} onChange={event => setExperimentRows(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, seed: Number(event.target.value) || 0 } : item))} /></td></tr>)}</tbody></table>
          {experimentDoc && <div className="wf-muted">Experiment status: {experimentDoc.status}{experimentDoc.kind === 'doe' ? ' · DOE' : ''}</div>}
          {experimentDoc?.queue && <div className="wf-summary"><strong>Queue</strong><span>{experimentDoc.queue.succeeded}/{experimentDoc.queue.total} succeeded · pending {experimentDoc.queue.pending} · running {experimentDoc.queue.running}</span><span>ETA {experimentDoc.queue.estimatedSecondsRemaining}s</span><span>cost ${experimentDoc.queue.estimatedCostUsd.toFixed(4)}</span><span>retries {experimentDoc.queue.retries}</span></div>}
          {experimentDoc && <table className="wf-table"><thead><tr><th>#</th><th>Status</th><th>Value</th><th>Run</th></tr></thead><tbody>{experimentDoc.variants.map(variant => <tr key={variant.ordinal}><td>{variant.ordinal}</td><td>{variant.replicates[0]?.status}</td><td>{variant.replicates[0]?.value ?? '—'} {variant.replicates[0]?.unit ?? ''}</td><td><code>{variant.replicates[0]?.runId?.slice(0, 8) ?? '—'}</code></td></tr>)}</tbody></table>}
          <div className="wf-card-subhead">DOE 全因子</div>
          {(project?.steps ?? []).filter(step => step.enabled).length === 0 && <div className="wf-muted">导入并采纳工程后可配置因子。</div>}
          {doeFactors.map((row, index) => <div className="wf-doe-row" key={index}>
            <select aria-label={`因子 ${index + 1} 步骤`} value={row.stepId} onChange={event => setDoeFactors(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, stepId: event.target.value } : item))}>
              <option value="">步骤</option>
              {(project?.steps ?? []).filter(step => step.enabled).map(step => <option key={step.id} value={step.id}>{step.id}</option>)}
            </select>
            <select aria-label={`因子 ${index + 1} 参数`} value={row.parameter} onChange={event => setDoeFactors(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, parameter: event.target.value } : item))}>
              <option value="rateMagnitude">rateMagnitude</option>
              <option value="duration">duration</option>
              <option value="stickingProbability">stickingProbability</option>
            </select>
            <input aria-label={`因子 ${index + 1} 取值`} value={row.values} onChange={event => setDoeFactors(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, values: event.target.value } : item))} />
          </div>)}
          <div className="wf-template-row">
            <input aria-label="DOE 重复数" type="number" min={1} max={8} value={doeReplicates} onChange={event => setDoeReplicates(Number(event.target.value) || 1)} />
            <input aria-label="DOE 种子" value={doeSeed} onChange={event => setDoeSeed(event.target.value)} />
            <input aria-label="单次成本" value={doeCost} onChange={event => setDoeCost(event.target.value)} />
            <button className="wf-button primary" disabled={!compile || !activeRecipeId || !!busy} onClick={() => void createDoe()}>设计 DOE</button>
          </div>
          {experimentDoc?.design && <div className="wf-summary"><strong>DOE</strong><span>{experimentDoc.design.conditionCount} 条件 × {experimentDoc.design.replicates} 重复 = {experimentDoc.design.logicalRunCount} 逻辑执行</span></div>}
          {experimentDoc?.kind === 'doe' && <div className="wf-template-row">
            <input aria-label="筛选最小均值" value={tableMin} onChange={event => setTableMin(event.target.value)} placeholder="mean ≥" />
            <input aria-label="筛选最大均值" value={tableMax} onChange={event => setTableMax(event.target.value)} placeholder="mean ≤" />
            <select aria-label="排序字段" value={tableSort} onChange={event => setTableSort(event.target.value)}>
              <option value="ordinal">#</option>
              <option value="mean">mean</option>
              <option value="std">std</option>
              <option value="n">n</option>
            </select>
            <button className="wf-button ghost" disabled={!!busy} onClick={() => void loadExperimentTable()}>筛选 / 排序</button>
            <a className="wf-button ghost" href={`/api/v1/workflow/experiments/${experimentDoc.experimentId}/export.csv`} download>导出 CSV</a>
          </div>}
          {experimentTable && <table className="wf-table"><thead><tr><th>#</th><th>n</th><th>Mean</th><th>Std</th><th>CI95</th><th>Status</th></tr></thead><tbody>{experimentTable.rows.map(row => <tr key={row.ordinal}><td>{row.ordinal}</td><td>{row.n}</td><td>{row.mean === null ? '—' : row.mean.toFixed(6)}</td><td>{row.std === null ? '—' : row.std.toFixed(6)}</td><td>{row.ci95 === null ? '—' : row.ci95.toFixed(6)}</td><td>{row.statisticalStatus}</td></tr>)}</tbody></table>}
          {experimentDoc && <div className="wf-card-subhead">保留策略</div>}
          {experimentDoc && <div className="wf-template-row">
            <button className="wf-button ghost" disabled={!!busy} onClick={() => void previewRetention()}>预览释放</button>
            <button className="wf-button ghost" disabled={!!busy} onClick={() => void applyRetention('release-runs')}>应用 release-runs</button>
            <button className="wf-button ghost" disabled={!!busy} onClick={() => void applyRetention('keep-all')}>恢复 keep-all</button>
            <span className="wf-muted">当前 {experimentDoc.retention?.mode ?? 'keep-all'}</span>
          </div>}
          {retentionInfo && <div className="wf-summary"><strong>Retention</strong><span>可释放 {retentionInfo.releasable.length} run</span><span>受保护 {retentionInfo.protected.length} run</span></div>}
        </section>}
        {zone === 'analyze' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>05</span><h2>Analyze</h2></div>
          <p className="wf-muted">候选快照对比、结构 diff、process window 与基础标定；数值等价独立于绑定状态。</p>
          {measurement ? <div className="wf-summary"><strong>{measurement.status}</strong><span>{measurement.value === null ? measurement.reason : `${measurement.value.toFixed(4)} ${measurement.unit}`}</span><span>binding {measurement.bindingStatus} · equivalence {measurement.equivalenceStatus}</span></div> : <div className="wf-empty">完成一次运行与测量后显示指标。</div>}
          <div className="wf-card-subhead">候选 / 多视图</div>
          <div className="wf-template-row">
            <button className="wf-button ghost" disabled={!experimentDoc || !!busy} onClick={loadExperimentCandidates}>载入实验前 3 个候选</button>
            <button className="wf-button ghost" disabled={!segment?.runId || !!busy} onClick={addCurrentCandidate}>加入当前 Run</button>
            <button className="wf-button primary" disabled={compareCandidates.length < 2 || !!busy} onClick={() => void runComparison()}>比较并加载视图</button>
          </div>
          {compareCandidates.length > 0 && <table className="wf-table"><thead><tr><th>候选</th><th>Run</th><th>Step</th></tr></thead><tbody>{compareCandidates.map(item => <tr key={item.runId}><td>{item.label}</td><td><code>{item.runId.slice(0, 10)}…</code></td><td><code>{item.stepId.slice(0, 10)}…</code></td></tr>)}</tbody></table>}
          {comparison && <div className="wf-compare-controls">
            <label>同步缩放 <input aria-label="同步缩放" type="range" min="0.5" max="3" step="0.1" value={syncZoom} onChange={event => setSyncZoom(Number(event.target.value))} /></label>
            <select aria-label="同步轴" value={syncAxis} onChange={event => setSyncAxis(event.target.value as typeof syncAxis)}>
              <option value="xy">XY</option>
              <option value="xz">XZ</option>
              <option value="yz">YZ</option>
            </select>
          </div>}
          {comparison && <div className="wf-compare-grid">
            {comparison.candidates.map((candidate, index) => {
              const view = candidateViews[`${candidate.runId}:${candidate.stepId}`];
              return <figure key={`${candidate.runId}:${candidate.stepId}`}>
                <figcaption><strong>{candidate.label ?? `candidate ${index + 1}`}</strong><span>{candidate.summary.dimension}D · {candidate.summary.measureKind} · {JSON.stringify(candidate.summary.materialMeasures)}</span></figcaption>
                {view ? <svg viewBox={analysisViewBox} className="wf-compare-canvas" role="img" aria-label={`候选 ${index + 1} 视图`}>
                  {view.dimension === 2 ? view.lines.map((line, lineIndex) => {
                    const a = view.points[line[0]];
                    const b = view.points[line[1]];
                    if (!a || !b) return null;
                    const material = view.lineMaterialIds[lineIndex] ?? -1;
                    return <line key={lineIndex} x1={a[0]} y1={-a[1]} x2={b[0]} y2={-b[1]} stroke={view.materials[String(material)]?.color ?? '#64d2ff'} strokeWidth={2} vectorEffect="non-scaling-stroke" />;
                  }) : view.points.map((point, pointIndex) => {
                    const x = syncAxis === 'yz' ? point[1] : point[0];
                    const y = syncAxis === 'xy' ? point[1] : point[2];
                    const material = view.materialIds[pointIndex] ?? -1;
                    return <circle key={pointIndex} cx={x} cy={-y} r={1.2} fill={view.materials[String(material)]?.color ?? '#64d2ff'} />;
                  })}
                </svg> : <div className="wf-empty">视图加载中…</div>}
              </figure>;
            })}
          </div>}
          {comparison && <table className="wf-table"><thead><tr><th>Pair</th><th>Status</th><th>max point Δ</th><th>bounds Δ</th><th>material Δ</th><th>Reasons</th></tr></thead><tbody>{comparison.pairwise.map((row, index) => <tr key={index}><td>{row.left} ↔ {row.right}</td><td><span className={`wf-status ${row.status}`}>{row.status}</span></td><td>{row.maxPointDelta === null ? '—' : row.maxPointDelta.toExponential(3)}</td><td>{row.boundsDelta.toExponential(3)}</td><td>{JSON.stringify(row.materialDelta)}</td><td>{row.reasons.join('; ') || '—'}</td></tr>)}</tbody></table>}
          <div className="wf-card-subhead">Process Window</div>
          <div className="wf-template-row">
            <input aria-label="窗口下限" value={windowLower} onChange={event => setWindowLower(event.target.value)} placeholder="lower" />
            <input aria-label="窗口上限" value={windowUpper} onChange={event => setWindowUpper(event.target.value)} placeholder="upper" />
            <button className="wf-button primary" disabled={!experimentDoc || !!busy} onClick={() => void computeProcessWindow()}>计算窗口</button>
          </div>
          {windowResult && <>
            <div className="wf-summary"><strong>Window</strong><span>pass {windowResult.summary.pass} · fail {windowResult.summary.fail} · uncertain {windowResult.summary.uncertain} · n/e {windowResult.summary.notEvaluated}</span><span>[{windowResult.lower}, {windowResult.upper}]</span></div>
            <table className="wf-table"><thead><tr><th>#</th><th>Mean</th><th>CI95</th><th>Status</th><th>Margin</th></tr></thead><tbody>{windowResult.rows.map(row => <tr key={row.ordinal}><td>{row.ordinal}</td><td>{row.mean === null ? '—' : row.mean.toExponential(4)}</td><td>{row.ci95 === null ? '—' : row.ci95.toExponential(3)}</td><td>{row.status}</td><td>{windowResult.candidates.find(item => item.ordinal === row.ordinal)?.margin?.toExponential(3) ?? '—'}</td></tr>)}</tbody></table>
            {windowResult.factorWindows.length > 0 && <div className="wf-muted">factor windows: {windowResult.factorWindows.map(item => `${item.stepId}/${item.parameter} [${item.min ?? '—'}, ${item.max ?? '—'}]`).join(' · ')}</div>}
          </>}
          <div className="wf-card-subhead">基础标定</div>
          <input aria-label="标定 CSV 文件" type="file" accept=".csv,text/csv" onChange={event => { const file = event.target.files?.[0]; if (file) void loadCalibrationFile(file); }} />
          <div className="wf-template-row">
            <select aria-label="标定模型" value={calibrationModel} onChange={event => setCalibrationModel(event.target.value)}>
              <option value="scale">scale (1p)</option>
              <option value="scale_offset">scale + offset (2p)</option>
              <option value="quadratic">quadratic (3p)</option>
              <option value="power">power (2p)</option>
              <option value="power_offset">power + offset (3p)</option>
            </select>
            <select aria-label="标定方法" value={calibrationMethod} onChange={event => setCalibrationMethod(event.target.value as typeof calibrationMethod)}>
              <option value="least-squares">least-squares</option>
              <option value="nelder-mead">nelder-mead</option>
            </select>
            <button className="wf-button ghost" disabled={!calibrationCsv || !!busy} onClick={() => void createCalibrationDataset()}>创建数据集</button>
            <button className="wf-button primary" disabled={!calibrationDataset || !!busy} onClick={() => void fitCalibration()}>拟合</button>
          </div>
          {calibrationDataset && <div className="wf-muted">dataset {calibrationDataset.name} · train {calibrationDataset.trainCount} / validation {calibrationDataset.validationCount} · {calibrationDataset.sha256.slice(0, 12)}…</div>}
          {calibrationFit && <>
            <div className="wf-summary"><strong>{calibrationFit.model} / {calibrationFit.method}</strong><span>{calibrationFit.parameterNames.map((name, index) => `${name}=${calibrationFit.parameters[index].toPrecision(5)}`).join(' · ')}</span><span className={calibrationFit.validationStatus === 'validated' ? 'wf-status verified' : 'wf-status'}>{calibrationFit.validationStatus}</span></div>
            <table className="wf-table"><thead><tr><th>Split</th><th>n</th><th>RMSE</th><th>MAE</th><th>Max abs</th></tr></thead><tbody>
              <tr><td>train</td><td>{calibrationFit.trainResiduals.n}</td><td>{calibrationFit.trainResiduals.rmse?.toPrecision(5) ?? '—'}</td><td>{calibrationFit.trainResiduals.mae?.toPrecision(5) ?? '—'}</td><td>{calibrationFit.trainResiduals.maxAbs?.toPrecision(5) ?? '—'}</td></tr>
              <tr><td>validation</td><td>{calibrationFit.validationResiduals.n}</td><td>{calibrationFit.validationResiduals.rmse?.toPrecision(5) ?? '—'}</td><td>{calibrationFit.validationResiduals.mae?.toPrecision(5) ?? '—'}</td><td>{calibrationFit.validationResiduals.maxAbs?.toPrecision(5) ?? '—'}</td></tr>
            </tbody></table>
          </>}
        </section>}
        {zone === 'report' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>06</span><h2>Report</h2></div>
          <p className="wf-muted">服务端 HTML 报告包含 Flow/Compile、Clip、变量、引擎版本、种子、指标、视图、process window 与结论；浏览器打印可保存 PDF。</p>
          <div className="wf-template-row">
            <input aria-label="报告标题" value={reportTitle} onChange={event => setReportTitle(event.target.value)} />
            <button className="wf-button primary" disabled={!!busy} onClick={() => void generateReport()}>{busy === 'report' ? '生成中…' : '生成 HTML 报告'}</button>
            <button className="wf-button ghost" disabled={!reportDocument} onClick={openReport}>打开报告</button>
            <button className="wf-button ghost" disabled={!reportDocument} onClick={downloadReportHtml}>下载 HTML</button>
          </div>
          <textarea aria-label="报告结论" value={reportConclusions} onChange={event => setReportConclusions(event.target.value)} placeholder="结论 / 候选选择说明" />
          {reportDocument && <div className="wf-summary"><strong>{reportDocument.reportId}</strong><span>{reportDocument.createdAt}</span><span><code>{reportDocument.sha256.slice(0, 16)}…</code></span></div>}
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
