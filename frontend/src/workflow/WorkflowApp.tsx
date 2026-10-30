import { useEffect, useMemo, useRef, useState } from 'react';
import './workflow.css';
import { SAMPLE_2D_PLANE, SAMPLE_2D_TRENCH, SAMPLE_3D_HOLE } from './samples';

type Health = { status: string; applicationVersion?: string; viennaps?: string; viennals?: string; activeRunId?: string | null };
type GdsAsset = { assetId: string; fileName: string };
type ProjectStep = {
  id: string; name: string; model: string; operation?: string; enabled: boolean;
  duration?: number; thickness?: number; rateMagnitude?: number;
  depositMaterial?: string; stickingProbability?: number; sourceExponent?: number;
  rateMode?: string;
  [key: string]: unknown;
};
type ProjectGeometrySource = {
  kind: 'parametric' | 'layout';
  clipId?: string;
  clipName?: string;
  layoutId?: string;
  clipHash?: string;
};
type ProjectDoc = {
  name?: string;
  dimension?: number;
  steps?: ProjectStep[];
  geometry?: Record<string, unknown>;
  domain?: Record<string, unknown>;
  solver?: Record<string, unknown>;
  [key: string]: unknown;
};
type ProjectRecord = {
  schemaVersion: number;
  projectId: string;
  name: string;
  dimension: number;
  stepCount: number;
  createdAt: string;
  updatedAt: string;
};
type LibraryCategory = 'material' | 'process' | 'flow' | 'experiment';
type LibraryItem = {
  schemaVersion: number;
  itemId: string;
  category: LibraryCategory;
  name: string;
  description: string;
  payload: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};
type MaterialRow = { id?: number; name: string; color?: string; category?: string; isCustom?: boolean };
type FlowDefinitionSummary = {
  definitionId: string;
  flowId: string;
  revision: number;
  name: string;
  nodeCount: number;
  variableCount: number;
  updatedAt: string;
};
type RecipeSummary = { recipeId: string; version: number; metricId: string; method: string; name: string; createdAt: string };
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
type MeasurementDecision = {
  schemaVersion: number;
  decisionId: string;
  workflowRunId: string;
  routeId: string;
  sourceSegmentId: string;
  sourceAttemptId: string | null;
  sourceCompileId: string;
  sourceCompileHash: string | null;
  sourceRunId: string;
  startAfterStepId: string;
  sourceCheckpointHash: string | null;
  sourcePrefixDigest: string | null;
  seed: number | null;
  measurementId: string;
  measurementStatus: string;
  measurementBindingStatus: string;
  measurementEquivalenceStatus: string;
  recipeId: string;
  recipeVersion: number;
  metricId: string;
  method: string;
  measuredValue: number;
  unit: string;
  operator: string;
  threshold: number;
  result: boolean;
  chosenBranch: 'true' | 'false';
  candidateParameters: {
    true: { parameters: Record<string, Record<string, unknown>> };
    false: { parameters: Record<string, Record<string, unknown>> };
  } | null;
  resolvedParameters: Record<string, Record<string, unknown>>;
  targetCompileId: string;
  targetCompileHash: string | null;
  branchAttemptId: string;
  branchSegmentId: string;
  branchThroughStepId: string | null;
  replayOf: string | null;
  createdAt: string;
};
type MeasurementDecisionResponse = {
  decision: MeasurementDecision;
  plan: RoutePlan;
  workflowRunId: string;
  measurement: Measurement;
  resolvedParameters: Record<string, Record<string, unknown>>;
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
type LibraryBundleSummary = {
  bundleId: string;
  name: string;
  description: string;
  createdAt: string;
  moduleCount: number;
  definitionCount: number;
  templateCount: number;
  experimentTemplateCount: number;
};
type LibraryBundle = {
  schemaVersion: number;
  bundleId: string;
  name: string;
  description: string;
  createdAt: string;
  modules: Array<{ module: { moduleId: string; name: string }; revision: number }>;
  definitions: Array<{ definitionId: string }>;
  templates: Array<{ templateId: string }>;
  experimentTemplates: Array<{ name: string }>;
};
type LibraryImportResult = {
  bundleId: string;
  policy: 'reject' | 'clone';
  modules: Array<{ moduleId: string; revision: number; status: string }>;
  definitionIds: string[];
  templateIds: string[];
  experimentIds: string[];
  skipped: string[];
  conflicts: string[];
  renames: Array<{ kind: string; previousId: string; importedId: string }>;
};
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
  gdsFileName: string;
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
  materialNames?: Record<string, string>;
  pointCount: number;
  lineCount: number;
  triangleCount: number;
  bounds: number[];
  sampledPointCount: number;
  coordinateUnit?: string;
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
type ExpandedModuleStep = {
  authoredStepId: string;
  stepId: string;
  flowPath: string;
  moduleInstanceIdPath: string;
  iterationKey: string;
  moduleId: string | null;
  moduleInstanceId: string | null;
  step: Record<string, unknown> & { name?: string; model?: string };
  parameterProvenance: Record<string, { kind: string; source: string | null; resolvedValue: unknown }>;
};
type ModuleExpansion = {
  definitionId: string;
  flowId: string;
  revision: number;
  stepCount: number;
  maxDepth: number;
  steps: ExpandedModuleStep[];
  variables: Array<{ name: string; value: unknown; unit: string | null; source: string }>;
  iterations: Array<{ path: string; variable: string; index: number; value: unknown }>;
  moduleInstances: Array<{ instancePath: string; moduleId: string; parameters: Record<string, unknown> }>;
};

const DEFAULT_MODULE_JSON = JSON.stringify({
  schemaVersion: 1,
  moduleId: 'mod_ui_etch',
  name: 'UI etch module',
  description: '',
  parameters: [
    { name: 'rate', default: 0.1, unit: 'um/s' },
    { name: 'duration', default: 0.1, unit: 's' },
  ],
  steps: [{
    id: 'etch_ui_a', name: 'etch', enabled: true, operation: 'etch', model: 'isotropic',
    rateMagnitude: '${rate}', duration: '${duration}', rateMode: 'uniform',
  }],
}, null, 2);

const DEFAULT_DEFINITION_JSON = JSON.stringify({
  schemaVersion: 2,
  definitionId: 'def_ui_demo',
  flowId: 'flow_ui_demo',
  revision: 1,
  name: 'UI modular flow',
  description: '',
  variables: [{ name: 'rate', default: 0.1, unit: 'um/s' }],
  nodes: [
    { kind: 'module-call', instanceId: 'call_ui_a', moduleId: 'mod_ui_etch', parameters: { rate: '${rate}' } },
    {
      kind: 'repeat', repeatId: 'loop_ui', loopVariable: 'level', count: 2,
      body: [{ kind: 'module-call', instanceId: 'call_ui_b', moduleId: 'mod_ui_etch', parameters: { rate: '${level * 0.05}' } }],
    },
  ],
  solver: {
    timeStepRatio: 0.25, raysPerPoint: 32, cpuThreads: 1,
    random: { mode: 'fixed', seed: 7 }, enginePreference: 'cpu', timeoutSeconds: 30,
  },
}, null, 2);

const ACTION_LABELS: Record<string, string> = {
  'compile-flow': '编译流程',
  'template': '模板操作',
  'diff': '比较编译版本',
  'route': '创建 Route',
  'execute': '执行 Segment',
  'measure': '测量',
  'decision': '测量决策',
  'decision-replay': '重放决策',
  'branch': '分支',
  'experiment': '实验',
  'layout': '注册版图',
  'preview': '预览 Clip',
  'clip': '保存 Clip',
  'annotation': '添加标注',
  'clip-use': '使用 Clip',
  'module': '保存模块',
  'definition': '保存流程定义',
  'expand': '展开流程定义',
  'definition-compile': '编译模块化流程',
  'library': '共享库',
  'library-save': '保存库条目',
  'library-delete': '删除库条目',
  'library-insert': '插入库条目',
  'doe': '设计 DOE',
  'retention': '保留策略',
  'table': '实验表格',
  'metric-detail': '测量证据',
  'compare': '候选比较',
  'window': 'Process Window',
  'report': '生成报告',
  'calibration': '标定',
  'gds': '上传 GDS',
  'project': '工程',
};

function actionLabel(name: string): string {
  return ACTION_LABELS[name] ?? name;
}

const ZONES = [
  { id: 'setup', num: '01', label: '工程', title: '工程定义', desc: '定义名称、维度、几何来源、计算域与求解器。' },
  { id: 'flow', num: '02', label: '流程', title: '流程编辑', desc: '从库插入步骤，编辑参数并编译流程。' },
  { id: 'run', num: '03', label: '运行', title: '运行与测量', desc: '执行 Segment、测量并查看指标。' },
  { id: 'experiment', num: '04', label: '实验', title: '实验设计', desc: '手工实验、全因子 DOE、队列与聚合。' },
  { id: 'analyze', num: '05', label: '分析', title: '分析', desc: '候选对比、结构 diff、process window 与标定。' },
  { id: 'report', num: '06', label: '报告', title: '报告', desc: '生成包含 provenance 的 HTML 报告。' },
] as const;

const STATUS_LABELS: Record<string, string> = {
  planned: '待执行',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
  verified: '已核验',
  measured: '已测量',
  stale: '待重跑',
  completed: '已完成',
  pending: '待处理',
  not_evaluated: '未评估',
  pass: '通过',
  fail: '失败',
  uncertain: '不确定',
};

const ENGINE_LABELS: Record<string, string> = { cpu: 'CPU', gpu: 'GPU', auto: 'Auto' };

const MATERIAL_ROLES: Array<[string, string]> = [
  ['substrate', '基底 (substrate)'],
  ['film', '薄膜 (film)'],
  ['mask', '掩膜 (mask)'],
  ['resist', '光刻胶 (resist)'],
];

const BINDING_LABELS: Record<string, string> = {
  verified: '已绑定',
  unverified: '未绑定',
  pending: '待绑定',
  not_applicable: '不适用',
  equivalent: '等价',
  not_equivalent: '不等价',
  not_evaluated: '未评估',
};

function bindingLabel(status: string): string {
  return BINDING_LABELS[status] ?? status;
}

function clockTime(date = new Date()): string {
  return date.toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.toLocaleDateString('en-CA')} ${date.toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit' })}`;
}

const PROJECT_STORAGE_KEY = 'viennaps.workflow.projectId';

async function projectRequest(path: string, method: 'POST' | 'PUT', document: unknown): Promise<ProjectRecord> {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(document),
  });
  const text = await response.text();
  let payload: unknown = text;
  try { payload = text ? JSON.parse(text) : {}; } catch { /* keep text */ }
  if (!response.ok) {
    const error = new Error(errorText(payload)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return payload as ProjectRecord;
}

const LIBRARY_CATEGORY_LABELS: Record<LibraryCategory, string> = {
  material: 'Materials · 材料',
  process: 'Processes · 工艺',
  flow: 'Flows · 流程',
  experiment: 'Experiments · 实验',
};

function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value.trim());
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function newStepId(): string {
  return `step_${Math.random().toString(16).slice(2, 10)}${Math.random().toString(16).slice(2, 6)}`;
}

function substituteTemplate(value: unknown, variables: Record<string, unknown>): unknown {
  if (Array.isArray(value)) return value.map(item => substituteTemplate(item, variables));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [key, substituteTemplate(item, variables)]));
  }
  if (typeof value === 'string') {
    const match = /^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$/.exec(value.trim());
    if (match && match[1] in variables) return variables[match[1]];
  }
  return value;
}

function blankStep(): ProjectStep {
  return {
    id: newStepId(), name: '新步骤', enabled: true,
    operation: 'etch', model: 'isotropic', rateMagnitude: 0.02,
    duration: 0.02, rateMode: 'uniform',
  };
}

function stepFromLibrary(payload: Record<string, unknown>, name: string): ProjectStep {
  const step = { ...payload } as Record<string, unknown>;
  delete step.id;
  return {
    ...step,
    id: newStepId(),
    name: typeof step.name === 'string' && step.name.trim() ? String(step.name) : name,
    enabled: true,
  } as ProjectStep;
}

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
    if (Array.isArray(detail)) return detail.map(item => {
      if (item && typeof item === 'object') {
        const record = item as { msg?: unknown; message?: unknown };
        return String(record.msg ?? record.message ?? JSON.stringify(item));
      }
      return String(item);
    }).join('; ');
    return JSON.stringify(payload);
  }
  return '请求失败';
}

function parseAuthoredParameters(text: string, label: string): Record<string, Record<string, unknown>> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label} 参数必须是合法 JSON，例如 {"step_02":{"duration":0.02}}`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} 参数必须是 JSON 对象，例如 {"step_02":{"duration":0.02}}`);
  }
  return value as Record<string, Record<string, unknown>>;
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

/* ---------------------------------------------------------------------------
   Shared structure renderer. 03 (current run) and 05 (candidate compare) use
   the same component so a snapshot is drawn exactly once in the code base.
--------------------------------------------------------------------------- */

type StructureAxis = 'xy' | 'xz' | 'yz';
type StructureFrame = { minU: number; maxU: number; minV: number; maxV: number; unit: string };
type StructureMarker = { orientation: 'vertical' | 'horizontal'; value: number; label: string } | null;

function structureUnit(view: CandidateView): string {
  return view.coordinateUnit === 'um' || !view.coordinateUnit ? 'µm' : view.coordinateUnit;
}

function structurePoint(point: number[], axis: StructureAxis): [number, number] {
  const x = point[0] ?? 0;
  const y = point[1] ?? 0;
  const z = point[2] ?? 0;
  if (axis === 'yz') return [y, z];
  if (axis === 'xz') return [x, z];
  return [x, y];
}

function structureFrame(view: CandidateView, axis: StructureAxis): StructureFrame {
  let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
  for (const point of view.points) {
    const [u, v] = structurePoint(point, axis);
    minU = Math.min(minU, u); maxU = Math.max(maxU, u);
    minV = Math.min(minV, v); maxV = Math.max(maxV, v);
  }
  if (!Number.isFinite(minU) || !Number.isFinite(minV)) {
    const bounds = view.bounds ?? [];
    const pair = axis === 'yz'
      ? [bounds[2], bounds[3], bounds[4], bounds[5]]
      : axis === 'xz' ? [bounds[0], bounds[1], bounds[4], bounds[5]] : [bounds[0], bounds[1], bounds[2], bounds[3]];
    [minU, maxU, minV, maxV] = pair.map(value => Number(value) || 0) as [number, number, number, number];
  }
  if (maxU - minU < 1e-9) { minU -= 0.5; maxU += 0.5; }
  if (maxV - minV < 1e-9) { minV -= 0.5; maxV += 0.5; }
  const padding = Math.max((maxU - minU), (maxV - minV)) * 0.1;
  return {
    minU: minU - padding, maxU: maxU + padding,
    minV: minV - padding, maxV: maxV + padding,
    unit: structureUnit(view),
  };
}

function mergeFrames(frames: StructureFrame[], zoom: number): StructureFrame {
  if (!frames.length) return { minU: 0, maxU: 1, minV: 0, maxV: 1, unit: 'µm' };
  const minU = Math.min(...frames.map(frame => frame.minU));
  const maxU = Math.max(...frames.map(frame => frame.maxU));
  const minV = Math.min(...frames.map(frame => frame.minV));
  const maxV = Math.max(...frames.map(frame => frame.maxV));
  const factor = Math.max(0.25, zoom);
  const width = (maxU - minU) / factor;
  const height = (maxV - minV) / factor;
  return {
    minU: (minU + maxU) / 2 - width / 2, maxU: (minU + maxU) / 2 + width / 2,
    minV: (minV + maxV) / 2 - height / 2, maxV: (minV + maxV) / 2 + height / 2,
    unit: frames[0].unit,
  };
}

function frameViewBox(frame: StructureFrame): string {
  return `${frame.minU} ${-frame.maxV} ${frame.maxU - frame.minU} ${frame.maxV - frame.minV}`;
}

function axisTicks(min: number, max: number): number[] {
  const span = max - min;
  if (!Number.isFinite(span) || span <= 0) return [];
  const raw = span / 4;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const normalized = raw / power;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  const step = factor * power;
  const values: number[] = [];
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-6; value += step) {
    values.push(Number(value.toFixed(9)));
  }
  return values;
}

function formatTick(value: number): string {
  const rounded = Number(value.toFixed(4));
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/0$/, '');
}

function StructureCanvas({ view, frame, axis = 'xy', marker = null, label, className = 'wf-structure-canvas', showAxes = true }: {
  view: CandidateView;
  frame: StructureFrame;
  axis?: StructureAxis;
  marker?: StructureMarker;
  label: string;
  className?: string;
  showAxes?: boolean;
}) {
  const span = Math.max(frame.maxU - frame.minU, frame.maxV - frame.minV);
  const fontSize = span / 32;
  const strokeWidth = span / 220;
  const colorOf = (materialId: number | undefined): string => {
    const entry = materialId === undefined ? undefined : view.materials?.[String(materialId)];
    return isHexColor(entry?.color ?? '') ? String(entry?.color) : '#9aa3ab';
  };
  const uTicks = showAxes ? axisTicks(frame.minU, frame.maxU) : [];
  const vTicks = showAxes ? axisTicks(frame.minV, frame.maxV) : [];
  const barLength = (() => {
    const ticks = uTicks.length > 1 ? uTicks : [frame.minU, frame.maxU];
    return Math.abs(ticks[1] - ticks[0]) || span / 4;
  })();
  const barY = -frame.minV + fontSize * 1.4;
  const barX = frame.maxU - barLength;
  const points = view.points.map(point => structurePoint(point, axis));
  return <svg viewBox={frameViewBox(frame)} className={className} role="img" aria-label={label}>
    <rect x={frame.minU} y={-frame.maxV} width={frame.maxU - frame.minU} height={frame.maxV - frame.minV} fill="#f8fafb" />
    {showAxes && uTicks.map(tick => <g key={`u-${tick}`} className="wf-axes">
      <line x1={tick} y1={-frame.minV} x2={tick} y2={-frame.maxV} stroke="#e6eaee" strokeWidth={strokeWidth} />
      <line x1={tick} y1={-frame.minV} x2={tick} y2={-frame.minV + fontSize * 0.5} stroke="#9aa3ab" strokeWidth={strokeWidth} />
      <text x={tick} y={-frame.minV + fontSize * 1.5} fontSize={fontSize} textAnchor="middle">{formatTick(tick)}</text>
    </g>)}
    {showAxes && vTicks.map(tick => <g key={`v-${tick}`} className="wf-axes">
      <line x1={frame.minU} y1={-tick} x2={frame.maxU} y2={-tick} stroke="#e6eaee" strokeWidth={strokeWidth} />
      <line x1={frame.minU} y1={-tick} x2={frame.minU + fontSize * 0.5} y2={-tick} stroke="#9aa3ab" strokeWidth={strokeWidth} />
      <text x={frame.minU + fontSize * 0.15} y={-tick - fontSize * 0.15} fontSize={fontSize} textAnchor="start">{formatTick(tick)}</text>
    </g>)}
    {view.dimension === 2
      ? (view.lines.length
        ? view.lines.map((line, index) => {
          const a = points[line[0]];
          const b = points[line[1]];
          if (!a || !b) return null;
          return <line key={`l-${index}`} x1={a[0]} y1={-a[1]} x2={b[0]} y2={-b[1]}
            stroke={colorOf(view.lineMaterialIds[index])} strokeWidth={2} vectorEffect="non-scaling-stroke" />;
        })
        : points.map((point, index) => <circle key={`p-${index}`} cx={point[0]} cy={-point[1]}
          r={strokeWidth * 2} fill={colorOf(view.materialIds[index])} />))
      : (view.triangles.length
        ? view.triangles.map((triangle, index) => {
          const a = points[triangle[0]];
          const b = points[triangle[1]];
          const c = points[triangle[2]];
          if (!a || !b || !c) return null;
          return <polygon key={`t-${index}`}
            points={`${a[0]},${-a[1]} ${b[0]},${-b[1]} ${c[0]},${-c[1]}`}
            fill={colorOf(view.triangleMaterialIds[index])} fillOpacity="0.9" stroke="none" />;
        })
        : points.map((point, index) => <circle key={`p-${index}`} cx={point[0]} cy={-point[1]}
          r={strokeWidth * 2} fill={colorOf(view.materialIds[index])} />))}
    {marker && <g className="wf-marker">
      {marker.orientation === 'vertical'
        ? <line x1={marker.value} y1={-frame.minV} x2={marker.value} y2={-frame.maxV} strokeWidth={strokeWidth * 2} />
        : <line x1={frame.minU} y1={-marker.value} x2={frame.maxU} y2={-marker.value} strokeWidth={strokeWidth * 2} />}
      <text x={marker.orientation === 'vertical' ? marker.value + fontSize * 0.3 : frame.minU + fontSize * 0.3}
        y={marker.orientation === 'vertical' ? -frame.maxV + fontSize * 1.4 : -marker.value - fontSize * 0.4}
        fontSize={fontSize}>{marker.label}</text>
    </g>}
    {showAxes && <g className="wf-axes">
      <line x1={barX} y1={barY} x2={barX + barLength} y2={barY} stroke="#34404b" strokeWidth={strokeWidth * 2.5} />
      <text x={barX + barLength / 2} y={barY + fontSize * 1.3} fontSize={fontSize} textAnchor="middle">{`${formatTick(barLength)} ${frame.unit}`}</text>
      <text x={frame.minU} y={-frame.maxV - fontSize * 0.3} fontSize={fontSize}>{axis.toUpperCase()}</text>
    </g>}
  </svg>;
}

function structureLegend(view: CandidateView): Array<{ id: string; name: string; color: string; count: number }> {
  const counts = new Map<string, number>();
  const add = (id: number | undefined) => {
    const key = String(id ?? -1);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  };
  if (view.dimension === 2) {
    if (view.lines.length) view.lineMaterialIds.forEach(add);
    else view.materialIds.forEach(add);
  } else if (view.triangles.length) {
    view.triangleMaterialIds.forEach(add);
  } else {
    view.materialIds.forEach(add);
  }
  return [...counts.entries()]
    .filter(([id]) => id !== '-1')
    .map(([id, count]) => {
      const entry = view.materials?.[id];
      const name = entry?.name ?? view.materialNames?.[id] ?? `材料 ${id}`;
      const color = isHexColor(entry?.color ?? '') ? String(entry.color) : '#9aa3ab';
      return { id, name, color, count };
    });
}

function materialColor(materials: MaterialRow[], name: string): string {
  const row = materials.find(item => item.name === name);
  return isHexColor(row?.color ?? '') ? String(row?.color) : '#9aa3ab';
}

export default function WorkflowApp({ onOpenLegacy }: { onOpenLegacy?: () => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [gds, setGds] = useState<GdsAsset | null>(null);
  const [project, setProject] = useState<ProjectDoc | null>(null);
  const [projectGeometrySource, setProjectGeometrySource] = useState<ProjectGeometrySource>({ kind: 'parametric' });
  const [compile, setCompile] = useState<CompileInfo | null>(null);
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [segment, setSegment] = useState<RoutePlan['segments'][number] | null>(null);
  const [measurement, setMeasurement] = useState<Measurement | null>(null);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [templateId, setTemplateId] = useState('');
  const [libraryName, setLibraryName] = useState('团队共享流程');
  const [libraryDescription, setLibraryDescription] = useState('');
  const [libraryPolicy, setLibraryPolicy] = useState<'reject' | 'clone'>('reject');
  const [libraryBundles, setLibraryBundles] = useState<LibraryBundleSummary[]>([]);
  const [libraryBundleId, setLibraryBundleId] = useState('');
  const [libraryBundleJson, setLibraryBundleJson] = useState('');
  const [libraryResult, setLibraryResult] = useState<LibraryImportResult | null>(null);
  const [libraryStatus, setLibraryStatus] = useState('');
  const [recipeMethod, setRecipeMethod] = useState<'cd_at_height' | 'etch_depth'>('etch_depth');
  const [recipeMaterial, setRecipeMaterial] = useState('Si');
  const [recipeHeight, setRecipeHeight] = useState('0');
  const [recipeX, setRecipeX] = useState('0');
  const [recipeRef, setRecipeRef] = useState('0');
  const [metrics, setMetrics] = useState<Measurement[]>([]);
  const [branchDuration, setBranchDuration] = useState('0.02');
  const [activeRecipeId, setActiveRecipeId] = useState('');
  const [activeRecipeVersion, setActiveRecipeVersion] = useState(1);
  const [decisions, setDecisions] = useState<MeasurementDecision[]>([]);
  const [decisionPrefixId, setDecisionPrefixId] = useState('');
  const [decisionThroughId, setDecisionThroughId] = useState('');
  const [decisionOperator, setDecisionOperator] = useState<'lt' | 'lte' | 'gt' | 'gte' | 'eq'>('gt');
  const [decisionThreshold, setDecisionThreshold] = useState('0');
  const [decisionTrueParameters, setDecisionTrueParameters] = useState('{}');
  const [decisionFalseParameters, setDecisionFalseParameters] = useState('{}');
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
  const [moduleJson, setModuleJson] = useState(DEFAULT_MODULE_JSON);
  const [definitionJson, setDefinitionJson] = useState(DEFAULT_DEFINITION_JSON);
  const [definitionVariables, setDefinitionVariables] = useState('{}');
  const [savedDefinitionId, setSavedDefinitionId] = useState('');
  const [moduleStatus, setModuleStatus] = useState('');
  const [expansion, setExpansion] = useState<ModuleExpansion | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [zone, setZone] = useState<'setup' | 'flow' | 'run' | 'experiment' | 'analyze' | 'report'>('setup');
  const [error, setError] = useState('');
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [compileStale, setCompileStale] = useState(true);
  const [libraryItems, setLibraryItems] = useState<LibraryItem[]>([]);
  const [libraryCategory, setLibraryCategory] = useState<LibraryCategory>('process');
  const [libraryManagerOpen, setLibraryManagerOpen] = useState(false);
  const [managerCategory, setManagerCategory] = useState<LibraryCategory>('material');
  const [managerItems, setManagerItems] = useState<LibraryItem[]>([]);
  const [managerSelectedId, setManagerSelectedId] = useState('');
  const [managerForm, setManagerForm] = useState<Record<string, string>>({});
  const [managerStatus, setManagerStatus] = useState('');
  const [managerDeleteArmed, setManagerDeleteArmed] = useState(false);
  const [managerLoading, setManagerLoading] = useState(false);
  const [managerError, setManagerError] = useState('');
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState('');
  const managerRequest = useRef(0);
  const [materialCatalog, setMaterialCatalog] = useState<MaterialRow[]>([]);
  const materialCategories = useMemo(
    () => Array.from(new Set(materialCatalog.map(row => row.category ?? 'Generic'))).sort(),
    [materialCatalog],
  );
  const [materialPreset, setMaterialPreset] = useState('');
  const [flowDefinitions, setFlowDefinitions] = useState<FlowDefinitionSummary[]>([]);
  const [recipes, setRecipes] = useState<RecipeSummary[]>([]);
  const [confirmLibraryItemId, setConfirmLibraryItemId] = useState('');
  const [outputTab, setOutputTab] = useState<'output' | 'problems' | 'provenance'>('output');
  const [flowAdvancedOpen, setFlowAdvancedOpen] = useState(false);
  const [libraryAdvancedOpen, setLibraryAdvancedOpen] = useState(false);
  const [setupMode, setSetupMode] = useState<'home' | 'new' | 'examples' | 'project' | 'layout'>('home');
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [projectId, setProjectId] = useState('');
  const [savedProjectJson, setSavedProjectJson] = useState('');
  const [projectSavedAt, setProjectSavedAt] = useState('');
  const [projectNotice, setProjectNotice] = useState('');
  const [overwriteArmed, setOverwriteArmed] = useState<{ projectId: string; name: string } | null>(null);
  const [closeArmed, setCloseArmed] = useState(false);
  const [newProjectArmed, setNewProjectArmed] = useState(false);
  const [deleteArmedId, setDeleteArmedId] = useState('');
  const [selectedFlowStepId, setSelectedFlowStepId] = useState<string | null>(null);
  const [structurePick, setStructurePick] = useState('');
  const [structureView, setStructureView] = useState<CandidateView | null>(null);
  const [structureLoading, setStructureLoading] = useState(false);
  const [structureError, setStructureError] = useState('');
  const [structureAxis, setStructureAxis] = useState<StructureAxis>('xy');
  const [measurementMark, setMeasurementMark] = useState<StructureMarker>(null);
  const [rebuildRouteArmed, setRebuildRouteArmed] = useState(false);
  const [replaceTemplateArmed, setReplaceTemplateArmed] = useState(false);
  const [materialTarget, setMaterialTarget] = useState<{ itemName: string; material: string; target: string; stepName?: string } | null>(null);
  const [layerMaterials, setLayerMaterials] = useState<Record<string, string>>({});
  const [clipNotice, setClipNotice] = useState('');
  const [draft, setDraft] = useState({
    name: 'New Project', dimension: 2 as 2 | 3, geometry: 'plane' as 'plane' | 'trench' | 'hole' | 'fin',
    source: 'parametric' as 'parametric' | 'layout',
    gridDelta: 0.2, xExtent: 4, yExtent: 4,
    width: 1, depth: 2, radius: 1, finHeight: 1.2, height: 0,
    taperAngle: 0, maskHeight: 0,
    substrateMaterial: 'Si', maskMaterial: 'Mask',
    engine: 'cpu', seed: 7, timeout: 60, raysPerPoint: 16, cpuThreads: 1,
  });
  const [aboutOpen, setAboutOpen] = useState(false);
  const gdsInput = useRef<HTMLInputElement>(null);
  const projectInput = useRef<HTMLInputElement>(null);

  const projectDirty = !!project && JSON.stringify(project) !== savedProjectJson;

  const refreshProjects = async (): Promise<ProjectRecord[]> => {
    try {
      const data = await api<{ projects: ProjectRecord[] }>('/api/v1/workflow/projects');
      setProjects(data.projects);
      return data.projects;
    } catch {
      setProjects([]);
      return [];
    }
  };

  const applyLoadedProject = (document: ProjectDoc, record?: ProjectRecord) => {
    setProject(document);
    setProjectId(record?.projectId ?? '');
    setSavedProjectJson(record ? JSON.stringify(document) : '');
    setProjectSavedAt(record?.updatedAt ?? '');
    setProjectNotice(record
      ? `已打开 · ${record.name} · ${formatDateTime(record.updatedAt)}`
      : '草稿（未保存）');
    const geometry = (document.geometry ?? {}) as Record<string, unknown>;
    setProjectGeometrySource(geometry.kind === 'gds'
      ? { kind: 'layout', clipName: typeof geometry.fileName === 'string' ? geometry.fileName : 'GDS Clip' }
      : { kind: 'parametric' });
    setCompile(null);
    setRoute(null);
    setSegment(null);
    setMeasurement(null);
    setProvenance(null);
    setCompileStale(true);
    setSetupMode('project');
  };

  const applySavedRecord = async (record: ProjectRecord, document: ProjectDoc) => {
    setProjectId(record.projectId);
    setSavedProjectJson(JSON.stringify(document));
    setProjectSavedAt(record.updatedAt);
    setProjectNotice(`已保存 · ${record.name} · ${formatDateTime(record.updatedAt)}`);
    window.localStorage.setItem(PROJECT_STORAGE_KEY, record.projectId);
    await refreshProjects();
  };

  const saveProject = () => run('project-save', async () => {
    if (!project) throw new Error('没有可保存的工程');
    const document = project;
    setOverwriteArmed(null);
    try {
      const record = projectId
        ? await projectRequest(`/api/v1/workflow/projects/${projectId}`, 'PUT', document)
        : await projectRequest('/api/v1/workflow/projects', 'POST', document);
      await applySavedRecord(record, document);
    } catch (exc) {
      if ((exc as { status?: number }).status === 409) {
        const listed = await refreshProjects();
        const existing = listed.find(row => row.name === (document.name ?? ''));
        setOverwriteArmed(existing ? { projectId: existing.projectId, name: existing.name } : null);
        setProjectNotice(`已存在同名工程「${document.name ?? ''}」且内容不同；确认覆盖会替换它的步骤，取消则保留当前编辑。`);
        return;
      }
      throw exc;
    }
  });

  const overwriteProject = () => run('project-save', async () => {
    if (!project || !overwriteArmed) return;
    const document = project;
    const record = await projectRequest(`/api/v1/workflow/projects/${overwriteArmed.projectId}`, 'PUT', document);
    setOverwriteArmed(null);
    await applySavedRecord(record, document);
  });

  const openProject = (record: ProjectRecord) => run('project-open', async () => {
    const document = await api<ProjectDoc>(`/api/v1/workflow/projects/${record.projectId}`);
    applyLoadedProject(document, record);
    window.localStorage.setItem(PROJECT_STORAGE_KEY, record.projectId);
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已打开工程 ${record.name}`]);
  });

  const deleteProjectRecord = (record: ProjectRecord) => run('project-delete', async () => {
    await api(`/api/v1/workflow/projects/${record.projectId}`, { method: 'DELETE' });
    setDeleteArmedId('');
    if (projectId === record.projectId) {
      setProject(null);
      setProjectId('');
      setSavedProjectJson('');
      setProjectSavedAt('');
      setProjectNotice('');
      window.localStorage.removeItem(PROJECT_STORAGE_KEY);
      setSetupMode('home');
    }
    await refreshProjects();
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已删除工程 ${record.name}`]);
  });

  const exportProjectJson = () => {
    if (!project) return;
    const blob = new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${project.name ?? 'project'}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已导出工程 JSON: ${project.name ?? 'project'}`]);
  };

  const resetToStart = () => {
    setProject(null);
    setProjectId('');
    setSavedProjectJson('');
    setProjectSavedAt('');
    setProjectNotice('');
    setOverwriteArmed(null);
    setCloseArmed(false);
    setNewProjectArmed(false);
    window.localStorage.removeItem(PROJECT_STORAGE_KEY);
    setCompile(null); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setCompileStale(true);
    setProjectGeometrySource({ kind: 'parametric' });
    setSetupMode('home');
  };

  const closeProject = () => {
    if (projectDirty) { setCloseArmed(true); return; }
    resetToStart();
  };

  useEffect(() => {
    if (!openMenu && !aboutOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpenMenu(null);
      setAboutOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [openMenu, aboutOpen]);

  useEffect(() => {
    const stored = window.localStorage.getItem(PROJECT_STORAGE_KEY);
    void (async () => {
      const listed = await refreshProjects();
      if (!stored) return;
      const record = listed.find(row => row.projectId === stored);
      try {
        const document = await api<ProjectDoc>(`/api/v1/workflow/projects/${stored}`);
        applyLoadedProject(document, record ?? {
          schemaVersion: 1, projectId: stored, name: document.name ?? '',
          dimension: document.dimension ?? 2, stepCount: document.steps?.length ?? 0,
          createdAt: '', updatedAt: '',
        });
        window.localStorage.setItem(PROJECT_STORAGE_KEY, stored);
      } catch {
        window.localStorage.removeItem(PROJECT_STORAGE_KEY);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadLibraryItems = async () => {
    setLibraryLoading(true);
    setLibraryError('');
    try {
      const data = await api<{ items: LibraryItem[] }>('/api/v1/workflow/library/items');
      setLibraryItems(data.items);
    } catch (exc) {
      setLibraryItems([]);
      setLibraryError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLibraryLoading(false);
    }
  };

  useEffect(() => {
    void api<Health>('/api/health').then(setHealth).catch(() => setHealth(null));
    void api<{ templates: TemplateSummary[] }>('/api/v1/workflow/templates').then(data => setTemplates(data.templates)).catch(() => undefined);
    void api<{ bundles: LibraryBundleSummary[] }>('/api/v1/workflow/library/bundles')
      .then(data => setLibraryBundles(data.bundles)).catch(() => undefined);
    void loadLibraryItems();
    void api<{ materials: MaterialRow[] }>('/api/materials')
      .then(data => setMaterialCatalog(data.materials)).catch(() => undefined);
    void api<{ definitions: FlowDefinitionSummary[] }>('/api/v1/workflow/flows/definitions')
      .then(data => setFlowDefinitions(data.definitions)).catch(() => undefined);
    void api<{ recipes: RecipeSummary[] }>('/api/v1/workflow/metrology-recipes')
      .then(data => setRecipes(data.recipes)).catch(() => undefined);
  }, []);

  useEffect(() => {
    const steps = project?.steps ?? [];
    setSelectedFlowStepId(previous => steps.some(step => step.id === previous) ? previous : (steps[0]?.id ?? null));
  }, [project?.steps]);

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

  useEffect(() => {
    const routeId = route?.route.routeId;
    if (!routeId) {
      setDecisions([]);
      return;
    }
    void api<{ decisions: MeasurementDecision[] }>(`/api/v1/workflow/routes/${routeId}/measurement-decisions`)
      .then(data => setDecisions(data.decisions))
      .catch(() => setDecisions([]));
  }, [route?.route.routeId]);

  const recipeMaterialKey = useRef('');
  useEffect(() => {
    const geometry = (project?.geometry ?? {}) as Record<string, unknown>;
    const substrate = typeof geometry.substrateMaterial === 'string' ? geometry.substrateMaterial : '';
    const key = `${project?.name ?? ''}|${substrate}`;
    if (!substrate || recipeMaterialKey.current === key) return;
    recipeMaterialKey.current = key;
    setRecipeMaterial(substrate);
  }, [project]);

  const branchablePrefixes = useMemo(() => {
    const last = route?.stepOrder[route.stepOrder.length - 1];
    return (route?.segments ?? []).filter(item => item.status === 'succeeded' && item.throughStepId !== last);
  }, [route]);

  useEffect(() => {
    const last = route?.stepOrder[route.stepOrder.length - 1];
    const eligible = (route?.segments ?? []).filter(
      item => item.status === 'succeeded' && item.throughStepId !== last,
    );
    setDecisionPrefixId(eligible[eligible.length - 1]?.throughStepId ?? '');
  }, [route]);

  const decisionSuffix = useMemo(() => {
    if (!route || !decisionPrefixId) return [];
    const index = route.stepOrder.indexOf(decisionPrefixId);
    return index < 0 ? [] : route.stepOrder.slice(index + 1);
  }, [route, decisionPrefixId]);

  useEffect(() => {
    setDecisionThroughId(previous => decisionSuffix.includes(previous)
      ? previous
      : (decisionSuffix[decisionSuffix.length - 1] ?? ''));
  }, [decisionSuffix]);

  const authoredByCompiled = useMemo(
    () => new Map((compile?.compile.sourceMap ?? []).map(entry => [entry.stepId, entry.authoredStepId])),
    [compile],
  );

  useEffect(() => {
    const step = (compile?.compile.project.steps ?? []).find(item => item.id === decisionThroughId);
    const authored = authoredByCompiled.get(decisionThroughId);
    if (!step || !authored) return;
    const fields: Array<[string, number | undefined]> = [
      ['duration', step.duration],
      ['thickness', step.thickness],
      ['rateMagnitude', step.rateMagnitude],
    ];
    const field = fields.find(([, value]) => typeof value === 'number');
    const name = field?.[0];
    const value = field?.[1];
    if (!name || typeof value !== 'number') return;
    const presets: Record<string, [number, number]> = {
      duration: [0.03, 0.2],
      thickness: [0.02, 0.05],
      rateMagnitude: [0.05, 0.2],
    };
    const [trueValue, falseValue] = presets[name] ?? [
      Number((value * 0.5).toFixed(6)),
      Number((value * 2).toFixed(6)),
    ];
    setDecisionTrueParameters(previous => (!previous.trim() || previous.trim() === '{}')
      ? JSON.stringify({ [authored]: { [name]: trueValue } })
      : previous);
    setDecisionFalseParameters(previous => (!previous.trim() || previous.trim() === '{}')
      ? JSON.stringify({ [authored]: { [name]: falseValue } })
      : previous);
  }, [compile, decisionThroughId, authoredByCompiled]);

  const authoredSteps = project?.steps ?? [];
  const selectedFlowStep = authoredSteps.find(step => step.id === selectedFlowStepId) ?? null;
  const canCompile = !!project && authoredSteps.length > 0 && !busy;
  const canRoute = !!compile && !compileStale && !busy;
  const canRun = !!segment && segment.status === 'planned' && !busy;
  const canMeasure = !!segment && segment.status === 'succeeded' && !!segment.runId && !busy;
  const routeStale = !!route && !!compile && route.attempts.some(attempt => attempt.compileId !== compile.compileId);

  const runStructureSteps = useMemo(() => {
    if (!route) return [] as Array<{ stepId: string; runId: string; segmentId: string; status: string }>;
    const rows: Array<{ stepId: string; runId: string; segmentId: string; status: string }> = [];
    for (const item of route.segments) {
      if (!item.runId || item.status !== 'succeeded') continue;
      const start = item.startAfterStepId ? route.stepOrder.indexOf(item.startAfterStepId) + 1 : 0;
      const end = route.stepOrder.indexOf(item.throughStepId);
      if (end < 0) continue;
      for (const stepId of route.stepOrder.slice(start, end + 1)) {
        rows.push({ stepId, runId: item.runId, segmentId: item.segmentId, status: item.status });
      }
    }
    return rows;
  }, [route]);

  useEffect(() => {
    setStructurePick(previous => runStructureSteps.some(row => `${row.runId}:${row.stepId}` === previous)
      ? previous
      : (() => {
        const last = runStructureSteps[runStructureSteps.length - 1];
        return last ? `${last.runId}:${last.stepId}` : '';
      })());
  }, [runStructureSteps]);

  const structureSelection = runStructureSteps.find(row => `${row.runId}:${row.stepId}` === structurePick) ?? null;
  const structureRunId = structureSelection?.runId ?? null;
  const structureStepId = structureSelection?.stepId ?? '';

  useEffect(() => {
    if (!structureRunId || !structureStepId) {
      setStructureView(null);
      setStructureError('');
      return;
    }
    let cancelled = false;
    setStructureLoading(true);
    setStructureError('');
    void api<CandidateView>(`/api/v1/workflow/analysis/candidates/${structureRunId}/${structureStepId}/view`)
      .then(view => { if (!cancelled) setStructureView(view); })
      .catch(exc => {
        if (cancelled) return;
        setStructureView(null);
        setStructureError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => { if (!cancelled) setStructureLoading(false); });
    return () => { cancelled = true; };
  }, [structureRunId, structureStepId, route?.segments.map(item => `${item.segmentId}:${item.status}`).join(',')]);

  const stepDisplayName = (stepId: string): string => {
    const compiled = compile?.compile.project.steps.find(step => step.id === stepId);
    const authoredId = authoredByCompiled.get(stepId);
    const authored = authoredSteps.find(step => step.id === authoredId);
    return compiled?.name ?? authored?.name ?? stepId.slice(0, 10);
  };

  const run = async (name: string, task: () => Promise<void>) => {
    const label = actionLabel(name);
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  ${label} …`]);
    setBusy(name);
    setError('');
    try {
      await task();
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  ${label} 完成`]);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : String(exc);
      setError(message);
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  ${label} 失败: ${message}`]);
    } finally { setBusy(null); }
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
      const parsed = JSON.parse(await file.text()) as ProjectDoc;
      setProject(parsed);
      setProjectId('');
      setSavedProjectJson('');
      setProjectSavedAt('');
      setProjectNotice('草稿（未保存）· 导入的工程尚未写入服务端');
      setProjectGeometrySource({ kind: 'parametric' });
      setCompileStale(true);
      setSetupMode('project');
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已导入工程: ${parsed.name ?? '未命名'}`]);
    } catch {
      setError('项目 JSON 无法解析');
    }
  };

  const beginNewProjectDraft = () => {
    setProject(null);
    setProjectId('');
    setSavedProjectJson('');
    setProjectSavedAt('');
    setProjectNotice('');
    setCompile(null); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setCompileStale(true);
    setProjectGeometrySource({ kind: 'parametric' });
    setNewProjectArmed(false);
    setSetupMode('new');
    setDraft(previous => ({ ...previous, name: 'New Project' }));
    window.localStorage.removeItem(PROJECT_STORAGE_KEY);
  };

  const startNewProject = () => {
    if (project && projectDirty) { setNewProjectArmed(true); return; }
    beginNewProjectDraft();
  };

  const setDraftField = (key: keyof typeof draft, value: string | number) => {
    setDraft(previous => ({ ...previous, [key]: value }));
  };

  const loadSample = (sample: Record<string, unknown>, label: string) => {
    setProject(sample as ProjectDoc);
    setProjectId('');
    setSavedProjectJson('');
    setProjectSavedAt('');
    setProjectNotice('草稿（未保存）');
    setProjectGeometrySource({ kind: 'parametric' });
    setCompileStale(true);
    setCompile(null); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setSetupMode('project');
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已载入示例: ${label}`]);
  };

  const setDraftDimension = (dimension: 2 | 3) => {
    setDraft(previous => ({
      ...previous,
      dimension,
      source: dimension === 3 ? 'parametric' : previous.source,
      geometry: dimension === 2
        ? (previous.geometry === 'trench' ? 'trench' : 'plane')
        : (previous.geometry === 'fin' ? 'fin' : 'hole'),
    }));
  };

  const chooseDraftSource = (source: 'parametric' | 'layout') => {
    if (source === 'layout' && draft.dimension === 3) return;
    setDraft(prev => ({ ...prev, source }));
  };

  const createDraftProject = () => {
    if (draft.source === 'layout' && !activeClip) {
      setError('GDS 几何需要先上传版图、保存 Clip 并选择该 Clip');
      return;
    }
    const dimension = draft.dimension;
    const is3d = dimension === 3;
    const name = draft.name.trim() || 'New Project';
    const gridDelta = Math.max(0.01, Number(draft.gridDelta) || 0.2);
    const xExtent = Math.max(gridDelta * 4, Number(draft.xExtent) || 4);
    const yExtent = Math.max(gridDelta * 4, Number(draft.yExtent) || 4);
    const common = {
      taperAngle: Number(draft.taperAngle) || 0,
      maskHeight: Math.max(0, Number(draft.maskHeight) || 0),
      maskTaperAngle: 0,
      substrateMaterial: draft.substrateMaterial || 'Si',
      maskMaterial: draft.maskMaterial || 'Mask',
    };
    const parametric = draft.geometry === 'trench'
      ? { kind: 'trench', width: Number(draft.width) || 1, depth: Number(draft.depth) || 2, ...common }
      : draft.geometry === 'hole'
        ? { kind: 'hole', radius: Number(draft.radius) || 1, depth: Number(draft.depth) || 2, ...common }
        : draft.geometry === 'fin'
          ? { kind: 'fin', width: Number(draft.width) || 1, height: Number(draft.finHeight) || 1.2, ...common, construction: 'native' }
          : { kind: 'plane', height: Number(draft.height) || 0, substrateMaterial: draft.substrateMaterial || 'Si' };
    const layout = !is3d && draft.source === 'layout' && activeClip
      ? {
        kind: 'gds', assetId: activeClip.gdsAssetId, fileName: activeClip.gdsFileName,
        structure: 'CLIP', unitScale: 1, position: [0, 0, 0],
        layers: activeClip.layers.map(item => ({ layer: item.layer, material: item.material || 'Si' })),
      }
      : null;
    const projectPayload: ProjectDoc = {
      schemaVersion: 7, name, dimension,
      units: { length: 'um', time: 's', rate: 'um/s', angle: 'deg' },
      domain: is3d
        ? { gridDelta, xExtent, yExtent, boundaryXY: 'reflective' }
        : { gridDelta, xExtent, xBoundary: 'reflective' },
      geometry: layout ?? parametric,
      steps: [],
      solver: {
        timeStepRatio: 0.25,
        raysPerPoint: Math.max(1, Number(draft.raysPerPoint) || 16),
        cpuThreads: Math.max(1, Number(draft.cpuThreads) || 1),
        random: { mode: 'fixed', seed: Number(draft.seed) || 7 },
        enginePreference: draft.engine,
        timeoutSeconds: Math.max(5, Number(draft.timeout) || 60),
      },
      ...(is3d ? { view3D: {
        projection: 'parallel', camera: null, displayMode: 'surface',
        visibleMaterialIds: [], opacityByMaterial: {},
        clip: { enabled: false, axis: 'x', position: 0.0, keep: 'positive' },
        section: { enabled: false, axis: 'y', position: 0.0 },
      } } : { view: { panX: 0, panH: 0, zoom: 1, showGrid: true } }),
    };
    setProject(projectPayload);
    setProjectGeometrySource(layout && activeClip
      ? { kind: 'layout', clipId: activeClip.clipId, clipName: activeClip.name, layoutId: activeClip.layoutId, clipHash: activeClip.clipHash }
      : { kind: 'parametric' });
    setCompileStale(true);
    setCompile(null); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setProjectId('');
    setSavedProjectJson('');
    setProjectSavedAt('');
    setProjectNotice('草稿（未保存）· 点击“保存工程”写入服务端');
    setSetupMode('project');
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  新工程草稿已建立: ${name}`]);
  };

  const compileFlow = () => run('compile-flow', async () => {
    if (!project) throw new Error('先创建或导入工程');
    const steps = project.steps ?? [];
    if (!steps.length) throw new Error('流程还没有步骤，先在 02 插入或添加步骤');
    const source = projectGeometrySource;
    let result: CompileInfo;
    if (source.kind === 'layout' && source.clipId) {
      result = await postJson<CompileInfo>(`/api/v1/workflow/clips/${source.clipId}/compile`, {
        project, flowId: compile?.flowId,
      });
    } else {
      result = await postJson<CompileInfo>('/api/v1/workflow/flows/from-project', {
        project, flowId: compile?.flowId,
      });
    }
    if (compile) setPreviousCompile(compile);
    setCompile(result);
    setCompileStale(false);
    setProject(previous => previous
      ? { ...previous, steps: (result.authoredSteps ?? previous.steps ?? []) as ProjectStep[] }
      : previous);
    setRoute(null);
    setSegment(null);
    setMeasurement(null);
    setProvenance(null);
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  流程已编译 · ${result.stepCount} 步`]);
  });

  const saveTemplate = () => run('template', async () => {
    if (!project) throw new Error('先创建或导入工程');
    const templateId = `tpl_${Date.now().toString(36)}`;
    const sourceSteps = (project.steps ?? []) as Array<Record<string, unknown>>;
    if (!sourceSteps.length) throw new Error('流程还没有步骤');
    const enabled = sourceSteps.filter(step => step.enabled);
    const variableIds = new Set(enabled.slice(-2).map(step => step.id));
    const steps = sourceSteps.map(step => variableIds.has(step.id) ? { ...step, duration: '${duration}' } : step);
    await postJson('/api/v1/workflow/templates', {
      schemaVersion: 1, templateId, name: `${project.name ?? 'Project'} · duration variable`, description: '',
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
    setCompile(result); setCompileStale(false); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setProject(previous => previous ? { ...previous, steps: (result.authoredSteps ?? previous.steps ?? []) as ProjectStep[] } : previous);
  });

  const createRoute = (rebuild = false) => run('route', async () => {
    if (!compile) throw new Error('先编译流程');
    if (compileStale) throw new Error('流程已修改，请重新编译');
    if (route && !rebuild) { setZone('run'); return; }
    const enabledSteps = compile.compile.project.steps.filter(step => step.enabled);
    const splitAfterStepId = enabledSteps.length > 1 ? enabledSteps[enabledSteps.length - 2].id : undefined;
    const result = await postJson<{ plan: RoutePlan }>('/api/v1/workflow/routes/from-compile', {
      compileId: compile.compileId,
      name: `${compile.projectName} · Route`,
      splitAfterStepId,
    });
    setRoute(result.plan);
    setSegment(result.plan.segments[0] ?? null);
    setMeasurement(null);
    setProvenance(null);
    setRebuildRouteArmed(false);
    setZone('run');
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  Route 已创建 · ${result.plan.segments.length} Segment`]);
  });

  const goToRun = () => {
    if (!route) { void createRoute(); return; }
    setZone('run');
  };

  const execute = () => run('execute', async () => {
    if (!segment) throw new Error('先创建 Route');
    const result = await postJson<{ segment: RoutePlan['segments'][number]; runId: string }>(
      `/api/v1/workflow/segments/${segment.segmentId}/execute`, {},
    );
    setSegment(result.segment);
    setRoute(previous => previous ? { ...previous, segments: previous.segments.map(item => item.segmentId === result.segment.segmentId ? result.segment : item) } : previous);
  });

  const ensureRecipe = async (): Promise<{ recipeId: string; recipeVersion: number; metricId: string }> => {
    if (!compile) throw new Error('先编译流程');
    const metricId = `metric_${compile.flowId}_${recipeMethod}`;
    const recipeId = `recipe_${compile.flowId}_${recipeMethod}`;
    const metricUnit = 'um';
    await postJson('/api/v1/workflow/metrics', {
      schemaVersion: 1, metricId, name: `${recipeMethod} metric`, unit: metricUnit, description: 'configurable recipe',
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
    return { recipeId, recipeVersion, metricId };
  };

  const measure = () => run('measure', async () => {
    if (!segment?.runId) throw new Error('先完成运行');
    const { recipeId, recipeVersion } = await ensureRecipe();
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
    const markValue = recipeMethod === 'cd_at_height' ? (Number(recipeHeight) || 0) : (Number(recipeX) || 0);
    setMeasurementMark(recipeMethod === 'cd_at_height'
      ? { orientation: 'horizontal', value: markValue, label: `测量 CD 高度 ${markValue} µm` }
      : { orientation: 'vertical', value: markValue, label: `测量 X ${markValue} µm` });
  });

  const decideMeasurementBranch = () => run('decision', async () => {
    if (!route) throw new Error('先创建 Route');
    if (!branchablePrefixes.length) throw new Error('没有保留后续步骤的成功前缀 Segment');
    if (!decisionPrefixId) throw new Error('没有可用的已成功前缀 Segment');
    if (!decisionThroughId) throw new Error('没有可用的分支结束步骤');
    const thresholdText = decisionThreshold.trim();
    const threshold = thresholdText ? Number(thresholdText) : 0;
    if (!Number.isFinite(threshold)) throw new Error('决策阈值必须是有限数字');
    const recipe = activeRecipeId
      ? { recipeId: activeRecipeId, recipeVersion: activeRecipeVersion }
      : await ensureRecipe();
    const trueParameters = parseAuthoredParameters(decisionTrueParameters, 'true 分支');
    const falseParameters = parseAuthoredParameters(decisionFalseParameters, 'false 分支');
    const result = await postJson<MeasurementDecisionResponse>(
      `/api/v1/workflow/routes/${route.route.routeId}/measurement-decisions`,
      {
        startAfterStepId: decisionPrefixId,
        branchThroughStepId: decisionThroughId,
        recipeId: recipe.recipeId,
        recipeVersion: recipe.recipeVersion,
        operator: decisionOperator,
        threshold,
        branches: {
          true: { parameters: trueParameters },
          false: { parameters: falseParameters },
        },
      },
    );
    setRoute(result.plan);
    setSegment(result.plan.segments.find(item => item.segmentId === result.decision.branchSegmentId) ?? null);
    setDecisions(previous => [...previous, result.decision]);
    setMeasurement(result.measurement);
    setActiveRecipeId(recipe.recipeId);
    setActiveRecipeVersion(recipe.recipeVersion);
    setProvenance(null);
  });

  const replayDecision = (decision: MeasurementDecision) => run('decision-replay', async () => {
    if (!route) throw new Error('先创建 Route');
    const result = await postJson<MeasurementDecisionResponse>(
      `/api/v1/workflow/routes/${route.route.routeId}/measurement-decisions/${decision.decisionId}/replay`, {},
    );
    setRoute(result.plan);
    setSegment(result.plan.segments.find(item => item.segmentId === result.decision.branchSegmentId) ?? null);
    setDecisions(previous => [...previous, result.decision]);
    setMeasurement(result.measurement);
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
    setCompileStale(false);
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
    const pairs = (structure?.layerDatatypes ?? []).map(([layer, datatype]) => `${layer}:${datatype}`);
    setSelectedPairs(pairs);
    setLayerMaterials(previous => Object.fromEntries(pairs.map(key => [key, previous[key] ?? 'Si'])));
    setClipNotice('');
    if (structure?.bounds) {
      setClipBounds({ ...structure.bounds });
      setCanvas(null);
    }
  };

  const clipLayers = () => selectedPairs.map(key => {
    const [layer, datatype] = key.split(':').map(Number);
    return { layer, datatype, material: layerMaterials[key] || 'Si' };
  });

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
    const layers = clipLayers();
    if (!layers.length) throw new Error('至少选择一个层');
    const preview = await postJson<ClipCanvas>(`/api/v1/workflow/layouts/${activeLayout.layoutId}/preview`, {
      structure: layoutStructure, layers, bbox: clipBounds,
    });
    setCanvas(preview);
    setActiveClip(null);
    setClipNotice('');
  });

  const sameClipContent = (clip: Clip, layers: Array<{ layer: number; datatype: number | null; material: string }>) => {
    if (clip.layoutId !== activeLayout?.layoutId) return false;
    if (clip.structure !== layoutStructure) return false;
    if (['x0', 'y0', 'x1', 'y1'].some(key => Math.abs(Number(clip.bbox[key as keyof Clip['bbox']]) - clipBounds[key as keyof typeof clipBounds]) > 1e-9)) return false;
    if (clip.layers.length !== layers.length) return false;
    return clip.layers.every((item, index) => item.layer === layers[index].layer
      && item.datatype === layers[index].datatype
      && item.material === layers[index].material);
  };

  const saveClip = () => run('clip', async () => {
    if (!activeLayout) throw new Error('先注册 Layout');
    const layers = clipLayers();
    if (!layers.length) throw new Error('至少选择一个层');
    const name = (clipName || `clip ${clips.length + 1}`).trim();
    const existing = clips.filter(item => item.name === name);
    const duplicate = existing.find(item => sameClipContent(item, layers));
    if (duplicate) {
      setActiveClip(duplicate);
      setCanvas(duplicate);
      setClipNotice(`已存在同名同内容的 Clip「${name}」，已直接选中它，未重复创建。`);
      return;
    }
    if (existing.length) {
      setClipNotice('');
      throw new Error(`已存在同名 Clip「${name}」但内容不同；请修改 Clip 名称后再保存，避免两个同名条目。`);
    }
    const annotations = annotationText.trim()
      ? [{ text: annotationText.trim(), x: Number(annotationX) || 0, y: Number(annotationY) || 0 }]
      : [];
    const clip = await postJson<Clip>('/api/v1/workflow/clips', {
      layoutId: activeLayout.layoutId, name,
      structure: layoutStructure, layers, bbox: clipBounds, annotations,
    });
    setClips(previous => [clip, ...previous.filter(item => item.name !== clip.name)]);
    setActiveClip(clip);
    setCanvas(clip);
    setAnnotationText('');
    setClipNotice(`已保存 Clip「${clip.name}」。`);
  });

  const selectClip = (clip: Clip) => {
    setActiveClip(clip);
    setCanvas(clip);
    setClipName(clip.name);
    setClipNotice('');
    if (activeLayout) selectStructure(activeLayout, clip.structure);
    setClipBounds({ ...clip.bbox });
    setLayerMaterials(previous => Object.fromEntries(clip.layers.map(item => [`${item.layer}:${item.datatype ?? 0}`, item.material || 'Si'])
      .map(([key, value]) => [key, previous[key] ?? value])));
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

  const useClipAsGeometry = (clip: Clip, compileNow: boolean) => run('clip-use', async () => {
    if (!project) throw new Error('先创建或导入工程');
    if (project.dimension === 3) throw new Error('GDS 版图几何仅支持 2D 工程');
    setProjectGeometrySource({
      kind: 'layout', clipId: clip.clipId, clipName: clip.name,
      layoutId: clip.layoutId, clipHash: clip.clipHash,
    });
    const geometry = {
      kind: 'gds', assetId: clip.gdsAssetId, fileName: clip.gdsFileName,
      structure: 'CLIP', unitScale: 1, position: [0, 0, 0],
      layers: clip.layers.map(item => ({ layer: item.layer, material: item.material || 'Si' })),
    };
    const nextProject: ProjectDoc = { ...project, geometry };
    setProject(nextProject);
    if (!compileNow) {
      setCompileStale(true);
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  工程几何已切换为 Clip ${clip.name}`]);
      return;
    }
    if (!(nextProject.steps ?? []).length) throw new Error('流程还没有步骤，先在 02 插入或添加步骤');
    const result = await postJson<CompileInfo>(`/api/v1/workflow/clips/${clip.clipId}/compile`, {
      project: nextProject, flowId: compile?.flowId,
    });
    if (compile) setPreviousCompile(compile);
    setCompile(result); setCompileStale(false); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setProject(previous => previous
      ? { ...previous, steps: (result.authoredSteps ?? previous.steps ?? []) as ProjectStep[] }
      : previous);
  });

  const parseJsonObject = (label: string, text: string): Record<string, unknown> => {
    let parsed: unknown;
    try { parsed = JSON.parse(text); } catch { throw new Error(`${label} 不是合法 JSON`); }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} 必须是 JSON 对象`);
    return parsed as Record<string, unknown>;
  };

  const definitionIdFromJson = useMemo(() => {
    try {
      const parsed = JSON.parse(definitionJson) as { definitionId?: unknown };
      return typeof parsed.definitionId === 'string' ? parsed.definitionId : '';
    } catch { return ''; }
  }, [definitionJson]);
  const activeDefinitionId = savedDefinitionId || definitionIdFromJson;

  const readDefinitionVariables = () => {
    const parsed = parseJsonObject('流程变量 JSON', definitionVariables);
    for (const [name, value] of Object.entries(parsed)) {
      const kind = typeof value;
      if (value === null || !['string', 'number', 'boolean'].includes(kind)) throw new Error(`变量 ${name} 必须是标量`);
    }
    return parsed as Record<string, string | number | boolean>;
  };

  const saveModule = () => run('module', async () => {
    const payload = parseJsonObject('模块 JSON', moduleJson);
    const saved = await postJson<{ moduleId: string }>('/api/v1/workflow/modules', payload);
    setModuleStatus(`模块 ${saved.moduleId} 已保存`);
    setExpansion(null);
  });

  const saveDefinition = () => run('definition', async () => {
    const payload = parseJsonObject('流程定义 JSON', definitionJson);
    const saved = await postJson<{ definitionId: string }>('/api/v1/workflow/flows/definitions', payload);
    setSavedDefinitionId(saved.definitionId);
    setModuleStatus(`流程定义 ${saved.definitionId} 已保存`);
    setExpansion(null);
  });

  const expandDefinition = () => run('expand', async () => {
    if (!activeDefinitionId) throw new Error('流程定义 JSON 缺少 definitionId');
    const result = await postJson<ModuleExpansion>(
      `/api/v1/workflow/flows/definitions/${activeDefinitionId}/expand`,
      { variables: readDefinitionVariables() },
    );
    setExpansion(result);
    setModuleStatus(`展开 ${result.stepCount} 步 · 深度 ${result.maxDepth}`);
  });

  const compileDefinition = () => run('definition-compile', async () => {
    if (!activeDefinitionId) throw new Error('流程定义 JSON 缺少 definitionId');
    if (!project) throw new Error('先导入工程 JSON');
    const result = await postJson<CompileInfo>(
      `/api/v1/workflow/flows/definitions/${activeDefinitionId}/compile`,
      { project, variables: readDefinitionVariables() },
    );
    if (compile) setPreviousCompile(compile);
    setCompile(result); setCompileStale(false); setRoute(null); setSegment(null); setMeasurement(null); setProvenance(null);
    setModuleStatus(`模块化流程已编译 · ${result.stepCount} 步`);
  });

  const refreshLibraryBundles = async () => {
    const data = await api<{ bundles: LibraryBundleSummary[] }>('/api/v1/workflow/library/bundles');
    setLibraryBundles(data.bundles);
    return data.bundles;
  };

  const describeLibraryResult = (result: LibraryImportResult) => {
    const parts = [
      `${result.modules.filter(item => item.status === 'imported').length} 模块修订`,
      `${result.definitionIds.length} 定义`,
      `${result.templateIds.length} 模板`,
      `${result.experimentIds.length} 实验模板`,
    ];
    const skipped = result.skipped.length ? ` · 跳过 ${result.skipped.length}` : '';
    const renamed = result.renames.length ? ` · 新 ID ${result.renames.length}` : '';
    return `导入 ${result.bundleId} (${result.policy}): ${parts.join(' / ')}${skipped}${renamed}`;
  };

  const exportLibrary = () => run('library', async () => {
    if (!libraryName.trim()) throw new Error('先填写共享库名称');
    const bundle = await postJson<LibraryBundle>('/api/v1/workflow/library/export', {
      name: libraryName.trim(),
      description: libraryDescription,
      experimentIds: experimentDoc ? [experimentDoc.experimentId] : [],
    });
    setLibraryBundleJson(JSON.stringify(bundle, null, 2));
    setLibraryBundleId(bundle.bundleId);
    setLibraryResult(null);
    setLibraryStatus(
      `已导出 ${bundle.bundleId} · ${bundle.modules.length} 模块修订 / ${bundle.definitions.length} 定义 / ${bundle.templates.length} 模板 / ${bundle.experimentTemplates.length} 实验模板`,
    );
    await refreshLibraryBundles();
  });

  const importLibraryBundle = () => run('library', async () => {
    if (!libraryBundleId) throw new Error('先选择共享库 bundle');
    const result = await postJson<LibraryImportResult>(
      `/api/v1/workflow/library/bundles/${libraryBundleId}/import`,
      { policy: libraryPolicy },
    );
    setLibraryResult(result);
    setLibraryStatus(describeLibraryResult(result));
    await refreshLibraryBundles();
  });

  const importLibraryJson = () => run('library', async () => {
    const bundle = parseJsonObject('共享库 JSON', libraryBundleJson);
    const result = await postJson<LibraryImportResult>('/api/v1/workflow/library/import', {
      bundle, policy: libraryPolicy,
    });
    setLibraryBundleId(result.bundleId);
    setLibraryResult(result);
    setLibraryStatus(describeLibraryResult(result));
    await refreshLibraryBundles();
  });

  const refreshLibraryItems = async (category?: LibraryCategory) => {
    const query = category ? `?category=${category}` : '';
    const data = await api<{ items: LibraryItem[] }>(`/api/v1/workflow/library/items${query}`);
    if (category) setManagerItems(data.items);
    else setLibraryItems(data.items);
    return data.items;
  };

  const managerDefaults = (category: LibraryCategory): Record<string, string> => {
    if (category === 'material') {
      const first = materialCatalog[0];
      return {
        name: '', catalog: first?.category ?? 'Generic', material: first?.name ?? 'Si',
        displayName: '', color: first?.color ?? '#4c80ff', role: 'film', notes: '',
      };
    }
    if (category === 'process') return {
      name: '', operation: 'etch', model: 'isotropic', rateMagnitude: '0.1', duration: '0.1',
      depositMaterial: '', stickingProbability: '1', sourceExponent: '1', notes: '',
    };
    if (category === 'flow') return { name: '', flowRef: '', notes: '' };
    return {
      name: '', recipeRef: recipes[0] ? `${recipes[0].recipeId}@${recipes[0].version}` : '',
      designKind: 'doe', variantCount: '8', replicates: '1', seed: '20261029', notes: '',
    };
  };

  const managerFormFromItem = (item: LibraryItem): Record<string, string> => {
    if (item.category === 'material') {
      return {
        name: item.name,
        catalog: String(item.payload.catalog ?? ''),
        material: String(item.payload.material ?? ''),
        displayName: String(item.payload.displayName ?? ''),
        color: String(item.payload.color ?? '#4c80ff'),
        role: MATERIAL_ROLES.some(([value]) => value === String(item.payload.role ?? ''))
          ? String(item.payload.role)
          : 'film',
        notes: String(item.payload.notes ?? ''),
      };
    }
    if (item.category === 'process') {
      return {
        name: item.name,
        operation: String(item.payload.operation ?? 'etch'),
        model: String(item.payload.model ?? 'isotropic'),
        rateMagnitude: String(item.payload.rateMagnitude ?? '0.1'),
        duration: String(item.payload.duration ?? '0.1'),
        depositMaterial: String(item.payload.depositMaterial ?? ''),
        stickingProbability: String(item.payload.stickingProbability ?? '1'),
        sourceExponent: String(item.payload.sourceExponent ?? '1'),
        notes: item.description,
      };
    }
    if (item.category === 'flow') {
      const definitionId = typeof item.payload.definitionId === 'string' ? item.payload.definitionId : '';
      const templateId = typeof item.payload.templateId === 'string' ? item.payload.templateId : '';
      return {
        name: item.name,
        flowRef: definitionId ? `definition:${definitionId}` : templateId ? `template:${templateId}` : '',
        notes: String(item.payload.notes ?? item.description),
      };
    }
    const design = (item.payload.design ?? {}) as Record<string, unknown>;
    return {
      name: item.name,
      recipeRef: `${String(item.payload.recipeId ?? '')}@${String(item.payload.recipeVersion ?? 1)}`,
      designKind: String(design.kind ?? 'doe'),
      variantCount: String(design.variantCount ?? design.variants ?? 1),
      replicates: String(design.replicates ?? 1),
      seed: String(design.seed ?? 0),
      notes: String(item.payload.notes ?? item.description),
    };
  };

  const loadManagerCategory = async (category: LibraryCategory) => {
    const requestId = managerRequest.current + 1;
    managerRequest.current = requestId;
    setManagerCategory(category);
    setManagerSelectedId('');
    setManagerForm(managerDefaults(category));
    setManagerStatus('');
    setManagerDeleteArmed(false);
    setManagerItems([]);
    setManagerLoading(true);
    setManagerError('');
    try {
      const data = await api<{ items: LibraryItem[] }>(`/api/v1/workflow/library/items?category=${category}`);
      if (managerRequest.current !== requestId) return;
      setManagerItems(data.items);
    } catch (exc) {
      if (managerRequest.current !== requestId) return;
      setManagerItems([]);
      setManagerError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (managerRequest.current === requestId) setManagerLoading(false);
    }
  };

  const openLibraryManager = (category: LibraryCategory = 'material') => {
    setZone('flow');
    setLibraryManagerOpen(true);
    void loadManagerCategory(category);
  };

  const switchManagerCategory = (category: LibraryCategory) => {
    void loadManagerCategory(category);
  };

  const selectManagerItem = (item: LibraryItem) => {
    setManagerSelectedId(item.itemId);
    setManagerForm(managerFormFromItem(item));
    setManagerStatus('');
    setManagerDeleteArmed(false);
  };

  const buildManagerPayload = (): Record<string, unknown> => {
    if (managerCategory === 'material') {
      const payload: Record<string, unknown> = {
        catalog: managerForm.catalog ?? '',
        material: managerForm.material ?? '',
      };
      if (managerForm.displayName?.trim()) payload.displayName = managerForm.displayName.trim();
      if (managerForm.color?.trim()) payload.color = managerForm.color.trim();
      if (managerForm.role?.trim()) payload.role = managerForm.role.trim();
      if (managerForm.notes?.trim()) payload.notes = managerForm.notes.trim();
      return payload;
    }
    if (managerCategory === 'process') {
      const model = managerForm.model || 'isotropic';
      const payload: Record<string, unknown> = {
        operation: managerForm.operation || 'etch',
        model,
        rateMagnitude: Number(managerForm.rateMagnitude) || 0,
        duration: Number(managerForm.duration) || 0.01,
        rateMode: 'uniform',
      };
      if ((managerForm.operation || 'etch') === 'deposit') {
        payload.depositMaterial = managerForm.depositMaterial?.trim() || 'SiO2';
      }
      if (model === 'singleParticle') {
        payload.stickingProbability = Number(managerForm.stickingProbability) || 0;
        payload.sourceExponent = Number(managerForm.sourceExponent) || 0;
      }
      if (model === 'directional') {
        payload.direction = [0, 0, -1];
        payload.directionalRateMagnitude = Number(managerForm.rateMagnitude) || 0;
        payload.isotropicRateMagnitude = 0;
        payload.calculateVisibility = true;
        delete payload.rateMagnitude;
        delete payload.rateMode;
      }
      return payload;
    }
    if (managerCategory === 'flow') {
      const payload: Record<string, unknown> = { name: managerForm.name };
      const [kind, value] = (managerForm.flowRef ?? '').split(':');
      if (kind === 'definition' && value) payload.definitionId = value;
      else if (kind === 'template' && value) payload.templateId = value;
      else throw new Error('选择要引用的流程定义或模板');
      if (managerForm.notes?.trim()) payload.notes = managerForm.notes.trim();
      return payload;
    }
    const [recipeId, versionText] = (managerForm.recipeRef ?? '').split('@');
    if (!recipeId) throw new Error('选择要引用的测量配方');
    const payload: Record<string, unknown> = {
      recipeId,
      recipeVersion: Number(versionText) || 1,
      design: {
        kind: managerForm.designKind || 'doe',
        variantCount: Math.max(1, Number(managerForm.variantCount) || 1),
        replicates: Math.max(1, Number(managerForm.replicates) || 1),
        seed: Number(managerForm.seed) || 0,
      },
    };
    if (managerForm.notes?.trim()) payload.notes = managerForm.notes.trim();
    return payload;
  };

  const saveManagerItem = () => run('library-save', async () => {
    const name = (managerForm.name ?? '').trim();
    if (!name) throw new Error('填写条目名称');
    const payload = buildManagerPayload();
    if (managerSelectedId) {
      await api<LibraryItem>(`/api/v1/workflow/library/items/${managerSelectedId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description: managerForm.notes ?? '', payload }),
      });
      setManagerStatus(`已保存 ${name}`);
    } else {
      const created = await postJson<LibraryItem>('/api/v1/workflow/library/items', {
        category: managerCategory, name, description: managerForm.notes ?? '', payload,
      });
      setManagerSelectedId(created.itemId);
      setManagerStatus(`已新增 ${name}`);
    }
    await refreshLibraryItems(managerCategory);
    await refreshLibraryItems();
  });

  const deleteManagerItem = () => run('library-delete', async () => {
    if (!managerSelectedId) throw new Error('先选择条目');
    const target = managerItems.find(item => item.itemId === managerSelectedId);
    await api(`/api/v1/workflow/library/items/${managerSelectedId}`, { method: 'DELETE' });
    setManagerSelectedId('');
    setManagerForm(managerDefaults(managerCategory));
    setManagerStatus(`已删除 ${target?.name ?? managerSelectedId}`);
    setManagerDeleteArmed(false);
    await refreshLibraryItems(managerCategory);
    await refreshLibraryItems();
  });

  const updateProjectMaterial = (key: 'substrateMaterial' | 'maskMaterial', value: string) => {
    setProject(previous => {
      if (!previous) return previous;
      const geometry = { ...(previous.geometry ?? {}) } as Record<string, unknown>;
      if (geometry.kind === 'gds') return previous;
      geometry[key] = value;
      return { ...previous, geometry };
    });
    setCompileStale(true);
  };

  const applyMaterialToProject = (material: string): string => {
    if (!project) return '';
    const geometry = { ...(project.geometry ?? {}) } as Record<string, unknown>;
    if (geometry.kind === 'gds' && Array.isArray(geometry.layers) && geometry.layers.length) {
      setProject(previous => {
        if (!previous) return previous;
        const next = { ...(previous.geometry ?? {}) } as Record<string, unknown>;
        next.layers = (next.layers as Array<Record<string, unknown>>).map((row, index) => index === 0 ? { ...row, material } : row);
        return { ...previous, geometry: next };
      });
      setCompileStale(true);
      return 'GDS 首层材料';
    }
    updateProjectMaterial('substrateMaterial', material);
    return '工程基底材料';
  };

  const commitSteps = (updater: (steps: ProjectStep[]) => ProjectStep[]) => {
    setProject(previous => previous ? { ...previous, steps: updater(previous.steps ?? []) } : previous);
    setCompileStale(true);
  };

  const updateSelectedStep = (patch: Record<string, unknown>) => {
    if (!selectedFlowStepId) return;
    commitSteps(steps => steps.map(step => step.id === selectedFlowStepId ? { ...step, ...patch } : step));
  };

  const addAuthoredStep = () => {
    const step = blankStep();
    commitSteps(steps => [...steps, step]);
    setSelectedFlowStepId(step.id);
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已添加步骤 ${step.name}`]);
  };

  const deleteAuthoredStep = (stepId: string) => {
    commitSteps(steps => steps.filter(step => step.id !== stepId));
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已删除步骤 ${stepId}`]);
  };

  const insertLibraryItem = (item: LibraryItem, flowMode: 'append' | 'replace' = 'append') => run('library-insert', async () => {
    if (item.category === 'material') {
      if (!project) throw new Error('材料插入需要先创建或导入工程（01）');
      const material = String(item.payload.material ?? item.name);
      setMaterialPreset(item.name);
      if (selectedFlowStep && selectedFlowStep.operation === 'deposit') {
        updateSelectedStep({ depositMaterial: material });
        setMaterialTarget({ itemName: item.name, material, target: '当前沉积步骤', stepName: selectedFlowStep.name });
        setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  材料 ${material} 已写入步骤 ${selectedFlowStep.name} 的 depositMaterial`]);
      } else {
        const target = applyMaterialToProject(material);
        setMaterialTarget({ itemName: item.name, material, target: target || '工程材料' });
        setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  材料 ${material} 已写入${target || '工程材料'}`]);
      }
      setManagerStatus('');
      return;
    }
    if (!project) throw new Error('先创建或导入工程');
    if (item.category === 'experiment') {
      setZone('experiment');
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  实验模板 ${item.name} 在 04 中使用`]);
      return;
    }
    if (item.category === 'process') {
      const step = stepFromLibrary(item.payload, item.name);
      commitSteps(steps => flowMode === 'replace' ? [step] : [...steps, step]);
      setSelectedFlowStepId(step.id);
      setConfirmLibraryItemId('');
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已从库插入 process: ${item.name}`]);
      return;
    }
    const templateId = typeof item.payload.templateId === 'string' ? item.payload.templateId : '';
    const definitionId = typeof item.payload.definitionId === 'string' ? item.payload.definitionId : '';
    let inserted: ProjectStep[] = [];
    if (templateId) {
      const template = await api<{ steps: Array<Record<string, unknown>>; variables?: Array<{ name: string; value: unknown }> }>(
        `/api/v1/workflow/templates/${templateId}`,
      );
      const variables = Object.fromEntries((template.variables ?? []).map(row => [row.name, row.value]));
      inserted = template.steps.map(row => stepFromLibrary(
        substituteTemplate(row, variables) as Record<string, unknown>, item.name,
      ));
    } else if (definitionId) {
      const expanded = await postJson<ModuleExpansion>(
        `/api/v1/workflow/flows/definitions/${definitionId}/expand`, { variables: {} },
      );
      inserted = expanded.steps.map(row => stepFromLibrary(row.step as Record<string, unknown>, item.name));
    } else {
      throw new Error('该流程预设没有可插入的 templateId 或 definitionId');
    }
    if (!inserted.length) throw new Error('流程预设没有步骤');
    commitSteps(steps => flowMode === 'replace' ? inserted : [...steps, ...inserted]);
    setSelectedFlowStepId(inserted[0].id);
    setConfirmLibraryItemId('');
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已插入流程 ${item.name} · ${inserted.length} 步`]);
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

  const analysisFrame = useMemo(() => {
    const views = Object.values(candidateViews);
    if (!views.length) return null;
    return mergeFrames(views.map(view => structureFrame(view, syncAxis)), syncZoom);
  }, [candidateViews, syncAxis, syncZoom]);

  const libraryDuplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    libraryItems.forEach(item => counts.set(item.name, (counts.get(item.name) ?? 0) + 1));
    return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([name]) => name));
  }, [libraryItems]);

  const managerDuplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    managerItems.forEach(item => counts.set(item.name, (counts.get(item.name) ?? 0) + 1));
    return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([name]) => name));
  }, [managerItems]);

  const recipeOptions = useMemo(() => {
    const latest = new Map<string, RecipeSummary>();
    for (const recipe of recipes) {
      const current = latest.get(recipe.recipeId);
      if (!current || recipe.version > current.version) latest.set(recipe.recipeId, recipe);
    }
    return [...latest.values()].sort((left, right) => left.name.localeCompare(right.name));
  }, [recipes]);

  const flowReferenceCount = flowDefinitions.length + templates.length;
  const managerSaveBlocked = managerCategory === 'flow'
    ? (!flowReferenceCount ? '先在 02 保存流程模板或流程定义，Flows 条目才有可引用对象。' : !(managerForm.flowRef ?? '') ? '选择要引用的流程定义或模板后才能保存。' : '')
    : managerCategory === 'experiment'
      ? (!recipeOptions.length ? '先在 03 完成一次测量以生成测量配方，Experiments 条目才能引用它。' : '')
      : '';

  const managerRowMeta = (item: LibraryItem): string => {
    if (item.category === 'material') {
      return [item.payload.catalog, item.payload.material, item.payload.role].filter(Boolean).join(' · ');
    }
    if (item.category === 'process') {
      const operation = item.payload.operation === 'deposit' ? '沉积' : item.payload.operation === 'etch' ? '刻蚀' : String(item.payload.operation ?? '');
      const model = STEP_LABELS[String(item.payload.model ?? '')] ?? String(item.payload.model ?? '');
      return `${operation} · ${model} · ${String(item.payload.rateMagnitude ?? '—')} µm/s · ${String(item.payload.duration ?? '—')} s`;
    }
    if (item.category === 'flow') {
      const definitionId = typeof item.payload.definitionId === 'string' ? item.payload.definitionId : '';
      const templateId = typeof item.payload.templateId === 'string' ? item.payload.templateId : '';
      return definitionId ? `流程定义 · ${definitionId}` : templateId ? `流程模板 · ${templateId}` : '流程引用';
    }
    return `配方 · ${String(item.payload.recipeId ?? '—')} v${String(item.payload.recipeVersion ?? 1)}`;
  };

  const renderFlowBar = () => (
    <div className="spx-flow-bar">
      <div className="spx-flow-viewswitch" role="tablist" aria-label="流程视图">
        <button type="button" role="tab" aria-selected={!libraryManagerOpen} className={!libraryManagerOpen ? 'active' : ''} onClick={() => setLibraryManagerOpen(false)}>流程编辑</button>
        <button type="button" role="tab" aria-selected={libraryManagerOpen} className={libraryManagerOpen ? 'active' : ''} onClick={() => openLibraryManager(libraryCategory)}>库管理</button>
      </div>
      <div className="spx-flow-actions">
        <span className={`spx-compile-badge${compile ? (compileStale ? ' stale' : ' ok') : ''}`}
          title={compile ? `Compile ${compile.compileId}` : '尚未编译流程'}>
          {compile ? (compileStale ? '需重新编译' : '已编译') : '未编译'}
        </span>
        <button className="wf-button primary" disabled={!canCompile}
          title={authoredSteps.length === 0 ? '流程为空：先插入或添加步骤' : '编译当前步骤，生成 Compile 与绑定 (binding)'}
          onClick={() => void compileFlow()}>{busy === 'compile-flow' ? '编译中…' : compile ? '重新编译' : '编译流程'}</button>
        {route
          ? <button className="wf-button" onClick={goToRun}>去 03</button>
          : compile && !compileStale
            ? <button className="wf-button" disabled={!canRoute} onClick={() => void createRoute()}>{busy === 'route' ? '创建中…' : '创建 Route'}</button>
            : null}
        {route && <button className="wf-button ghost" onClick={() => setRebuildRouteArmed(true)}>重建 Route…</button>}
      </div>
    </div>
  );

  const renderLibraryManager = () => (
    <div className="spx-library-view" aria-label="库管理">
      <div className="spx-library-view-head">
        <strong>库管理</strong>
        <span className="wf-muted">按分类维护可复用的材料、工艺、流程与实验条目。</span>
        <button type="button" className="wf-button" onClick={() => setLibraryManagerOpen(false)}>返回流程编辑</button>
      </div>
      <div className="spx-library-manager-tabs">
        {([
          ['material', 'Materials'],
          ['process', 'Processes'],
          ['flow', 'Flows'],
          ['experiment', 'Experiments'],
        ] as Array<[LibraryCategory, string]>).map(([category, label]) => <button
          key={category} type="button" className={managerCategory === category ? 'active' : ''}
          onClick={() => switchManagerCategory(category)}
        >{label}</button>)}
      </div>
      <div className="spx-library-category-head">
        <strong>{LIBRARY_CATEGORY_LABELS[managerCategory]}</strong>
        <span className={managerError ? 'spx-library-load-error' : 'wf-muted'}>
          {managerLoading ? '加载中…' : managerError ? `加载失败：${managerError}` : `${managerItems.length} 条`}
        </span>
        {managerError && <button type="button" className="wf-button ghost" onClick={() => void loadManagerCategory(managerCategory)}>重试</button>}
      </div>
      <div className="spx-library-manager-body">
        <div className="spx-library-manager-list">
          {managerLoading && <div className="wf-muted spx-library-empty">加载中…</div>}
          {!managerLoading && managerError && <div className="spx-library-load-error spx-library-empty">{managerError}<button type="button" className="wf-button ghost" onClick={() => void loadManagerCategory(managerCategory)}>重试</button></div>}
          {!managerLoading && !managerError && managerItems.length === 0 && <div className="wf-muted spx-library-empty">该分类暂无条目。</div>}
          {!managerLoading && !managerError && managerItems.map(item => {
            const duplicate = managerDuplicateNames.has(item.name);
            const color = item.category === 'material' && isHexColor(String(item.payload.color ?? ''))
              ? String(item.payload.color)
              : '#c7cfd6';
            return <button
              key={item.itemId} type="button"
              className={`spx-library-row${managerSelectedId === item.itemId ? ' active' : ''}`}
              title={`${item.name} · ${managerRowMeta(item)}${duplicate ? ` · ${item.itemId}` : ''}`}
              onClick={() => selectManagerItem(item)}
            >
              <i style={{ background: color }} aria-hidden="true" />
              <strong>{item.name}</strong>
              <span>{managerRowMeta(item)}</span>
              {duplicate && <span className="spx-library-row-id">#{item.itemId.slice(-6)}</span>}
            </button>;
          })}
        </div>
        <div className="spx-library-manager-actions">
          <button type="button" className="text" aria-label="新增库条目" title="新增条目" onClick={() => {
            setManagerSelectedId('');
            setManagerForm(managerDefaults(managerCategory));
            setManagerStatus('');
            setManagerDeleteArmed(false);
          }}>新增</button>
          <button type="button" aria-label="删除库条目" title={managerSelectedId ? '删除选中条目（需二次确认）' : '先选择条目'}
            disabled={!managerSelectedId}
            className={managerDeleteArmed ? 'danger text' : 'text'}
            onClick={() => {
              if (!managerDeleteArmed) { setManagerDeleteArmed(true); return; }
              void deleteManagerItem();
            }}>删除</button>
          {managerDeleteArmed && <>
            <div className="spx-library-confirm-delete">
              确认删除「{managerItems.find(item => item.itemId === managerSelectedId)?.name ?? '所选条目'}」？删除后无法恢复。
            </div>
            <button type="button" className="spx-link" onClick={() => { setManagerDeleteArmed(false); }}>取消删除</button>
          </>}
        </div>
        <div className="spx-library-manager-form">
          <label className="spx-field"><span>名称</span><input aria-label="库条目名称" value={managerForm.name ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, name: event.target.value }))} /></label>
          {managerCategory === 'material' && <>
            <label className="spx-field"><span>Catalog</span>
              <select aria-label="库材料 Catalog" value={managerForm.catalog ?? ''} onChange={event => {
                const catalog = event.target.value;
                const first = materialCatalog.find(row => (row.category ?? 'Generic') === catalog);
                setManagerForm(prev => ({
                  ...prev, catalog,
                  material: first?.name ?? '',
                  color: first?.color ?? prev.color,
                }));
              }}>
                {materialCategories.map(category => <option key={category} value={category}>{category}</option>)}
              </select>
            </label>
            <label className="spx-field"><span>Material</span>
              <select aria-label="库材料名称" value={managerForm.material ?? ''} onChange={event => {
                const material = event.target.value;
                const row = materialCatalog.find(item => item.name === material);
                setManagerForm(prev => ({ ...prev, material, color: row?.color ?? prev.color }));
              }}>
                {materialCatalog.filter(row => (row.category ?? 'Generic') === (managerForm.catalog ?? '')).map(row => <option key={row.name} value={row.name}>{row.name}</option>)}
              </select>
            </label>
            <div className="spx-field"><span>颜色</span>
              <div className="spx-color-row">
                <input aria-label="库材料颜色" className={isHexColor(managerForm.color ?? '') ? '' : 'invalid'}
                  value={managerForm.color ?? ''} placeholder="#rrggbb"
                  onChange={event => setManagerForm(prev => ({ ...prev, color: event.target.value }))} />
                <label className={isHexColor(managerForm.color ?? '') ? 'spx-color-swatch' : 'spx-color-swatch invalid'}
                  title={isHexColor(managerForm.color ?? '') ? '点击选择颜色' : '颜色必须是 #rrggbb'}
                  style={isHexColor(managerForm.color ?? '') ? { background: managerForm.color } : undefined}>
                  <input type="color" aria-label="库材料颜色选择器"
                    value={isHexColor(managerForm.color ?? '') ? (managerForm.color ?? '#000000') : '#000000'}
                    onChange={event => setManagerForm(prev => ({ ...prev, color: event.target.value }))} />
                </label>
              </div>
            </div>
            <label className="spx-field"><span>显示名</span><input aria-label="库材料显示名" value={managerForm.displayName ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, displayName: event.target.value }))} /></label>
            <label className="spx-field"><span>角色</span>
              <select aria-label="库材料角色" value={managerForm.role ?? 'film'} onChange={event => setManagerForm(prev => ({ ...prev, role: event.target.value }))}>
                {MATERIAL_ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
          </>}
          {managerCategory === 'process' && <>
            <label className="spx-field"><span>操作</span>
              <select aria-label="库工艺操作" value={managerForm.operation ?? 'etch'} onChange={event => setManagerForm(prev => ({ ...prev, operation: event.target.value }))}>
                <option value="etch">etch · 刻蚀</option>
                <option value="deposit">deposit · 沉积</option>
              </select>
            </label>
            <label className="spx-field"><span>模型</span>
              <select aria-label="库工艺模型" value={managerForm.model ?? 'isotropic'} onChange={event => setManagerForm(prev => ({ ...prev, model: event.target.value }))}>
                <option value="isotropic">isotropic · 各向同性</option>
                <option value="singleParticle">singleParticle · 单粒子</option>
                <option value="directional">directional · 定向</option>
                <option value="multiParticle">multiParticle · 多粒子</option>
                <option value="removeMaterial">removeMaterial · 移除材料</option>
                <option value="planarize">planarize · 平坦化</option>
              </select>
            </label>
            <label className="spx-field"><span>速率 (µm/s)</span><input aria-label="库工艺速率" type="number" step="0.01" value={managerForm.rateMagnitude ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, rateMagnitude: event.target.value }))} /></label>
            <label className="spx-field"><span>时长 (s)</span><input aria-label="库工艺时长" type="number" step="0.01" value={managerForm.duration ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, duration: event.target.value }))} /></label>
            {managerForm.operation === 'deposit' && <label className="spx-field"><span>沉积材料</span>
              <select aria-label="库工艺沉积材料" value={managerForm.depositMaterial ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, depositMaterial: event.target.value }))}>
                <option value="">选择材料</option>
                {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name}</option>)}
              </select>
            </label>}
            {managerForm.model === 'singleParticle' && <>
              <label className="spx-field"><span>粘附系数 stickingProbability</span><input aria-label="库工艺 stickingProbability" type="number" step="0.01" value={managerForm.stickingProbability ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, stickingProbability: event.target.value }))} /></label>
              <label className="spx-field"><span>源指数 sourceExponent</span><input aria-label="库工艺 sourceExponent" type="number" step="0.1" value={managerForm.sourceExponent ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, sourceExponent: event.target.value }))} /></label>
            </>}
          </>}
          {managerCategory === 'flow' && <label className="spx-field"><span>引用流程定义 / 模板</span>
            <select aria-label="库流程引用" value={managerForm.flowRef ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, flowRef: event.target.value }))} disabled={!flowReferenceCount}>
              <option value="">{flowReferenceCount ? '选择引用' : '无可引用的流程'}</option>
              <optgroup label="流程定义">
                {flowDefinitions.map(row => <option key={row.definitionId} value={`definition:${row.definitionId}`}>{row.name} · {row.definitionId}</option>)}
              </optgroup>
              <optgroup label="流程模板">
                {templates.map(row => <option key={row.templateId} value={`template:${row.templateId}`}>{row.name} · {row.stepCount} 步</option>)}
              </optgroup>
            </select>
          </label>}
          {managerCategory === 'experiment' && <>
            <label className="spx-field"><span>测量配方</span>
              <select aria-label="库实验配方" value={managerForm.recipeRef ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, recipeRef: event.target.value }))} disabled={!recipeOptions.length}>
                <option value="">{recipeOptions.length ? '选择配方' : '尚无测量配方'}</option>
                {recipeOptions.map(row => <option key={row.recipeId} value={`${row.recipeId}@${row.version}`}>{row.name} · v{row.version} · {row.method}</option>)}
              </select>
            </label>
            <label className="spx-field"><span>设计类型</span>
              <select aria-label="库实验设计" value={managerForm.designKind ?? 'doe'} onChange={event => setManagerForm(prev => ({ ...prev, designKind: event.target.value }))}>
                <option value="doe">doe · 全因子</option>
                <option value="manual">manual · 手工</option>
              </select>
            </label>
            <label className="spx-field"><span>变体数</span><input aria-label="库实验变体数" type="number" min={1} value={managerForm.variantCount ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, variantCount: event.target.value }))} /></label>
            <label className="spx-field"><span>重复数</span><input aria-label="库实验重复数" type="number" min={1} value={managerForm.replicates ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, replicates: event.target.value }))} /></label>
            <label className="spx-field"><span>随机种子</span><input aria-label="库实验种子" type="number" value={managerForm.seed ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, seed: event.target.value }))} /></label>
          </>}
          <label className="spx-field"><span>备注 / 描述</span><input aria-label="库条目备注" value={managerForm.notes ?? ''} onChange={event => setManagerForm(prev => ({ ...prev, notes: event.target.value }))} /></label>
          {managerSaveBlocked && <div className="spx-library-reason" role="note">{managerSaveBlocked}</div>}
          <div className="spx-library-manager-form-actions">
            <button type="button" className="wf-button primary" disabled={!!busy || !!managerSaveBlocked} onClick={() => void saveManagerItem()}>{busy === 'library-save' ? '保存中…' : '保存'}</button>
            <button type="button" className="wf-button" onClick={() => {
              if (managerSelectedId) {
                const item = managerItems.find(row => row.itemId === managerSelectedId);
                if (item) setManagerForm(managerFormFromItem(item));
              } else {
                setManagerForm(managerDefaults(managerCategory));
              }
              setManagerStatus('');
              setManagerDeleteArmed(false);
            }}>取消</button>
          </div>
          {managerStatus && <div className="wf-summary" role="status">{managerStatus}</div>}
        </div>
      </div>
    </div>
  );

  const clipLayerMaterial = (layer: number): string => {
    const key = selectedPairs.find(item => Number(item.split(':')[0]) === layer);
    return (key ? layerMaterials[key] : '') || activeClip?.layers.find(item => item.layer === layer)?.material || 'Si';
  };

  const duplicateClipNames = new Set(clips
    .map(clip => clip.name)
    .filter((name, index, list) => list.indexOf(name) !== index));

  const clipLabel = (clip: Clip) => duplicateClipNames.has(clip.name)
    ? `${clip.name} #${clip.clipId.slice(-6)}`
    : clip.name;

  const renderClipCanvas = (source: ClipCanvas) => {
    const unit = 'µm';
    const xTicks = axisTicks(source.bbox.x0, source.bbox.x1);
    const yTicks = axisTicks(source.bbox.y0, source.bbox.y1);
    const span = Math.max(source.width, source.height, 1e-6);
    const fontSize = span / 26;
    const strokeWidth = span / 400;
    const barLength = xTicks.length > 1 ? Math.abs(xTicks[1] - xTicks[0]) : span / 4;
    const legendRows = Array.from(new Set(source.polygons.map(polygon => polygon.layer)))
      .map(layer => ({ layer, material: clipLayerMaterial(layer), color: materialColor(materialCatalog, clipLayerMaterial(layer)) }));
    return <div className="wf-canvas-wrap">
      <svg viewBox={`0 0 ${source.width} ${source.height}`} className="wf-clip-canvas" role="img" aria-label="版图 Clip 画布">
        <rect x={0} y={0} width={source.width} height={source.height} fill="#f8fafb" />
        {xTicks.map(tick => <g key={`x-${tick}`} className="wf-axes">
          <line x1={tick} y1={0} x2={tick} y2={source.height} stroke="#e6eaee" strokeWidth={strokeWidth} />
          <text x={tick} y={source.height - fontSize * 0.3} fontSize={fontSize} textAnchor="middle">{formatTick(tick)}</text>
        </g>)}
        {yTicks.map(tick => <g key={`y-${tick}`} className="wf-axes">
          <line x1={0} y1={source.height - tick} x2={source.width} y2={source.height - tick} stroke="#e6eaee" strokeWidth={strokeWidth} />
          <text x={fontSize * 0.2} y={source.height - tick - fontSize * 0.2} fontSize={fontSize}>{formatTick(tick)}</text>
        </g>)}
        {source.polygons.map((polygon, index) => {
          const color = materialColor(materialCatalog, clipLayerMaterial(polygon.layer));
          return <polygon
            key={`${polygon.layer}-${index}`}
            points={polygon.points.map(([x, y]) => `${x},${source.height - y}`).join(' ')}
            fill={color}
            fillOpacity="0.45"
            stroke={color}
            strokeWidth={strokeWidth}
          />;
        })}
        {activeClip?.annotations.map(annotation => <g key={annotation.annotationId}>
          <circle cx={annotation.x} cy={activeClip.height - annotation.y} r={Math.max(source.width, source.height) / 80} className="wf-annotation" />
          <text x={annotation.x} y={activeClip.height - annotation.y} className="wf-annotation-text">{annotation.text}</text>
        </g>)}
        <g className="wf-axes">
          <line x1={source.width - barLength} y1={source.height - fontSize * 1.6} x2={source.width} y2={source.height - fontSize * 1.6} stroke="#34404b" strokeWidth={strokeWidth * 3} />
          <text x={source.width - barLength / 2} y={source.height - fontSize * 2.1} fontSize={fontSize} textAnchor="middle">{`${formatTick(barLength)} ${unit}`}</text>
        </g>
      </svg>
      <div className="wf-muted">{source.polygonCount} 多边形 · {source.pointCount} 点 · {source.width.toFixed(3)} × {source.height.toFixed(3)} {unit}</div>
      <div className="spx-layer-legend">
        {legendRows.map(row => <span key={row.layer}>
          <i style={{ background: row.color }} aria-hidden="true" />L{row.layer} · {row.material}
        </span>)}
      </div>
    </div>;
  };

  const renderClipEditor = (mode: 'draft' | 'project', showList = true) => (
    <div className="wf-clip-editor">
      <div className="spx-file-row">
        <div className={gds ? 'spx-file-name filled' : 'spx-file-name'}>{gds ? gds.fileName : '未选择 GDS 文件'}</div>
        <button type="button" className="wf-button ghost spx-browse" disabled={!!busy} onClick={() => gdsInput.current?.click()}>{gds ? '更换 GDS' : '上传 GDS'}</button>
      </div>
      {gds && <div className="spx-form-note">asset {gds.assetId.slice(0, 20)}…</div>}
      <div className="wf-template-row">
        <button className="wf-button ghost" disabled={!!busy || !gds} title={!gds ? '先上传 GDS' : '注册为版图'} onClick={() => void registerLayout()}>注册为 Layout</button>
        <select aria-label="版图选择" value={activeLayout?.layoutId ?? ''} onChange={event => void chooseLayout(event.target.value)}>
          <option value="">{layouts.length ? '选择 Layout' : '尚无 Layout'}</option>
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
            const material = layerMaterials[key] || 'Si';
            return <label key={key} className="spx-layer-pick">
              <input type="checkbox" checked={selectedPairs.includes(key)}
                onChange={event => setSelectedPairs(previous => event.target.checked ? [...previous, key] : previous.filter(item => item !== key))} />
              <span>L{layer}/{datatype}</span>
              <select aria-label={`图层 ${layer} 材料`} value={material}
                onChange={event => setLayerMaterials(previous => ({ ...previous, [key]: event.target.value }))}>
                {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name}</option>)}
              </select>
              <i style={{ background: materialColor(materialCatalog, material) }} aria-hidden="true" />
            </label>;
          })}
        </div>
        <div className="wf-bounds">
          {(['x0', 'y0', 'x1', 'y1'] as const).map(key => <input key={key} aria-label={`Clip ${key}`} value={String(clipBounds[key])} onChange={event => setClipBounds(previous => ({ ...previous, [key]: Number(event.target.value) }))} />)}
          <button className="wf-button ghost" disabled={!!busy} onClick={() => void previewClip()}>预览</button>
          <button className="wf-button ghost" disabled={!!busy} onClick={() => void saveClip()}>保存 Clip</button>
        </div>
      </>}
      {clipNotice && <div className="spx-clip-conflict" role="status">{clipNotice}</div>}
      {canvas && renderClipCanvas(canvas)}
      {activeClip && <div className="wf-summary"><strong>{activeClip.name}</strong><span>{activeClip.layers.length} 层 · {activeClip.polygonCount} 多边形</span><span><code>{activeClip.clipHash.slice(0, 12)}…</code></span><span>{activeClip.annotations.length} 标注</span></div>}
      {activeClip && <div className="wf-template-row">
        <input aria-label="标注文本" value={annotationText} onChange={event => setAnnotationText(event.target.value)} placeholder="标注文本" />
        <input aria-label="标注 X" value={annotationX} onChange={event => setAnnotationX(event.target.value)} />
        <input aria-label="标注 Y" value={annotationY} onChange={event => setAnnotationY(event.target.value)} />
        <button className="wf-button ghost" disabled={!!busy} onClick={() => void addAnnotation()}>添加标注</button>
      </div>}
      {showList && clips.length > 0 && <div className="spx-clip-summary-list" aria-label="Clip 列表">
        {clips.map(clip => <button key={clip.clipId} type="button" className={activeClip?.clipId === clip.clipId ? 'active' : ''} onClick={() => selectClip(clip)}>
          <strong>{clipLabel(clip)}</strong>
          <span>{clip.layers.length} 层 · {clip.polygonCount} 多边形</span>
          <span>x {clip.bbox.x0}…{clip.bbox.x1} · y {clip.bbox.y0}…{clip.bbox.y1} µm</span>
        </button>)}
      </div>}
      {activeClip && mode === 'draft' && <div className="wf-muted">已选择 Clip：{activeClip.name}。保存工程后，02 编译将使用该 Clip 作为几何。</div>}
      {activeClip && mode === 'project' && <div className="wf-template-row">
        <button className="wf-button ghost" disabled={!!busy} onClick={() => void useClipAsGeometry(activeClip, false)}>用作工程几何</button>
        <button className="wf-button primary" disabled={!!busy || !(project?.steps?.length)}
          title={!(project?.steps?.length) ? '流程还没有步骤，先在 02 添加步骤' : '使用该 Clip 编译当前流程'}
          onClick={() => void useClipAsGeometry(activeClip, true)}>{busy === 'clip-use' ? '编译中…' : '用此 Clip 编译'}</button>
      </div>}
    </div>
  );

  const currentSection = ZONES.find(item => item.id === zone) ?? ZONES[0];
  const propertyRows: Array<[string, string]> = [
    ['工程', project?.name ?? '—'],
    ['维度', project ? `${project.dimension ?? '?'}D` : '—'],
    ['编译', compile ? `${compile.compileId.slice(0, 18)}…` : '—'],
    ['绑定 (binding)', compile?.bindingId ? `${compile.bindingId.slice(0, 18)}…` : '—'],
    ['Route', route?.route.name ?? '—'],
    ['Segment', segment ? `${statusLabel(segment.status)} · ${segment.segmentId.slice(0, 12)}…` : '—'],
    ['Run', segment?.runId ? `${segment.runId.slice(0, 16)}…` : '—'],
    ['测量', measurement ? `${measurement.value === null ? '—' : measurement.value.toFixed(4)} ${measurement.unit}` : '—'],
    ['引擎', health ? `ViennaPS ${health.viennaps ?? '—'}` : '—'],
  ];

  return (
    <div className="spx-app">
      <header className="spx-frame">
        <div className="spx-titlebar">
          <div className="spx-brand">
            <span className="spx-brand-mark">VP</span>
            <strong>ViennaPS Workbench</strong>
            <span className="spx-doc-name">{currentSection.num} {currentSection.label}</span>
          </div>
        </div>
        <div className="spx-menubar">
          {openMenu && <div className="spx-menu-scrim" onClick={() => setOpenMenu(null)} />}
          {[
            ['file', 'File'], ['edit', 'Edit'], ['view', 'View'], ['project', 'Project'],
            ['run', 'Run'], ['tools', 'Tools'], ['help', 'Help'],
          ].map(([id, label]) => <div className="spx-menu" key={id}>
            <button type="button" className={openMenu === id ? 'spx-menu-button active' : 'spx-menu-button'} aria-expanded={openMenu === id} onClick={() => setOpenMenu(openMenu === id ? null : id)}>{label}</button>
            {openMenu === id && <div className="spx-menu-popover" role="menu">
              {id === 'file' && <>
                <button role="menuitem" onClick={() => { setOpenMenu(null); projectInput.current?.click(); }}>导入工程 JSON…</button>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); void saveProject(); }}>保存工程</button>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); exportProjectJson(); }}>导出工程 JSON…</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); setSetupMode('new'); setDraft(prev => ({ ...prev, source: 'layout' })); gdsInput.current?.click(); }}>上传 GDS 版图…</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); closeProject(); }}>关闭工程 / 返回开始页</button>
              </>}
              {id === 'edit' && <>
                <button role="menuitem" onClick={() => { setOpenMenu(null); setActivityLog([]); }}>清空 Output</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); setComparison(null); setCandidateViews({}); setSyncZoom(1); setSyncAxis('xy'); }}>重置分析视图</button>
              </>}
              {id === 'view' && <>
                {ZONES.map(item => <button key={item.id} role="menuitem" onClick={() => { setOpenMenu(null); setZone(item.id); }}>{item.num} {item.label} — {item.title}</button>)}
                {onOpenLegacy && <button role="menuitem" onClick={() => { setOpenMenu(null); onOpenLegacy(); }}>传统工作区</button>}
              </>}
              {id === 'project' && <>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); void saveProject(); }}>保存工程</button>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); exportProjectJson(); }}>导出工程 JSON…</button>
                <button role="menuitem" disabled={!canCompile} onClick={() => { setOpenMenu(null); void compileFlow(); }}>编译流程</button>
                <button role="menuitem" disabled={!canRoute} onClick={() => { setOpenMenu(null); void createRoute(); }}>创建 Route</button>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); void saveTemplate(); }}>保存当前步骤为模板</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); openLibraryManager('process'); }}>管理库</button>
                <button role="menuitem" disabled={!libraryName.trim()} onClick={() => { setOpenMenu(null); void exportLibrary(); }}>导出共享库</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); setZone('flow'); }}>打开流程编辑器</button>
              </>}
              {id === 'run' && <>
                <button role="menuitem" disabled={!canRun} onClick={() => { setOpenMenu(null); void execute(); }}>执行当前 Segment</button>
                <button role="menuitem" disabled={!route} onClick={() => { setOpenMenu(null); void executeAll(); }}>执行全部 Segment</button>
                <button role="menuitem" disabled={!canMeasure} onClick={() => { setOpenMenu(null); void measure(); }}>测量并绑定 Provenance</button>
                <button role="menuitem" disabled={!route || !decisionPrefixId} onClick={() => { setOpenMenu(null); void decideMeasurementBranch(); }}>测量驱动分支</button>
              </>}
              {id === 'tools' && <>
                <button role="menuitem" disabled={!gds} onClick={() => { setOpenMenu(null); void registerLayout(); }}>注册当前 GDS 为 Layout</button>
                <button role="menuitem" disabled={!activeLayout} onClick={() => { setOpenMenu(null); void previewClip(); }}>预览 Clip</button>
                <button role="menuitem" disabled={!activeLayout} onClick={() => { setOpenMenu(null); void saveClip(); }}>保存 Clip</button>
                <button role="menuitem" disabled={!compile || !activeRecipeId} onClick={() => { setOpenMenu(null); void createDoe(); }}>设计 DOE</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); setZone('analyze'); }}>打开分析与标定</button>
              </>}
              {id === 'help' && <>
                <button role="menuitem" onClick={() => { setOpenMenu(null); setAboutOpen(true); }}>关于 ViennaPS Workbench</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); window.open('/api/health', '_blank'); }}>打开健康检查 JSON</button>
              </>}
            </div>}
          </div>)}
          <div className="spx-menu-spacer" />
          <div className="spx-functions" role="tablist" aria-label="Workflow functions">
            {ZONES.map(item => <button
              key={item.id}
              type="button"
              aria-pressed={zone === item.id}
              className={zone === item.id ? 'active' : ''}
              onClick={() => setZone(item.id)}
            >{item.num} {item.label}</button>)}
          </div>
        </div>
      </header>

      {error && <div className="wf-error spx-error">{error}</div>}

      <div className="spx-body">
        <aside className="spx-left">
          <div className="spx-panel">
            <div className="spx-panel-title"><span>工程</span></div>
            <div className="spx-tree" role="tree" aria-label="Project tree">
              <div className="spx-tree-root"><span>▾</span><strong>{project?.name ?? '未打开工程'}</strong></div>
              <div className="spx-tree-meta">{project ? `${project.dimension ?? '?'}D · ${project.steps?.length ?? 0} 步 · ${projectGeometrySource.kind === 'layout' ? 'GDS Clip' : '参数化'}` : '新建工程或导入 ProjectV7'}</div>
              {[
                {
                  label: '编译',
                  target: 'flow' as const,
                  value: compile ? `${compile.stepCount} 步` : '未编译',
                  badge: compile ? (compileStale ? '需重新编译' : '已编译') : '未编译',
                  badgeClass: compile ? (compileStale ? 'stale' : 'succeeded') : 'planned',
                  title: compile ? `Compile ${compile.compileId}` : '尚未编译流程',
                },
                {
                  label: 'Route',
                  target: 'run' as const,
                  value: route ? `${route.segments.length} Segment` : '未创建',
                  badge: route ? '已创建' : '未创建',
                  badgeClass: route ? 'succeeded' : 'planned',
                  title: route ? `Route ${route.route.routeId}` : '尚未创建 Route',
                },
                {
                  label: `Run · ${segment ? statusLabel(segment.status) : '未运行'}`,
                  target: 'run' as const,
                  value: segment ? `第 ${Math.max(1, (route?.segments.findIndex(item => item.segmentId === segment.segmentId) ?? 0) + 1)} 个 Segment` : '—',
                  badge: segment?.status === 'succeeded' ? '成功' : segment ? statusLabel(segment.status) : '',
                  badgeClass: segment?.status === 'succeeded' ? 'succeeded' : segment?.status === 'running' ? 'running' : segment?.status === 'failed' ? 'failed' : 'planned',
                  title: segment?.runId ? `Run ${segment.runId}` : '尚未执行 Segment',
                },
                {
                  label: '测量',
                  target: 'run' as const,
                  value: measurement ? `${measurement.value === null ? '—' : measurement.value.toFixed(4)} ${measurement.unit}` : '—',
                  badge: measurement ? '已测量' : '未测量',
                  badgeClass: measurement ? 'measured' : 'planned',
                  title: measurement ? `Measurement ${measurement.measurementId}` : '尚未测量',
                },
                {
                  label: '实验',
                  target: 'experiment' as const,
                  value: experimentDoc ? `${experimentDoc.variants.length} 变体` : '—',
                  badge: experimentDoc ? statusLabel(experimentDoc.status) : '未创建',
                  badgeClass: experimentDoc?.status === 'completed' ? 'succeeded' : experimentDoc ? 'running' : 'planned',
                  title: experimentDoc ? `Experiment ${experimentDoc.experimentId}` : '尚未创建实验',
                },
                {
                  label: '分析',
                  target: 'analyze' as const,
                  value: comparison ? `${comparison.candidates.length} 候选` : '—',
                  badge: comparison ? '已载入' : '未载入',
                  badgeClass: comparison ? 'succeeded' : 'planned',
                  title: '候选比较 / process window / 标定',
                },
                {
                  label: '报告',
                  target: 'report' as const,
                  value: reportDocument ? reportDocument.reportId.slice(0, 10) : '—',
                  badge: reportDocument ? '已生成' : '未生成',
                  badgeClass: reportDocument ? 'succeeded' : 'planned',
                  title: reportDocument ? `Report ${reportDocument.reportId}` : '尚未生成报告',
                },
              ].map(item => <div
                key={item.label}
                role="treeitem"
                tabIndex={0}
                aria-selected={zone === item.target}
                className={zone === item.target ? 'spx-tree-item active' : 'spx-tree-item'}
                onClick={() => setZone(item.target)}
                onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') setZone(item.target); }}
                title={item.title}
              ><span className="spx-tree-label">{item.label}</span><span className="spx-tree-value">{item.value}</span>{item.badge && <span className={`spx-tree-status ${item.badgeClass}`}>{item.badge}</span>}</div>)}
            </div>
            <div className="spx-tree-legend" aria-label="状态图例">
              <span>待执行/未创建</span><span>运行中</span><span>成功/已测量</span><span>失败/待重跑</span>
            </div>
          </div>
          <div className="spx-panel">
            <div className="spx-panel-title"><span>库</span></div>
            <div className="spx-tree">
              {([
                { label: '材料', count: libraryItems.filter(item => item.category === 'material').length, onClick: () => openLibraryManager('material') },
                { label: '工艺', count: libraryItems.filter(item => item.category === 'process').length, onClick: () => openLibraryManager('process') },
                { label: '流程', count: libraryItems.filter(item => item.category === 'flow').length, onClick: () => setZone('flow') },
                { label: '实验', count: libraryItems.filter(item => item.category === 'experiment').length, onClick: () => openLibraryManager('experiment') },
                { label: '版图', count: layouts.length, onClick: () => { setZone('setup'); setSetupMode('layout'); void refreshLayouts(); } },
                { label: 'Clip', count: clips.length, onClick: () => { setZone('setup'); setSetupMode('layout'); void refreshLayouts(); } },
                { label: 'Bundles', count: libraryBundles.length, onClick: () => { setZone('flow'); setLibraryAdvancedOpen(true); } },
              ]).map(item => <div
                key={item.label}
                role="treeitem"
                tabIndex={0}
                className="spx-tree-item"
                onClick={item.onClick}
                onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') item.onClick(); }}
              ><span>{item.label}</span><span className="spx-count">{item.count}</span></div>)}
            </div>
          </div>
          <div className="spx-left-foot">
            {onOpenLegacy && <button type="button" className="spx-link" onClick={onOpenLegacy}>传统工作区</button>}
            <span>v0.1.0</span>
          </div>
        </aside>

        <section className="spx-center">
          <div className="spx-doc-header">
            <div>
              <div className="spx-eyebrow">WORKFLOW / {currentSection.num}</div>
              <h1>{currentSection.title}</h1>
              <p>{currentSection.desc}</p>
            </div>
            <div className="spx-doc-state">
              <span className="spx-state-pill">{project ? `${project.dimension ?? '?'}D · ${projectGeometrySource.kind === 'layout' ? 'GDS Clip' : '参数化'}` : '未打开工程'}</span>
              {project && <span className={projectDirty ? 'spx-state-pill' : 'spx-state-pill ok'}>{projectId ? (projectDirty ? '有未保存更改' : '已保存') : '草稿（未保存）'}</span>}
            </div>
          </div>

          <main className="wf-grid spx-doc-body">
        <section className={`spx-page wf-setup ${zone === 'setup' ? '' : 'wf-hidden'}`}>
          <div className="spx-command-row">
            <button type="button" className={setupMode === 'new' ? 'spx-command active' : 'spx-command'} onClick={startNewProject}>
              <span className="spx-command-icon">＋</span>
              <strong>新建工程</strong>
              <span>定义名称、维度与几何来源</span>
            </button>
            <button type="button" className={setupMode === 'examples' ? 'spx-command active' : 'spx-command'} onClick={() => setSetupMode('examples')}>
              <span className="spx-command-icon">▦</span>
              <strong>从示例开始</strong>
              <span>载入 2D / 3D starter project</span>
            </button>
            <button type="button" className="spx-command" onClick={() => projectInput.current?.click()}>
              <span className="spx-command-icon">⇧</span>
              <strong>导入 JSON</strong>
              <span>高级：导入现有 ProjectV7</span>
            </button>
          </div>

          {newProjectArmed && <div className="spx-rebuild" role="alert">
            <span>当前工程「{project?.name ?? '未命名'}」有未保存更改。新建工程会清空当前编辑，未保存内容将丢失。</span>
            <button type="button" className="wf-button" onClick={() => setNewProjectArmed(false)}>取消</button>
            <button type="button" className="wf-button primary" onClick={beginNewProjectDraft}>确认新建</button>
          </div>}

          {!project && <div className="spx-sheet spx-recent-projects">
            <div className="spx-sheet-title">最近工程</div>
            <div className="spx-layout-list" aria-label="最近工程列表">
              {projects.length === 0 && <div className="wf-empty spx-library-empty">
                还没有已保存工程。用上方“新建工程”建立草稿，保存后会出现在这里。
              </div>}
              {projects.map(record => <div key={record.projectId} className="spx-project-row">
                <button type="button" className="spx-project-open"
                  onClick={() => void openProject(record)}
                  title={`${record.projectId} · 更新于 ${formatDateTime(record.updatedAt)}`}
                >
                  <strong>{record.name}</strong>
                  <span>{record.dimension}D · {record.stepCount} 步 · {formatDateTime(record.updatedAt)}</span>
                </button>
                <button type="button" className="wf-button ghost"
                  onClick={() => setDeleteArmedId(deleteArmedId === record.projectId ? '' : record.projectId)}
                >{deleteArmedId === record.projectId ? '取消' : '删除'}</button>
              </div>)}
            </div>
            {deleteArmedId && <div className="spx-rebuild" role="alert">
              <span>确认删除工程「{projects.find(item => item.projectId === deleteArmedId)?.name ?? deleteArmedId}」？删除后无法恢复。</span>
              <button type="button" className="wf-button" onClick={() => setDeleteArmedId('')}>取消</button>
              <button type="button" className="wf-button primary" disabled={!!busy} onClick={() => {
                const record = projects.find(item => item.projectId === deleteArmedId);
                if (record) void deleteProjectRecord(record);
              }}>确认删除</button>
            </div>}
          </div>}

          {(project || gds) && <div className="spx-attached-row">
            {project && <div className="spx-file-name filled">工程 · {project.name ?? '未命名'} · {project.dimension ?? '?'}D · {project.steps?.length ?? 0} 步</div>}
            {gds && <div className="spx-file-name filled">GDS · {gds.fileName}</div>}
          </div>}

          {setupMode === 'new' && <div className="spx-sheet">
            <div className="spx-sheet-title">新建工程</div>
            <div className="spx-form-groups">
              <section className="spx-field-group">
                <div className="spx-group-title">基本</div>
                <div className="spx-field-grid">
                  <label className="spx-field span-2"><span>工程名称</span><input aria-label="新工程名称" value={draft.name} onChange={event => setDraftField('name', event.target.value)} /></label>
                  <div className="spx-field"><span>维度</span>
                    <div className="spx-segment">
                      <button type="button" className={draft.dimension === 2 ? 'active' : ''} onClick={() => setDraftDimension(2)}>2D</button>
                      <button type="button" className={draft.dimension === 3 ? 'active' : ''} onClick={() => setDraftDimension(3)}>3D</button>
                    </div>
                  </div>
                  <label className="spx-field"><span>基底材料</span>
                    <select aria-label="新工程基底材料" value={draft.substrateMaterial} onChange={event => setDraftField('substrateMaterial', event.target.value)}>
                      {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                    </select>
                  </label>
                  <label className="spx-field"><span>掩膜材料</span>
                    <select aria-label="新工程掩膜材料" value={draft.maskMaterial} onChange={event => setDraftField('maskMaterial', event.target.value)}>
                      {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                    </select>
                  </label>
                </div>
              </section>

              <section className="spx-field-group">
                <div className="spx-group-title">几何来源</div>
                <div className="spx-segment spx-source-pick">
                  <button type="button" className={draft.source === 'parametric' ? 'active' : ''} onClick={() => chooseDraftSource('parametric')}>参数化</button>
                  <button type="button" className={draft.source === 'layout' ? 'active' : ''} disabled={draft.dimension === 3}
                    title={draft.dimension === 3 ? 'GDS 版图几何仅支持 2D 工程' : '从 GDS 版图 Clip 构建几何'}
                    onClick={() => chooseDraftSource('layout')}>{draft.dimension === 3 ? '🔒 ' : ''}GDS / Layout</button>
                </div>
                {draft.dimension === 3 && <p className="spx-lock-note">3D 工程不支持 GDS 版图几何：图层→材料映射只在 2D 中定义，请切回 2D 或使用参数化几何。</p>}
                {draft.source === 'parametric' && <>
                  <div className="spx-geometry-picks">
                    {(draft.dimension === 2 ? ['plane', 'trench'] : ['hole', 'fin']).map(kind => <button
                      type="button" key={kind}
                      className={draft.geometry === kind ? 'active' : ''}
                      onClick={() => setDraftField('geometry', kind)}
                    >{kind === 'plane' ? 'Plane' : kind === 'trench' ? 'Trench' : kind === 'hole' ? 'Hole' : 'Fin'}</button>)}
                  </div>
                  <div className="spx-field-grid">
                    {draft.geometry === 'plane' && <label className="spx-field"><span>Plane height (µm)</span><input aria-label="新工程高度" type="number" value={draft.height} onChange={event => setDraftField('height', Number(event.target.value))} /></label>}
                    {draft.geometry === 'trench' && <>
                      <label className="spx-field"><span>Trench width (µm)</span><input aria-label="新工程 Trench 宽度" type="number" value={draft.width} onChange={event => setDraftField('width', Number(event.target.value))} /></label>
                      <label className="spx-field"><span>Trench depth (µm)</span><input aria-label="新工程 Trench 深度" type="number" value={draft.depth} onChange={event => setDraftField('depth', Number(event.target.value))} /></label>
                    </>}
                    {draft.geometry === 'hole' && <>
                      <label className="spx-field"><span>Hole radius (µm)</span><input aria-label="新工程孔半径" type="number" value={draft.radius} onChange={event => setDraftField('radius', Number(event.target.value))} /></label>
                      <label className="spx-field"><span>Hole depth (µm)</span><input aria-label="新工程孔深度" type="number" value={draft.depth} onChange={event => setDraftField('depth', Number(event.target.value))} /></label>
                    </>}
                    {draft.geometry === 'fin' && <>
                      <label className="spx-field"><span>Fin width (µm)</span><input aria-label="新工程 Fin 宽度" type="number" value={draft.width} onChange={event => setDraftField('width', Number(event.target.value))} /></label>
                      <label className="spx-field"><span>Fin height (µm)</span><input aria-label="新工程 Fin 高度" type="number" value={draft.finHeight} onChange={event => setDraftField('finHeight', Number(event.target.value))} /></label>
                    </>}
                    {draft.geometry !== 'plane' && <>
                      <label className="spx-field"><span>Taper angle (deg)</span><input aria-label="新工程 taper" type="number" value={draft.taperAngle} onChange={event => setDraftField('taperAngle', Number(event.target.value))} /></label>
                      <label className="spx-field"><span>Mask height (µm)</span><input aria-label="新工程 maskHeight" type="number" value={draft.maskHeight} onChange={event => setDraftField('maskHeight', Number(event.target.value))} /></label>
                    </>}
                  </div>
                </>}
                {draft.source === 'layout' && <div className="spx-inline-section">
                  {renderClipEditor('draft')}
                </div>}
              </section>

              <section className="spx-field-group">
                <div className="spx-group-title">Domain &amp; Grid</div>
                <div className="spx-field-grid">
                  <label className="spx-field"><span>gridDelta (µm)</span><input aria-label="新工程 gridDelta" type="number" value={draft.gridDelta} onChange={event => setDraftField('gridDelta', Number(event.target.value))} /></label>
                  <label className="spx-field"><span>X extent (µm)</span><input aria-label="新工程 xExtent" type="number" value={draft.xExtent} onChange={event => setDraftField('xExtent', Number(event.target.value))} /></label>
                  {draft.dimension === 3 && <label className="spx-field"><span>Y extent (µm)</span><input aria-label="新工程 yExtent" type="number" value={draft.yExtent} onChange={event => setDraftField('yExtent', Number(event.target.value))} /></label>}
                </div>
              </section>

              <section className="spx-field-group">
                <div className="spx-group-title">Solver</div>
                <div className="spx-field-grid">
                  <label className="spx-field"><span>Engine</span>
                    <select aria-label="新工程引擎" value={draft.engine} onChange={event => setDraftField('engine', event.target.value)}>
                      <option value="cpu">CPU</option>
                      <option value="gpu">GPU</option>
                      <option value="auto">Auto</option>
                    </select>
                  </label>
                  <label className="spx-field"><span>Seed</span><input aria-label="新工程 seed" type="number" value={draft.seed} onChange={event => setDraftField('seed', Number(event.target.value))} /></label>
                  <label className="spx-field"><span>Timeout (s)</span><input aria-label="新工程 timeout" type="number" value={draft.timeout} onChange={event => setDraftField('timeout', Number(event.target.value))} /></label>
                  <label className="spx-field"><span>Rays per point</span><input aria-label="新工程 rays" type="number" value={draft.raysPerPoint} onChange={event => setDraftField('raysPerPoint', Number(event.target.value))} /></label>
                  <label className="spx-field"><span>CPU threads</span><input aria-label="新工程线程" type="number" value={draft.cpuThreads} onChange={event => setDraftField('cpuThreads', Number(event.target.value))} /></label>
                </div>
              </section>
            </div>
            <div className="spx-sheet-actions">
              <button type="button" className="wf-button primary" onClick={createDraftProject}
                title={draft.source === 'layout' && !activeClip ? '先上传 GDS 并保存 Clip' : '建立工程草稿；保存到服务端在工程页完成'}>创建工程草稿</button>
              <button type="button" className="wf-button" onClick={() => setSetupMode('home')}>取消</button>
            </div>
            <p className="spx-form-note">草稿只存在于当前页面；到工程页点击“保存工程”才会写入服务端。步骤在 02 中从库插入或手动添加。</p>
          </div>}

          {setupMode === 'layout' && <div className="spx-sheet">
            <div className="spx-sheet-title">版图 / Clip</div>
            <div className="spx-sheet-actions">
              {project && <button type="button" className="wf-button" onClick={() => setSetupMode('project')}>返回工程定义</button>}
              <span className="spx-help" style={{ margin: 0 }}>版图/Clip 只影响几何来源，不改变工程步骤。</span>
            </div>
            <div className="spx-inline-section">
              <div className="spx-form-title">Layouts</div>
              {layouts.length === 0
                ? <div className="wf-empty spx-library-empty">还没有 Layout。用下方“上传 GDS”选择文件，再点“注册为 Layout”。</div>
                : <div className="spx-layout-list" aria-label="版图列表">
                  {layouts.map(item => <button key={item.layoutId} type="button"
                    className={activeLayout?.layoutId === item.layoutId ? 'active' : ''}
                    onClick={() => void chooseLayout(item.layoutId)}
                  >
                    <strong>{item.fileName}</strong>
                    <span className="wf-muted">{item.structureCount} 结构</span>
                    <span className="spx-count">{activeLayout?.layoutId === item.layoutId ? '当前' : '查看'}</span>
                  </button>)}
                </div>}
            </div>
            <div className="spx-inline-section">
              <div className="spx-form-title">Clips</div>
              {clips.length === 0
                ? <div className="wf-empty spx-library-empty">该 Layout 还没有 Clip。选择结构、图层材料与范围后保存 Clip，即可作为 2D 工程几何。</div>
                : <div className="spx-clip-summary-list" aria-label="Clip 列表">
                  {clips.map(clip => <button key={clip.clipId} type="button"
                    className={activeClip?.clipId === clip.clipId ? 'active' : ''}
                    onClick={() => selectClip(clip)}
                  >
                    <strong>{clipLabel(clip)}</strong>
                    <span>{clip.layers.length} 层 · {clip.polygonCount} 多边形</span>
                    <span>x {clip.bbox.x0}…{clip.bbox.x1} · y {clip.bbox.y0}…{clip.bbox.y1} µm</span>
                  </button>)}
                </div>}
            </div>
            <div className="spx-inline-section">{renderClipEditor(project ? 'project' : 'draft', false)}</div>
            <p className="spx-form-note">版图/Clip 只作为几何来源，不会覆盖当前工程；需要时在工程页把 Clip 设为几何。</p>
          </div>}

          {setupMode === 'examples' && <div className="spx-samples">
            {[
              { label: '2D 平面刻蚀', sample: SAMPLE_2D_PLANE, meta: '2D · plane · 1 step' },
              { label: '2D Trench', sample: SAMPLE_2D_TRENCH, meta: '2D · trench · 1 step' },
              { label: '3D 孔刻蚀', sample: SAMPLE_3D_HOLE, meta: '3D · hole · 1 step' },
            ].map(item => <button type="button" className="spx-sample" key={item.label} onClick={() => loadSample(item.sample, item.label)}>
              <strong>{item.label}</strong>
              <span>{item.meta}</span>
              <em>载入示例</em>
            </button>)}
          </div>}

          {setupMode === 'project' && project && <div className="spx-sheet">
            <div className="spx-sheet-title">工程定义 / {project.name ?? '未命名工程'}</div>
            <div className="spx-project-status" role="status">
              <strong className={projectId && !projectDirty ? 'ok' : ''}>{projectId ? (projectDirty ? '有未保存更改' : '已保存') : '草稿（未保存）'}</strong>
              <span>{projectId ? `工程 ${projectId}` : '尚未写入服务端'}</span>
              {projectSavedAt && <span>更新于 {formatDateTime(projectSavedAt)}</span>}
              {projectNotice && <span className="spx-project-notice">{projectNotice}</span>}
            </div>
            <div className="spx-summary-grid">
              <div><span>维度</span><strong>{project.dimension ?? '—'}D</strong></div>
              <div><span>几何来源</span><strong>{projectGeometrySource.kind === 'layout' ? `Clip · ${projectGeometrySource.clipName ?? '未选择'}` : '参数化'}</strong></div>
              <div><span>步骤</span><strong>{project.steps?.length ?? 0}</strong></div>
              <div><span>求解器</span><strong>{ENGINE_LABELS[String((project.solver as Record<string, unknown> | undefined)?.enginePreference ?? '')] ?? '—'}</strong></div>
            </div>
            <div className="spx-sheet-actions">
              <button type="button" className="wf-button primary" disabled={!!busy} onClick={() => void saveProject()}>
                {busy === 'project-save' ? '保存中…' : projectId ? '保存更改' : '保存工程'}
              </button>
              <button type="button" className="wf-button" disabled={!!busy} onClick={exportProjectJson}>导出工程 JSON</button>
              <button type="button" className="wf-button" onClick={() => setZone('flow')}>去 02 定义流程</button>
              <button type="button" className="wf-button" onClick={closeProject}>关闭工程</button>
            </div>
            {overwriteArmed && <div className="spx-rebuild" role="alert">
              <span>已存在同名工程「{overwriteArmed.name}」。覆盖已有工程？会替换它的步骤（当前编辑 {project.steps?.length ?? 0} 步）。</span>
              <button type="button" className="wf-button" onClick={() => { setOverwriteArmed(null); setProjectNotice('已取消覆盖，当前编辑保留。'); }}>取消</button>
              <button type="button" className="wf-button primary" disabled={!!busy} onClick={() => void overwriteProject()}>确认覆盖</button>
            </div>}
            {closeArmed && <div className="spx-rebuild" role="alert">
              <span>工程「{project.name ?? '未命名'}」有未保存更改，关闭会丢弃这些修改。</span>
              <button type="button" className="wf-button" onClick={() => setCloseArmed(false)}>取消</button>
              <button type="button" className="wf-button primary" onClick={resetToStart}>确认关闭</button>
            </div>}
            {compile && <div className={compileStale ? 'wf-summary' : 'wf-summary success'}>
              <strong>{compileStale ? '流程已修改，需要重新编译' : '流程已编译'}</strong>
              <span>{compile.stepCount} 步 · Compile {compile.compileId.slice(0, 16)}… · 绑定 {compile.bindingId.slice(0, 16)}…</span>
            </div>}
            <div className="spx-inline-section">
              <div className="spx-form-title">材料</div>
              {(project.geometry as Record<string, unknown> | undefined)?.kind === 'gds' && <p className="spx-help">GDS 工程的基底材料由图层→材料映射决定，请在下方 Clip 编辑器设置。</p>}
              <div className="spx-field-grid" hidden={(project.geometry as Record<string, unknown> | undefined)?.kind === 'gds'}>
                <label className="spx-field"><span>基底材料 substrateMaterial</span>
                  <select aria-label="工程基底材料" value={String((project.geometry as Record<string, unknown> | undefined)?.substrateMaterial ?? 'Si')}
                    onChange={event => updateProjectMaterial('substrateMaterial', event.target.value)}>
                    {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                  </select>
                </label>
                <label className="spx-field"><span>掩膜材料 maskMaterial</span>
                  <select aria-label="工程掩膜材料" value={String((project.geometry as Record<string, unknown> | undefined)?.maskMaterial ?? 'Mask')}
                    onChange={event => updateProjectMaterial('maskMaterial', event.target.value)}>
                    {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                  </select>
                </label>
              </div>
              <p className="spx-help">材料来源 /api/materials，直接写入 ProjectV7 的 geometry 字段。</p>
            </div>
            <div className="spx-inline-section">
              <div className="spx-form-title">几何来源</div>
              <div className="spx-segment spx-source-pick">
                <button type="button" className={projectGeometrySource.kind === 'parametric' ? 'active' : ''}
                  onClick={() => { setProjectGeometrySource({ kind: 'parametric' }); setCompileStale(true); }}>参数化</button>
                <button type="button" className={projectGeometrySource.kind === 'layout' ? 'active' : ''} disabled={project.dimension === 3}
                  title={project.dimension === 3 ? 'GDS 版图几何仅支持 2D 工程' : '使用 GDS 版图 Clip 作为工程几何'}
                  onClick={() => { setProjectGeometrySource({ kind: 'layout', clipId: activeClip?.clipId, clipName: activeClip?.name }); setCompileStale(true); }}>{project.dimension === 3 ? '🔒 ' : ''}GDS / Layout</button>
              </div>
              {project.dimension === 3 && <p className="spx-lock-note">3D 工程不支持 GDS 版图几何，图层材料映射仅用于 2D。</p>}
              {projectGeometrySource.kind === 'parametric'
                ? <div className="wf-muted">几何类型：{String((project.geometry as Record<string, unknown> | undefined)?.kind ?? '—')}</div>
                : renderClipEditor('project')}
            </div>
          </div>}

          <input ref={gdsInput} type="file" accept=".gds,.gdsii" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void uploadGds(file); }} />
          <input ref={projectInput} type="file" accept=".json" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void importProject(file); }} />
        </section>

        <section className={`wf-card wf-flow ${zone === 'flow' ? '' : 'wf-hidden'}`}>
          <div className="wf-card-head"><span>02</span><h2>流程编辑</h2></div>
          {!project && !libraryManagerOpen && <div className="wf-empty">先在 01 创建或导入工程；库管理不需要打开工程。</div>}
          {(project || libraryManagerOpen) && renderFlowBar()}
          {libraryManagerOpen && renderLibraryManager()}
          {project && !libraryManagerOpen && <>
            <div className="spx-flow-summary">
              <div><span>工程</span><strong>{project.name ?? '未命名'}</strong></div>
              <div><span>维度</span><strong>{project.dimension ?? '?'}D</strong></div>
              <div><span>几何</span><strong>{projectGeometrySource.kind === 'layout' ? `Clip · ${projectGeometrySource.clipName ?? '—'}` : '参数化'}</strong></div>
              <div><span>步骤</span><strong>{authoredSteps.length}</strong></div>
            </div>

            {compile && <div className={compileStale ? 'wf-summary' : 'wf-summary success'}>
              <strong>{compileStale ? 'Compile 已过期，需要重新编译' : '编译成功'}</strong>
              <span>{compile.stepCount} 步 · {compile.dimension}D · 工程 {compile.projectName}</span>
              {compileStale && <span>重新编译后才能创建新的 Route。</span>}
              <details className="spx-compile-details">
                <summary>查看编译详情</summary>
                <dl>
                  <dt>Compile ID</dt><dd>{compile.compileId}</dd>
                  <dt>绑定 (binding)</dt><dd>{compile.bindingId}</dd>
                  <dt>步骤数</dt><dd>{compile.stepCount}</dd>
                  <dt>Flow</dt><dd>{compile.flowId}</dd>
                </dl>
              </details>
            </div>}

            {!libraryManagerOpen && <>

            {rebuildRouteArmed && <div className="spx-rebuild" role="alert">
              <span>重建 Route 会丢弃当前 Run 与测量结果（现有 {route?.segments.length ?? 0} Segment）。</span>
              <button type="button" className="wf-button" onClick={() => setRebuildRouteArmed(false)}>取消</button>
              <button type="button" className="wf-button primary" disabled={!!busy} onClick={() => void createRoute(true)}>确认重建</button>
            </div>}
            <div className="spx-flow-editor">
              <div className="spx-library-browser">
                <div className="spx-library-head">
                  <span>库</span>
                  <button type="button" className="spx-link" onClick={() => openLibraryManager(libraryCategory)}>管理库</button>
                </div>
                <div className="spx-library-tabs">
                  {([
                    ['material', 'Materials'],
                    ['process', 'Processes'],
                    ['flow', 'Flows'],
                    ['experiment', 'Experiments'],
                  ] as Array<[LibraryCategory, string]>).map(([category, label]) => <button
                    key={category} type="button" className={libraryCategory === category ? 'active' : ''}
                    onClick={() => setLibraryCategory(category)}
                  >{label}<span className="spx-count">{libraryItems.filter(item => item.category === category).length}</span></button>)}
                </div>
                <div className="spx-library-category-head compact">
                  <strong>{LIBRARY_CATEGORY_LABELS[libraryCategory]}</strong>
                  <span className={libraryError ? 'spx-library-load-error' : 'wf-muted'}>
                    {libraryLoading ? '加载中…' : libraryError ? '加载失败' : `${libraryItems.filter(item => item.category === libraryCategory).length} 条`}
                  </span>
                  {libraryError && <button type="button" className="wf-button ghost" onClick={() => void loadLibraryItems()}>重试</button>}
                </div>
                <div className="spx-library-list" aria-label="库条目">
                  {libraryLoading && <div className="wf-muted spx-library-empty">加载中…</div>}
                  {!libraryLoading && libraryError && <div className="wf-muted spx-library-empty">加载失败：{libraryError}<button type="button" className="wf-button ghost" onClick={() => void loadLibraryItems()}>重试</button></div>}
                  {!libraryLoading && !libraryError && libraryItems.filter(item => item.category === libraryCategory).length === 0 && <div className="wf-muted spx-library-empty">
                    {libraryCategory === 'flow'
                      ? '还没有流程条目。先在流程编辑器保存当前步骤为模板，再到库管理引用它。'
                      : libraryCategory === 'experiment'
                        ? '还没有实验模板。先在 03 完成一次测量生成配方，再到库管理引用它。'
                        : '该分类暂无条目，可从“管理库”新增。'}
                  </div>}
                  {!libraryLoading && !libraryError && libraryItems.filter(item => item.category === libraryCategory).map(item => {
                    const needsProject = item.category !== 'experiment';
                    const blocked = needsProject && !project;
                    const meta = item.category === 'process'
                      ? `${item.payload.operation === 'deposit' ? '沉积' : '刻蚀'} · ${STEP_LABELS[String(item.payload.model ?? '')] ?? String(item.payload.model ?? '')} · ${String(item.payload.rateMagnitude ?? '—')} µm/s · ${String(item.payload.duration ?? '—')} s`
                      : item.category === 'material'
                        ? `${String(item.payload.catalog ?? '')} · ${String(item.payload.material ?? '')} · ${String(item.payload.role ?? '')}`
                        : item.category === 'flow' ? '流程引用' : '实验模板';
                    const swatch = item.category === 'material' && isHexColor(String(item.payload.color ?? '')) ? String(item.payload.color) : '';
                    return <div
                      key={item.itemId}
                      className="spx-library-item"
                      draggable={!blocked}
                      onDragStart={event => { event.dataTransfer.setData('text/plain', item.itemId); event.dataTransfer.effectAllowed = 'copy'; }}
                    >
                      <div className="spx-library-item-main">
                        <strong>{swatch && <i className="spx-library-swatch" style={{ background: swatch }} aria-hidden="true" />}{item.name}{libraryDuplicateNames.has(item.name) ? ` #${item.itemId.slice(-4)}` : ''}</strong>
                        <span>{meta}</span>
                      </div>
                      {item.category === 'experiment'
                        ? <button type="button" className="spx-link" onClick={() => { setZone('experiment'); setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  实验模板 ${item.name} 在 04 中使用`]); }}>去 04</button>
                        : <button type="button" className="spx-link" disabled={blocked}
                          title={blocked ? '先在 01 创建或导入工程' : item.category === 'material' ? '插入到当前沉积步骤或工程基底材料' : '插入到流程'}
                          onClick={() => {
                            if (item.category === 'flow') setConfirmLibraryItemId(confirmLibraryItemId === item.itemId ? '' : item.itemId);
                            else void insertLibraryItem(item);
                          }}>插入</button>}
                      {confirmLibraryItemId === item.itemId && <div className="spx-library-confirm">
                        <span>插入方式</span>
                        <button type="button" className="wf-button ghost" disabled={!!busy} onClick={() => void insertLibraryItem(item, 'append')}>追加</button>
                        <button type="button" className="wf-button ghost" disabled={!!busy} onClick={() => void insertLibraryItem(item, 'replace')}>替换</button>
                        <button type="button" className="spx-link" onClick={() => setConfirmLibraryItemId('')}>取消</button>
                      </div>}
                    </div>;
                  })}
                </div>
                {libraryCategory === 'material' && !project && <p className="spx-material-blocked">插入材料需要先创建工程（01）；当前只能查看预设。</p>}
                {(libraryCategory === 'process' || libraryCategory === 'flow') && !project && <p className="spx-material-blocked">插入流程内容需要先创建工程（01）。</p>}
                {materialTarget && <div className="spx-material-badge" role="status">
                  <i style={{ background: materialColor(materialCatalog, materialTarget.material) }} aria-hidden="true" />
                  <span>当前材料 <code>{materialTarget.material}</code>（预设 {materialTarget.itemName}）→ {materialTarget.target}{materialTarget.stepName ? ` · ${materialTarget.stepName}` : ''}</span>
                </div>}
              </div>

              <div className="spx-flow-steps"
                aria-label="流程步骤编辑区"
                onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
                onDrop={event => {
                  event.preventDefault();
                  const itemId = event.dataTransfer.getData('text/plain');
                  const item = libraryItems.find(row => row.itemId === itemId);
                  if (item) void insertLibraryItem(item);
                }}
              >
                <div className="spx-flow-steps-head">
                  <span>流程步骤</span>
                  <button type="button" className="wf-button ghost" onClick={addAuthoredStep}>＋ 添加步骤</button>
                </div>
                {authoredSteps.length === 0
                  ? <div className="wf-empty">把左侧 Process 拖到这里，或点击“插入”。</div>
                  : <table className="wf-table" aria-label="流程步骤">
                    <thead><tr><th>#</th><th>步骤</th><th>模型</th><th>操作</th><th>材料</th><th>启用</th></tr></thead>
                    <tbody>{authoredSteps.map((step, index) => (
                      <tr key={step.id}
                        className={`${selectedFlowStepId === step.id ? 'selected' : ''}${materialTarget && step.depositMaterial === materialTarget.material ? ' material-match' : ''}`}
                        onClick={() => setSelectedFlowStepId(step.id)}>
                        <td>{String(index + 1).padStart(2, '0')}</td>
                        <td>{step.name}</td>
                        <td>{STEP_LABELS[step.model] ?? step.model}</td>
                        <td>{step.operation === 'deposit' ? '沉积' : step.operation === 'etch' ? '刻蚀' : step.operation ?? '—'}</td>
                        <td>{step.depositMaterial ?? '—'}</td>
                        <td>{step.enabled ? '是' : '否'}</td>
                      </tr>
                    ))}</tbody>
                  </table>}
                <div className="spx-step-inspector">
                  <div className="spx-step-inspector-title">步骤参数</div>
                  {selectedFlowStep ? <div className="spx-step-form">
                    <label className="spx-field"><span>名称</span><input aria-label="步骤名称" value={selectedFlowStep.name} onChange={event => updateSelectedStep({ name: event.target.value })} /></label>
                    <label className="spx-field"><span>启用</span><input aria-label="步骤启用" type="checkbox" checked={selectedFlowStep.enabled} onChange={event => updateSelectedStep({ enabled: event.target.checked })} /></label>
                    <label className="spx-field"><span>操作</span>
                      <select aria-label="步骤操作" value={selectedFlowStep.operation ?? 'etch'} onChange={event => updateSelectedStep({ operation: event.target.value })}>
                        <option value="etch">刻蚀 (etch)</option>
                        <option value="deposit">沉积 (deposit)</option>
                      </select>
                    </label>
                    <label className="spx-field"><span>模型</span>
                      <select aria-label="步骤模型" value={selectedFlowStep.model} onChange={event => updateSelectedStep({ model: event.target.value })}>
                        {[selectedFlowStep.model, 'isotropic', 'singleParticle', 'directional', 'multiParticle', 'removeMaterial', 'planarize']
                          .filter((value, index, list) => list.indexOf(value) === index)
                          .map(model => <option key={model} value={model}>{STEP_LABELS[model] ?? model}</option>)}
                      </select>
                    </label>
                    <label className="spx-field"><span>duration (s)</span><input aria-label="步骤时长" type="number" step="0.01" value={Number(selectedFlowStep.duration ?? 0)} onChange={event => updateSelectedStep({ duration: Number(event.target.value) })} /></label>
                    <label className="spx-field"><span>rate (µm/s)</span><input aria-label="步骤速率" type="number" step="0.01" value={Number(selectedFlowStep.rateMagnitude ?? selectedFlowStep.directionalRateMagnitude ?? 0)} onChange={event => updateSelectedStep({ rateMagnitude: Number(event.target.value) })} /></label>
                    <label className="spx-field"><span>沉积材料</span>
                      <select aria-label="步骤沉积材料" value={String(selectedFlowStep.depositMaterial ?? '')} onChange={event => updateSelectedStep({ depositMaterial: event.target.value || undefined })}>
                        <option value="">—</option>
                        {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name}{materialPreset === row.name ? ' · 库' : ''}{materialTarget?.material === row.name ? ' · 当前材料' : ''}</option>)}
                      </select>
                    </label>
                    {(selectedFlowStep.model === 'singleParticle') && <>
                      <label className="spx-field"><span>stickingProbability</span><input aria-label="步骤 stickingProbability" type="number" step="0.01" value={Number(selectedFlowStep.stickingProbability ?? 1)} onChange={event => updateSelectedStep({ stickingProbability: Number(event.target.value) })} /></label>
                      <label className="spx-field"><span>sourceExponent</span><input aria-label="步骤 sourceExponent" type="number" step="0.1" value={Number(selectedFlowStep.sourceExponent ?? 1)} onChange={event => updateSelectedStep({ sourceExponent: Number(event.target.value) })} /></label>
                    </>}
                    <div className="spx-step-form-actions">
                      <code>{selectedFlowStep.id}</code>
                      <button type="button" className="wf-button ghost" onClick={() => deleteAuthoredStep(selectedFlowStep.id)}>删除步骤</button>
                    </div>
                  </div> : <p className="spx-help">选择左侧步骤查看并编辑参数。</p>}
                </div>
              </div>
            </div>

            <details className="spx-advanced">
              <summary>模板（可选）</summary>
              <div className="spx-advanced-body">
                <p className="spx-help">模板作用于当前流程：“插入模板”把模板步骤追加到末尾；“替换流程”丢弃现有步骤后写入模板。</p>
                <div className="spx-flow-template">
                  <label><span>模板</span>
                    <select aria-label="模板选择" value={templateId} onChange={event => setTemplateId(event.target.value)}>
                      <option value="">{templates.length ? '选择模板' : '尚无模板'}</option>
                      {templates.map(item => <option key={item.templateId} value={item.templateId}>{item.name} · {item.stepCount} 步</option>)}
                    </select>
                  </label>
                  <label><span>覆盖变量 duration（s）</span><input aria-label="模板变量值" value={templateVariable} onChange={event => setTemplateVariable(event.target.value)} /></label>
                  <button className="wf-button" aria-label="保存当前步骤为模板" disabled={!project || !authoredSteps.length || !!busy} onClick={() => void saveTemplate()}>保存为模板</button>
                  <button className="wf-button" disabled={!project || !templateId || !!busy} onClick={() => setReplaceTemplateArmed(true)}>替换流程</button>
                  <button className="wf-button" disabled={!project || !templateId || !!busy} onClick={() => void applyTemplate('append')}>插入模板</button>
                  <button className="wf-button" disabled={!compile || !previousCompile || !!busy} onClick={() => void compareCompile()}>比较上一版</button>
                </div>
                <p className="spx-help">“覆盖变量 duration（s）”会替换所选模板里的 <code>{'${duration}'}</code>，单位秒，仅作用于该模板。</p>
                {templates.length === 0 && <p className="spx-material-blocked">尚无模板：先点击“保存为模板”把当前步骤写成模板。</p>}
                {replaceTemplateArmed && <div className="spx-rebuild" role="alert">
                  <span>替换流程会丢弃当前 {authoredSteps.length} 个步骤，并写入所选模板的步骤。此操作不可撤销。</span>
                  <button type="button" className="wf-button" onClick={() => setReplaceTemplateArmed(false)}>取消</button>
                  <button type="button" className="wf-button primary" disabled={!!busy} onClick={() => { setReplaceTemplateArmed(false); void applyTemplate('replace'); }}>确认替换</button>
                </div>}
                {compileDiff && <div className="wf-summary"><strong>编译差异</strong><span>+{compileDiff.addedStepIds.length} / -{compileDiff.removedStepIds.length} / 移动 {compileDiff.movedStepIds.length} / 修改 {compileDiff.changedStepIds.length}</span><span>受影响步骤: {compileDiff.affectedStepIds.map(id => id.slice(0, 8)).join(', ') || '—'}</span>{compileDiff.details?.slice(0, 3).map(item => <span key={item.stepId}>{item.stepId.slice(0, 8)}: {String(item.before.duration ?? '—')} → {String(item.after.duration ?? '—')}</span>)}</div>}
              </div>
            </details>

            <details className="spx-advanced" open={flowAdvancedOpen} onToggle={event => setFlowAdvancedOpen(event.currentTarget.open)}>
              <summary>高级：模块化流程定义（JSON）</summary>
              <div className="spx-advanced-body">
                <div className="wf-card-subhead">模块化流程</div>
                <p className="wf-muted">保存模块与流程定义后展开预览；编译复用现有 Compile 与 SourceMap，不另起执行路径。</p>
                <textarea aria-label="模块 JSON" value={moduleJson} onChange={event => setModuleJson(event.target.value)} placeholder="模块 JSON" />
                <div className="wf-template-row">
                  <button className="wf-button ghost" disabled={!!busy} onClick={() => void saveModule()}>{busy === 'module' ? '保存中…' : '保存模块'}</button>
                  <span className="wf-muted">moduleId 唯一；内容变更会递增模块修订</span>
                </div>
                <textarea aria-label="流程定义 JSON" value={definitionJson} onChange={event => setDefinitionJson(event.target.value)} placeholder="流程定义 JSON" />
                <div className="wf-template-row">
                  <input aria-label="流程变量 JSON" value={definitionVariables} onChange={event => setDefinitionVariables(event.target.value)} placeholder='{"rate": 0.2}' />
                  <button className="wf-button ghost" disabled={!!busy} onClick={() => void saveDefinition()}>{busy === 'definition' ? '保存中…' : '保存定义'}</button>
                  <button className="wf-button ghost" disabled={!activeDefinitionId || !!busy} onClick={() => void expandDefinition()}>{busy === 'expand' ? '展开中…' : '展开预览'}</button>
                  <button className="wf-button primary" disabled={!activeDefinitionId || !project || !!busy} onClick={() => void compileDefinition()}>{busy === 'definition-compile' ? '编译中…' : '编译模块化流程'}</button>
                </div>
                {moduleStatus && <div className="wf-summary"><strong>{moduleStatus}</strong><span>definition {activeDefinitionId || '—'}</span></div>}
                {expansion && <>
                  <div className="wf-summary">
                    <strong>展开预览</strong>
                    <span>{expansion.stepCount} 步 · 深度 {expansion.maxDepth}</span>
                    <span>{expansion.moduleInstances.length} 模块实例 · {expansion.iterations.length} 循环迭代</span>
                  </div>
                  {expansion.variables.length > 0 && <div className="wf-muted">变量: {expansion.variables.map(item => `${item.name}=${String(item.value)}${item.unit ? ` ${item.unit}` : ''} (${item.source})`).join(' · ')}</div>}
                  {expansion.iterations.length > 0 && <div className="wf-muted">循环: {expansion.iterations.map(item => `${item.path} · ${item.variable}[${item.index}]=${String(item.value)}`).join(' · ')}</div>}
                  {expansion.moduleInstances.length > 0 && <div className="wf-muted">模块实例: {expansion.moduleInstances.map(item => `${item.instancePath} → ${item.moduleId}`).join(' · ')}</div>}
                  <table className="wf-table">
                    <thead><tr><th>步骤 ID</th><th>模块 / 路径</th><th>参数</th></tr></thead>
                    <tbody>
                      {expansion.steps.map(step => <tr key={step.authoredStepId}>
                        <td><code>{step.authoredStepId}</code></td>
                        <td><code>{step.moduleId ? `${step.moduleId} @ ${step.moduleInstanceIdPath}` : step.flowPath}</code></td>
                        <td>{Object.entries(step.parameterProvenance).map(([key, item]) => `${key}=${JSON.stringify(item.resolvedValue)}`).join(' · ')}</td>
                      </tr>)}
                    </tbody>
                  </table>
                </>}
              </div>
            </details>

            <details className="spx-advanced" open={libraryAdvancedOpen} onToggle={event => setLibraryAdvancedOpen(event.currentTarget.open)}>
              <summary>高级：共享流程库</summary>
              <div className="spx-advanced-body">
                <p className="wf-muted">导出全部模块、流程定义、模板与当前实验的可复用条件；导入时明确 reject/clone 冲突策略。实验只保留条件、重复数与 seed，不带运行状态。</p>
                <div className="wf-template-row">
                  <input aria-label="共享库名称" value={libraryName} onChange={event => setLibraryName(event.target.value)} placeholder="共享库名称" />
                  <input aria-label="共享库描述" value={libraryDescription} onChange={event => setLibraryDescription(event.target.value)} placeholder="描述（可选）" />
                  <button className="wf-button ghost" aria-label="导出共享库" disabled={!!busy} onClick={() => void exportLibrary()}>{busy === 'library' ? '处理中…' : '导出共享库'}</button>
                </div>
                <div className="wf-template-row">
                  <select aria-label="共享库 bundle 选择" value={libraryBundleId} onChange={event => setLibraryBundleId(event.target.value)}>
                    <option value="">选择 bundle</option>
                    {libraryBundles.map(item => <option key={item.bundleId} value={item.bundleId}>{item.name} · {item.bundleId.slice(0, 12)}…</option>)}
                  </select>
                  <select aria-label="共享库导入策略" value={libraryPolicy} onChange={event => setLibraryPolicy(event.target.value as 'reject' | 'clone')}>
                    <option value="reject">reject · 冲突即失败</option>
                    <option value="clone">clone · 生成新 ID</option>
                  </select>
                  <button className="wf-button primary" aria-label="导入共享库" disabled={!libraryBundleId || !!busy} onClick={() => void importLibraryBundle()}>{busy === 'library' ? '处理中…' : '导入共享库'}</button>
                </div>
                {libraryStatus && <div className="wf-summary" role="status"><strong>{libraryStatus}</strong><span>{libraryBundles.length} 个本地 bundle</span></div>}
                {libraryBundles.length > 0 && <table className="wf-table" aria-label="共享库列表">
                  <thead><tr><th>共享库</th><th>内容</th><th>创建时间</th></tr></thead>
                  <tbody>
                    {libraryBundles.slice(0, 8).map(item => <tr key={item.bundleId}>
                      <td><code>{item.bundleId}</code><div className="wf-muted">{item.name}</div></td>
                      <td>{item.moduleCount} 模块修订 · {item.definitionCount} 定义 · {item.templateCount} 模板 · {item.experimentTemplateCount} 实验模板</td>
                      <td>{formatDateTime(item.createdAt)}</td>
                    </tr>)}
                  </tbody>
                </table>}
                <details className="spx-advanced">
                  <summary>高级：粘贴 bundle JSON</summary>
                  <div className="spx-advanced-body">
                    <textarea aria-label="共享库 JSON" value={libraryBundleJson} onChange={event => setLibraryBundleJson(event.target.value)} placeholder="粘贴 bundle JSON 以便跨机器交换" />
                    <div className="wf-template-row">
                      <button className="wf-button ghost" aria-label="从 JSON 导入共享库" disabled={!libraryBundleJson.trim() || !!busy} onClick={() => void importLibraryJson()}>{busy === 'library' ? '处理中…' : '从 JSON 导入'}</button>
                      <span className="wf-muted">bundleId 由内容 hash 生成；reject 保留原 ID，clone 为定义/模板/实验生成新 ID。</span>
                    </div>
                  </div>
                </details>
                {libraryResult && <div className="wf-summary" aria-label="共享库导入结果">
                  <strong>{libraryResult.bundleId}</strong>
                  <span>定义: {libraryResult.definitionIds.join(', ') || '—'}</span>
                  <span>模板: {libraryResult.templateIds.join(', ') || '—'}</span>
                  <span>实验: {libraryResult.experimentIds.join(', ') || '—'}</span>
                  {libraryResult.renames.length > 0 && <span>新 ID: {libraryResult.renames.map(item => `${item.previousId} → ${item.importedId}`).join(' · ')}</span>}
                  {libraryResult.skipped.length > 0 && <span>跳过: {libraryResult.skipped.join(' · ')}</span>}
                  {libraryResult.conflicts.length > 0 && <span>冲突: {libraryResult.conflicts.join(' · ')}</span>}
                </div>}
              </div>
            </details>
            </>}
          </>}
        </section>

        <section className={`wf-card wf-run ${zone === 'run' ? '' : 'wf-hidden'}`}>
          <div className="wf-card-head"><span>03</span><h2>运行与测量</h2></div>
          {!route && <div className="wf-empty">在 02 编译流程并创建 Route 后，这里执行 Segment 与测量。</div>}
          {route && <div className="wf-summary success">
            <strong>Route 已创建</strong>
            <span>{route.route.name} · {route.segments.length} Segment · {route.stepOrder.length} 步</span>
            {routeStale && <span className="wf-rerun">流程已变更，现有 Segment 待重跑</span>}
          </div>}

          <div className="wf-structure-panel" aria-label="当前 Run 结构">
            <div className="wf-structure-head">
              <strong>结构</strong>
              {runStructureSteps.length > 0
                ? <select aria-label="结构步骤选择" value={structurePick} onChange={event => setStructurePick(event.target.value)}>
                  {runStructureSteps.map(row => <option key={`${row.runId}:${row.stepId}`} value={`${row.runId}:${row.stepId}`}>
                    {stepDisplayName(row.stepId)}
                  </option>)}
                </select>
                : <span className="wf-muted">尚未执行</span>}
              {structureSelection && <span className="wf-structure-step">当前步骤：{stepDisplayName(structureSelection.stepId)}</span>}
              {structureView && <span className="wf-muted">{structureView.dimension}D · {structureView.coordinateUnit === 'um' || !structureView.coordinateUnit ? 'µm' : structureView.coordinateUnit}</span>}
            </div>
            {structureView
              ? <div className="wf-structure-body">
                <StructureCanvas
                  view={structureView}
                  frame={structureFrame(structureView, structureAxis)}
                  axis={structureAxis}
                  marker={measurement ? measurementMark : null}
                  label="当前 Run 结构视图"
                />
                <div className="wf-structure-side">
                  <label className="spx-field"><span>视图轴</span>
                    <select aria-label="结构视图轴" value={structureAxis} onChange={event => setStructureAxis(event.target.value as StructureAxis)}>
                      <option value="xy">XY</option>
                      <option value="xz">XZ</option>
                      <option value="yz">YZ</option>
                    </select>
                  </label>
                  <div className="wf-structure-stats">
                    <span>拓扑</span><code>{structureView.topology}</code>
                    <span>点</span><code>{structureView.pointCount}</code>
                    <span>线</span><code>{structureView.lineCount}</code>
                    <span>三角形</span><code>{structureView.triangleCount}</code>
                    <span>单位</span><code>µm</code>
                    <span>X 范围</span><code>{`${Number(structureView.bounds[0]).toFixed(3)} … ${Number(structureView.bounds[1]).toFixed(3)}`}</code>
                    <span>Y 范围</span><code>{`${Number(structureView.bounds[2]).toFixed(3)} … ${Number(structureView.bounds[3]).toFixed(3)}`}</code>
                  </div>
                  <div className="wf-structure-legend">
                    {structureLegend(structureView).map(row => <div key={row.id}>
                      <i style={{ background: row.color }} aria-hidden="true" />
                      <span style={{ marginLeft: 0, color: '#26313a' }}>{row.name}</span>
                      <span>{row.count}</span>
                    </div>)}
                  </div>
                  {measurement && measurementMark && <div className="wf-muted">测量标记：{measurementMark.label}</div>}
                </div>
              </div>
              : <div className="wf-structure-empty">
                <div className="wf-empty">
                  {structureLoading
                    ? '结构快照加载中…'
                    : structureError
                      ? `暂无结构快照：${structureError}`
                      : segment?.status === 'running'
                        ? 'Segment 运行中，完成后显示结构。'
                        : '执行当前 Segment 后，这里显示该 Run 的结构视图（2D/3D）。'}
                </div>
                {canRun && <div className="spx-run-actions">
                  <button className="wf-button primary" onClick={() => void execute()}>{busy === 'execute' ? '启动中…' : '启动 Run 并生成结构'}</button>
                </div>}
              </div>}
          </div>

          {segment && <>
            <div className="wf-status-row">
              <span className={`wf-status ${segment.status}`}>{statusLabel(segment.status)}</span>
              <code title={segment.runId ?? ''}>{segment.runId ? `Run ${segment.runId.slice(0, 14)}…` : '尚未运行'}</code>
            </div>
            {route && <div className="wf-route-tree">
              {route.attempts.map((attempt, attemptIndex) => {
                const attemptSegments = route.segments.filter(item => item.attemptId === attempt.attemptId);
                const attemptStatus = attemptSegments.some(item => item.status === 'failed')
                  ? 'failed'
                  : attemptSegments.some(item => item.status === 'running')
                    ? 'running'
                    : attemptSegments.length > 0 && attemptSegments.every(item => item.status === 'succeeded')
                      ? 'succeeded'
                      : attempt.status;
                return <div key={attempt.attemptId} className="wf-attempt">
                <div className="wf-attempt-title"><strong>尝试 {attemptIndex + 1}/{route.attempts.length}</strong><span>{statusLabel(attemptStatus)}</span></div>
                {route.reuse.filter(item => item.attemptId === attempt.attemptId).map(item => <div key={item.reuseId} className="wf-segment-row"><span className="wf-status verified">复用</span><code>{stepDisplayName(item.fromStepId)} → {stepDisplayName(item.toStepId)}</code></div>)}
                {route.segments.filter(item => item.attemptId === attempt.attemptId).map(item => <div key={item.segmentId} className="wf-segment-row">
                  <span className={`wf-status ${item.status}`}>{statusLabel(item.status)}</span>
                  <code>{item.startAfterStepId ? `自 ${stepDisplayName(item.startAfterStepId)} 之后` : '从首步'} → {stepDisplayName(item.throughStepId)}</code>
                  {(item.status === 'planned' && routeStale) || (item.status === 'succeeded' && routeStale) ? <span className="wf-rerun">待重跑</span> : null}
                </div>)}
              </div>;})}
            </div>}
            <div className="spx-run-actions">
              <button className="wf-button primary" disabled={!canRun} onClick={() => void execute()}>
                {busy === 'execute' ? '启动中…' : '执行当前 Segment'}
              </button>
              <button className="wf-button" disabled={!route || !!busy} onClick={() => void executeAll()}>执行全部 Segment</button>
              <button className="wf-button" disabled={!canMeasure} onClick={() => void measure()}>
                {busy === 'measure' ? '测量中…' : '测量'}
              </button>
            </div>
            <div className="wf-recipe spx-recipe-inline">
              <div className="wf-recipe-title">测量配方 · 作用于当前 Run 的 {stepDisplayName(segment.throughStepId)}</div>
              <div className="wf-recipe-fields">
                <label><span>测量方法</span>
                  <select aria-label="测量方法" value={recipeMethod} onChange={event => setRecipeMethod(event.target.value as typeof recipeMethod)}>
                    <option value="etch_depth">深度 (etch_depth)</option>
                    <option value="cd_at_height">指定高度 CD (cd_at_height)</option>
                  </select>
                </label>
                <label><span>测量材料</span>
                  <select aria-label="测量材料" value={recipeMaterial} onChange={event => setRecipeMaterial(event.target.value)}>
                    {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                  </select>
                </label>
                {recipeMethod === 'cd_at_height'
                  ? <label><span>CD 高度（µm）</span><input aria-label="CD 高度" value={recipeHeight} onChange={event => setRecipeHeight(event.target.value)} /></label>
                  : <>
                    <label><span>X 位置（µm）</span><input aria-label="深度 X" value={recipeX} onChange={event => setRecipeX(event.target.value)} /></label>
                    <label><span>参考高度（µm）</span><input aria-label="参考高度" value={recipeRef} onChange={event => setRecipeRef(event.target.value)} /></label>
                  </>}
              </div>
              <p className="wf-muted wf-recipe-note">深度：在 X 位置量到参考高度的高度差；指定高度 CD：在给定高度量开口宽度。材料决定交点所属材料。</p>
            </div>
            {segment.status === 'running' && <div className="wf-progress"><i /></div>}
            {measurement && <div className="wf-summary">
              <strong>测量 · {statusLabel(measurement.status)}</strong>
              <span>{measurement.value === null ? measurement.reason ?? '无值' : `${measurement.value.toFixed(4)} ${measurement.unit}`}</span>
              <span>绑定 (binding)：{bindingLabel(measurement.bindingStatus)} · 等价 (equivalence)：{bindingLabel(measurement.equivalenceStatus)}</span>
            </div>}
            {metrics.length > 0 && <table className="wf-table"><thead><tr><th>状态</th><th>数值</th><th>配方 / 步骤</th><th>绑定 / 等价</th><th /></tr></thead><tbody>{metrics.map(item => <tr key={item.measurementId}><td>{statusLabel(item.status)}</td><td>{item.value === null ? item.reason : `${item.value.toFixed(4)} ${item.unit}`}</td><td>{item.recipeId} v{item.recipeVersion} · {item.method} · {item.material} · {stepDisplayName(item.sourceStepId ?? '')}</td><td>{bindingLabel(item.bindingStatus)} / {bindingLabel(item.equivalenceStatus)}</td><td><button className="wf-link" aria-label={`查看 ${item.measurementId} 证据`} onClick={() => void showMetricDetail(item)}>详情</button></td></tr>)}</tbody></table>}
            {metricDetail && <div className="wf-evidence">
              <div className="wf-evidence-head"><strong>{metricDetail.measurement.measurementId}</strong><span className={`wf-status ${metricDetail.measurement.status}`}>{statusLabel(metricDetail.measurement.status)}</span></div>
              <div className="wf-evidence-grid">
                <span>方法</span><code>{metricDetail.recipe.method}</code>
                <span>材料</span><code>{metricDetail.recipe.material}</code>
                <span>配方</span><code>{metricDetail.recipe.recipeId} v{metricDetail.recipe.version}</code>
                <span>参数</span><code>{JSON.stringify(metricDetail.recipe.parameters)}</code>
                <span>快照</span><code>{metricDetail.measurement.sourceSnapshotHash.slice(0, 16)}…</code>
                <span>交点</span><code>{metricDetail.measurement.intersections.join(', ') || '—'}</code>
                <span>不确定度</span><code>{metricDetail.measurement.uncertainty ?? '—'} ({metricDetail.measurement.uncertaintyStatus})</code>
                <span>绑定 (binding)</span><code>{bindingLabel(metricDetail.measurement.bindingStatus)}{metricDetail.measurement.bindingEvidence ? ` · ${metricDetail.measurement.bindingEvidence.methodVersion}` : ''}</code>
                <span>等价 (equivalence)</span><code>{bindingLabel(metricDetail.measurement.equivalenceStatus)}</code>
                <span>证据记录</span><code>{metricDetail.provenance.verifications.map(row => `${row.status}/${row.evidence.methodVersion}`).join(', ') || '—'}</code>
              </div>
            </div>}
            {provenance && <div className="wf-provenance">
              <div className="wf-muted">验证记录: {provenance.verifications.length}</div>
              {provenance.verifications.map(item => <div className="wf-verify" key={item.verificationId}><span className={`wf-status ${item.status}`}>{statusLabel(item.status)}</span><code>{item.verificationId.slice(0, 10)}…</code><span>{item.evidence.methodVersion}</span></div>)}
            </div>}

            <details className="spx-advanced">
              <summary>高级：分支与测量决策</summary>
              <div className="spx-advanced-body">
                <div className="wf-branch-row">
                  <input aria-label="尾步时长" value={branchDuration} onChange={event => setBranchDuration(event.target.value)} />
                  <button className="wf-button ghost" disabled={!route || !!busy} onClick={() => void branchLastStep()}>修改尾步并分支</button>
                </div>
                <div className="wf-decision">
                  <div className="wf-recipe-title">测量驱动分支</div>
                  <select
                    aria-label="决策前缀 Segment"
                    value={decisionPrefixId}
                    onChange={event => setDecisionPrefixId(event.target.value)}
                    disabled={!branchablePrefixes.length}
                  >
                    {!branchablePrefixes.length && <option value="">无可用前缀（先执行保留后续步骤的 Segment）</option>}
                    {branchablePrefixes.map(item => (
                      <option key={item.segmentId} value={item.throughStepId}>
                        {item.segmentId.slice(0, 12)} · through {authoredByCompiled.get(item.throughStepId) ?? item.throughStepId.slice(0, 10)}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label="决策分支止于"
                    value={decisionThroughId}
                    onChange={event => setDecisionThroughId(event.target.value)}
                    disabled={!decisionSuffix.length}
                  >
                    {!decisionSuffix.length && <option value="">无后续步骤</option>}
                    {decisionSuffix.map(stepId => (
                      <option key={stepId} value={stepId}>{authoredByCompiled.get(stepId) ?? stepId.slice(0, 10)}</option>
                    ))}
                  </select>
                  <div className="wf-muted">
                    可用前缀：{branchablePrefixes.length ? branchablePrefixes.map(item => authoredByCompiled.get(item.throughStepId) ?? item.throughStepId.slice(0, 10)).join(' / ') : '无（先执行前缀 Segment）'}
                    {' · '}当前配方：{activeRecipeId ? `${activeRecipeId} v${activeRecipeVersion}` : '未固定（将按上方配方创建）'}
                  </div>
                  <div className="wf-decision-row">
                    <select aria-label="决策比较符" value={decisionOperator} onChange={event => setDecisionOperator(event.target.value as typeof decisionOperator)}>
                      <option value="gt">&gt;</option>
                      <option value="gte">≥</option>
                      <option value="lt">&lt;</option>
                      <option value="lte">≤</option>
                      <option value="eq">=</option>
                    </select>
                    <input aria-label="决策阈值" value={decisionThreshold} onChange={event => setDecisionThreshold(event.target.value)} placeholder="threshold" />
                  </div>
                  <textarea aria-label="true 分支参数 JSON" value={decisionTrueParameters} onChange={event => setDecisionTrueParameters(event.target.value)} placeholder='true 分支：{"step_02":{"duration":0.02}}' />
                  <textarea aria-label="false 分支参数 JSON" value={decisionFalseParameters} onChange={event => setDecisionFalseParameters(event.target.value)} placeholder='false 分支：{"step_02":{"duration":0.05}}' />
                  <button className="wf-button primary" aria-label="测量并生成分支" disabled={!route || !branchablePrefixes.length || !decisionThroughId || !!busy} onClick={() => void decideMeasurementBranch()}>
                    {busy === 'decision' ? '测量并决策中…' : '测量并生成分支'}
                  </button>
                  <div className="wf-muted">决策只改动「分支止于」之前的步骤；止于中间步骤时，可在其运行结束后继续测量决策，形成同一 workflow run 的序列。</div>
                </div>
                {decisions.length > 0 && <div className="wf-muted">
                  工作流 run：{Array.from(new Set(decisions.map(item => item.workflowRunId))).map(runId => {
                    const rows = decisions.filter(item => item.workflowRunId === runId);
                    return `${runId}（${rows.length} 决策 / ${rows.filter(item => item.replayOf).length} 重放）`;
                  }).join('；')}
                </div>}
                {decisions.length > 0 && <table className="wf-table" aria-label="测量决策列表">
                  <thead><tr><th>测量</th><th>条件</th><th>结果</th><th>分支 / 解析参数</th><th>Workflow run</th><th /></tr></thead>
                  <tbody>{decisions.map(item => <tr key={item.decisionId}>
                    <td>{item.measuredValue.toFixed(4)} {item.unit}<br /><code className="wf-mono">{item.measurementId.slice(0, 12)}</code><br /><span className="wf-muted">绑定 {bindingLabel(item.measurementBindingStatus)}</span></td>
                    <td><code className="wf-mono">{item.operator} {item.threshold}</code><br /><span className="wf-muted">{item.recipeId} v{item.recipeVersion}</span></td>
                    <td><span className={`wf-status ${item.result ? 'succeeded' : 'failed'}`}>{item.result ? 'true' : 'false'}</span><br />{item.chosenBranch}</td>
                    <td><code className="wf-mono">{JSON.stringify(item.resolvedParameters)}</code><br /><span className="wf-muted">止于 {authoredByCompiled.get(item.branchThroughStepId ?? '') ?? item.branchThroughStepId?.slice(0, 10) ?? '—'}</span>{item.replayOf && <><br /><span className="wf-muted">replay of {item.replayOf.slice(0, 12)}</span></>}</td>
                    <td><code className="wf-mono">{item.workflowRunId}</code><br /><span className="wf-muted">{item.targetCompileId.slice(0, 12)}</span></td>
                    <td><button className="wf-link" aria-label={`重放决策 ${item.decisionId}`} disabled={!!busy} onClick={() => void replayDecision(item)}>重放决策</button></td>
                  </tr>)}</tbody>
                </table>}
              </div>
            </details>
          </>}
        </section>

        {zone === 'experiment' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>04</span><h2>实验设计</h2></div>
          <p className="wf-muted">保存 10 个可分别设置变体参数的实验，可设重复数与种子，串行执行并使用当前配方测量。</p>
          <button className="wf-button primary" disabled={!compile || !activeRecipeId || !!busy} onClick={() => void createPersistentExperiment()}>创建 10 变体实验</button>
          <button className="wf-button ghost" disabled={!experimentDoc || !!busy} onClick={() => void runPersistentExperiment()}>{busy === 'experiment' ? '实验运行中…' : '运行持久化实验'}</button>
          <table className="wf-table"><thead><tr><th>#</th><th>时长</th><th>重复</th><th>Seed</th></tr></thead><tbody>{experimentRows.map((row, index) => <tr key={index}><td>{index + 1}</td><td><input aria-label={`变体 ${index + 1} duration`} value={row.duration} onChange={event => setExperimentRows(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, duration: event.target.value } : item))} /></td><td><input aria-label={`变体 ${index + 1} replicates`} type="number" min={1} max={8} value={row.replicates} onChange={event => setExperimentRows(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, replicates: Number(event.target.value) || 1 } : item))} /></td><td><input aria-label={`变体 ${index + 1} seed`} type="number" value={row.seed} onChange={event => setExperimentRows(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, seed: Number(event.target.value) || 0 } : item))} /></td></tr>)}</tbody></table>
          {experimentDoc && <div className="wf-muted">实验状态：{statusLabel(experimentDoc.status)}{experimentDoc.kind === 'doe' ? ' · DOE' : ''}</div>}
          {experimentDoc?.queue && <div className="wf-summary"><strong>队列</strong><span>{experimentDoc.queue.succeeded}/{experimentDoc.queue.total} 已成功 · 待处理 {experimentDoc.queue.pending} · 运行中 {experimentDoc.queue.running}</span><span>预计剩余 {experimentDoc.queue.estimatedSecondsRemaining}s</span><span>成本 ${experimentDoc.queue.estimatedCostUsd.toFixed(4)}</span><span>重试 {experimentDoc.queue.retries}</span></div>}
          {experimentDoc && <table className="wf-table"><thead><tr><th>#</th><th>状态</th><th>数值</th><th>Run</th></tr></thead><tbody>{experimentDoc.variants.map(variant => <tr key={variant.ordinal}><td>{variant.ordinal}</td><td>{variant.replicates[0]?.status}</td><td>{variant.replicates[0]?.value ?? '—'} {variant.replicates[0]?.unit ?? ''}</td><td><code>{variant.replicates[0]?.runId?.slice(0, 8) ?? '—'}</code></td></tr>)}</tbody></table>}
          <div className="wf-card-subhead">DOE 全因子</div>
          {(project?.steps ?? []).filter(step => step.enabled).length === 0 && <div className="wf-muted">先在 02 编译流程后可配置因子。</div>}
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
          {experimentTable && <table className="wf-table"><thead><tr><th>#</th><th>n</th><th>均值</th><th>标准差</th><th>CI95</th><th>状态</th></tr></thead><tbody>{experimentTable.rows.map(row => <tr key={row.ordinal}><td>{row.ordinal}</td><td>{row.n}</td><td>{row.mean === null ? '—' : row.mean.toFixed(6)}</td><td>{row.std === null ? '—' : row.std.toFixed(6)}</td><td>{row.ci95 === null ? '—' : row.ci95.toFixed(6)}</td><td>{row.statisticalStatus}</td></tr>)}</tbody></table>}
          {experimentDoc && <details className="spx-advanced">
            <summary>高级：保留策略</summary>
            <div className="spx-advanced-body">
              <div className="wf-template-row">
                <button className="wf-button ghost" disabled={!!busy} onClick={() => void previewRetention()}>预览释放</button>
                <button className="wf-button ghost" disabled={!!busy} onClick={() => void applyRetention('release-runs')}>应用 release-runs</button>
                <button className="wf-button ghost" disabled={!!busy} onClick={() => void applyRetention('keep-all')}>恢复 keep-all</button>
                <span className="wf-muted">当前 {experimentDoc.retention?.mode ?? 'keep-all'}</span>
              </div>
              {retentionInfo && <div className="wf-summary"><strong>Retention</strong><span>可释放 {retentionInfo.releasable.length} run</span><span>受保护 {retentionInfo.protected.length} run</span></div>}
            </div>
          </details>}
        </section>}
        {zone === 'analyze' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>05</span><h2>分析</h2></div>
          <p className="wf-muted">候选快照对比、结构 diff、process window 与基础标定；数值等价独立于绑定状态。</p>
          {measurement ? <div className="wf-summary"><strong>{measurement.status}</strong><span>{measurement.value === null ? measurement.reason : `${measurement.value.toFixed(4)} ${measurement.unit}`}</span><span>绑定 {bindingLabel(measurement.bindingStatus)} · 等价 {bindingLabel(measurement.equivalenceStatus)}</span></div> : <div className="wf-empty">完成一次运行与测量后显示指标。</div>}
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
                {view && analysisFrame ? <StructureCanvas
                  view={view}
                  frame={analysisFrame}
                  axis={syncAxis}
                  className="wf-compare-canvas"
                  label={`候选 ${index + 1} 视图`}
                  showAxes={false}
                /> : <div className="wf-empty">视图加载中…</div>}
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
          <div className="wf-card-head"><span>06</span><h2>报告</h2></div>
          <p className="wf-muted">服务端 HTML 报告包含 Flow/Compile、Clip、变量、引擎版本、种子、指标、视图、process window 与结论；浏览器打印可保存 PDF。</p>
          <div className="wf-template-row">
            <input aria-label="报告标题" value={reportTitle} onChange={event => setReportTitle(event.target.value)} />
            <button className="wf-button primary" disabled={!!busy} onClick={() => void generateReport()}>{busy === 'report' ? '生成中…' : '生成 HTML 报告'}</button>
            <button className="wf-button ghost" disabled={!reportDocument} onClick={openReport}>打开报告</button>
            <button className="wf-button ghost" disabled={!reportDocument} onClick={downloadReportHtml}>下载 HTML</button>
          </div>
          <textarea aria-label="报告结论" value={reportConclusions} onChange={event => setReportConclusions(event.target.value)} placeholder="结论 / 候选选择说明" />
          {reportDocument && <div className="wf-summary"><strong>{reportDocument.reportId}</strong><span>{reportDocument.createdAt}</span><span><code>{reportDocument.sha256.slice(0, 16)}…</code></span></div>}
          <div className="wf-summary"><strong>工作流摘要</strong><span>Flow {compile?.flowId ?? '—'}</span><span>Compile {compile?.compileId ?? '—'}</span><span>Run {segment?.runId ?? '—'}</span><span>Measurement {measurement?.measurementId ?? '—'}</span><span>绑定 {measurement ? bindingLabel(measurement.bindingStatus) : '—'}</span></div>
        </section>}
          </main>

          <div className="spx-output">
            <div className="spx-output-tabs">
              {([
                ['output', '输出'],
                ['problems', '问题'],
                ['provenance', '来源证据'],
              ] as Array<[typeof outputTab, string]>).map(([id, label]) => <button
                key={id} type="button" className={outputTab === id ? 'active' : ''}
                onClick={() => setOutputTab(id)}
              >{label}</button>)}
            </div>
            <div className="spx-output-body">
              {outputTab === 'output' && <>
                {activityLog.length === 0 && <div className="spx-log muted">就绪。新建工程或上传版图开始。</div>}
                {activityLog.slice(-8).map((line, index) => <div className="spx-log" key={`${index}-${line}`}>{line}</div>)}
                {error && <div className="spx-log error">{error}</div>}
              </>}
              {outputTab === 'problems' && (() => {
                const problems: string[] = [];
                if (error) problems.push(`错误：${error}`);
                if (!project) problems.push('尚未打开工程：在 01 新建、打开或导入工程。');
                else if (!(project.steps ?? []).length) problems.push('流程为空：在 02 从库插入或添加步骤。');
                else if (!compile) problems.push('流程未编译：在 02 点击“编译流程”。');
                else if (compileStale) problems.push('流程已修改：需要重新编译后才能创建 Route。');
                else if (!route) problems.push('尚未创建 Route：在 02 点击“创建 Route”。');
                else if (routeStale) problems.push('Route 基于旧 Compile：重新编译并重建 Route。');
                return problems.length
                  ? <>{problems.map(line => <div className="spx-log error" key={line}>{line}</div>)}</>
                  : <div className="spx-log muted">没有阻塞问题。</div>;
              })()}
              {outputTab === 'provenance' && (provenance
                ? <>
                  {provenance.verifications.length === 0 && <div className="spx-log muted">该测量尚无验证记录。</div>}
                  {provenance.verifications.map(item => <div className="spx-log" key={item.verificationId}>{item.verificationId} · {statusLabel(item.status)} · {item.evidence.methodVersion} · {formatDateTime(item.createdAt)}</div>)}
                </>
                : <div className="spx-log muted">完成测量后显示来源证据。</div>)}
            </div>
          </div>
        </section>

        <aside className="spx-right">
          <div className="spx-panel-title"><span>属性</span></div>
          <div className="spx-props">
            {propertyRows.map(([label, value]) => <div className="spx-prop" key={label}><span>{label}</span><code>{value}</code></div>)}
          </div>
          <div className="spx-panel-title"><span>工作流</span></div>
          <div className="spx-props">
            <div className="spx-prop"><span>活动运行</span><code>{health?.activeRunId ? `${health.activeRunId.slice(0, 14)}…` : '—'}</code></div>
            <div className="spx-prop"><span>ViennaPS</span><code>{health?.viennaps ?? '—'}</code></div>
            <div className="spx-prop"><span>ViennaLS</span><code>{health?.viennals ?? '—'}</code></div>
          </div>
        </aside>
      </div>

      <footer className="spx-statusbar">
        <span>ViennaPS Workbench</span>
        <span>ViennaPS {health?.viennaps ?? '—'}</span>
        <span>Compile {compile ? compile.compileId.slice(0, 16) + '…' : '—'}</span>
        <span>Run {segment?.runId ? segment.runId.slice(0, 16) + '…' : '—'}</span>
        <span className="spx-status-right">{busy ? `${busy} running…` : 'localhost'}</span>
      </footer>
      {aboutOpen && <div className="spx-modal-backdrop" onClick={() => setAboutOpen(false)}>
        <div className="spx-modal" role="dialog" aria-label="About ViennaPS Workbench" onClick={event => event.stopPropagation()}>
          <div className="spx-modal-title">About ViennaPS Workbench</div>
          <div className="spx-modal-body">
            <p>Workflow layer for ViennaPS process simulation.</p>
            <div className="spx-prop"><span>Application</span><code>{health?.applicationVersion ?? '0.1.0'}</code></div>
            <div className="spx-prop"><span>ViennaPS</span><code>{health?.viennaps ?? '—'}</code></div>
            <div className="spx-prop"><span>ViennaLS</span><code>{health?.viennals ?? '—'}</code></div>
            <div className="spx-prop"><span>Backend</span><code>{health?.status ?? 'offline'}</code></div>
          </div>
          <div className="spx-modal-actions"><button className="wf-button" onClick={() => setAboutOpen(false)}>Close</button></div>
        </div>
      </div>}
    </div>
  );
}
