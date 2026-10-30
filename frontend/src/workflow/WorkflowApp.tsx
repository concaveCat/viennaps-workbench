import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './workflow.css';
import { SAMPLE_2D_PLANE, SAMPLE_2D_TRENCH, SAMPLE_3D_HOLE } from './samples';
import WorkflowViewer3D from './viewer3d/WorkflowViewer3D';
import type { Viewer3DStats } from './viewer3d/types';

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
/** Per-project 03 page state, mirroring backend `ProjectWorkflowStateV1`. */
type ProjectWorkflowState = {
  schemaVersion: 1;
  compileId: string | null;
  routeId: string | null;
  segmentRuns: Array<{ segmentId: string; runId: string; measurementId: string | null }>;
  measurementIds: string[];
  experimentIds: string[];
  updatedAt?: string | null;
};

type CompiledEnvelope = {
  project: { name: string; dimension: number; steps: ProjectStep[] };
  sourceMap: Array<{ stepId: string; authoredStepId: string }>;
  compileRecord: { compileId: string; flowId: string; geometryBindingId: string };
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
  createdAt?: string;
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
  unit?: string;
  design?: { conditionCount: number; logicalRunCount: number; replicates: number };
  queue?: { total: number; pending: number; running: number; succeeded: number; failed: number; retries: number; estimatedSecondsRemaining: number; estimatedCostUsd: number };
  aggregation?: { rows: Array<{ ordinal: number; n: number; mean: number | null; std: number | null; ci95: number | null; unit: string; statisticalStatus: string }>; ciMethod?: string };
  retention?: { mode: string };
  baseCompileId?: string | null;
  variants: Array<{
    ordinal: number;
    parameters?: Record<string, Record<string, number>>;
    replicates: Array<{
      status: string; value?: number | null; unit?: string; runId?: string; seed?: number;
      attemptCount?: number; statisticalSample?: boolean; measurementId?: string; reason?: string;
    }>;
  }>;
  project?: { steps: Array<{ id: string }> };
};
type ExperimentSummaryRow = { experimentId: string; name: string; updatedAt: string; status: string; variantCount: number };
type DoeFactorRow = { stepId: string; parameter: string; values: string };

/** Numeric step parameters the DOE endpoint accepts. */
const DOE_PARAMETERS: Array<{ id: string; label: string }> = [
  { id: 'rateMagnitude', label: 'rateMagnitude（µm/s）' },
  { id: 'duration', label: 'duration（s）' },
  { id: 'stickingProbability', label: 'stickingProbability' },
  { id: 'sourceExponent', label: 'sourceExponent' },
];

const DOE_DEFAULT_VALUES: Record<string, string> = {
  rateMagnitude: '0.01,0.02,0.03',
  duration: '0.02,0.04,0.06',
  stickingProbability: '0.01,0.1',
  sourceExponent: '0.5,1.0',
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
  /** Point indices for x / h / z; 2D snapshots keep the height in `h`. */
  axisMap?: { x?: number; h?: number; z?: number };
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
  { id: 'analyze', num: '05', label: '分析', title: '分析', desc: '候选对比、结构差异、工艺窗口与基础标定。' },
  { id: 'report', num: '06', label: '报告', title: '报告', desc: '生成包含来源证据、结构视图与实验结果的 HTML 报告。' },
] as const;

const STATUS_LABELS: Record<string, string> = {
  planned: '待执行',
  queued: '排队中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
  verified: '已核验',
  measured: '已测量',
  ambiguous: '有歧义',
  region_empty: '区域为空',
  no_intersection: '无交点',
  stale: '待重跑',
  completed: '已完成',
  pending: '待处理',
  evaluated: '已评估',
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

/** Compact × for one-shot banners. Confirmation banners keep 取消/确认 instead. */
function BannerClose({ onClose }: { onClose: () => void }) {
  return <button type="button" className="spx-banner-close" aria-label="关闭提示" onClick={onClose}>×</button>;
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
  material: '材料库',
  process: '工艺库',
  flow: '流程库',
  experiment: '实验库',
};

/** Empty `ProjectWorkflowStateV1`; unset server state reads exactly like this. */
function emptyWorkflowState(): ProjectWorkflowState {
  return { schemaVersion: 1, compileId: null, routeId: null, segmentRuns: [], measurementIds: [], experimentIds: [] };
}

/** Strip server-owned timestamps so a re-save of identical content stays a no-op. */
function workflowStatePayload(state: ProjectWorkflowState) {
  return {
    schemaVersion: 1 as const,
    compileId: state.compileId,
    routeId: state.routeId,
    segmentRuns: state.segmentRuns,
    measurementIds: state.measurementIds,
    experimentIds: state.experimentIds,
  };
}

function compileInfoFromEnvelope(envelope: CompiledEnvelope): CompileInfo {
  return {
    flowId: envelope.compileRecord.flowId,
    bindingId: envelope.compileRecord.geometryBindingId,
    compileId: envelope.compileRecord.compileId,
    projectName: envelope.project.name,
    dimension: envelope.project.dimension,
    stepCount: envelope.project.steps.length,
    compile: { project: envelope.project, sourceMap: envelope.sourceMap },
  };
}

/** ProjectV7 geometry binding for one saved Clip (same shape the server stores). */
function clipGeometryBinding(clip: Clip) {
  return {
    kind: 'gds', assetId: clip.gdsAssetId, fileName: clip.gdsFileName,
    structure: 'CLIP', unitScale: 1, position: [0, 0, 0],
    layers: clip.layers.map(item => ({ layer: item.layer, material: item.material || 'Si' })),
  };
}

/** Default parametric geometry for a dimension/kind switch inside 01. */
function defaultParametricGeometry(dimension: number, kind: string, previous: Record<string, unknown>): Record<string, unknown> {
  const substrateMaterial = typeof previous.substrateMaterial === 'string' ? previous.substrateMaterial : 'Si';
  const maskMaterial = typeof previous.maskMaterial === 'string' ? previous.maskMaterial : 'Mask';
  if (kind === 'trench') {
    return {
      kind: 'trench', width: Number(previous.width) || 1, depth: Number(previous.depth) || 2,
      taperAngle: Number(previous.taperAngle) || 0, maskHeight: Number(previous.maskHeight) || 0,
      maskTaperAngle: 0, substrateMaterial, maskMaterial,
    };
  }
  if (kind === 'hole') {
    return {
      kind: 'hole', radius: Number(previous.radius) || 1, depth: Number(previous.depth) || 2,
      taperAngle: Number(previous.taperAngle) || 0, maskHeight: Number(previous.maskHeight) || 0,
      maskTaperAngle: 0, substrateMaterial, maskMaterial,
    };
  }
  if (kind === 'fin') {
    return {
      kind: 'fin', width: Number(previous.width) || 1, height: Number(previous.finHeight ?? previous.height) || 1.2,
      taperAngle: Number(previous.taperAngle) || 0, maskHeight: Number(previous.maskHeight) || 0,
      maskTaperAngle: 0, substrateMaterial, maskMaterial, construction: 'native',
    };
  }
  return dimension === 2
    ? { kind: 'plane', height: Number(previous.height) || 0, substrateMaterial }
    : { kind: 'fin', width: 1, height: 1.2, taperAngle: 0, maskHeight: 0, maskTaperAngle: 0, substrateMaterial, maskMaterial, construction: 'native' };
}

function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value.trim());
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

const TOLERANCE_LABELS: Record<string, string> = {
  identical: '一致',
  within_tolerance: '在公差内',
  WITHIN_TOLERANCE: '在公差内',
  different: '超出公差',
  OUTSIDE_TOLERANCE: '超出公差',
  not_evaluated: '未评估',
  NOT_EVALUATED: '未评估',
};

function toleranceLabel(status: string): string {
  return TOLERANCE_LABELS[status] ?? status;
}

const TOPOLOGY_LABELS: Record<string, string> = {
  lines: '线框',
  triangles: '三角面',
  points: '点云',
};

function topologyLabel(topology: string): string {
  return TOPOLOGY_LABELS[topology] ?? topology;
}

/** Micron values: three decimals (`0.020`, `2.000`) so the unit column aligns. */
function formatMicrons(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return Math.abs(value) >= 1000 ? formatNumber(value) : value.toFixed(3);
}

/** One-line annotation drawn inside a 2D structure view. */
function structureSurfaceNote(view: CandidateView, axis: StructureAxis): string {
  const extent = structureExtent(view, axis);
  const unit = structureUnit(view);
  const depth = extent.maxV - extent.minV;
  if (!extent.flatV) return `深度 ${formatMicrons(depth)} ${unit}`;
  const surface = Math.abs(extent.maxV) < 5e-4 ? '0.000' : formatMicrons(extent.maxV);
  return `表面高度 ${surface} ${unit}`;
}

/** `{"Si": 0}` reads as "无材料差异"; non-zero entries use a full-width colon. */
function materialDeltaText(delta: Record<string, number>): string {
  const entries = Object.entries(delta ?? {});
  if (!entries.length) return '无材料差异';
  const changed = entries.filter(([, value]) => Math.abs(Number(value) || 0) > 1e-12);
  if (!changed.length) return '无材料差异';
  return changed.map(([key, value]) => `${key}：${formatNumber(value)}`).join(' · ');
}

const COMPARE_REASON_PATTERNS: Array<[RegExp, (match: RegExpMatchArray) => string]> = [
  [/^snapshot topology or element counts differ$/, () => '拓扑或元素数量不同'],
  [/^snapshot line\/triangle connectivity differs$/, () => '线/三角形连接关系不同'],
  [/^max point delta ([\d.eE+-]+) exceeds tolerance ([\d.eE+-]+)$/, match => `最大点偏差 ${formatNumber(Number(match[1]))} 超过公差 ${formatNumber(Number(match[2]))}`],
  [/^bounds delta ([\d.eE+-]+) exceeds tolerance ([\d.eE+-]+)$/, match => `边界偏差 ${formatNumber(Number(match[1]))} 超过公差 ${formatNumber(Number(match[2]))}`],
  [/^material (.+) measure delta ([\d.eE+-]+) exceeds material tolerance ([\d.eE+-]+)$/, match => `材料 ${match[1]} 偏差 ${formatNumber(Number(match[2]))} 超过材料公差 ${formatNumber(Number(match[3]))}`],
];

function compareReasonLabel(reason: string): string {
  for (const [pattern, render] of COMPARE_REASON_PATTERNS) {
    const match = reason.match(pattern);
    if (match) return render(match);
  }
  return reason;
}

const METRIC_METHOD_LABELS: Record<string, string> = {
  etch_depth: '刻蚀深度',
  cd_at_height: '指定高度 CD',
};

function metricMethodLabel(method: string): string {
  return METRIC_METHOD_LABELS[method] ?? method;
}

/** Backend stores `um`; the UI shows the micro sign. */
function displayUnit(unit: string | null | undefined): string {
  if (!unit) return 'µm';
  return unit === 'um' ? 'µm' : unit;
}

/** Compact number for tables: 4 significant digits, no float noise, no needless exponents. */
function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (value === 0) return '0';
  const abs = Math.abs(value);
  if (abs >= 1e6 || abs < 1e-6) return value.toExponential(3);
  return String(Number(value.toPrecision(digits)));
}

/** `V1 · 0.02 s · seed 20261029` — condition label used by 04 results and 05 candidates. */
function variantConditionLabel(variant: ExperimentDoc['variants'][number], replicateSeed?: number): string {
  const parameters = variant.parameters ?? {};
  const parts: string[] = [`V${variant.ordinal}`];
  for (const [stepId, values] of Object.entries(parameters)) {
    for (const [parameter, value] of Object.entries(values ?? {})) {
      const unit = parameter === 'duration' ? ' s' : parameter === 'rateMagnitude' ? ' µm/s' : '';
      parts.push(`${parameter === 'duration' ? '' : `${parameter} `}${formatNumber(value)}${unit}`);
    }
    if (!Object.keys(values ?? {}).length) parts.push(stepId);
  }
  const seed = replicateSeed ?? variant.replicates.find(item => item.seed !== undefined)?.seed;
  if (seed !== undefined) parts.push(`seed ${seed}`);
  return parts.join(' · ');
}

function experimentVariantStatus(variant: ExperimentDoc['variants'][number]): string {
  const statuses = variant.replicates.map(item => item.status);
  if (!statuses.length) return 'planned';
  if (statuses.some(status => status === 'running')) return 'running';
  if (statuses.some(status => status === 'queued')) return 'pending';
  if (statuses.every(status => status === 'succeeded')) return 'succeeded';
  if (statuses.some(status => status === 'failed')) return 'failed';
  if (statuses.every(status => status === 'planned')) return 'planned';
  return statuses[0];
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

function stepOptionLabel(step: ProjectStep, index: number): string {
  const operation = step.operation === 'deposit' ? '沉积' : step.operation === 'etch' ? '刻蚀' : (step.operation ?? '');
  return `${step.name || `步骤 ${index + 1}`}${operation ? ` · ${operation}` : ''}`;
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
  if (!response.ok) {
    const error = new Error(errorText(payload)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
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
/** Point indices actually drawn on the horizontal / vertical axis. */
type StructureAxes = { horizontal: number; vertical: number; verticalLabel: string; is2D: boolean };
type StructureFrame = { minU: number; maxU: number; minV: number; maxV: number; unit: string; flatV: boolean };
type StructureMarker = { orientation: 'vertical' | 'horizontal'; value: number; label: string } | null;
type StructureStepRow = {
  runId: string;
  stepId: string;
  segmentId: string;
  segmentIndex: number;
  kind: 'initial' | 'step';
  label: string;
};

function structureUnit(view: CandidateView): string {
  return view.coordinateUnit === 'um' || !view.coordinateUnit ? 'µm' : view.coordinateUnit;
}

/**
 * Resolve the point indices drawn on each axis.
 *
 * 3D views follow the XY/XZ/YZ selector. 2D snapshots are authored on x with the
 * height in the `h` slot (`z` is a constant 0 for those writers), so the old
 * hard-coded `point[1]`/`point[2]` pair collapsed the profile into one flat
 * line. Prefer `axisMap.h`, then `axisMap.z`, then the first axis with a real
 * span, so both the current and older snapshots keep their depth.
 */
function structureAxes(view: CandidateView, axis: StructureAxis): StructureAxes {
  if (view.dimension !== 2) {
    if (axis === 'yz') return { horizontal: 1, vertical: 2, verticalLabel: 'Z', is2D: false };
    if (axis === 'xz') return { horizontal: 0, vertical: 2, verticalLabel: 'Z', is2D: false };
    return { horizontal: 0, vertical: 1, verticalLabel: 'Y', is2D: false };
  }
  const map = view.axisMap ?? {};
  const asIndex = (value: unknown): number | null =>
    typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < 3 ? value : null;
  const horizontal = asIndex(map.x) ?? 0;
  const preferred = [asIndex(map.h), asIndex(map.z), 1, 2]
    .filter((value): value is number => value !== null);
  const candidates = [...new Set(preferred)].filter(value => value !== horizontal);
  const spanOf = (index: number): number => {
    let low = Infinity, high = -Infinity;
    for (const point of view.points) {
      const value = Number(point[index] ?? 0);
      if (value < low) low = value;
      if (value > high) high = value;
    }
    return Number.isFinite(low) ? high - low : 0;
  };
  const vertical = candidates.find(index => spanOf(index) > 1e-12)
    ?? candidates[0]
    ?? (horizontal === 1 ? 2 : 1);
  return { horizontal, vertical, verticalLabel: '高度', is2D: true };
}

function structurePoint(point: number[], axes: StructureAxes): [number, number] {
  return [Number(point[axes.horizontal] ?? 0), Number(point[axes.vertical] ?? 0)];
}

type StructureExtent = { minU: number; maxU: number; minV: number; maxV: number; flatV: boolean };

/** Raw data extent of the drawn axes (no padding). */
function structureExtent(view: CandidateView, axis: StructureAxis): StructureExtent {
  const axes = structureAxes(view, axis);
  let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
  for (const point of view.points) {
    const [u, v] = structurePoint(point, axes);
    minU = Math.min(minU, u); maxU = Math.max(maxU, u);
    minV = Math.min(minV, v); maxV = Math.max(maxV, v);
  }
  if (!Number.isFinite(minU) || !Number.isFinite(minV)) {
    const bounds = view.bounds ?? [];
    const pair = axes.horizontal === 0 && axes.vertical === 1
      ? [bounds[0], bounds[1], bounds[2], bounds[3]]
      : axes.horizontal === 0 && axes.vertical === 2
        ? [bounds[0], bounds[1], bounds[4], bounds[5]]
        : [bounds[2], bounds[3], bounds[4], bounds[5]];
    [minU, maxU, minV, maxV] = pair.map(value => Number(value) || 0) as [number, number, number, number];
  }
  if (maxU - minU < 1e-9) { minU -= 0.5; maxU += 0.5; }
  return { minU, maxU, minV, maxV, flatV: maxV - minV < 1e-9 };
}

function structureFrame(view: CandidateView, axis: StructureAxis): StructureFrame {
  const extent = structureExtent(view, axis);
  let { minU, maxU, minV, maxV } = extent;
  if (extent.flatV) {
    // A no-op / zero-rate step must still read as a substrate with thickness
    // instead of a single hairline at y = 0.
    const thickness = Math.max(maxU - minU, 1e-6) * 0.35;
    minV -= thickness;
    maxV += thickness * 0.15;
  }
  const paddingU = Math.max((maxU - minU) * 0.12, 1e-9);
  const paddingV = Math.max((maxV - minV) * 0.12, 1e-9);
  return {
    minU: minU - paddingU, maxU: maxU + paddingU,
    minV: minV - paddingV, maxV: maxV + paddingV,
    unit: structureUnit(view),
    flatV: extent.flatV,
  };
}

function mergeFrames(frames: StructureFrame[], zoom: number): StructureFrame {
  if (!frames.length) return { minU: 0, maxU: 1, minV: 0, maxV: 1, unit: 'µm', flatV: false };
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
    flatV: frames.every(frame => frame.flatV),
  };
}

function frameViewBox(frame: StructureFrame): string {
  return `${frame.minU} ${-frame.maxV} ${frame.maxU - frame.minU} ${frame.maxV - frame.minV}`;
}

function axisTicks(min: number, max: number): number[] {
  const span = max - min;
  if (!Number.isFinite(span) || span <= 0) return [];
  const raw = span / 5;
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
  if (!Number.isFinite(value)) return '—';
  if (value === 0) return '0';
  return String(Number(value.toPrecision(3)));
}

/**
 * Split the per-material line set into polylines. A 2D cross section is a set
 * of open interface polylines (plus the occasional closed contour), so walking
 * shared endpoints is enough to recover the drawable chains.
 */
function structureChains(edges: Array<[number, number]>): number[][] {
  const used = new Array<boolean>(edges.length).fill(false);
  const adjacency = new Map<number, number[]>();
  edges.forEach(([a, b], index) => {
    adjacency.set(a, [...(adjacency.get(a) ?? []), index]);
    adjacency.set(b, [...(adjacency.get(b) ?? []), index]);
  });
  const other = (edgeIndex: number, node: number): number => (edges[edgeIndex][0] === node ? edges[edgeIndex][1] : edges[edgeIndex][0]);
  const chains: number[][] = [];
  for (let start = 0; start < edges.length; start++) {
    if (used[start]) continue;
    used[start] = true;
    const chain = [edges[start][0], edges[start][1]];
    for (let grew = true; grew;) {
      grew = false;
      const tail = chain[chain.length - 1];
      for (const edgeIndex of adjacency.get(tail) ?? []) {
        if (used[edgeIndex]) continue;
        used[edgeIndex] = true;
        chain.push(other(edgeIndex, tail));
        grew = true;
        break;
      }
    }
    for (let grew = true; grew;) {
      grew = false;
      const head = chain[0];
      for (const edgeIndex of adjacency.get(head) ?? []) {
        if (used[edgeIndex]) continue;
        used[edgeIndex] = true;
        chain.unshift(other(edgeIndex, head));
        grew = true;
        break;
      }
    }
    chains.push(chain);
  }
  return chains;
}

type StructureFill = { materialId: number; coords: Array<[number, number]>; closed: boolean };

/**
 * Fill the material below every interface line down to the bottom of the view,
 * one polygon per material chain. Overlaps between materials stack their
 * translucency (the outlines are drawn afterwards and stay crisp).
 */
function structureFillPolygons(view: CandidateView, points: Array<[number, number]>): StructureFill[] {
  if (view.dimension !== 2 || !view.lines.length) return [];
  const byMaterial = new Map<number, Array<[number, number]>>();
  view.lines.forEach((line, index) => {
    if (!line || line.length < 2) return;
    const a = Number(line[0]);
    const b = Number(line[1]);
    if (!Number.isFinite(a) || !Number.isFinite(b) || a === b) return;
    if (!points[a] || !points[b]) return;
    const materialId = Number(view.lineMaterialIds[index] ?? -1);
    const edges = byMaterial.get(materialId) ?? [];
    edges.push([a, b]);
    byMaterial.set(materialId, edges);
  });
  const fills: StructureFill[] = [];
  for (const [materialId, edges] of byMaterial) {
    for (const chain of structureChains(edges)) {
      const closed = chain.length > 2 && chain[0] === chain[chain.length - 1];
      const coords = chain.map(index => points[index]);
      if (!closed) {
        const xs = coords.map(([x]) => x);
        if (Math.max(...xs) - Math.min(...xs) < 1e-12) continue;
      }
      fills.push({ materialId, coords, closed });
    }
  }
  return fills;
}

function StructureCanvas({ view, frame, axis = 'xy', marker = null, note = null, label, className = 'wf-structure-canvas', showAxes = true }: {
  view: CandidateView;
  frame: StructureFrame;
  axis?: StructureAxis;
  marker?: StructureMarker;
  /** Free-form annotation drawn in the top-left corner (surface height / depth). */
  note?: string | null;
  label: string;
  className?: string;
  showAxes?: boolean;
}) {
  const axes = structureAxes(view, axis);
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
  const barY = -frame.maxV + fontSize * 2.6;
  const barX = frame.maxU - barLength - span * 0.02;
  const points = view.points.map(point => structurePoint(point, axes));
  const fills = structureFillPolygons(view, points);
  const bottom = -frame.minV;
  const tickHalo = fontSize * 0.1;
  const axisTitle = `${axes.verticalLabel} (${frame.unit})`;
  const horizontalTitle = axes.is2D ? `X (${frame.unit})` : `${axis.toUpperCase().charAt(0)} (${frame.unit})`;
  return <svg viewBox={frameViewBox(frame)} className={className} role="img" aria-label={label}
    data-dimension={view.dimension} data-flat={frame.flatV ? '1' : '0'}>
    <rect x={frame.minU} y={-frame.maxV} width={frame.maxU - frame.minU} height={frame.maxV - frame.minV} fill="#f8fafb" />
    {showAxes && uTicks.map(tick => <line key={`ug-${tick}`} className="wf-axes wf-axes-grid"
      x1={tick} y1={-frame.minV} x2={tick} y2={-frame.maxV} stroke="#e6eaee" strokeWidth={strokeWidth} />)}
    {showAxes && vTicks.map(tick => <line key={`vg-${tick}`} className="wf-axes wf-axes-grid"
      x1={frame.minU} y1={-tick} x2={frame.maxU} y2={-tick} stroke="#e6eaee" strokeWidth={strokeWidth} />)}
    {fills.map((fill, index) => {
      const coords = fill.closed
        ? fill.coords
        : [...fill.coords, [fill.coords[fill.coords.length - 1][0], bottom], [fill.coords[0][0], bottom]];
      return <polygon key={`f-${index}`} className="wf-fill" data-material={fill.materialId}
        points={coords.map(([x, y]) => `${x},${-y}`).join(' ')}
        fill={colorOf(fill.materialId)} fillOpacity={0.3} stroke="none" />;
    })}
    {view.dimension === 2
      ? (view.lines.length
        ? view.lines.map((line, index) => {
          const a = points[line[0]];
          const b = points[line[1]];
          if (!a || !b) return null;
          return <line key={`l-${index}`} className="wf-interface"
            x1={a[0]} y1={-a[1]} x2={b[0]} y2={-b[1]}
            stroke={colorOf(view.lineMaterialIds[index])} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />;
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
        ? <line x1={marker.value} y1={-frame.maxV + span * 0.05} x2={marker.value} y2={-frame.minV - span * 0.05} strokeWidth={strokeWidth * 2} />
        : <line x1={frame.minU + span * 0.1} y1={-marker.value} x2={frame.maxU - span * 0.02} y2={-marker.value} strokeWidth={strokeWidth * 2} />}
      <text x={marker.orientation === 'vertical' ? marker.value + fontSize * 0.3 : frame.minU + span * 0.11}
        y={marker.orientation === 'vertical' ? bottom - span * 0.075 : -marker.value - fontSize * 0.4}
        fontSize={fontSize} stroke="#ffffff" strokeWidth={tickHalo} paintOrder="stroke">{marker.label}</text>
    </g>}
    {showAxes && <g className="wf-axes wf-axis-line">
      <line x1={frame.minU} y1={bottom} x2={frame.maxU} y2={bottom} stroke="#6b7a8a" strokeWidth={strokeWidth} />
      <line x1={frame.minU} y1={bottom} x2={frame.minU} y2={-frame.maxV} stroke="#6b7a8a" strokeWidth={strokeWidth} />
      {uTicks.map(tick => <g key={`u-${tick}`} className="wf-axes wf-tick">
        <line x1={tick} y1={bottom} x2={tick} y2={bottom - fontSize * 0.55} stroke="#6b7a8a" strokeWidth={strokeWidth} />
        {tick > frame.minU + fontSize * 1.8 && <text x={tick} y={bottom - fontSize * 0.9} fontSize={fontSize} textAnchor="middle"
          stroke="#ffffff" strokeWidth={tickHalo} paintOrder="stroke">{formatTick(tick)}</text>}
      </g>)}
      {vTicks.map(tick => <g key={`v-${tick}`} className="wf-axes wf-tick">
        <line x1={frame.minU} y1={-tick} x2={frame.minU + fontSize * 0.4} y2={-tick} stroke="#6b7a8a" strokeWidth={strokeWidth} />
        {-tick < bottom - fontSize * 1.9 && <text x={frame.minU + fontSize * 0.55} y={-tick - fontSize * 0.2} fontSize={fontSize} textAnchor="start"
          stroke="#ffffff" strokeWidth={tickHalo} paintOrder="stroke">{formatTick(tick)}</text>}
      </g>)}
      <text className="wf-axis-title" x={frame.maxU - span * 0.012} y={-frame.maxV + fontSize * 1.1} fontSize={fontSize} textAnchor="end"
        stroke="#ffffff" strokeWidth={tickHalo} paintOrder="stroke">{horizontalTitle}</text>
      <text className="wf-axis-title" x={frame.minU + span * 0.012} y={-frame.maxV + fontSize * 1.1} fontSize={fontSize} textAnchor="start"
        stroke="#ffffff" strokeWidth={tickHalo} paintOrder="stroke">{axisTitle}</text>
      <line x1={barX} y1={barY} x2={barX + barLength} y2={barY} stroke="#34404b" strokeWidth={strokeWidth * 2.5} />
      <text x={barX + barLength / 2} y={barY - fontSize * 0.5} fontSize={fontSize} textAnchor="middle">{`${formatTick(barLength)} ${frame.unit}`}</text>
      {note && <text className="wf-structure-note" x={frame.minU + span * 0.22} y={-frame.maxV + fontSize * 2.3} fontSize={fontSize} textAnchor="start"
        stroke="#ffffff" strokeWidth={tickHalo} paintOrder="stroke">{note}</text>}
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
  const [doeFactors, setDoeFactors] = useState<DoeFactorRow[]>([
    { stepId: '', parameter: 'rateMagnitude', values: '0.01,0.02,0.03' },
    { stepId: '', parameter: 'duration', values: '0.02,0.04,0.06' },
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
  const [windowError, setWindowError] = useState('');
  const [windowResult, setWindowResult] = useState<ProcessWindowResult | null>(null);
  const windowLowerRef = useRef<HTMLInputElement>(null);
  const windowUpperRef = useRef<HTMLInputElement>(null);
  const [reportTitle, setReportTitle] = useState('工作流报告');
  const [reportConclusions, setReportConclusions] = useState('');
  const [reportDocument, setReportDocument] = useState<ReportDocument | null>(null);
  const reportFrameRef = useRef<HTMLIFrameElement>(null);
  const [calibrationCsv, setCalibrationCsv] = useState('');
  const [calibrationName, setCalibrationName] = useState('calibration');
  const [calibrationModel, setCalibrationModel] = useState('scale_offset');
  const [calibrationMethod, setCalibrationMethod] = useState<'least-squares' | 'nelder-mead'>('least-squares');
  const [calibrationDataset, setCalibrationDataset] = useState<CalibrationDataset | null>(null);
  const [calibrationFit, setCalibrationFit] = useState<CalibrationFit | null>(null);
  const [previousCompile, setPreviousCompile] = useState<CompileInfo | null>(null);
  const [compileDiff, setCompileDiff] = useState<{ addedStepIds: string[]; removedStepIds: string[]; movedStepIds: string[]; changedStepIds: string[]; affectedStepIds: string[]; details?: Array<{ stepId: string; before: Record<string, unknown>; after: Record<string, unknown> }> } | null>(null);
  const [templateVariable, setTemplateVariable] = useState('0.02');
  const [experimentRows, setExperimentRows] = useState<Array<{ duration: string; replicates: number; seed: string }>>([
    { duration: '0.01', replicates: 2, seed: '20261029' },
    { duration: '0.02', replicates: 2, seed: '20261030' },
    { duration: '0.03', replicates: 2, seed: '20261031' },
  ]);
  const [experimentName, setExperimentName] = useState('');
  const [experimentStepId, setExperimentStepId] = useState('');
  const [experimentMaxAttempts, setExperimentMaxAttempts] = useState(2);
  const [experimentCost, setExperimentCost] = useState('0');
  const [experimentList, setExperimentList] = useState<ExperimentSummaryRow[]>([]);
  const [experimentInputsOpen, setExperimentInputsOpen] = useState(true);
  const [autoAnalysisId, setAutoAnalysisId] = useState('');
  const autoAnalysisRef = useRef('');
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
  const [measurementMark, setMeasurementMark] = useState<StructureMarker>(null);
  const [runSnapshots, setRunSnapshots] = useState<Record<string, string[]>>({});
  const [structureStats, setStructureStats] = useState<Viewer3DStats | null>(null);
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
  const [projectState, setProjectState] = useState<ProjectWorkflowState>(emptyWorkflowState);
  const [stateNotice, setStateNotice] = useState('');
  const [restoreNotice, setRestoreNotice] = useState('');
  const [stateSaving, setStateSaving] = useState(false);
  const gdsInput = useRef<HTMLInputElement>(null);
  const projectInput = useRef<HTMLInputElement>(null);
  const parametricGeometryRef = useRef<Record<string, unknown> | null>(null);
  const stateSaveTimer = useRef<number | null>(null);
  const lastStateJson = useRef('');
  const restorePending = useRef(false);

  const projectDirty = !!project && JSON.stringify(project) !== savedProjectJson;

  const geometrySaveBlock = (() => {
    if (!project || projectGeometrySource.kind !== 'layout' || projectGeometrySource.clipId) return '';
    const geometry = (project.geometry ?? {}) as Record<string, unknown>;
    if (geometry.kind === 'gds') return '';
    return '先选择或保存一个 Clip 作为工程几何';
  })();

  // Field-level validation shared by the 01 editor and every save entry point
  // (top bar, Ctrl/Cmd+S, File menu). Values match backend ProjectV7 bounds.
  const projectFormErrors = useMemo(() => {
    const errors: Record<string, string> = {};
    if (!project) return errors;
    const domain = (project.domain ?? {}) as Record<string, unknown>;
    const geometry = (project.geometry ?? {}) as Record<string, unknown>;
    const solver = (project.solver ?? {}) as Record<string, unknown>;
    const gridDelta = Number(domain.gridDelta);
    const xExtent = Number(domain.xExtent);
    if (!Number.isFinite(gridDelta) || gridDelta <= 0 || gridDelta > 0.5) errors['domain.gridDelta'] = 'gridDelta 必须 > 0 且 ≤ 0.5 µm';
    if (!Number.isFinite(xExtent) || xExtent < 1 || xExtent > 40) errors['domain.xExtent'] = 'xExtent 必须在 1–40 µm';
    if (project.dimension === 3) {
      const yExtent = Number(domain.yExtent);
      if (!Number.isFinite(yExtent) || yExtent < 1 || yExtent > 40) errors['domain.yExtent'] = 'yExtent 必须在 1–40 µm';
      if (Number.isFinite(gridDelta) && gridDelta > 0) {
        const minimum = 2 * gridDelta;
        if (xExtent < minimum) errors['domain.xExtent'] = `3D xExtent 至少为 2×gridDelta（${minimum} µm）`;
        if (yExtent < minimum) errors['domain.yExtent'] = `3D yExtent 至少为 2×gridDelta（${minimum} µm）`;
      }
    }
    const kind = String(geometry.kind ?? '');
    const positiveFields: Array<[string, string]> = kind === 'trench'
      ? [['width', '宽度'], ['depth', '深度']]
      : kind === 'hole' ? [['radius', '半径'], ['depth', '深度']]
        : kind === 'fin' ? [['width', '宽度'], ['height', '高度']] : [];
    for (const [key, label] of positiveFields) {
      const value = Number(geometry[key]);
      if (!Number.isFinite(value) || value <= 0) errors[`geometry.${key}`] = `${label}必须 > 0 µm`;
    }
    if (['trench', 'hole', 'fin'].includes(kind)) {
      const taper = Number(geometry.taperAngle ?? 0);
      if (!Number.isFinite(taper) || taper < -45 || taper > 45) errors['geometry.taperAngle'] = 'taperAngle 必须在 -45–45°';
      const maskHeight = Number(geometry.maskHeight ?? 0);
      if (!Number.isFinite(maskHeight) || maskHeight < 0) errors['geometry.maskHeight'] = 'maskHeight 必须 ≥ 0 µm';
    }
    if (kind === 'fin' && Number.isFinite(gridDelta) && gridDelta > 0) {
      const minimum = 2 * gridDelta;
      if (Number(geometry.width) < minimum) errors['geometry.width'] = `Fin 宽度至少为 2×gridDelta（${minimum} µm）`;
      if (Number(geometry.height) < minimum) errors['geometry.height'] = `Fin 高度至少为 2×gridDelta（${minimum} µm）`;
    }
    const rays = Number(solver.raysPerPoint);
    if (!Number.isInteger(rays) || rays < 16 || rays > 4096) errors['solver.raysPerPoint'] = 'raysPerPoint 必须是 16–4096 的整数';
    const threads = Number(solver.cpuThreads);
    if (!Number.isInteger(threads) || threads < 1) errors['solver.cpuThreads'] = 'cpuThreads 必须是 ≥ 1 的整数';
    const timeout = Number(solver.timeoutSeconds ?? 0);
    if (!Number.isInteger(timeout) || timeout < 0 || timeout > 600) errors['solver.timeoutSeconds'] = 'timeoutSeconds 必须是 0–600 的整数';
    const seed = Number(((solver.random ?? {}) as Record<string, unknown>).seed ?? 0);
    if (!Number.isInteger(seed) || seed < 0 || seed > 4294967295) errors['solver.seed'] = 'seed 必须是 0–4294967295 的整数';
    if (geometrySaveBlock) errors['geometry.source'] = geometrySaveBlock;
    return errors;
  }, [project, geometrySaveBlock]);

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
    restorePending.current = !!record?.projectId;
    setSavedProjectJson(record ? JSON.stringify(document) : '');
    setProjectSavedAt(record?.updatedAt ?? '');
    setProjectNotice(record
      ? `已打开 · ${record.name} · ${formatDateTime(record.updatedAt)}`
      : '草稿（未保存）');
    const geometry = (document.geometry ?? {}) as Record<string, unknown>;
    if (geometry.kind !== 'gds') parametricGeometryRef.current = geometry;
    setProjectGeometrySource(geometry.kind === 'gds'
      ? { kind: 'layout', clipName: typeof geometry.fileName === 'string' ? geometry.fileName : 'GDS Clip' }
      : { kind: 'parametric' });
    setCompile(null);
    setRoute(null);
    setSegment(null);
    setMeasurement(null);
    setProvenance(null);
    setCompileStale(true);
    setProjectState(emptyWorkflowState());
    lastStateJson.current = '';
    setStateNotice('');
    setRestoreNotice('');
    setActiveClip(null);
    setCanvas(null);
    setActiveLayout(null);
    setClips([]);
    setClipNotice('');
    setSetupMode('project');
  };

  /**
   * Recover the saved 01 geometry binding. The project only stores the clip's
   * generated GDS asset, so the clip record is looked up by asset/file name;
   * a deleted clip degrades to a named-but-unbound source instead of flipping
   * the page back to "parametric".
   */
  const resolveClipBinding = async (document: ProjectDoc): Promise<ProjectGeometrySource | null> => {
    const geometry = (document.geometry ?? {}) as Record<string, unknown>;
    if (geometry.kind !== 'gds') return null;
    const assetId = typeof geometry.assetId === 'string' ? geometry.assetId : '';
    const fileName = typeof geometry.fileName === 'string' ? geometry.fileName : '';
    try {
      const data = await api<{ clips: Clip[] }>('/api/v1/workflow/clips');
      const clip = data.clips.find(item => item.gdsAssetId === assetId
        || item.gdsFileName === fileName
        || item.clipId === fileName.replace(/\.gds$/i, ''));
      if (clip) {
        return { kind: 'layout', clipId: clip.clipId, clipName: clip.name, layoutId: clip.layoutId, clipHash: clip.clipHash };
      }
    } catch { /* the clip list is best effort */ }
    return { kind: 'layout', clipName: fileName || 'GDS Clip' };
  };

  /**
   * Load the project's 03 pointer document. Every reference is restored on its
   * own: one 404 clears only that pointer and adds a closable notice, so a
   * collected compile never blanks the whole page.
   */
  const restoreWorkflowState = async (id: string) => {
    let stored: ProjectWorkflowState;
    try {
      stored = await api<ProjectWorkflowState>(`/api/v1/workflow/projects/${id}/state`);
    } catch {
      restorePending.current = false;
      return;
    }
    const notices: string[] = [];
    const next: ProjectWorkflowState = {
      ...emptyWorkflowState(),
      compileId: stored.compileId ?? null,
      routeId: stored.routeId ?? null,
      segmentRuns: stored.segmentRuns ?? [],
      measurementIds: stored.measurementIds ?? [],
      experimentIds: stored.experimentIds ?? [],
    };
    if (next.compileId) {
      try {
        const envelope = await api<CompiledEnvelope>(`/api/v1/workflow/compiles/${next.compileId}`);
        setCompile(compileInfoFromEnvelope(envelope));
        setCompileStale(false);
      } catch (exc) {
        if ((exc as { status?: number }).status === 404) {
          next.compileId = null;
          notices.push('上次编译记录已不可用，已清空编译状态。');
        } else {
          notices.push(`编译记录恢复失败：${exc instanceof Error ? exc.message : String(exc)}`);
        }
      }
    }
    if (next.routeId) {
      try {
        const data = await api<{ plan: RoutePlan }>(`/api/v1/workflow/routes/${next.routeId}`);
        setRoute(data.plan);
        const executed = data.plan.segments.filter(item => item.runId && !item.runId.startsWith('pending_'));
        const current = [...executed].reverse().find(item => next.segmentRuns.some(row => row.segmentId === item.segmentId))
          ?? executed[executed.length - 1]
          ?? data.plan.segments[data.plan.segments.length - 1]
          ?? null;
        setSegment(current);
      } catch (exc) {
        if ((exc as { status?: number }).status === 404) {
          next.routeId = null;
          next.segmentRuns = [];
          notices.push('上次 Route 已不可用，已清空 Route 状态。');
        } else {
          notices.push(`Route 恢复失败：${exc instanceof Error ? exc.message : String(exc)}`);
        }
      }
    }
    if (next.measurementIds.length) {
      const rows = await Promise.all(next.measurementIds.map(async measurementId => {
        try { return await api<Measurement>(`/api/v1/workflow/measurements/${measurementId}`); } catch { return null; }
      }));
      const found = rows.filter((row): row is Measurement => !!row);
      if (found.length !== next.measurementIds.length) notices.push('部分测量记录已不可用，已保留其余测量。');
      if (found.length) {
        setMetrics(found);
        const latest = found[found.length - 1];
        setMeasurement(latest);
        if (latest.recipeId) {
          setActiveRecipeId(latest.recipeId);
          setActiveRecipeVersion(latest.recipeVersion);
          try {
            const recipe = await api<Recipe>(`/api/v1/workflow/metrology-recipes/${latest.recipeId}?version=${latest.recipeVersion}`);
            const parameters = (recipe.parameters ?? {}) as Record<string, unknown>;
            const markValue = latest.method === 'cd_at_height' ? Number(parameters.height ?? 0) : Number(parameters.x ?? 0);
            setMeasurementMark(latest.method === 'cd_at_height'
              ? { orientation: 'horizontal', value: markValue, label: `测量 CD 高度 ${markValue} µm` }
              : { orientation: 'vertical', value: markValue, label: `测量 X ${markValue} µm` });
          } catch { /* the marker is optional */ }
        }
      } else {
        next.measurementIds = [];
      }
    }
    setProjectState(next);
    lastStateJson.current = notices.length ? '' : JSON.stringify(workflowStatePayload(next));
    restorePending.current = false;
    setRestoreNotice(notices.join(' '));
    if (notices.length) {
      setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  工程状态部分恢复：${notices.join(' ')}`]);
    }
  };

  /** Replace the 03 pointer set after a compile/route/segment/measurement change. */
  const recordCompile = (compileId: string) => {
    setProjectState(previous => ({
      ...previous, compileId, routeId: null, segmentRuns: [],
    }));
  };

  const recordRoute = (plan: RoutePlan) => {
    setProjectState(previous => {
      const known = new Map(previous.segmentRuns.map(row => [row.segmentId, row]));
      const segmentRuns = plan.segments
        .filter(item => item.runId && !item.runId.startsWith('pending_'))
        .map(item => ({
          segmentId: item.segmentId,
          runId: item.runId as string,
          measurementId: known.get(item.segmentId)?.measurementId ?? null,
        }));
      return { ...previous, routeId: plan.route.routeId, segmentRuns };
    });
  };

  const recordMeasurement = (measurementId: string, segmentId?: string | null) => {
    setProjectState(previous => ({
      ...previous,
      measurementIds: previous.measurementIds.includes(measurementId)
        ? previous.measurementIds
        : [...previous.measurementIds, measurementId],
      segmentRuns: segmentId
        ? previous.segmentRuns.map(row => row.segmentId === segmentId ? { ...row, measurementId } : row)
        : previous.segmentRuns,
    }));
  };

  const recordExperiment = (experimentId: string) => {
    setProjectState(previous => ({
      ...previous,
      experimentIds: previous.experimentIds.includes(experimentId)
        ? previous.experimentIds
        : [...previous.experimentIds, experimentId],
    }));
  };

  /** 300 ms debounce: every pointer change writes one PUT, not one per render. */
  useEffect(() => {
    if (!projectId) return;
    if (restorePending.current) return;
    const payload = workflowStatePayload(projectState);
    const serialized = JSON.stringify(payload);
    if (serialized === lastStateJson.current) return;
    if (stateSaveTimer.current) window.clearTimeout(stateSaveTimer.current);
    stateSaveTimer.current = window.setTimeout(() => {
      stateSaveTimer.current = null;
      void (async () => {
        setStateSaving(true);
        try {
          const saved = await api<ProjectWorkflowState>(`/api/v1/workflow/projects/${projectId}/state`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: serialized,
          });
          lastStateJson.current = serialized;
          setStateNotice('');
          setProjectState(previous => ({ ...previous, updatedAt: saved.updatedAt ?? null }));
        } catch (exc) {
          setStateNotice(`工作流状态未能保存：${exc instanceof Error ? exc.message : String(exc)}`);
        } finally {
          setStateSaving(false);
        }
      })();
    }, 300);
    return () => {
      if (stateSaveTimer.current) { window.clearTimeout(stateSaveTimer.current); stateSaveTimer.current = null; }
    };
  }, [projectId, projectState]);

  const applySavedRecord = async (record: ProjectRecord, document: ProjectDoc) => {
    setProjectId(record.projectId);
    setSavedProjectJson(JSON.stringify(document));
    setProjectSavedAt(record.updatedAt);
    setProjectNotice(`已保存 · ${record.name} · ${formatDateTime(record.updatedAt)}`);
    window.localStorage.setItem(PROJECT_STORAGE_KEY, record.projectId);
    await refreshProjects();
  };

  /** The document that actually goes to the server: geometry follows the 01 source tab. */
  const projectDocumentForSave = (): ProjectDoc => {
    if (!project) throw new Error('没有可保存的工程');
    if (projectGeometrySource.kind !== 'layout') return project;
    const clip = (activeClip && activeClip.clipId === projectGeometrySource.clipId ? activeClip : null)
      ?? clips.find(item => item.clipId === projectGeometrySource.clipId)
      ?? null;
    if (clip) return { ...project, geometry: clipGeometryBinding(clip) };
    const geometry = (project.geometry ?? {}) as Record<string, unknown>;
    if (geometry.kind !== 'gds') throw new Error('先选择或保存一个 Clip 作为工程几何');
    return project;
  };

  const saveProject = () => run('project-save', async () => {
    if (!project) throw new Error('没有可保存的工程');
    const invalid = Object.values(projectFormErrors);
    if (invalid.length) throw new Error(`工程参数有误：${invalid[0]}`);
    const document = projectDocumentForSave();
    setOverwriteArmed(null);
    try {
      const record = projectId
        ? await projectRequest(`/api/v1/workflow/projects/${projectId}`, 'PUT', document)
        : await projectRequest('/api/v1/workflow/projects', 'POST', document);
      setProject(document);
      await applySavedRecord(record, document);
      // A new projectId (or a just-created draft) still needs its state PUT.
      lastStateJson.current = '';
      setProjectState(previous => ({ ...previous }));
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
    const invalid = Object.values(projectFormErrors);
    if (invalid.length) throw new Error(`工程参数有误：${invalid[0]}`);
    const document = projectDocumentForSave();
    const record = await projectRequest(`/api/v1/workflow/projects/${overwriteArmed.projectId}`, 'PUT', document);
    setOverwriteArmed(null);
    setProject(document);
    await applySavedRecord(record, document);
    lastStateJson.current = '';
    setProjectState(previous => ({ ...previous }));
  });

  const openProject = (record: ProjectRecord) => run('project-open', async () => {
    const document = await api<ProjectDoc>(`/api/v1/workflow/projects/${record.projectId}`);
    applyLoadedProject(document, record);
    await activateClipBinding(await resolveClipBinding(document));
    window.localStorage.setItem(PROJECT_STORAGE_KEY, record.projectId);
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  已打开工程 ${record.name}`]);
    await restoreWorkflowState(record.projectId);
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
      setProjectState(emptyWorkflowState());
      lastStateJson.current = '';
      setRestoreNotice('');
      setStateNotice('');
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
    setProjectState(emptyWorkflowState());
    lastStateJson.current = '';
    setStateNotice('');
    setRestoreNotice('');
    parametricGeometryRef.current = null;
    setSetupMode('home');
    void refreshProjects();
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

  // Ctrl/Cmd+S saves the current project instead of opening the browser's
  // "save page" dialog; every zone shares the same save entry point.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
      event.preventDefault();
      if (!project) { navigateZone('setup'); return; }
      if (busy === 'project-save') return;
      void saveProject();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [project, projectId, busy, projectFormErrors, projectGeometrySource, activeClip, clips]);

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
        await activateClipBinding(await resolveClipBinding(document));
        window.localStorage.setItem(PROJECT_STORAGE_KEY, stored);
        await restoreWorkflowState(stored);
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
    setDoeFactors(previous => {
      const used = new Set<string>();
      let changed = false;
      const next = previous.map((row, index) => {
        if (row.stepId && steps.some(step => step.id === row.stepId) && !used.has(row.stepId)) {
          used.add(row.stepId);
          return row;
        }
        // Rotate through the enabled steps so the default factors never collide
        // on `(stepId, parameter)`. A single-step project keeps every factor on
        // that step and relies on distinct parameters instead.
        const pick = steps.find(step => !used.has(step.id)) ?? steps[Math.min(index, steps.length - 1)];
        used.add(pick.id);
        changed = true;
        return { ...row, stepId: pick.id };
      });
      return changed ? next : previous;
    });
  }, [project]);

  useEffect(() => {
    const steps = (project?.steps ?? []).filter(step => step.enabled);
    setExperimentStepId(previous => steps.some(step => step.id === previous) ? previous : (steps[steps.length - 1]?.id ?? ''));
  }, [project?.steps]);

  useEffect(() => {
    setExperimentName(previous => previous.trim() ? previous : (project?.name ? `${project.name} · 实验` : ''));
  }, [project?.name]);

  useEffect(() => {
    void refreshExperimentList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  const compiledByAuthored = useMemo(
    () => new Map((compile?.compile.sourceMap ?? []).map(entry => [entry.authoredStepId, entry.stepId])),
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

  const authoredSteps = useMemo(() => project?.steps ?? [], [project]);
  const selectedFlowStep = authoredSteps.find(step => step.id === selectedFlowStepId) ?? null;
  const canCompile = !!project && authoredSteps.length > 0 && !busy;
  const canRoute = !!compile && !compileStale && !busy;
  const canRun = !!segment && segment.status === 'planned' && !busy;
  const canMeasure = !!segment && segment.status === 'succeeded' && !!segment.runId && !busy;
  const experimentRunCount = (experimentDoc?.variants ?? []).reduce(
    (total, variant) => total + variant.replicates.filter(replicate => !!replicate.runId).length, 0,
  );
  const hasExecutedRun = !!segment?.runId
    && !segment.runId.startsWith('pending_')
    && ['succeeded', 'failed'].includes(segment.status);
  const experimentReady = !!project && !!compile && !compileStale && !!activeRecipeId && authoredSteps.length > 0;
  const experimentBlockReason = !project
    ? '还没有工程：先在 01 新建、打开或导入工程。'
    : !authoredSteps.length
      ? '流程为空：先在 02 添加步骤。'
      : !compile
        ? '流程未编译：先在 02 编译流程。'
        : compileStale
          ? '流程已修改：先在 02 重新编译。'
          : !activeRecipeId
            ? '还没有测量配方：先在 03 执行 Run 并完成一次测量。'
            : '';
  const analysisReady = !!project && !!compile && (experimentRunCount > 0 || !!measurement);
  const reportReady = hasExecutedRun || !!measurement || experimentRunCount > 0;
  const routeStale = !!route && !!compile && route.attempts.some(attempt => attempt.compileId !== compile.compileId);

  const stepDisplayName = useCallback((stepId: string): string => {
    const compiled = compile?.compile.project.steps.find(step => step.id === stepId);
    const authoredId = authoredByCompiled.get(stepId);
    const authored = authoredSteps.find(step => step.id === authoredId);
    return compiled?.name ?? authored?.name ?? stepId.slice(0, 10);
  }, [compile, authoredByCompiled, authoredSteps]);

  const structureDimension: 2 | 3 = compile?.dimension === 3 || project?.dimension === 3 ? 3 : 2;

  // The 03 timeline lists the initial structure of every executed segment plus
  // the step snapshots that run actually produced. The manifest is the source
  // of truth: older runs may not have every step, and continuation segments
  // start from a saved Domain state instead of the project geometry.
  const succeededSegmentRuns = useMemo(() => (route?.segments ?? [])
    .filter(item => !!item.runId && !item.runId.startsWith('pending_') && item.status === 'succeeded')
    .map(item => item.runId as string), [route]);

  useEffect(() => {
    const missing = succeededSegmentRuns.filter(runId => !(runId in runSnapshots));
    if (missing.length === 0) return;
    let cancelled = false;
    void Promise.all(missing.map(async runId => {
      try {
        const manifest = await api<{ snapshots?: Array<{ stepId?: unknown }> }>(`/api/runs/${runId}`);
        const stepIds = (manifest.snapshots ?? [])
          .map(item => (typeof item?.stepId === 'string' ? item.stepId : ''))
          .filter(Boolean);
        return [runId, stepIds] as const;
      } catch {
        return [runId, [] as string[]] as const;
      }
    })).then(entries => {
      if (cancelled) return;
      setRunSnapshots(previous => ({ ...previous, ...Object.fromEntries(entries) }));
    });
    return () => { cancelled = true; };
  }, [succeededSegmentRuns, runSnapshots]);

  const runStructureSteps = useMemo(() => {
    if (!route) return [] as StructureStepRow[];
    const rows: StructureStepRow[] = [];
    route.segments.forEach((item, segmentIndex) => {
      if (!item.runId || item.runId.startsWith('pending_') || item.status !== 'succeeded') return;
      const snapshots = runSnapshots[item.runId];
      if (!snapshots) return;
      if (snapshots.includes('initial')) {
        rows.push({
          runId: item.runId,
          stepId: 'initial',
          segmentId: item.segmentId,
          segmentIndex,
          kind: 'initial',
          label: segmentIndex === 0 ? '初始结构' : `Segment ${segmentIndex + 1} 起始`,
        });
      }
      const start = item.startAfterStepId ? route.stepOrder.indexOf(item.startAfterStepId) + 1 : 0;
      const end = route.stepOrder.indexOf(item.throughStepId);
      if (end < 0) return;
      for (const stepId of route.stepOrder.slice(start, end + 1)) {
        if (!snapshots.includes(stepId)) continue;
        rows.push({
          runId: item.runId,
          stepId,
          segmentId: item.segmentId,
          segmentIndex,
          kind: 'step',
          label: stepDisplayName(stepId),
        });
      }
    });
    return rows;
  }, [route, runSnapshots, stepDisplayName]);

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
  const structureStepIds = useMemo(
    () => (structureRunId ? (runSnapshots[structureRunId] ?? [structureStepId]) : []),
    [structureRunId, structureStepId, runSnapshots],
  );
  const structureStatsForStep = structureStats && structureStats.stepId === structureStepId ? structureStats : null;
  const structureViewExtent = useMemo(
    () => (structureView ? structureExtent(structureView, 'xy') : null),
    [structureView],
  );
  // Flat (no-op / zero-rate) steps still need a readable label; a measurement
  // reports the etched depth instead of the surface height.
  const structureNote = useMemo(() => {
    if (!structureView || !structureViewExtent) return null;
    const measuredDepth = measurement && measurement.value !== null && measurement.method === 'etch_depth'
      ? Math.abs(measurement.value)
      : null;
    if (measuredDepth !== null && measuredDepth >= 5e-4 && measurement) {
      return `深度 ${formatMicrons(measuredDepth)} ${displayUnit(measurement.unit)}`;
    }
    return structureSurfaceNote(structureView, 'xy');
  }, [structureView, structureViewExtent, measurement]);

  useEffect(() => {
    // 3D reads the raw snapshot inside its worker; fetching a candidate view too
    // would double the network and memory cost for no visible gain.
    if (structureDimension === 3) {
      setStructureView(null);
      setStructureError('');
      setStructureLoading(false);
      return;
    }
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
  }, [structureRunId, structureStepId, structureDimension, route?.segments.map(item => `${item.segmentId}:${item.status}`).join(',')]);

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
      const geometry = (parsed.geometry ?? {}) as Record<string, unknown>;
      if (geometry.kind !== 'gds') parametricGeometryRef.current = geometry;
      setProjectGeometrySource(geometry.kind === 'gds'
        ? { kind: 'layout', clipName: typeof geometry.fileName === 'string' ? geometry.fileName : 'GDS Clip' }
        : { kind: 'parametric' });
      await activateClipBinding(await resolveClipBinding(parsed));
      setCompileStale(true);
      setProjectState(emptyWorkflowState());
      lastStateJson.current = '';
      setRestoreNotice('');
      setStateNotice('');
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
    setProjectState(emptyWorkflowState());
    lastStateJson.current = '';
    setRestoreNotice('');
    setStateNotice('');
    parametricGeometryRef.current = null;
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
    setProjectState(emptyWorkflowState());
    lastStateJson.current = '';
    setRestoreNotice('');
    setStateNotice('');
    parametricGeometryRef.current = (sample.geometry ?? null) as Record<string, unknown> | null;
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
    const document = projectDocumentForSave();
    let result: CompileInfo;
    if (source.kind === 'layout' && source.clipId) {
      result = await postJson<CompileInfo>(`/api/v1/workflow/clips/${source.clipId}/compile`, {
        project: document, flowId: compile?.flowId,
      });
    } else {
      result = await postJson<CompileInfo>('/api/v1/workflow/flows/from-project', {
        project: document, flowId: compile?.flowId,
      });
    }
    if (compile) setPreviousCompile(compile);
    setCompile(result);
    setCompileStale(false);
    recordCompile(result.compileId);
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
    recordCompile(result.compileId);
    setProject(previous => previous ? { ...previous, steps: (result.authoredSteps ?? previous.steps ?? []) as ProjectStep[] } : previous);
  });

  const createRoute = (rebuild = false) => run('route', async () => {
    if (!compile) throw new Error('先编译流程');
    if (compileStale) throw new Error('流程已修改，请重新编译');
    if (route && !rebuild) { navigateZone('run'); return; }
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
    recordRoute(result.plan);
    setRebuildRouteArmed(false);
    navigateZone('run');
    setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  Route 已创建 · ${result.plan.segments.length} Segment`]);
  });

  const goToRun = () => {
    if (!route) { void createRoute(); return; }
    navigateZone('run');
  };

  const execute = () => run('execute', async () => {
    if (!segment) throw new Error('先创建 Route');
    const result = await postJson<{ segment: RoutePlan['segments'][number]; runId: string }>(
      `/api/v1/workflow/segments/${segment.segmentId}/execute`, {},
    );
    setSegment(result.segment);
    const nextPlan = route
      ? { ...route, segments: route.segments.map(item => item.segmentId === result.segment.segmentId ? result.segment : item) }
      : null;
    if (nextPlan) { setRoute(nextPlan); recordRoute(nextPlan); }
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
    recordMeasurement(result.measurementId, segment.segmentId);
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
    recordRoute(result.plan);
    recordMeasurement(result.measurement.measurementId, result.decision.branchSegmentId);
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
    recordRoute(result.plan);
    recordMeasurement(result.measurement.measurementId, result.decision.branchSegmentId);
  });


  const executeAll = () => run('execute', async () => {
    if (!route) throw new Error('先创建 Route');
    let working = route;
    for (const item of route.segments.filter(segmentItem => segmentItem.status === 'planned')) {
      const started = await postJson<{ segment: RoutePlan['segments'][number]; runId: string }>(
        `/api/v1/workflow/segments/${item.segmentId}/execute`, {},
      );
      setSegment(started.segment);
      working = { ...working, segments: working.segments.map(row => row.segmentId === started.segment.segmentId ? started.segment : row) };
      setRoute(working);
      recordRoute(working);
      const done = await waitSegment(item.segmentId);
      setSegment(done);
      working = { ...working, segments: working.segments.map(row => row.segmentId === done.segmentId ? done : row) };
      setRoute(working);
      recordRoute(working);
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
    setProjectState(previous => ({ ...previous, compileId: adopted.compileId }));
    const result = await postJson<{ plan: RoutePlan }>(`/api/v1/workflow/routes/${route.route.routeId}/branches`, {
      compileId: adopted.compileId, startAfterStepId: prefix.id, changedStepIds: [tail.id],
    });
    setRoute(result.plan);
    const tailSegment = result.plan.segments[result.plan.segments.length - 1];
    setSegment(tailSegment);
    recordRoute(result.plan);
    setMeasurement(null); setProvenance(null); setMetrics([]);
  });

  const refreshExperimentList = async (preferred: string[] = []): Promise<ExperimentSummaryRow[]> => {
    try {
      const data = await api<{ experiments: Array<{ experimentId: string; name: string; updatedAt: string }> }>('/api/v1/workflow/experiments');
      const listed = await Promise.all(data.experiments.slice(0, 12).map(async row => {
        try {
          const doc = await api<ExperimentDoc>(`/api/v1/workflow/experiments/${row.experimentId}`);
          return { ...row, name: doc.name || row.name, status: doc.status, variantCount: doc.variants.length };
        } catch {
          return { ...row, status: 'unknown', variantCount: 0 };
        }
      }));
      const known = new Set(listed.map(row => row.experimentId));
      // Experiments saved in the project state must stay visible even when they
      // are older than the newest page of the global list.
      const restored = await Promise.all(preferred.filter(id => !known.has(id)).slice(0, 8).map(async id => {
        try {
          const doc = await api<ExperimentDoc>(`/api/v1/workflow/experiments/${id}`);
          return { experimentId: id, name: doc.name, updatedAt: '', status: doc.status, variantCount: doc.variants.length };
        } catch {
          return null;
        }
      }));
      const byId = new Map<string, ExperimentSummaryRow>();
      for (const row of [...listed, ...restored.filter((item): item is ExperimentSummaryRow => !!item)]) byId.set(row.experimentId, row);
      const ordered = [
        ...preferred.map(id => byId.get(id)).filter((row): row is ExperimentSummaryRow => !!row),
        ...listed.filter(row => !preferred.includes(row.experimentId)).map(row => byId.get(row.experimentId) as ExperimentSummaryRow),
      ].slice(0, 12);
      setExperimentList(ordered);
      return ordered;
    } catch {
      setExperimentList([]);
      return [];
    }
  };

  // Restore the experiments recorded in the project state (schema 11) so 04
  // lists them after reopening a project instead of only after a new run.
  const stateExperimentIds = projectState.experimentIds.join(',');
  useEffect(() => {
    if (!projectId) return;
    const ids = projectState.experimentIds;
    if (!ids.length) return;
    let cancelled = false;
    void refreshExperimentList(ids).then(() => {
      if (cancelled) return;
      const target = ids[ids.length - 1];
      void api<ExperimentDoc>(`/api/v1/workflow/experiments/${target}`)
        .then(doc => setExperimentDoc(previous => previous ?? doc))
        .catch(() => undefined);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, stateExperimentIds]);

  const resizeVariants = (count: number) => {
    const size = Math.max(1, Math.min(16, Math.floor(count) || 1));
    setExperimentRows(previous => {
      if (size <= previous.length) return previous.slice(0, size);
      return [...previous, ...Array.from({ length: size - previous.length }, (_, offset) => {
        const index = previous.length + offset;
        return { duration: (0.01 + index * 0.01).toFixed(2), replicates: 2, seed: String(20261029 + index) };
      })];
    });
  };

  const updateVariantRow = (index: number, patch: Partial<{ duration: string; replicates: number; seed: string }>) => {
    setExperimentRows(previous => previous.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
  };

  const removeVariantRow = (index: number) => {
    setExperimentRows(previous => previous.length <= 1 ? previous : previous.filter((_, rowIndex) => rowIndex !== index));
  };

  const experimentTargetStep = (project?.steps ?? []).filter(step => step.enabled)
    .find(step => step.id === experimentStepId) ?? null;

  const enabledProjectSteps = useMemo(
    () => (project?.steps ?? []).filter(step => step.enabled),
    [project?.steps],
  );

  /**
   * Inline DOE validation: a repeated `(stepId, parameter)` pair is a hard 422
   * from the API, so it is surfaced on the factor row and blocks the button.
   */
  const doeFactorErrors = useMemo(() => {
    const seen = new Map<string, number>();
    return doeFactors.map((row, index) => {
      if (!row.stepId) return '请选择该因子作用的步骤';
      const key = `${row.stepId}/${row.parameter}`;
      const first = seen.get(key);
      if (first !== undefined) return `与因子 ${first + 1} 重复：同一 (步骤, 参数) 只能配置一次`;
      seen.set(key, index);
      const values = row.values.split(',').map(value => Number(value.trim())).filter(value => Number.isFinite(value));
      if (values.length < 2 || values.length > 10) return '取值需要 2–10 个数字（逗号分隔）';
      return '';
    });
  }, [doeFactors]);
  const doeBlocked = doeFactorErrors.some(Boolean);

  const addDoeFactor = () => {
    setDoeFactors(previous => {
      const used = new Set(previous.map(row => `${row.stepId}/${row.parameter}`));
      for (const step of enabledProjectSteps) {
        for (const parameter of DOE_PARAMETERS) {
          if (!used.has(`${step.id}/${parameter.id}`)) {
            return [...previous, {
              stepId: step.id,
              parameter: parameter.id,
              values: DOE_DEFAULT_VALUES[parameter.id] ?? '0.01,0.02',
            }];
          }
        }
      }
      const fallback = enabledProjectSteps[0]?.id ?? '';
      return [...previous, { stepId: fallback, parameter: 'duration', values: DOE_DEFAULT_VALUES.duration }];
    });
  };

  const createPersistentExperiment = () => run('experiment', async () => {
    if (!project || !compile || !activeRecipeId) throw new Error('先在 03 完成一次测量以固定配方');
    if (compileStale) throw new Error('流程已修改，请先在 02 重新编译');
    const target = experimentTargetStep;
    if (!target) throw new Error('流程没有可调步骤');
    if (experimentRows.length < 1 || experimentRows.length > 16) throw new Error('变体数必须在 1–16 之间');
    const variants = experimentRows.map((row, index) => {
      const duration = Number(row.duration);
      if (!Number.isFinite(duration) || duration <= 0) throw new Error(`变体 ${index + 1} 的 duration 必须是正数（s）`);
      const replicates = Math.round(Number(row.replicates));
      if (!Number.isFinite(replicates) || replicates < 1 || replicates > 8) throw new Error(`变体 ${index + 1} 的重复数必须在 1–8 之间`);
      const seed = Number(row.seed);
      if (!Number.isFinite(seed)) throw new Error(`变体 ${index + 1} 的 seed 必须是整数`);
      return { parameters: { [target.id]: { duration } }, replicates, seed: Math.round(seed) };
    });
    const name = experimentName.trim() || `${compile.projectName} · 实验`;
    const doc = await postJson<ExperimentDoc>('/api/v1/workflow/experiments', {
      name, project, flowId: compile.flowId, compileId: compile.compileId,
      recipeId: activeRecipeId, recipeVersion: activeRecipeVersion, variants,
      maxAttempts: Math.min(4, Math.max(1, Math.round(experimentMaxAttempts) || 1)),
      costPerRunUsd: Number(experimentCost) || 0,
    });
    setExperimentDoc(doc);
    setExperimentTable(null);
    setComparison(null);
    setCandidateViews({});
    setAutoAnalysisId('');
    setExperimentInputsOpen(false);
    recordExperiment(doc.experimentId);
    await refreshExperimentList();
  });

  const runPersistentExperiment = () => run('experiment', async () => {
    if (!experimentDoc) throw new Error('先创建实验');
    const experimentId = experimentDoc.experimentId;
    await postJson(`/api/v1/workflow/experiments/${experimentId}/run`, {});
    let finished: ExperimentDoc | null = null;
    for (let index = 0; index < 900; index += 1) {
      const doc = await api<ExperimentDoc>(`/api/v1/workflow/experiments/${experimentId}`);
      setExperimentDoc(doc);
      if (['completed', 'failed'].includes(doc.status)) { finished = doc; break; }
      await new Promise(resolve => window.setTimeout(resolve, 500));
    }
    setExperimentInputsOpen(false);
    await refreshExperimentList();
    if (finished) {
      setExperimentTable(null);
      setAutoAnalysisId('');
      autoAnalysisRef.current = '';
    }
  });

  const openExperiment = (experimentId: string) => run('experiment-open', async () => {
    const doc = await api<ExperimentDoc>(`/api/v1/workflow/experiments/${experimentId}`);
    setExperimentDoc(doc);
    setExperimentTable(null);
    setComparison(null);
    setCandidateViews({});
    setAutoAnalysisId('');
    autoAnalysisRef.current = '';
    setExperimentInputsOpen(!['completed', 'failed'].includes(doc.status));
    navigateZone('experiment');
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

  /** 01 project tab: selecting a clip only binds the source; save writes geometry. */
  const chooseClipForProject = (clip: Clip) => {
    selectClip(clip);
    if (project?.dimension === 3) return;
    setProjectGeometrySource({ kind: 'layout', clipId: clip.clipId, clipName: clip.name, layoutId: clip.layoutId, clipHash: clip.clipHash });
    setCompileStale(true);
  };

  /** Reopened GDS project: show the bound clip as the active one, not just its name. */
  const activateClipBinding = async (binding: ProjectGeometrySource | null) => {
    if (!binding) return;
    setProjectGeometrySource(binding);
    if (!binding.clipId) return;
    try {
      const clip = await api<Clip>(`/api/v1/workflow/clips/${binding.clipId}`);
      selectClip(clip);
    } catch { /* the header still shows the bound clip name */ }
  };

  // The 01 GDS tab must list saved layouts/clips without requiring a fresh upload.
  useEffect(() => {
    if (!project || projectGeometrySource.kind !== 'layout') return;
    void (async () => {
      try {
        const [clipList, layoutList] = await Promise.all([
          api<{ clips: Clip[] }>('/api/v1/workflow/clips'),
          api<{ layouts: Array<{ layoutId: string; fileName: string; structureCount: number }> }>('/api/v1/workflow/layouts'),
        ]);
        setClips(clipList.clips);
        setLayouts(layoutList.layouts);
        const layoutId = projectGeometrySource.layoutId
          ?? clipList.clips.find(item => item.clipId === projectGeometrySource.clipId)?.layoutId;
        if (layoutId && !activeLayout) {
          const layout = await api<LayoutDetail>(`/api/v1/workflow/layouts/${layoutId}`);
          setActiveLayout(layout);
          const bound = clipList.clips.find(item => item.clipId === projectGeometrySource.clipId);
          if (bound) selectStructure(layout, bound.structure);
        }
      } catch { /* the tab still works with the upload path */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.name, projectGeometrySource.kind, projectGeometrySource.clipId]);

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
    recordCompile(result.compileId);
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
    recordCompile(result.compileId);
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

  // 01–06 (top bar, menus, left tree) are the only navigation: leaving 02
  // always closes the library manager so coming back lands on the flow editor.
  const navigateZone = (target: typeof zone) => {
    setLibraryManagerOpen(false);
    setZone(target);
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
      // A plane has no mask layer; writing maskMaterial would be rejected by ProjectV7.
      if (geometry.kind === 'plane' && key === 'maskMaterial') return previous;
      geometry[key] = value;
      return { ...previous, geometry };
    });
    setCompileStale(true);
  };

  /** 01 editor: domain / geometry / solver fields write straight into ProjectV7. */
  const updateProjectDomain = (patch: Record<string, unknown>) => {
    setProject(previous => previous
      ? { ...previous, domain: { ...(previous.domain ?? {}), ...patch } }
      : previous);
    setCompileStale(true);
  };

  const updateProjectSolver = (patch: Record<string, unknown>) => {
    setProject(previous => {
      if (!previous) return previous;
      const solver = { ...(previous.solver ?? {}) } as Record<string, unknown>;
      const random = { ...((solver.random ?? {}) as Record<string, unknown>) };
      const { seed, ...rest } = patch;
      if (seed !== undefined) {
        random.mode = 'fixed';
        random.seed = seed;
      }
      return { ...previous, solver: { ...solver, ...rest, random } };
    });
    setCompileStale(true);
  };

  const updateProjectGeometryField = (patch: Record<string, unknown>) => {
    setProject(previous => {
      if (!previous) return previous;
      const geometry = { ...(previous.geometry ?? {}) } as Record<string, unknown>;
      if (geometry.kind === 'gds') return previous;
      const next = { ...geometry, ...patch };
      parametricGeometryRef.current = next;
      return { ...previous, geometry: next };
    });
    setCompileStale(true);
  };

  const changeProjectGeometryKind = (kind: string) => {
    setProject(previous => {
      if (!previous) return previous;
      const geometry = defaultParametricGeometry(previous.dimension ?? 2, kind, (previous.geometry ?? {}) as Record<string, unknown>);
      parametricGeometryRef.current = geometry;
      return { ...previous, geometry };
    });
    setCompileStale(true);
  };

  /** Switch 01 back to parametric geometry and drop the Clip-only fields. */
  const chooseParametricGeometry = () => {
    setProject(previous => {
      if (!previous) return previous;
      const current = (previous.geometry ?? {}) as Record<string, unknown>;
      if (current.kind !== 'gds') {
        parametricGeometryRef.current = current;
        return previous;
      }
      const fallback = parametricGeometryRef.current
        ?? defaultParametricGeometry(previous.dimension ?? 2, previous.dimension === 3 ? 'hole' : 'plane', {});
      return { ...previous, geometry: fallback };
    });
    setProjectGeometrySource({ kind: 'parametric' });
    setCompileStale(true);
  };

  const chooseLayoutGeometry = () => {
    if (!project || project.dimension === 3) return;
    const clip = activeClip ?? clips.find(item => item.clipId === projectGeometrySource.clipId) ?? null;
    setProjectGeometrySource(clip
      ? { kind: 'layout', clipId: clip.clipId, clipName: clip.name, layoutId: clip.layoutId, clipHash: clip.clipHash }
      : { kind: 'layout', clipName: projectGeometrySource.clipName });
    setCompileStale(true);
  };

  /** 2D ↔ 3D switch: domain, geometry and view fields must move together. */
  const changeProjectDimension = (dimension: 2 | 3) => {
    if (!project || project.dimension === dimension) return;
    setProject(previous => {
      if (!previous) return previous;
      const domain = (previous.domain ?? {}) as Record<string, unknown>;
      const gridDelta = Math.min(0.5, Math.max(0.01, Number(domain.gridDelta) || 0.2));
      const xExtent = Math.max(1, Number(domain.xExtent) || 4);
      const yExtent = Math.max(2 * gridDelta, Number(domain.yExtent) || xExtent);
      const boundary = String(domain.xBoundary ?? domain.boundaryXY ?? 'reflective');
      const currentGeometry = (previous.geometry ?? {}) as Record<string, unknown>;
      const geometry = dimension === 3 && currentGeometry.kind === 'gds'
        ? defaultParametricGeometry(3, 'hole', {})
        : currentGeometry;
      if (geometry.kind !== 'gds') parametricGeometryRef.current = geometry;
      return {
        ...previous,
        dimension,
        domain: dimension === 3
          ? { gridDelta, xExtent, yExtent, boundaryXY: boundary === 'periodic' ? 'periodic' : 'reflective' }
          : { gridDelta, xExtent, xBoundary: boundary === 'periodic' ? 'periodic' : 'reflective' },
        geometry,
        view: dimension === 2
          ? (previous.view ?? { panX: 0, panH: 0, zoom: 1, showGrid: true })
          : undefined,
        view3D: dimension === 3
          ? (previous.view3D ?? {
            projection: 'parallel', camera: null, displayMode: 'surface',
            visibleMaterialIds: [], opacityByMaterial: {},
            clip: { enabled: false, axis: 'x', position: 0.0, keep: 'positive' },
            section: { enabled: false, axis: 'y', position: 0.0 },
          })
          : undefined,
      };
    });
    if (dimension === 3) setProjectGeometrySource({ kind: 'parametric' });
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
      navigateZone('experiment');
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
    if (!project || !compile || !activeRecipeId) throw new Error('先在 03 完成一次测量以固定配方');
    if (compileStale) throw new Error('流程已修改，请先在 02 重新编译');
    if (doeFactors.length < 1 || doeFactors.length > 5) throw new Error('DOE 因子数必须在 1–5 之间');
    if (doeBlocked) throw new Error(doeFactorErrors.find(Boolean) ?? '先修正 DOE 因子');
    const factors = doeFactors.map(row => ({
      stepId: row.stepId,
      parameter: row.parameter,
      values: row.values.split(',').map(value => Number(value.trim())).filter(value => Number.isFinite(value)),
    }));
    const duplicates = new Set(factors.map(row => `${row.stepId}/${row.parameter}`));
    if (duplicates.size !== factors.length) throw new Error('同一 (步骤, 参数) 只能配置一次');
    const replicates = Math.round(Number(doeReplicates));
    if (!Number.isFinite(replicates) || replicates < 1 || replicates > 8) throw new Error('DOE 重复数必须在 1–8 之间');
    const seed = Number(doeSeed);
    if (!Number.isFinite(seed)) throw new Error('DOE 种子必须是整数');
    const name = experimentName.trim() || `${compile.projectName} · DOE`;
    const document = await postJson<ExperimentDoc>('/api/v1/workflow/experiments/doe', {
      name, project, flowId: compile.flowId,
      compileId: compile.compileId,
      recipeId: activeRecipeId, recipeVersion: activeRecipeVersion, factors,
      replicates, seed: Math.round(seed),
      maxAttempts: Math.min(4, Math.max(1, Math.round(experimentMaxAttempts) || 1)),
      costPerRunUsd: Number(doeCost) || 0,
    });
    setExperimentDoc(document);
    setExperimentTable(null);
    setComparison(null);
    setCandidateViews({});
    setAutoAnalysisId('');
    setExperimentInputsOpen(false);
    recordExperiment(document.experimentId);
    await refreshExperimentList();
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

  const analysisStepId = useMemo(() => {
    const steps = (compile?.compile.project.steps ?? []).filter(step => step.enabled);
    return steps[steps.length - 1]?.id ?? '';
  }, [compile]);

  const experimentCandidates = (doc: ExperimentDoc | null): Array<{ runId: string; stepId: string; label: string }> => {
    if (!doc || !analysisStepId) return [];
    return doc.variants.slice(0, 3).map(variant => {
      const replicate = variant.replicates.find(item => item.runId) ?? variant.replicates[0];
      return {
        runId: replicate?.runId ?? '',
        stepId: analysisStepId,
        label: variantConditionLabel(variant, replicate?.seed),
      };
    }).filter(row => row.runId && row.stepId);
  };

  const loadAnalysis = async (rows: Array<{ runId: string; stepId: string; label: string }>) => {
    const candidates = rows.filter(item => item.runId && item.stepId);
    if (!candidates.length) throw new Error('实验还没有可分析的 Run');
    if (candidates.length >= 2) {
      const result = await postJson<CompareResult>('/api/v1/workflow/analysis/compare', {
        candidates, tolerance: 1e-4,
      });
      setComparison(result);
    } else {
      setComparison(null);
    }
    const views = await Promise.all(candidates.map(item => api<CandidateView>(
      `/api/v1/workflow/analysis/candidates/${item.runId}/${item.stepId}/view`,
    )));
    setCandidateViews(Object.fromEntries(candidates.map((item, index) => [`${item.runId}:${item.stepId}`, views[index]])));
  };

  const loadExperimentCandidates = () => {
    const rows = experimentCandidates(experimentDoc);
    if (!rows.length) return;
    setCompareCandidates(rows);
  };

  const addCurrentCandidate = () => {
    if (!hasExecutedRun || !segment?.runId) return;
    setCompareCandidates(previous => [
      ...previous.filter(item => item.runId !== segment.runId),
      { runId: segment.runId!, stepId: segment.throughStepId, label: `当前 Run · ${statusLabel(segment.status)}` },
    ].slice(0, 3));
  };

  const runComparison = () => run('compare', async () => {
    const candidates = compareCandidates.filter(item => item.runId && item.stepId);
    if (candidates.length < 1) throw new Error('实验还没有可分析的 Run');
    await loadAnalysis(candidates);
  });

  // 04 → 05 hand-off: once the experiment has results, 05 presents them without
  // asking the user to press "load candidates" again.
  useEffect(() => {
    if (zone !== 'analyze') return;
    if (!experimentDoc || !['completed', 'failed'].includes(experimentDoc.status)) return;
    if (autoAnalysisRef.current === experimentDoc.experimentId) return;
    const rows = experimentCandidates(experimentDoc);
    if (!rows.length) return;
    autoAnalysisRef.current = experimentDoc.experimentId;
    setAutoAnalysisId(experimentDoc.experimentId);
    setCompareCandidates(rows);
    void run('compare', () => loadAnalysis(rows)).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zone, experimentDoc, analysisStepId]);

  const experimentConditionRows = useMemo(() => {
    if (!experimentDoc) return [];
    return experimentDoc.variants.map(variant => ({
      ordinal: variant.ordinal,
      label: variantConditionLabel(variant),
      status: experimentVariantStatus(variant),
    }));
  }, [experimentDoc]);

  const experimentResultRows = useMemo(() => {
    if (!experimentDoc) return [];
    return experimentDoc.variants.map(variant => {
      const values = variant.replicates
        .map(replicate => replicate.value)
        .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
      const mean = values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
      const std = values.length > 1 && mean !== null
        ? Math.sqrt(values.reduce((total, value) => total + (value - mean) ** 2, 0) / (values.length - 1))
        : null;
      return {
        ordinal: variant.ordinal,
        condition: variantConditionLabel(variant),
        status: experimentVariantStatus(variant),
        replicates: variant.replicates,
        mean,
        std,
        unit: displayUnit(variant.replicates.find(replicate => replicate.unit)?.unit ?? experimentDoc.unit),
      };
    });
  }, [experimentDoc]);

  const windowBlockedReason = !experimentDoc
    ? '先完成实验或测量：还没有可分析的实验。'
    : experimentRunCount === 0
      ? '先完成实验或测量：实验尚未运行。'
      : '';

  const computeProcessWindow = () => {
    const lower = Number(windowLower);
    const upper = Number(windowUpper);
    const problem = windowBlockedReason
      || (!windowLower.trim() || !windowUpper.trim() || !Number.isFinite(lower) || !Number.isFinite(upper)
        ? '填写有效的窗口上下限（数字）'
        : lower >= upper ? '窗口下限必须小于上限' : '');
    if (problem) {
      // Inline next to the offending inputs, then bring them into view instead
      // of relying on the single red line at the top of the page.
      setWindowError(problem);
      const target = windowLowerRef.current ?? windowUpperRef.current;
      target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      target?.focus({ preventScroll: true });
      return;
    }
    setWindowError('');
    void run('window', async () => {
      if (!experimentDoc) throw new Error('先创建实验');
      setWindowResult(await postJson<ProcessWindowResult>('/api/v1/workflow/analysis/process-window', {
        experimentId: experimentDoc.experimentId, lower, upper,
      }));
    });
  };

  const generateReport = () => run('report', async () => {
    if (!reportReady) throw new Error('先完成 03 运行与测量，或在 04 运行一个实验');
    const lower = Number(windowLower);
    const upper = Number(windowUpper);
    const payload: Record<string, unknown> = {
      title: reportTitle.trim() || '工作流报告',
      conclusions: reportConclusions,
    };
    if (experimentDoc) payload.experimentId = experimentDoc.experimentId;
    if (measurement) payload.measurementId = measurement.measurementId;
    if (hasExecutedRun && segment?.runId) { payload.runId = segment.runId; payload.stepId = segment.throughStepId; }
    if (Number.isFinite(lower) && Number.isFinite(upper) && lower < upper) { payload.lower = lower; payload.upper = upper; }
    setReportDocument(await postJson<ReportDocument>('/api/v1/workflow/analysis/report', payload));
  });

  const reportFileName = (): string => {
    const title = (reportDocument?.title || reportTitle || '报告').replace(/[\\/:*?"<>|]+/g, '-').trim();
    return `${title || '报告'}.html`;
  };

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
    anchor.download = reportFileName();
    anchor.click();
    URL.revokeObjectURL(url);
  };

  /** Grow the preview to the report body so the structure view is on screen. */
  const resizeReportFrame = () => {
    const frame = reportFrameRef.current;
    if (!frame) return;
    const apply = () => {
      const doc = frame.contentDocument;
      if (!doc) return;
      const height = Math.max(
        doc.documentElement?.scrollHeight ?? 0,
        doc.body?.scrollHeight ?? 0,
        520,
      );
      frame.style.height = `${Math.min(height + 16, 12000)}px`;
    };
    apply();
    // The body is laid out one frame after `load`; re-measure so the preview
    // never keeps a half-sized box that clips the structure section.
    window.requestAnimationFrame(apply);
    window.setTimeout(apply, 120);
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

  const candidateViewsHave3D = useMemo(
    () => Object.values(candidateViews).some(view => view.dimension === 3),
    [candidateViews],
  );
  const candidateUnit = Object.values(candidateViews)[0]?.coordinateUnit ?? 'um';
  /** The metric table always has at least the current measurement. */
  const analysisMetrics = useMemo(
    () => (metrics.length ? metrics : (measurement ? [measurement] : [])),
    [metrics, measurement],
  );

  /** Readable run name for the metric table; the raw id stays in `title`. */
  const runDisplayLabel = useCallback((runId: string | null | undefined): string => {
    if (!runId) return '—';
    const variant = experimentDoc?.variants.find(item =>
      item.replicates.some(replicate => replicate.runId === runId));
    if (variant) return variantConditionLabel(variant);
    const index = route?.segments.findIndex(item => item.runId === runId) ?? -1;
    if (index >= 0) return `当前 Run · 第 ${index + 1} 段`;
    return 'Run 已记录';
  }, [experimentDoc, route]);

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
    ? (!flowReferenceCount ? '先在 02 保存流程模板或流程定义，流程库条目才有可引用对象。' : !(managerForm.flowRef ?? '') ? '选择要引用的流程定义或模板后才能保存。' : '')
    : managerCategory === 'experiment'
      ? (!recipeOptions.length ? '先在 03 完成一次测量以生成测量配方，实验库条目才能引用它。' : '')
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

  const renderExperimentList = () => experimentList.length === 0 ? null : (
    <div className="wf-experiment-list" aria-label="实验列表">
      <div className="wf-card-subhead">实验列表（点击切换查看）</div>
      {experimentList.map(row => <button
        type="button"
        key={row.experimentId}
        className={row.experimentId === experimentDoc?.experimentId ? 'wf-experiment-row active' : 'wf-experiment-row'}
        onClick={() => void openExperiment(row.experimentId)}
      >
        <strong>{row.name}</strong>
        <span>{statusLabel(row.status)} · {row.variantCount} 变体 · {formatDateTime(row.updatedAt)}</span>
      </button>)}
    </div>
  );

  const renderFlowBar = () => (
    <div className="spx-flow-bar">
      <div className="spx-flow-actions">
        <button type="button" className="wf-button ghost spx-flow-save" disabled={!project || busy === 'project-save'}
          title={project ? '保存当前工程（Ctrl/Cmd+S）' : '先打开工程'}
          onClick={() => void saveProject()}>{busy === 'project-save' ? '保存中…' : '保存工程'}</button>
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
      <div className="spx-library-category-head">
        <strong>{LIBRARY_CATEGORY_LABELS[managerCategory]}</strong>
        <span className={managerError ? 'spx-library-load-error' : 'wf-muted'}>
          {managerLoading ? '加载中…' : managerError ? `加载失败：${managerError}` : `· ${managerItems.length} 条`}
        </span>
        {managerError && <button type="button" className="wf-button ghost" onClick={() => void loadManagerCategory(managerCategory)}>重试</button>}
      </div>
      <div className="spx-library-manager-body">
        <div className="spx-library-manager-list">
          {managerLoading && <div className="wf-muted spx-library-empty">加载中…</div>}
          {!managerLoading && managerError && <div className="spx-library-load-error spx-library-empty">{managerError}<button type="button" className="wf-button ghost" onClick={() => void loadManagerCategory(managerCategory)}>重试</button></div>}
          {!managerLoading && !managerError && managerItems.length === 0 && <div className="wf-muted spx-library-empty">
            {managerCategory === 'flow'
              ? <>
                  <span>流程库暂无条目</span>
                  <button type="button" className="spx-link" onClick={() => navigateZone('flow')}>先在 02 保存流程模板</button>
                </>
              : managerCategory === 'experiment'
                ? '实验库暂无条目，可先在 04 创建实验模板。'
                : `${LIBRARY_CATEGORY_LABELS[managerCategory]}暂无条目，可用中间栏的“新增”创建。`}
          </div>}
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
          {managerStatus && <div className="wf-summary spx-banner" role="status"><span className="spx-banner-text">{managerStatus}</span><BannerClose onClose={() => setManagerStatus('')} /></div>}
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
      {clipNotice && <div className="spx-clip-conflict spx-banner" role="status"><span className="spx-banner-text">{clipNotice}</span><BannerClose onClose={() => setClipNotice('')} /></div>}
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
  const projectDomain = (project?.domain ?? {}) as Record<string, unknown>;
  const projectGeometry = (project?.geometry ?? {}) as Record<string, unknown>;
  const projectSolver = (project?.solver ?? {}) as Record<string, unknown>;
  const projectRandom = (projectSolver.random ?? {}) as Record<string, unknown>;
  const projectGeometryKind = String(projectGeometry.kind ?? '');
  const projectParametricKinds = project?.dimension === 3 ? ['hole', 'fin'] : ['plane', 'trench'];
  const projectFormInvalid = Object.keys(projectFormErrors).length > 0;
  const geometrySourceLabel = projectGeometrySource.kind === 'layout'
    ? `Clip · ${projectGeometrySource.clipName ?? '未选择'}`
    : '参数化';
  const saveLabel = busy === 'project-save'
    ? '保存中…'
    : !project
      ? '未打开工程'
      : !projectId
        ? '草稿（未保存）· 保存'
        : projectDirty
          ? '有未保存更改 · 保存'
          : `已保存${projectSavedAt ? ` · ${formatDateTime(projectSavedAt)}` : ''}`;
  const propertyRows: Array<{ label: string; value: string; title?: string }> = [
    { label: '工程', value: project?.name ?? '—' },
    { label: '维度', value: project ? `${project.dimension ?? '?'}D` : '—' },
    { label: '编译', value: compile ? (compileStale ? '需重新编译' : '已编译') : '—', title: compile?.compileId },
    { label: '绑定 (binding)', value: compile?.bindingId ? '已记录' : '—', title: compile?.bindingId },
    { label: 'Route', value: route?.route.name ?? '—', title: route?.route.routeId },
    { label: 'Segment', value: segment ? statusLabel(segment.status) : '—', title: segment?.segmentId },
    { label: 'Run', value: segment?.runId ? '已记录' : '—', title: segment?.runId ?? undefined },
    { label: '测量', value: measurement ? `${measurement.value === null ? '—' : formatNumber(measurement.value)} ${displayUnit(measurement.unit)}` : '—', title: measurement?.measurementId },
    { label: '引擎', value: health ? `ViennaPS ${health.viennaps ?? '—'}` : '—' },
  ];

  const renderDomainField = (label: string, key: 'gridDelta' | 'xExtent' | 'yExtent', step = '0.01') => {
    const error = projectFormErrors[`domain.${key}`];
    return <label className={`spx-field${error ? ' invalid' : ''}`} key={key}>
      <span>{label}</span>
      <input aria-label={`工程 ${key}`} type="number" step={step} value={String(projectDomain[key] ?? '')}
        aria-invalid={!!error}
        onChange={event => updateProjectDomain({ [key]: Number(event.target.value) })} />
      {error && <em className="spx-field-error">{error}</em>}
    </label>;
  };

  const renderGeometryField = (label: string, key: 'height' | 'width' | 'depth' | 'radius' | 'taperAngle' | 'maskHeight', step = '0.01') => {
    const error = projectFormErrors[`geometry.${key}`];
    return <label className={`spx-field${error ? ' invalid' : ''}`} key={key}>
      <span>{label}</span>
      <input aria-label={`工程几何 ${key}`} type="number" step={step} value={String(projectGeometry[key] ?? '')}
        aria-invalid={!!error}
        onChange={event => updateProjectGeometryField({ [key]: Number(event.target.value) })} />
      {error && <em className="spx-field-error">{error}</em>}
    </label>;
  };

  const renderSolverField = (label: string, key: 'enginePreference' | 'seed' | 'timeoutSeconds' | 'raysPerPoint' | 'cpuThreads') => {
    const error = projectFormErrors[`solver.${key}`];
    const value = key === 'seed' ? projectRandom.seed : projectSolver[key];
    if (key === 'enginePreference') {
      return <label className="spx-field" key={key}>
        <span>{label}</span>
        <select aria-label="工程引擎" value={String(value ?? 'cpu')}
          onChange={event => updateProjectSolver({ enginePreference: event.target.value })}>
          <option value="cpu">CPU</option>
          <option value="gpu">GPU</option>
          <option value="auto">Auto</option>
        </select>
      </label>;
    }
    return <label className={`spx-field${error ? ' invalid' : ''}`} key={key}>
      <span>{label}</span>
      <input aria-label={`工程 ${key}`} type="number" step="1" value={String(value ?? '')}
        aria-invalid={!!error}
        onChange={event => updateProjectSolver({ [key]: Number(event.target.value) })} />
      {error && <em className="spx-field-error">{error}</em>}
    </label>;
  };

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
                {ZONES.map(item => <button key={item.id} role="menuitem" onClick={() => { setOpenMenu(null); navigateZone(item.id); }}>{item.num} {item.label} — {item.title}</button>)}
                {onOpenLegacy && <button role="menuitem" onClick={() => { setOpenMenu(null); onOpenLegacy(); }}>传统工作区</button>}
              </>}
              {id === 'project' && <>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); void saveProject(); }}>保存工程</button>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); exportProjectJson(); }}>导出工程 JSON…</button>
                <button role="menuitem" disabled={!canCompile} onClick={() => { setOpenMenu(null); void compileFlow(); }}>编译流程</button>
                <button role="menuitem" disabled={!canRoute} onClick={() => { setOpenMenu(null); void createRoute(); }}>创建 Route</button>
                <button role="menuitem" disabled={!project} onClick={() => { setOpenMenu(null); void saveTemplate(); }}>保存当前步骤为模板</button>
                <button role="menuitem" disabled={!libraryName.trim()} onClick={() => { setOpenMenu(null); void exportLibrary(); }}>导出共享库</button>
                <button role="menuitem" onClick={() => { setOpenMenu(null); navigateZone('flow'); }}>打开流程编辑器</button>
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
                <button role="menuitem" onClick={() => { setOpenMenu(null); navigateZone('analyze'); }}>打开分析与标定</button>
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
              onClick={() => navigateZone(item.id)}
            >{item.num} {item.label}</button>)}
          </div>
        </div>
      </header>

      {error && <div className="wf-error spx-error spx-banner" role="alert"><span className="spx-banner-text">{error}</span><BannerClose onClose={() => setError('')} /></div>}

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
                  value: compile ? `${compile.stepCount} 步` : '—',
                  badge: compile ? (compileStale ? '需重新编译' : '已编译') : '未编译',
                  badgeClass: compile ? (compileStale ? 'stale' : 'succeeded') : 'planned',
                  title: compile ? `Compile ${compile.compileId}` : '尚未编译流程',
                },
                {
                  label: 'Route',
                  target: 'run' as const,
                  value: route ? `${route.segments.length} Segment` : '—',
                  badge: route ? '已创建' : '未创建',
                  badgeClass: route ? 'succeeded' : 'planned',
                  title: route ? `Route ${route.route.routeId}` : '尚未创建 Route',
                },
                {
                  label: 'Run',
                  target: 'run' as const,
                  value: segment ? `第 ${Math.max(1, (route?.segments.findIndex(item => item.segmentId === segment.segmentId) ?? 0) + 1)} 个 Segment` : '—',
                  badge: segment ? statusLabel(segment.status) : '未运行',
                  badgeClass: segment?.status === 'succeeded' ? 'succeeded' : segment?.status === 'running' ? 'running' : segment?.status === 'failed' ? 'failed' : 'planned',
                  title: segment?.runId ? `Run ${segment.runId}` : '尚未执行 Segment',
                },
                {
                  label: '测量',
                  target: 'run' as const,
                  value: measurement ? `${measurement.value === null ? '—' : formatNumber(measurement.value)} ${displayUnit(measurement.unit)}` : '—',
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
                  value: reportDocument ? formatDateTime(reportDocument.createdAt) : '—',
                  badge: reportDocument ? '已生成' : '未生成',
                  badgeClass: reportDocument ? 'succeeded' : 'planned',
                  title: reportDocument ? `${reportDocument.title} · ${formatDateTime(reportDocument.createdAt)}` : '尚未生成报告',
                },
              ].map(item => <div
                key={item.label}
                role="treeitem"
                tabIndex={0}
                aria-selected={zone === item.target}
                className={zone === item.target ? 'spx-tree-item active' : 'spx-tree-item'}
                onClick={() => navigateZone(item.target)}
                onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') navigateZone(item.target); }}
                title={item.title}
              ><span className="spx-tree-label">{item.label}</span><span className="spx-tree-value">{item.value}</span>{item.badge && <span className={`spx-tree-status ${item.badgeClass}`}>{item.badge}</span>}</div>)}
            </div>
            <div className="spx-tree-legend" aria-label="状态图例">
              <span>待执行/未创建</span><span>运行中</span><span>成功/已测量</span><span>失败/待重跑</span>
            </div>
          </div>
          <div className="spx-panel">
            <div className="spx-panel-title"><span>库</span></div>
            <div className="spx-tree" role="tree" aria-label="库分类">
              {([
                { label: '材料', category: 'material' as const, count: libraryItems.filter(item => item.category === 'material').length },
                { label: '工艺', category: 'process' as const, count: libraryItems.filter(item => item.category === 'process').length },
                { label: '流程', category: 'flow' as const, count: libraryItems.filter(item => item.category === 'flow').length },
                { label: '实验', category: 'experiment' as const, count: libraryItems.filter(item => item.category === 'experiment').length },
              ]).map(item => {
                const active = zone === 'flow' && libraryManagerOpen && managerCategory === item.category;
                return <div
                  key={item.label}
                  role="treeitem"
                  tabIndex={0}
                  aria-selected={active}
                  className={active ? 'spx-tree-item active' : 'spx-tree-item'}
                  onClick={() => openLibraryManager(item.category)}
                  onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') openLibraryManager(item.category); }}
                ><span>{item.label}</span><span className="spx-count">{item.count}</span></div>;
              })}
              {([
                { label: '版图', count: layouts.length, onClick: () => { navigateZone('setup'); setSetupMode('layout'); void refreshLayouts(); } },
                { label: 'Clip', count: clips.length, onClick: () => { navigateZone('setup'); setSetupMode('layout'); void refreshLayouts(); } },
                { label: 'Bundles', count: libraryBundles.length, onClick: () => { navigateZone('flow'); setLibraryAdvancedOpen(true); } },
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
              <span className="spx-state-pill">{project ? `${project.dimension ?? '?'}D · ${geometrySourceLabel}` : '未打开工程'}</span>
              <button
                type="button"
                className={`spx-state-pill spx-save-button${projectId && !projectDirty && project ? ' ok' : ''}`}
                disabled={busy === 'project-save'}
                title={project ? '保存当前工程（Ctrl/Cmd+S）' : '未打开工程：先在 01 新建或打开工程'}
                onClick={() => {
                  if (!project) { navigateZone('setup'); setSetupMode('home'); return; }
                  void saveProject();
                }}
              >{saveLabel}</button>
              {stateSaving && <span className="spx-sync-hint">同步状态…</span>}
            </div>
          </div>
          {(restoreNotice || stateNotice) && <div className="spx-banner spx-inline-notice" role="status">
            <span className="spx-banner-text">{[restoreNotice, stateNotice].filter(Boolean).join(' ')}</span>
            <BannerClose onClose={() => { setRestoreNotice(''); setStateNotice(''); }} />
          </div>}

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
                  aria-label={`打开工程 ${record.name}`}
                  disabled={!!busy}
                  onClick={() => void openProject(record)}
                >打开</button>
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
              {projectNotice && <span className="spx-project-notice spx-banner-inline">{projectNotice}<BannerClose onClose={() => setProjectNotice('')} /></span>}
            </div>
            <div className="spx-summary-grid">
              <div><span>维度</span><strong>{project.dimension ?? '—'}D</strong></div>
              <div><span>几何来源</span><strong>{geometrySourceLabel}</strong></div>
              <div><span>步骤</span><strong>{project.steps?.length ?? 0}</strong></div>
              <div><span>求解器</span><strong>{ENGINE_LABELS[String(projectSolver.enginePreference ?? '')] ?? '—'}</strong></div>
            </div>
            <div className="spx-sheet-actions">
              <button type="button" className="wf-button primary" disabled={!!busy || projectFormInvalid}
                title={projectFormInvalid ? Object.values(projectFormErrors)[0] : '保存工程定义与步骤'}
                onClick={() => void saveProject()}>
                {busy === 'project-save' ? '保存中…' : projectId ? '保存更改' : '保存工程'}
              </button>
              <button type="button" className="wf-button" disabled={!!busy} onClick={exportProjectJson}>导出工程 JSON</button>
              <button type="button" className="wf-button" onClick={() => navigateZone('flow')}>去 02 定义流程</button>
              <button type="button" className="wf-button" onClick={closeProject}>关闭工程</button>
            </div>
            {projectFormInvalid && <div className="spx-field-error-summary" role="alert" aria-label="工程参数错误">
              参数有误：{Object.values(projectFormErrors).join('；')}
            </div>}
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
              <span>{compile.stepCount} 步 · {compile.dimension}D · {compile.projectName}</span>
              <details className="spx-compile-details">
                <summary>编译详情</summary>
                <dl>
                  <dt>Compile ID</dt><dd>{compile.compileId}</dd>
                  <dt>绑定 (binding)</dt><dd>{compile.bindingId}</dd>
                  <dt>Flow</dt><dd>{compile.flowId}</dd>
                </dl>
              </details>
            </div>}

            <div className="spx-inline-section" aria-label="编辑计算域">
              <div className="spx-form-title">编辑参数 · 计算域 / 网格</div>
              <div className="spx-field-grid">
                <div className="spx-field"><span>维度</span>
                  <div className="spx-segment">
                    <button type="button" className={project.dimension === 2 ? 'active' : ''} onClick={() => changeProjectDimension(2)}>2D</button>
                    <button type="button" className={project.dimension === 3 ? 'active' : ''} onClick={() => changeProjectDimension(3)}>3D</button>
                  </div>
                </div>
                {renderDomainField('gridDelta (µm)', 'gridDelta')}
                {renderDomainField('X extent (µm)', 'xExtent')}
                {project.dimension === 3 && renderDomainField('Y extent (µm)', 'yExtent')}
                <label className="spx-field"><span>{project.dimension === 3 ? '边界 XY' : '边界 X'}</span>
                  <select aria-label="工程边界类型"
                    value={String((project.dimension === 3 ? projectDomain.boundaryXY : projectDomain.xBoundary) ?? 'reflective')}
                    onChange={event => updateProjectDomain(project.dimension === 3
                      ? { boundaryXY: event.target.value }
                      : { xBoundary: event.target.value })}>
                    <option value="reflective">reflective · 反射</option>
                    <option value="periodic">periodic · 周期</option>
                  </select>
                </label>
              </div>
            </div>

            <div className="spx-inline-section" aria-label="编辑几何">
              <div className="spx-form-title">编辑参数 · 几何</div>
              {projectGeometrySource.kind === 'layout'
                ? <p className="spx-help">当前几何来自 GDS Clip「{projectGeometrySource.clipName ?? '未选择'}」；几何字段由 Clip 的图层→材料映射决定。</p>
                : <>
                  <div className="spx-geometry-picks">
                    {projectParametricKinds.map(kind => <button type="button" key={kind}
                      className={projectGeometryKind === kind ? 'active' : ''}
                      onClick={() => changeProjectGeometryKind(kind)}
                    >{kind === 'plane' ? 'Plane' : kind === 'trench' ? 'Trench' : kind === 'hole' ? 'Hole' : 'Fin'}</button>)}
                  </div>
                  <div className="spx-field-grid">
                    {projectGeometryKind === 'plane' && renderGeometryField('Plane height (µm)', 'height')}
                    {projectGeometryKind === 'trench' && <>
                      {renderGeometryField('Trench width (µm)', 'width')}
                      {renderGeometryField('Trench depth (µm)', 'depth')}
                    </>}
                    {projectGeometryKind === 'hole' && <>
                      {renderGeometryField('Hole radius (µm)', 'radius')}
                      {renderGeometryField('Hole depth (µm)', 'depth')}
                    </>}
                    {projectGeometryKind === 'fin' && <>
                      {renderGeometryField('Fin width (µm)', 'width')}
                      {renderGeometryField('Fin height (µm)', 'height')}
                    </>}
                    {['trench', 'hole', 'fin'].includes(projectGeometryKind) && <>
                      {renderGeometryField('Taper angle (deg)', 'taperAngle')}
                      {renderGeometryField('Mask height (µm)', 'maskHeight')}
                    </>}
                  </div>
                </>}
              {geometrySaveBlock && <p className="spx-field-error" role="alert">{geometrySaveBlock}</p>}
            </div>

            <div className="spx-inline-section" aria-label="编辑求解器">
              <div className="spx-form-title">编辑参数 · 求解器</div>
              <div className="spx-field-grid">
                {renderSolverField('Engine', 'enginePreference')}
                {renderSolverField('Seed', 'seed')}
                {renderSolverField('Timeout (s)', 'timeoutSeconds')}
                {renderSolverField('Rays per point', 'raysPerPoint')}
                {renderSolverField('CPU threads', 'cpuThreads')}
              </div>
            </div>

            <div className="spx-inline-section">
              <div className="spx-form-title">材料</div>
              {projectGeometryKind === 'gds' && <p className="spx-help">GDS 工程的基底材料由图层→材料映射决定，请在下方 Clip 编辑器设置。</p>}
              <div className="spx-field-grid" hidden={projectGeometryKind === 'gds'}>
                <label className="spx-field"><span>基底材料 substrateMaterial</span>
                  <select aria-label="工程基底材料" value={String(projectGeometry.substrateMaterial ?? 'Si')}
                    onChange={event => updateProjectMaterial('substrateMaterial', event.target.value)}>
                    {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                  </select>
                </label>
                {projectGeometryKind !== 'plane' && <label className="spx-field"><span>掩膜材料 maskMaterial</span>
                  <select aria-label="工程掩膜材料" value={String(projectGeometry.maskMaterial ?? 'Mask')}
                    onChange={event => updateProjectMaterial('maskMaterial', event.target.value)}>
                    {materialCatalog.map(row => <option key={row.name} value={row.name}>{row.name} · {row.category ?? 'Generic'}</option>)}
                  </select>
                </label>}
              </div>
              <p className="spx-help">材料来源 /api/materials，直接写入 ProjectV7 的 geometry 字段。</p>
            </div>
            <div className="spx-inline-section">
              <div className="spx-form-title">几何来源</div>
              <div className="spx-segment spx-source-pick">
                <button type="button" className={projectGeometrySource.kind === 'parametric' ? 'active' : ''}
                  onClick={chooseParametricGeometry}>参数化</button>
                <button type="button" className={projectGeometrySource.kind === 'layout' ? 'active' : ''} disabled={project.dimension === 3}
                  title={project.dimension === 3 ? 'GDS 版图几何仅支持 2D 工程' : '使用 GDS 版图 Clip 作为工程几何'}
                  onClick={chooseLayoutGeometry}>{project.dimension === 3 ? '🔒 ' : ''}GDS / Layout</button>
              </div>
              {project.dimension === 3 && <p className="spx-lock-note">3D 工程不支持 GDS 版图几何，图层材料映射仅用于 2D。</p>}
              {projectGeometrySource.kind === 'parametric'
                ? <div className="wf-muted">几何类型：{projectGeometryKind || '—'}</div>
                : <>
                  {!projectGeometrySource.clipId && <p className="spx-field-error" role="alert">先选择或保存一个 Clip 作为工程几何</p>}
                  {clips.length > 0 && <div className="spx-clip-summary-list" aria-label="已有 Clip">
                    {clips.map(clip => <button key={clip.clipId} type="button"
                      className={projectGeometrySource.clipId === clip.clipId ? 'active' : ''}
                      onClick={() => chooseClipForProject(clip)}
                    >
                      <strong>{clipLabel(clip)}</strong>
                      <span>{clip.layers.length} 层 · {clip.polygonCount} 多边形</span>
                      <span>x {clip.bbox.x0}…{clip.bbox.x1} · y {clip.bbox.y0}…{clip.bbox.y1} µm</span>
                    </button>)}
                  </div>}
                  {renderClipEditor('project', false)}
                </>}
            </div>
          </div>}

          <input ref={gdsInput} type="file" accept=".gds,.gdsii" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void uploadGds(file); }} />
          <input ref={projectInput} type="file" accept=".json" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void importProject(file); }} />
        </section>

        <section className={`wf-card wf-flow ${zone === 'flow' ? '' : 'wf-hidden'}`}>
          <div className="wf-card-head"><span>02</span><h2>流程编辑</h2></div>
          {!project && !libraryManagerOpen && <div className="wf-empty">先在 01 创建或导入工程；库管理不需要打开工程。</div>}
          {project && !libraryManagerOpen && renderFlowBar()}
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
                      ? '还没有流程条目。先在 02 保存流程模板，再到左树“库 → 流程”引用它。'
                      : libraryCategory === 'experiment'
                        ? '还没有实验模板。先在 03 完成一次测量生成配方，再到左树“库 → 实验”引用它。'
                        : '该分类暂无条目，可从左树“库”分类进入库管理新增。'}
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
                        ? <button type="button" className="spx-link" onClick={() => { navigateZone('experiment'); setActivityLog(previous => [...previous.slice(-79), `${clockTime()}  实验模板 ${item.name} 在 04 中使用`]); }}>去 04</button>
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
                    <thead><tr><th>#</th><th>步骤</th><th>模型</th><th>操作</th><th>材料</th><th>启用</th><th>结构</th></tr></thead>
                    <tbody>{authoredSteps.map((step, index) => {
                      const compiledStepId = compiledByAuthored.get(step.id);
                      const structureRow = compiledStepId
                        ? runStructureSteps.find(row => row.kind === 'step' && row.stepId === compiledStepId)
                        : undefined;
                      return (
                        <tr key={step.id}
                          className={`${selectedFlowStepId === step.id ? 'selected' : ''}${materialTarget && step.depositMaterial === materialTarget.material ? ' material-match' : ''}`}
                          onClick={() => setSelectedFlowStepId(step.id)}>
                          <td>{String(index + 1).padStart(2, '0')}</td>
                          <td>{step.name}</td>
                          <td>{STEP_LABELS[step.model] ?? step.model}</td>
                          <td>{step.operation === 'deposit' ? '沉积' : step.operation === 'etch' ? '刻蚀' : step.operation ?? '—'}</td>
                          <td>{step.depositMaterial ?? '—'}</td>
                          <td>{step.enabled ? '是' : '否'}</td>
                          <td className="wf-step-structure">
                            <button type="button" className="wf-link"
                              disabled={!structureRow}
                              title={structureRow ? `在 03 查看 ${step.name} 的结构` : '先执行包含该步骤的 Segment'}
                              onClick={event => {
                                event.stopPropagation();
                                if (!structureRow) return;
                                setZone('run');
                                setStructurePick(`${structureRow.runId}:${structureRow.stepId}`);
                              }}>
                              结构
                            </button>
                          </td>
                        </tr>
                      );
                    })}</tbody>
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
                {moduleStatus && <div className="wf-summary spx-banner" role="status"><strong>{moduleStatus}</strong><span>definition {activeDefinitionId || '—'}</span><BannerClose onClose={() => setModuleStatus('')} /></div>}
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
                {libraryStatus && <div className="wf-summary spx-banner" role="status"><strong>{libraryStatus}</strong><span>{libraryBundles.length} 个本地 bundle</span><BannerClose onClose={() => setLibraryStatus('')} /></div>}
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
            {structureSelection && <span>结构：{structureSelection.label}</span>}
            {routeStale && <span className="wf-rerun">流程已变更，现有 Segment 待重跑</span>}
          </div>}

          <div className="wf-structure-panel" aria-label="当前 Run 结构">
            <div className="wf-structure-head">
              <strong>结构</strong>
              {structureSelection
                ? <span className="wf-structure-step">当前：{structureSelection.label}</span>
                : <span className="wf-muted">{route ? '执行 Segment 后生成结构时间轴' : '尚未创建 Route'}</span>}
              <span className="wf-muted">{structureDimension}D · µm</span>
            </div>
            {runStructureSteps.length > 0 && <div className="wf-structure-timeline" role="tablist" aria-label="结构步骤时间轴">
              {runStructureSteps.map((row, index) => {
                const key = `${row.runId}:${row.stepId}`;
                const active = key === structurePick;
                return <Fragment key={key}>
                  {index > 0 && <span className="wf-step-chip-sep" aria-hidden="true">→</span>}
                  <button type="button" role="tab" aria-selected={active}
                    className={`wf-step-chip${active ? ' active' : ''}${row.kind === 'initial' ? ' initial' : ''}`}
                    data-step-id={row.stepId} data-run-id={row.runId} data-segment-id={row.segmentId}
                    title={`${row.label} · ${row.runId}`}
                    onClick={() => setStructurePick(key)}>
                    <span className="wf-step-chip-index">{String(index + 1).padStart(2, '0')}</span>
                    <span className="wf-step-chip-label">{row.label}</span>
                  </button>
                </Fragment>;
              })}
            </div>}
            {structureSelection && structureDimension === 3
              ? <div className="wf-structure-body wf-structure-body--3d">
                <div className="wf-structure-3d">
                  <WorkflowViewer3D
                    runId={structureSelection.runId}
                    stepId={structureSelection.stepId}
                    dimension={3}
                    height={420}
                    stepIds={structureStepIds}
                    onStats={setStructureStats}
                  />
                  <div className="wf-structure-3d-stats" aria-label="三维结构统计" role="status">
                    {structureStatsForStep
                      ? <>
                        <span>三角形 <code>{structureStatsForStep.triangles.toLocaleString('en-US')}</code></span>
                        <span>点 <code>{structureStatsForStep.points.toLocaleString('en-US')}</code></span>
                        <span>解析耗时 <code>{structureStatsForStep.parseMs} ms</code></span>
                        {structureStatsForStep.simplified && <span className="warn">简化 {Number(structureStatsForStep.sourceTriangles ?? structureStatsForStep.triangles).toLocaleString('en-US')} → {structureStatsForStep.triangles.toLocaleString('en-US')} 面</span>}
                      </>
                      : <span className="wf-muted">等待三维统计…</span>}
                  </div>
                </div>
                <div className="wf-structure-side">
                  <div className="wf-structure-info">
                    <span className="wf-muted">步骤</span>
                    <strong>{structureSelection.label}</strong>
                    <code>{structureSelection.stepId}</code>
                    <span className="wf-muted" title={structureSelection.runId}>Segment {structureSelection.segmentIndex + 1} · Run 已记录</span>
                  </div>
                  {measurement && <div className="wf-structure-info">
                    <span className="wf-muted">测量值</span>
                    <strong>{measurement.value === null ? measurement.reason ?? '无值' : `${formatMicrons(measurement.value)} ${displayUnit(measurement.unit)}`}</strong>
                    <span className="wf-muted">测量位置在 2D 视图显示</span>
                  </div>}
                  {!measurement && <div className="wf-muted">测量后在 2D 视图显示位置</div>}
                </div>
              </div>
              : structureSelection && structureView
                ? <div className="wf-structure-body">
                  <StructureCanvas
                    view={structureView}
                    frame={structureFrame(structureView, 'xy')}
                    axis="xy"
                    marker={measurement ? measurementMark : null}
                    note={structureNote}
                    label="当前 Run 结构视图"
                  />
                  <div className="wf-structure-side">
                    <div className="spx-field">
                      <span>视图轴</span>
                      <code className="wf-axis-chip" aria-label="结构视图轴">X – 高度</code>
                    </div>
                    <div className="wf-structure-stats">
                      <span>拓扑</span><code>{topologyLabel(structureView.topology)}</code>
                      <span>点</span><code>{structureView.pointCount}</code>
                      <span>线</span><code>{structureView.lineCount}</code>
                      <span>三角形</span><code>{structureView.triangleCount}</code>
                      <span>单位</span><code>{structureUnit(structureView)}</code>
                      <span>X 范围</span><code>{structureViewExtent ? `${formatNumber(structureViewExtent.minU)} … ${formatNumber(structureViewExtent.maxU)}` : '—'}</code>
                      <span>高度范围</span><code>{structureViewExtent
                        ? structureViewExtent.flatV
                          ? `${formatMicrons(structureViewExtent.maxV)}（平坦）`
                          : `${formatMicrons(structureViewExtent.minV)} … ${formatMicrons(structureViewExtent.maxV)}`
                        : '—'}</code>
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
                    {structureSelection
                      ? structureLoading
                        ? '结构快照加载中…'
                        : structureError
                          ? `暂无结构快照：${structureError}`
                          : '结构视图准备中…'
                      : segment?.status === 'running'
                        ? 'Segment 运行中，完成后显示结构。'
                        : route
                          ? '执行下方的「执行当前 Segment」后，这里按「初始结构 → 每个工艺步骤」预览结构。'
                          : '在 02 编译流程并创建 Route 后，这里显示每个工艺步骤的结构。'}
                  </div>
                </div>}
          </div>

          {segment && <>
            <div className="wf-status-row">
              <span className={`wf-status ${segment.status}`}>{statusLabel(segment.status)}</span>
              <code title={segment.runId ?? ''}>{segment.runId ? 'Run 已记录' : '尚未运行'}</code>
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
                {route.segments.filter(item => item.attemptId === attempt.attemptId).map(item => {
                  const structureRow = runStructureSteps.find(row => row.runId === item.runId && row.stepId === item.throughStepId);
                  return <div key={item.segmentId} className="wf-segment-row">
                    <span className={`wf-status ${item.status}`}>{statusLabel(item.status)}</span>
                    <code>{item.startAfterStepId ? `自 ${stepDisplayName(item.startAfterStepId)} 之后` : '从首步'} → {stepDisplayName(item.throughStepId)}</code>
                    {(item.status === 'planned' && routeStale) || (item.status === 'succeeded' && routeStale) ? <span className="wf-rerun">待重跑</span> : null}
                    <button type="button" className="wf-link wf-segment-structure"
                      disabled={!structureRow}
                      title={structureRow ? `查看 ${stepDisplayName(item.throughStepId)} 的结构` : '先执行该 Segment'}
                      onClick={() => { if (structureRow) setStructurePick(`${structureRow.runId}:${structureRow.stepId}`); }}>
                      查看结构
                    </button>
                  </div>;
                })}
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
              <span>{measurement.value === null ? measurement.reason ?? '无值' : `${formatMicrons(measurement.value)} ${displayUnit(measurement.unit)}`}</span>
              <span>绑定 (binding)：{bindingLabel(measurement.bindingStatus)} · 等价 (equivalence)：{bindingLabel(measurement.equivalenceStatus)}</span>
            </div>}
            {metrics.length > 0 && <table className="wf-table"><thead><tr><th>状态</th><th>数值</th><th>配方 / 步骤</th><th>绑定 / 等价</th><th /></tr></thead><tbody>{metrics.map(item => <tr key={item.measurementId}><td>{statusLabel(item.status)}</td><td>{item.value === null ? item.reason : `${formatMicrons(item.value)} ${displayUnit(item.unit)}`}</td><td>{item.recipeId} v{item.recipeVersion} · {item.method} · {item.material} · {stepDisplayName(item.sourceStepId ?? '')}</td><td>{bindingLabel(item.bindingStatus)} / {bindingLabel(item.equivalenceStatus)}</td><td><button className="wf-link" aria-label={`查看 ${item.measurementId} 证据`} onClick={() => void showMetricDetail(item)}>详情</button></td></tr>)}</tbody></table>}
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
                    <td>{item.measuredValue.toFixed(4)} {displayUnit(item.unit)}<br /><code className="wf-mono">{item.measurementId.slice(0, 12)}</code><br /><span className="wf-muted">绑定 {bindingLabel(item.measurementBindingStatus)}</span></td>
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

          {experimentDoc && <div className="wf-result-panel" aria-label="实验结果">
            <div className="wf-result-head">
              <strong>{experimentDoc.name}</strong>
              <span className={`wf-status ${experimentDoc.status}`}>{statusLabel(experimentDoc.status)}</span>
              <span className="wf-muted">实验状态：{statusLabel(experimentDoc.status)}{experimentDoc.kind === 'doe' ? ' · DOE' : ''}</span>
              <span className="wf-muted">{experimentDoc.variants.length} 变体 · 配方 v{experimentDoc.recipeVersion}</span>
              {experimentDoc.design && <span className="wf-muted" aria-label="DOE 设计摘要">
                {experimentDoc.design.conditionCount} 条件 × {experimentDoc.design.replicates} 重复 = {experimentDoc.design.logicalRunCount} 逻辑执行
              </span>}
              <span className="wf-spacer" />
              <button className={experimentRunCount > 0 ? 'wf-button' : 'wf-button primary'} disabled={!!busy} onClick={() => void runPersistentExperiment()}>
                {busy === 'experiment' ? '实验运行中…' : experimentRunCount > 0 ? '重新运行实验' : '运行实验'}
              </button>
              <button className={experimentRunCount > 0 ? 'wf-button primary' : 'wf-button'} disabled={experimentRunCount === 0 || !!busy} onClick={() => navigateZone('analyze')}>在 05 分析</button>
            </div>
            {experimentDoc.queue && <div className="wf-summary">
              <strong>队列</strong>
              <span>{experimentDoc.queue.succeeded}/{experimentDoc.queue.total} 已成功 · 待处理 {experimentDoc.queue.pending} · 运行中 {experimentDoc.queue.running}</span>
              <span>预计剩余 {experimentDoc.queue.estimatedSecondsRemaining} s</span>
              <span>{experimentDoc.queue.estimatedCostUsd > 0
                ? `成本 $${experimentDoc.queue.estimatedCostUsd.toFixed(4)}`
                : '本地 CPU · 无云成本'}</span>
              <span>重试 {experimentDoc.queue.retries}</span>
            </div>}
            {experimentResultRows.length > 0 ? <table className="wf-table wf-result-table" aria-label="实验结果表">
              <thead><tr><th>变体</th><th>条件</th><th>重复结果（每次）</th><th>均值（{displayUnit(experimentResultRows[0].unit)}）</th><th>标准差（{displayUnit(experimentResultRows[0].unit)}）</th><th>状态</th></tr></thead>
              <tbody>{experimentResultRows.map(row => <tr key={row.ordinal}>
                <td>V{row.ordinal}</td>
                <td className="wf-condition">{row.condition}</td>
                <td><div className="wf-repeats">{row.replicates.map((replicate, index) => <span className="wf-repeat" key={`${row.ordinal}-${index}`}>
                  <span>#{index + 1}</span>
                  <code>{formatNumber(replicate.value)}</code>
                  <span>{displayUnit(replicate.unit ?? row.unit)}</span>
                  <span className={`wf-status ${replicate.status}`}>{statusLabel(replicate.status)}</span>
                </span>)}</div></td>
                <td>{formatNumber(row.mean)}</td>
                <td>{formatNumber(row.std)}</td>
                <td><span className={`wf-status ${row.status}`}>{statusLabel(row.status)}</span></td>
              </tr>)}</tbody>
            </table> : <div className="wf-empty">实验已创建，点击“运行实验”后在这里显示每个变体的重复测量与统计。</div>}
          </div>}

          {!experimentReady && <div className="wf-empty" aria-label="实验空态">
            <strong>还不能创建实验</strong>
            <span>{experimentBlockReason}</span>
            <div className="wf-empty-actions">
              {!project && <button className="wf-button primary" onClick={() => navigateZone('setup')}>去 01 新建工程</button>}
              {project && (!compile || compileStale) && <button className="wf-button primary" onClick={() => navigateZone('flow')}>去 02 编译流程</button>}
              {project && compile && !compileStale && !activeRecipeId && <button className="wf-button primary" onClick={() => navigateZone('run')}>去 03 运行测量</button>}
            </div>
          </div>}

          {renderExperimentList()}

          {experimentReady && <details className="spx-advanced wf-experiment-inputs" open={experimentInputsOpen}
            onToggle={event => setExperimentInputsOpen((event.currentTarget as HTMLDetailsElement).open)}>
            <summary>实验输入（名称 / 变体 / DOE 因子）</summary>
            <div className="spx-advanced-body">
              <div className="spx-field-grid">
                <label className="spx-field span-2"><span>实验名称</span>
                  <input aria-label="实验名称" value={experimentName} onChange={event => setExperimentName(event.target.value)} />
                </label>
                <label className="spx-field"><span>变体数（1–16）</span>
                  <input aria-label="变体数" type="number" min={1} max={16} value={experimentRows.length} onChange={event => resizeVariants(Number(event.target.value))} />
                </label>
                <label className="spx-field"><span>可调步骤（duration）</span>
                  <select aria-label="实验可调步骤" value={experimentTargetStep?.id ?? ''} onChange={event => setExperimentStepId(event.target.value)}>
                    {(project?.steps ?? []).filter(step => step.enabled).map((step, index) => <option key={step.id} value={step.id}>{stepOptionLabel(step, index)}</option>)}
                  </select>
                </label>
                <label className="spx-field"><span>失败重试（1–4）</span>
                  <input aria-label="实验失败重试" type="number" min={1} max={4} value={experimentMaxAttempts} onChange={event => setExperimentMaxAttempts(Number(event.target.value) || 1)} />
                </label>
                <label className="spx-field"><span>单次成本（USD）</span>
                  <input aria-label="实验单次成本" type="number" min={0} step="0.01" value={experimentCost} onChange={event => setExperimentCost(event.target.value)} />
                </label>
              </div>

              <div className="wf-card-subhead">变体条件 · 作用于 {experimentTargetStep?.name ?? '—'} 的 duration</div>
              <table className="wf-table wf-variant-table" aria-label="变体输入表">
                <thead><tr><th>#</th><th>条件摘要</th><th>duration（s）</th><th>重复数（1–8）</th><th>seed</th><th>操作</th></tr></thead>
                <tbody>{experimentRows.map((row, index) => <tr key={index}>
                  <td>V{index + 1}</td>
                  <td className="wf-condition">V{index + 1} · {formatNumber(Number(row.duration))} s · seed {row.seed} · ×{row.replicates}</td>
                  <td><input aria-label={`变体 ${index + 1} duration`} value={row.duration} onChange={event => updateVariantRow(index, { duration: event.target.value })} /></td>
                  <td><input aria-label={`变体 ${index + 1} replicates`} type="number" min={1} max={8} value={row.replicates} onChange={event => updateVariantRow(index, { replicates: Number(event.target.value) || 1 })} /></td>
                  <td><input aria-label={`变体 ${index + 1} seed`} type="number" value={row.seed} onChange={event => updateVariantRow(index, { seed: event.target.value })} /></td>
                  <td><button type="button" className="wf-link" disabled={experimentRows.length <= 1} onClick={() => removeVariantRow(index)}>移除</button></td>
                </tr>)}</tbody>
              </table>
              <div className="spx-run-actions">
                <button className="wf-button ghost" disabled={experimentRows.length >= 16} onClick={() => resizeVariants(experimentRows.length + 1)}>＋ 添加变体</button>
                <button className="wf-button primary" disabled={!!busy} onClick={() => void createPersistentExperiment()}>{busy === 'experiment' ? '创建中…' : '创建实验'}</button>
                <span className="wf-muted">{experimentRows.length} 变体 × 平均 {formatNumber(experimentRows.reduce((total, row) => total + row.replicates, 0) / Math.max(1, experimentRows.length), 3)} 次重复</span>
              </div>

              <div className="wf-card-subhead">DOE 全因子</div>
              <p className="wf-muted">每个因子 2–10 个取值（逗号分隔），1–5 个因子；组合数 = 各因子取值数相乘。</p>
              <div className="wf-doe-factors">
                {doeFactors.map((row, index) => <div className={`wf-doe-factor${doeFactorErrors[index] ? ' has-error' : ''}`} key={index}>
                  <label className="spx-field"><span>因子 {index + 1} 步骤</span>
                    <select aria-label={`因子 ${index + 1} 步骤`} value={row.stepId} onChange={event => setDoeFactors(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, stepId: event.target.value } : item))}>
                      {(project?.steps ?? []).filter(step => step.enabled).map((step, stepIndex) => <option key={step.id} value={step.id}>{stepOptionLabel(step, stepIndex)}</option>)}
                    </select>
                  </label>
                  <label className="spx-field"><span>参数</span>
                    <select aria-label={`因子 ${index + 1} 参数`} value={row.parameter} onChange={event => setDoeFactors(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, parameter: event.target.value } : item))}>
                      {DOE_PARAMETERS.map(parameter => <option key={parameter.id} value={parameter.id}>{parameter.label}</option>)}
                    </select>
                  </label>
                  <label className="spx-field"><span>取值（2–10 个，逗号分隔）</span>
                    <input aria-label={`因子 ${index + 1} 取值`} value={row.values}
                      aria-invalid={doeFactorErrors[index] ? 'true' : undefined}
                      onChange={event => setDoeFactors(previous => previous.map((item, itemIndex) => itemIndex === index ? { ...item, values: event.target.value } : item))} />
                  </label>
                  <button type="button" className="wf-link" disabled={doeFactors.length <= 1} onClick={() => setDoeFactors(previous => previous.filter((_, itemIndex) => itemIndex !== index))}>移除</button>
                  {doeFactorErrors[index] && <div className="wf-field-error" role="alert" data-factor={index + 1}>{doeFactorErrors[index]}</div>}
                </div>)}
              </div>
              <div className="spx-run-actions">
                <button className="wf-button ghost" disabled={doeFactors.length >= 5} onClick={addDoeFactor}>＋ 添加因子</button>
              </div>
              <div className="spx-field-grid">
                <label className="spx-field"><span>DOE 重复数（1–8）</span>
                  <input aria-label="DOE 重复数" type="number" min={1} max={8} value={doeReplicates} onChange={event => setDoeReplicates(Number(event.target.value) || 1)} />
                </label>
                <label className="spx-field"><span>随机种子</span>
                  <input aria-label="DOE 种子" value={doeSeed} onChange={event => setDoeSeed(event.target.value)} />
                </label>
                <label className="spx-field"><span>单次成本（USD）</span>
                  <input aria-label="单次成本" type="number" min={0} step="0.01" value={doeCost} onChange={event => setDoeCost(event.target.value)} />
                </label>
              </div>
              <div className="spx-run-actions">
                <button className="wf-button primary" disabled={!!busy || doeBlocked}
                  title={doeBlocked ? '有重复或无效的因子，请先修正高亮的行' : '按全因子组合设计 DOE'}
                  onClick={() => void createDoe()}>设计 DOE</button>
              </div>
              {doeBlocked && <div className="wf-block-reason" aria-label="DOE 禁用原因">
                先修正因子：{doeFactorErrors.find(Boolean)}
              </div>}
              {experimentDoc?.design && <div className="wf-summary"><strong>DOE</strong><span>{experimentDoc.design.conditionCount} 条件 × {experimentDoc.design.replicates} 重复 = {experimentDoc.design.logicalRunCount} 逻辑执行</span></div>}
              {experimentDoc?.kind === 'doe' && <div className="spx-field-grid">
                <label className="spx-field"><span>均值下限（{displayUnit(experimentResultRows[0]?.unit)}）</span>
                  <input aria-label="筛选最小均值" value={tableMin} onChange={event => setTableMin(event.target.value)} placeholder="均值 ≥" />
                </label>
                <label className="spx-field"><span>均值上限（{displayUnit(experimentResultRows[0]?.unit)}）</span>
                  <input aria-label="筛选最大均值" value={tableMax} onChange={event => setTableMax(event.target.value)} placeholder="均值 ≤" />
                </label>
                <label className="spx-field"><span>排序字段</span>
                  <select aria-label="排序字段" value={tableSort} onChange={event => setTableSort(event.target.value)}>
                    <option value="ordinal">#</option>
                    <option value="mean">均值</option>
                    <option value="std">标准差</option>
                    <option value="n">样本数</option>
                  </select>
                </label>
              </div>}
              {experimentDoc?.kind === 'doe' && <div className="spx-run-actions">
                <button className="wf-button ghost" disabled={!!busy} onClick={() => void loadExperimentTable()}>筛选 / 排序</button>
                <a className="wf-button ghost" href={`/api/v1/workflow/experiments/${experimentDoc.experimentId}/export.csv`} download>导出 CSV</a>
              </div>}
              {experimentTable && <table className="wf-table"><thead><tr><th>#</th><th>样本数</th><th>均值</th><th>标准差</th><th>CI95</th><th>统计状态</th></tr></thead><tbody>{experimentTable.rows.map(row => <tr key={row.ordinal}><td>{row.ordinal}</td><td>{row.n}</td><td>{formatNumber(row.mean)}</td><td>{formatNumber(row.std)}</td><td>{formatNumber(row.ci95)}</td><td>{statusLabel(row.statisticalStatus)}</td></tr>)}</tbody></table>}

              {experimentDoc && <details className="spx-advanced">
                <summary>高级：保留策略</summary>
                <div className="spx-advanced-body">
                  <div className="spx-run-actions">
                    <button className="wf-button ghost" disabled={!!busy} onClick={() => void previewRetention()}>预览释放</button>
                    <button className="wf-button ghost" disabled={!!busy} onClick={() => void applyRetention('release-runs')}>应用 release-runs</button>
                    <button className="wf-button ghost" disabled={!!busy} onClick={() => void applyRetention('keep-all')}>恢复 keep-all</button>
                    <span className="wf-muted">当前 {experimentDoc.retention?.mode ?? 'keep-all'}</span>
                  </div>
                  {retentionInfo && <div className="wf-summary"><strong>保留策略</strong><span>可释放 {retentionInfo.releasable.length} 个 Run</span><span>受保护 {retentionInfo.protected.length} 个 Run</span></div>}
                </div>
              </details>}
            </div>
          </details>}
        </section>}
        {zone === 'analyze' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>05</span><h2>分析</h2></div>

          {!analysisReady && <div className="wf-empty" aria-label="分析空态">
            <strong>还没有可分析的结果</strong>
            <span>{!project
              ? '先在 01 新建、打开或导入工程。'
              : !compile
                ? '先在 02 编译流程。'
                : '先在 04 创建并运行实验，或先在 03 完成一次运行与测量。'}</span>
            <div className="wf-empty-actions">
              {!project && <button className="wf-button primary" onClick={() => navigateZone('setup')}>去 01 新建工程</button>}
              {project && !compile && <button className="wf-button primary" onClick={() => navigateZone('flow')}>去 02 编译流程</button>}
              <button className="wf-button primary" onClick={() => navigateZone('experiment')}>去 04 创建实验</button>
              <button className="wf-button ghost" onClick={() => navigateZone('run')}>去 03 运行测量</button>
            </div>
          </div>}

          {analysisReady && <>
            {measurement && <div className="wf-summary" aria-label="测量结果">
              <strong>{statusLabel(measurement.status)}</strong>
              <span>{measurement.value === null ? (measurement.reason || '无数值') : `${formatNumber(measurement.value)} ${displayUnit(measurement.unit)}`}</span>
              <span>绑定：{bindingLabel(measurement.bindingStatus)} · 等价：{bindingLabel(measurement.equivalenceStatus)}</span>
              <span>配方 v{measurement.recipeVersion}{measurement.createdAt ? ` · ${formatDateTime(measurement.createdAt)}` : ''}</span>
            </div>}

            {experimentDoc && <div className="wf-analysis-conditions" aria-label="实验条件">
              <div className="wf-result-head">
                <strong>{experimentDoc.name}</strong>
                <span className={`wf-status ${experimentDoc.status}`}>{statusLabel(experimentDoc.status)}</span>
                <span className="wf-muted">{experimentDoc.variants.length} 变体 · 配方 v{experimentDoc.recipeVersion}{experimentDoc.kind === 'doe' ? ' · DOE' : ''}</span>
              </div>
              <div className="wf-condition-chips">
                {experimentConditionRows.map(row => <span className="wf-condition-chip" key={row.ordinal}>
                  <strong>{row.label}</strong>
                  <span className={`wf-status ${row.status}`}>{statusLabel(row.status)}</span>
                </span>)}
              </div>
            </div>}

            <div className="wf-card-subhead">候选 / 多视图</div>
            <div className="spx-run-actions">
              <button className="wf-button ghost" disabled={!experimentDoc || !!busy} onClick={loadExperimentCandidates}>刷新候选</button>
              <button className="wf-button ghost" disabled={!hasExecutedRun || !!busy} onClick={addCurrentCandidate}>加入当前 Run</button>
              <button className="wf-button primary" disabled={compareCandidates.length < 2 || !!busy}
                title={compareCandidates.length < 2 ? '先选择至少 2 个候选' : '比较所选候选并加载结构视图'}
                onClick={() => void runComparison()}>
                {busy === 'compare' ? '比较中…' : '比较并加载视图'}
              </button>
              {autoAnalysisId && autoAnalysisId === experimentDoc?.experimentId && <span className="wf-muted">已自动载入该实验的候选</span>}
            </div>
            {compareCandidates.length < 2 && <div className="wf-block-reason" aria-label="比较禁用原因">
              先选择至少 2 个候选：点击“刷新候选”载入实验变体，或“加入当前 Run”添加一个对照。
            </div>}

            {compareCandidates.length > 0 && <table className="wf-table" aria-label="候选表">
              <thead><tr><th>候选</th><th>步骤</th><th>状态</th><th>来源</th></tr></thead>
              <tbody>{compareCandidates.map(candidate => {
                const variant = experimentDoc?.variants.find(item =>
                  item.replicates.some(replicate => replicate.runId === candidate.runId));
                return <tr key={`${candidate.runId}:${candidate.stepId}`} title={`${candidate.runId} / ${candidate.stepId}`}>
                  <td>{candidate.label}</td>
                  <td>{stepDisplayName(candidate.stepId)}</td>
                  <td><span className={`wf-status ${variant ? experimentVariantStatus(variant) : 'succeeded'}`}>
                    {variant ? statusLabel(experimentVariantStatus(variant)) : '已记录'}
                  </span></td>
                  <td title={candidate.runId}>{variant ? '实验 Run' : '当前 Run'}</td>
                </tr>;
              })}</tbody>
            </table>}

            {Object.keys(candidateViews).length > 0 && <div className="wf-compare-controls">
              <label>同步缩放 <input aria-label="同步缩放" type="range" min="0.5" max="3" step="0.1" value={syncZoom} onChange={event => setSyncZoom(Number(event.target.value))} /></label>
              {candidateViewsHave3D
                ? <label>视图轴
                  <select aria-label="同步轴" value={syncAxis} onChange={event => setSyncAxis(event.target.value as typeof syncAxis)}>
                    <option value="xy">XY</option>
                    <option value="xz">XZ</option>
                    <option value="yz">YZ</option>
                  </select>
                </label>
                : <span className="wf-axis-chip" aria-label="同步轴">X – 高度</span>}
              <span className="wf-muted">单位 {displayUnit(candidateUnit)} · 所有候选共用同一坐标范围</span>
            </div>}

            {compareCandidates.length > 0 && <div className="wf-compare-grid">
              {compareCandidates.map((candidate, index) => {
                const view = candidateViews[`${candidate.runId}:${candidate.stepId}`];
                const firstLabel = comparison?.candidates[0]?.label ?? compareCandidates[0]?.label;
                const pairRow = index > 0
                  ? comparison?.pairwise.find(row =>
                    (row.left === candidate.label && row.right === firstLabel) || (row.right === candidate.label && row.left === firstLabel))
                  : undefined;
                return <figure key={`${candidate.runId}:${candidate.stepId}`} title={`${candidate.runId} / ${candidate.stepId}`}>
                  <figcaption>
                    <strong>{candidate.label}</strong>
                    <span>{view ? `${view.dimension}D · ${topologyLabel(view.topology)} · ${view.dimension === 2 ? 'X–高度' : syncAxis.toUpperCase()} · ${structureUnit(view)}` : '视图加载中…'}</span>
                    {pairRow && <span>与 {firstLabel} 最大点偏差 {formatNumber(pairRow.maxPointDelta)} µm · 边界偏差 {formatNumber(pairRow.boundsDelta)} µm</span>}
                  </figcaption>
                  {view && analysisFrame ? <StructureCanvas
                    view={view}
                    frame={analysisFrame}
                    axis={syncAxis}
                    note={view.dimension === 2 ? structureSurfaceNote(view, syncAxis) : null}
                    className="wf-compare-canvas"
                    label={`候选 ${index + 1} 结构视图`}
                  /> : <div className="wf-empty">视图加载中…</div>}
                  {view && <div className="wf-compare-legend">
                    {structureLegend(view).map(row => <span key={row.id}><i style={{ background: row.color }} aria-hidden="true" />{row.name}<em>{row.count}</em></span>)}
                  </div>}
                </figure>;
              })}
            </div>}

            {comparison && <table className="wf-table" aria-label="候选对比表">
              <thead><tr><th>对比</th><th>状态</th><th>最大点偏差（µm）</th><th>边界偏差（µm）</th><th>材料偏差</th><th>原因</th></tr></thead>
              <tbody>{comparison.pairwise.map((row, index) => <tr key={index}>
                <td>{row.left} ↔ {row.right}</td>
                <td><span className={`wf-status ${row.status}`}>{toleranceLabel(row.status)}</span></td>
                <td>{formatNumber(row.maxPointDelta)}</td>
                <td>{formatNumber(row.boundsDelta)}</td>
                <td>{materialDeltaText(row.materialDelta)}</td>
                <td>{row.reasons.map(reason => compareReasonLabel(reason)).join('；') || '—'}</td>
              </tr>)}</tbody>
            </table>}

            <div className="wf-card-subhead">测量指标</div>
            {analysisMetrics.length > 0
              ? <table className="wf-table" aria-label="测量指标表">
                <thead><tr><th>Run</th><th>步骤</th><th>方法</th><th>数值</th><th>单位</th><th>绑定状态</th></tr></thead>
                <tbody>{analysisMetrics.map(item => <tr key={item.measurementId} title={item.measurementId}>
                  <td title={item.sourceRunId ?? undefined}>{runDisplayLabel(item.sourceRunId)}</td>
                  <td>{item.sourceStepId ? stepDisplayName(item.sourceStepId) : '—'}</td>
                  <td>{metricMethodLabel(item.method)}</td>
                  <td>{item.value === null ? (item.reason ?? '—') : formatNumber(item.value)}</td>
                  <td>{displayUnit(item.unit)}</td>
                  <td><span className={`wf-status ${item.bindingStatus === 'verified' ? 'succeeded' : 'planned'}`}>{bindingLabel(item.bindingStatus)}</span></td>
                </tr>)}</tbody>
              </table>
              : <div className="wf-empty" aria-label="测量指标空态">
                <strong>还没有测量记录</strong>
                <span>在 03 执行 Segment 后点击“测量”，这里会列出每次测量的数值与绑定状态。</span>
              </div>}

            <details className="spx-advanced wf-analysis-advanced">
              <summary>高级：Process Window（工艺窗口）与基础标定</summary>
              <div className="spx-advanced-body">
                <div className="wf-card-subhead">Process Window（工艺窗口）</div>
                <p className="wf-muted">窗口按测量单位（{displayUnit(experimentDoc?.unit)}）给出；每个条件至少 2 次重复才能评估置信区间。</p>
                {!experimentDoc && <div className="wf-muted">需要实验数据：先在 04 创建并运行实验。</div>}
                {experimentDoc && experimentRunCount === 0 && <div className="wf-muted">实验尚未运行，窗口无法计算。</div>}
                <div className="spx-field-grid">
                  <label className="spx-field"><span>窗口下限（{displayUnit(experimentDoc?.unit)}）</span>
                    <input aria-label="窗口下限" ref={windowLowerRef} value={windowLower}
                      aria-invalid={windowError ? 'true' : undefined}
                      onChange={event => { setWindowLower(event.target.value); setWindowError(''); }} placeholder="下限" />
                  </label>
                  <label className="spx-field"><span>窗口上限（{displayUnit(experimentDoc?.unit)}）</span>
                    <input aria-label="窗口上限" ref={windowUpperRef} value={windowUpper}
                      aria-invalid={windowError ? 'true' : undefined}
                      onChange={event => { setWindowUpper(event.target.value); setWindowError(''); }} placeholder="上限" />
                  </label>
                  <div className="wf-field-action">
                    <button className="wf-button primary"
                      disabled={!experimentDoc || experimentRunCount === 0 || !!busy}
                      title={windowBlockedReason || '按当前实验的重复测量计算工艺窗口'}
                      onClick={() => void computeProcessWindow()}>计算窗口</button>
                  </div>
                  {windowError && <div className="wf-field-error span-2" role="alert" aria-label="窗口校验错误">{windowError}</div>}
                </div>
                {windowBlockedReason && <div className="wf-block-reason" aria-label="窗口禁用原因">{windowBlockedReason}</div>}
                {windowResult && <>
                  <div className="wf-summary"><strong>工艺窗口 [{formatNumber(windowResult.lower)}, {formatNumber(windowResult.upper)}] {displayUnit(experimentDoc?.unit)}</strong>
                    <span>通过 {windowResult.summary.pass} · 超出 {windowResult.summary.fail} · 不确定 {windowResult.summary.uncertain} · 未评估 {windowResult.summary.notEvaluated}</span>
                  </div>
                  <table className="wf-table" aria-label="工艺窗口表"><thead><tr><th>变体</th><th>均值</th><th>CI95</th><th>状态</th><th>余量</th></tr></thead><tbody>{windowResult.rows.map(row => <tr key={row.ordinal}>
                    <td>V{row.ordinal}</td>
                    <td>{formatNumber(row.mean)}</td>
                    <td>{formatNumber(row.ci95)}</td>
                    <td><span className={`wf-status ${row.status}`}>{statusLabel(row.status)}</span></td>
                    <td>{formatNumber(windowResult.candidates.find(item => item.ordinal === row.ordinal)?.margin ?? null)}</td>
                  </tr>)}</tbody></table>
                  {windowResult.factorWindows.length > 0 && <div className="wf-muted">因子窗口：{windowResult.factorWindows.map(item => `${item.parameter} [${formatNumber(item.min)}, ${formatNumber(item.max)}]`).join(' · ')}</div>}
                </>}

                <div className="wf-card-subhead">基础标定</div>
                <label className="spx-field"><span>标定数据 CSV</span>
                  <input aria-label="标定 CSV 文件" type="file" accept=".csv,text/csv" onChange={event => { const file = event.target.files?.[0]; if (file) void loadCalibrationFile(file); }} />
                </label>
                <div className="spx-field-grid">
                  <label className="spx-field"><span>模型</span>
                    <select aria-label="标定模型" value={calibrationModel} onChange={event => setCalibrationModel(event.target.value)}>
                      <option value="scale">scale (1p)</option>
                      <option value="scale_offset">scale + offset (2p)</option>
                      <option value="quadratic">quadratic (3p)</option>
                      <option value="power">power (2p)</option>
                      <option value="power_offset">power + offset (3p)</option>
                    </select>
                  </label>
                  <label className="spx-field"><span>方法</span>
                    <select aria-label="标定方法" value={calibrationMethod} onChange={event => setCalibrationMethod(event.target.value as typeof calibrationMethod)}>
                      <option value="least-squares">least-squares</option>
                      <option value="nelder-mead">nelder-mead</option>
                    </select>
                  </label>
                </div>
                {!calibrationCsv && <div className="wf-block-reason" aria-label="标定禁用原因">先选择标定 CSV，再创建数据集并拟合。</div>}
                <div className="spx-run-actions">
                  <button className="wf-button ghost" disabled={!calibrationCsv || !!busy}
                    title={calibrationCsv ? '用所选 CSV 创建标定数据集' : '先选择标定 CSV'}
                    onClick={() => void createCalibrationDataset()}>创建数据集</button>
                  <button className="wf-button primary" disabled={!calibrationDataset || !!busy}
                    title={!calibrationCsv ? '先选择标定 CSV' : !calibrationDataset ? '先创建数据集' : '按所选模型与方法拟合'}
                    onClick={() => void fitCalibration()}>拟合</button>
                </div>
                {calibrationDataset && <div className="wf-muted">数据集 {calibrationDataset.name} · 训练 {calibrationDataset.trainCount} / 验证 {calibrationDataset.validationCount} · {calibrationDataset.sha256.slice(0, 12)}…</div>}
                {calibrationFit && <>
                  <div className="wf-summary"><strong>{calibrationFit.model} / {calibrationFit.method}</strong><span>{calibrationFit.parameterNames.map((name, index) => `${name}=${formatNumber(calibrationFit.parameters[index], 5)}`).join(' · ')}</span><span className={calibrationFit.validationStatus === 'validated' ? 'wf-status verified' : 'wf-status'}>{calibrationFit.validationStatus === 'validated' ? '已验证' : calibrationFit.validationStatus}</span></div>
                  <table className="wf-table"><thead><tr><th>数据集</th><th>样本数</th><th>RMSE</th><th>MAE</th><th>最大绝对误差</th></tr></thead><tbody>
                    <tr><td>训练</td><td>{calibrationFit.trainResiduals.n}</td><td>{formatNumber(calibrationFit.trainResiduals.rmse, 5)}</td><td>{formatNumber(calibrationFit.trainResiduals.mae, 5)}</td><td>{formatNumber(calibrationFit.trainResiduals.maxAbs, 5)}</td></tr>
                    <tr><td>验证</td><td>{calibrationFit.validationResiduals.n}</td><td>{formatNumber(calibrationFit.validationResiduals.rmse, 5)}</td><td>{formatNumber(calibrationFit.validationResiduals.mae, 5)}</td><td>{formatNumber(calibrationFit.validationResiduals.maxAbs, 5)}</td></tr>
                  </tbody></table>
                </>}
              </div>
            </details>
          </>}
        </section>}
        {zone === 'report' && <section className="wf-card wf-wide">
          <div className="wf-card-head"><span>06</span><h2>报告</h2></div>

          <div className="spx-summary-grid wf-report-summary" aria-label="报告内容摘要">
            <div><span>工程</span><strong>{project?.name ?? '未提供'}</strong></div>
            <div><span>流程</span><strong>{compile ? `${compile.projectName} · ${compile.stepCount} 步` : '未提供'}</strong></div>
            <div><span>编译</span><strong>{compile ? `${compile.dimension}D · ${compile.stepCount} 步` : '未提供'}</strong></div>
            <div><span>Route</span><strong>{route ? `${route.route.name} · ${route.segments.length} Segment` : '未提供'}</strong></div>
            <div><span>Run</span><strong>{hasExecutedRun && segment ? statusLabel(segment.status) : '未提供'}</strong></div>
            <div><span>测量</span><strong>{measurement ? `${formatNumber(measurement.value)} ${displayUnit(measurement.unit)}` : '未提供'}</strong></div>
            <div><span>实验</span><strong>{experimentDoc ? `${experimentDoc.name} · ${experimentDoc.variants.length} 变体` : '未提供'}</strong></div>
            <div><span>报告</span><strong>{reportDocument ? `已生成 · ${formatDateTime(reportDocument.createdAt)}` : '未生成'}</strong></div>
          </div>

          <div className="spx-field-grid wf-report-form">
            <label className="spx-field span-2"><span>报告标题</span>
              <input aria-label="报告标题" value={reportTitle} onChange={event => setReportTitle(event.target.value)} />
            </label>
            <label className="spx-field span-2"><span>结论 / 候选说明（写入报告）</span>
              <textarea aria-label="报告结论" value={reportConclusions} onChange={event => setReportConclusions(event.target.value)} placeholder="例如：选择 V2（0.02 s），CD 在公差内。" />
            </label>
          </div>

          {!reportReady && <div className="wf-block-reason" aria-label="报告禁用原因">
            生成报告前需先完成 03 运行与测量，或在 04 运行一个实验。
          </div>}

          <div className="spx-run-actions">
            <button className="wf-button primary" disabled={!reportReady || !!busy || !reportTitle.trim()} onClick={() => void generateReport()}>
              {busy === 'report' ? '生成中…' : '生成报告'}
            </button>
            <button className="wf-button ghost" disabled={!reportDocument} onClick={openReport}>打开报告</button>
            <button className="wf-button ghost" disabled={!reportDocument} onClick={downloadReportHtml}>下载 HTML</button>
            {!reportReady && <button className="wf-button ghost" onClick={() => navigateZone('run')}>去 03 运行测量</button>}
          </div>

          {!reportReady && <div className="wf-empty" aria-label="报告空态">
            <strong>还没有可报告的结果</strong>
            <span>报告包含流程/编译、Route、Run、测量、实验统计与结构视图；先完成 03 运行与测量。</span>
            <div className="wf-empty-actions">
              <button className="wf-button primary" onClick={() => navigateZone('run')}>去 03 运行测量</button>
              <button className="wf-button ghost" onClick={() => navigateZone('experiment')}>去 04 创建实验</button>
            </div>
          </div>}

          {reportDocument && <>
            <div className="wf-result-head wf-report-result">
              <strong>{reportDocument.title}</strong>
              <span className="wf-status succeeded">已生成</span>
              <span className="wf-muted">{formatDateTime(reportDocument.createdAt)}</span>
            </div>
            <div className="wf-report-conclusions">
              <strong>结论 / 候选说明</strong>
              <p>{reportConclusions.trim() || '未提供'}</p>
            </div>
            <details className="spx-advanced wf-report-tech">
              <summary>技术详情（ID 与哈希）</summary>
              <div className="wf-evidence-grid">
                <span>报告</span><code>{reportDocument.reportId}</code>
                <span>工程</span><code>{projectId ?? '—'}</code>
                <span>编译</span><code>{compile?.compileId ?? '—'}</code>
                <span>Route</span><code>{route?.route.routeId ?? '—'}</code>
                <span>Run</span><code>{segment?.runId ?? '—'}</code>
                <span>测量</span><code>{measurement?.measurementId ?? '—'}</code>
                <span>实验</span><code>{experimentDoc?.experimentId ?? '—'}</code>
                <span>SHA256</span><code>{reportDocument.sha256}</code>
              </div>
            </details>
            <iframe ref={reportFrameRef} className="wf-report-preview" title="报告预览" srcDoc={reportDocument.html}
              onLoad={resizeReportFrame} />
          </>}
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
            {propertyRows.map(row => <div className="spx-prop" key={row.label} title={row.title}><span>{row.label}</span><code>{row.value}</code></div>)}
          </div>
          <div className="spx-panel-title"><span>工作流</span></div>
          <div className="spx-props">
            <div className="spx-prop" title={health?.activeRunId ?? undefined}><span>活动运行</span><code>{health?.activeRunId ? '已记录' : '—'}</code></div>
            <div className="spx-prop"><span>ViennaPS</span><code>{health?.viennaps ?? '—'}</code></div>
            <div className="spx-prop"><span>ViennaLS</span><code>{health?.viennals ?? '—'}</code></div>
          </div>
        </aside>
      </div>

      <footer className="spx-statusbar">
        <span>ViennaPS Workbench</span>
        <span>ViennaPS {health?.viennaps ?? '—'}</span>
        <span title={compile?.compileId ?? undefined}>Compile {compile ? (compileStale ? '需重新编译' : '已编译') : '—'}</span>
        <span title={segment?.runId ?? undefined}>Run {segment?.runId ? statusLabel(segment.status) : '—'}</span>
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
