import Viewer3D from './Viewer3D';
import NativeIntermediatePanel from './NativeIntermediatePanel';
import { RunErrorDialog } from './RunErrorDialog';
import { defaultView3D, snapshotDimension } from './types3d';
import type { View3D } from './types3d';
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type PointerEvent, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { TopBar } from './app/TopBar';
import { Rail } from './app/Rail';
import { ContextPanel } from './app/ContextPanel';
import { Inspector } from './app/Inspector';
import { Dock } from './app/Dock';
import { StatusBar } from './app/StatusBar';
import type { Activity, DockTab, Selection } from './app/types';
import { Icon } from './ui/Icon';
import { Menu } from './ui/Menu';
import { StructurePanel } from './panels/StructurePanel';
import { RecipePanel } from './panels/RecipePanel';
import { MaterialsPanel } from './panels/MaterialsPanel';
import { ResultsPanel } from './panels/ResultsPanel';
import { SettingsPanel } from './panels/SettingsPanel';
import { StepInspector } from './inspector/StepInspector';
import { MaterialInspector } from './inspector/MaterialInspector';
import { RunInspector } from './inspector/RunInspector';

type Units = { length: 'um'; time: 's'; rate: 'um/s'; angle: 'deg' };
type Domain = { gridDelta: number; xExtent: number; xBoundary?: 'reflective' | 'periodic'; yExtent?: number; boundaryXY?: 'reflective' | 'periodic' };
type Layer = { id: string; name: string; material: string; thickness: number; role: 'film' | 'mask'; source?: 'gds-mask' };
type GDSMaskPattern = { name: string; assetId: string; fileName: string; structure: string; layer: number; datatype: number; unitScale: number; position: [number, number, number]; baseHeight: number; height: number; material: string; retainedRegion: 'inside' | 'outside'; protectsEtch: boolean };
type StackGeometry = { kind: 'stack'; substrateMaterial: string; baseHeight: number; layers: Layer[]; pattern: { kind: 'none' } | { kind: 'trench'; width: number; taperAngle: number } | { kind: 'hole'; radius: number; taperAngle: number } | { kind: 'gds' }; gdsMask?: GDSMaskPattern };
type HoleGeometry = { kind: 'hole'; radius: number; depth: number; taperAngle: number; maskHeight: number; maskTaperAngle: number; substrateMaterial: string; maskMaterial?: string };
type PlaneGeometry = { kind: 'plane'; height: number; substrateMaterial: string };
type TrenchGeometry = { kind: 'trench'; width: number; depth: number; taperAngle: number; maskHeight: number; maskTaperAngle: number; substrateMaterial: string; maskMaterial?: string };
type FinGeometry = { kind: 'fin'; width: number; height: number; taperAngle: number; maskHeight: number; maskTaperAngle: number; substrateMaterial: string; maskMaterial: string; construction?: 'native' | 'boolean'; extrusion?: { extent: [number, number]; axis: 1 } };
type GDSLayerMapping = { layer: number; material: string; baseHeight?: number; height?: number; mask?: boolean };
type GDSGeometry = { kind: 'gds'; assetId: string; fileName: string; structure: string; unitScale: number; position: [number, number, number]; layers: GDSLayerMapping[] };
type Geometry = PlaneGeometry | TrenchGeometry | HoleGeometry | StackGeometry | FinGeometry | GDSGeometry;
type GDSAsset = { assetId: string; fileName: string; structures: { name: string; layers: number[]; layerDatatypes?: [number, number][]; maskLayerDatatypes?: [number, number][]; references: string[] }[]; units: { userUnitsPerDatabaseUnit: number; metersPerDatabaseUnit: number; micrometersPerNativeUserUnit: number }; fileSizeBytes: number };
type IonSpecies = { kind: 'ion'; label: string; sourcePower: number; thetaRMin: number; thetaRMax: number; minAngle: number; B_sp: number; meanEnergy: number; sigmaEnergy: number; thresholdEnergy: number; inflectAngle: number; n: number };
type NeutralSpecies = { kind: 'neutral'; label: string; defaultStickingProbability: number; stickingProbabilityByMaterial: Record<string, number> };
type ParticleSpecies = IonSpecies | NeutralSpecies;
type MultiParticleRateCoefficients = { defaultBySpecies: Record<string, number>; byMaterial: Record<string, Record<string, number>> };
type SpecializedField = { key: string; label: string; kind: 'number' | 'boolean' | 'enum' | 'vector3' | 'material' | 'materialOptional' | 'text' | 'materialRates' | 'fluorocarbonMaterials'; default: unknown; minimum?: number; maximum?: number; step?: number; nativeUnit?: string | null; options?: { value: string; label: string }[]; defaults?: Record<string, number> };
type SpecializedModel = { id: string; label: string; nativeClass: string; operation: 'etch' | 'deposit' | 'native'; usesFlux: boolean; depositMaterial?: string; depositMaterialSelectable?: boolean; fields: SpecializedField[] };
type SpecializedCatalog = { schemaVersion: number; engineVersion: string; unitsNote: string; models: SpecializedModel[] };
type Step = {
  id: string; name: string; enabled: boolean; operation?: 'etch' | 'deposit' | 'native'; model: 'isotropic' | 'singleParticle' | 'multiParticle' | 'directional' | 'specialized' | 'removeMaterial' | 'planarize' | 'boolean';
  rateMagnitude?: number; directionalRateMagnitude?: number; isotropicRateMagnitude?: number; direction?: [number, number, number]; calculateVisibility?: boolean;
  duration?: number; depositMaterial?: string; stickingProbability?: number; sourceExponent?: number;
  rateMode?: 'uniform' | 'material'; materialRates?: Record<string, number>; selectivityReference?: string;
  species?: ParticleSpecies[]; rateCoefficients?: MultiParticleRateCoefficients;
  nativeModel?: string; nativeParameters?: Record<string, unknown>;
  material?: string; cutoffHeight?: number; booleanOperation?: 'union' | 'intersection' | 'difference'; position?: [number, number, number]; width?: number; height?: number; depth?: number; angle?: number; applyToAll?: boolean;
};
type FixedRandom = { mode: 'fixed'; seed: number };
type RandomPolicy = FixedRandom | { mode: 'random' };
type AdvectionSettings = { spatialScheme: string; temporalScheme: string; adaptiveTimeStepping: boolean; adaptiveTimeStepSubdivisions: number; calculateIntermediateVelocities: boolean; checkDissipation: boolean; dissipationAlpha: number; ignoreVoids: boolean; velocityOutput: boolean };
type RayTracingSettings = { diskRadius: number; ignoreFluxBoundaries: boolean; maxBoundaryHits: number; maxReflections: number; minNodeDistanceFactor: number; normalizationType: 'SOURCE' | 'MAX'; smoothingNeighbors: number };
type Solver = { timeStepRatio: number; raysPerPoint: number; cpuThreads: number; random: RandomPolicy; enginePreference: 'auto' | 'cpu' | 'gpu'; gpuDeviceId?: string; timeoutSeconds: number; advection?: AdvectionSettings; rayTracing?: RayTracingSettings };
type ViewSettings = { panX: number; panH: number; zoom: number; showGrid: boolean };
type DomainStateRef = { formatVersion: 1; stateId: string; structureHash: string; dimension: 2 | 3; gridDelta: number; xExtent: number; yExtent?: number; boundary: 'reflective' | 'periodic'; boundaryConditions: string[]; materialIdsByLevelSetIndex: number[]; levelSetCount: number; viennapsVersion: string; viennalsVersion: string; sourceRunId?: string; sourceStepId?: string };
type OutputSettings = { levelSets: boolean; levelSetMeshes: boolean; surfaceMeshVtk: boolean; volumeMesh: boolean; nativeMetadata: boolean };
type MaterialDefinition = { name: string; id: number; customId: number; color: string; category?: string; densityGcm3?: number; conductive: boolean; description: string };
type Project = { schemaVersion: 1 | 2 | 3 | 4 | 5 | 6 | 7; name: string; dimension: 2 | 3; units: Units; domain: Domain; geometry: Geometry; steps: Step[]; solver: Solver; view?: ViewSettings; view3D?: View3D; continuationState?: DomainStateRef; materialDefinitions?: MaterialDefinition[]; outputSettings?: OutputSettings };
type NativeArtifact = { kind: string; fileName: string; url: string; format: string; sizeBytes: number; sha256: string; levelSetIndex?: number; materialId?: number; association?: string; nativeUnit?: string | null; metadataLevel?: string };
type Snapshot = {
  schemaVersion: number; dimension?: number; surfaceBounds?: number[]; substrateMaterialId?: number; runId: string; stepId: string; stepName: string; stepIndex: number; axisMap: { x: number; h: number; z: number };
  coordinateUnit: string; points: number[][]; lines?: number[][]; triangles?: number[][]; cellData: { materialIds: number[] };
  materials: Record<string, { name: string; color?: string; levelSetIndexes: number[]; sourceStepId: string | null }>;
  geometryReadback?: Record<string, unknown>;
  outputs?: NativeArtifact[];
  sliceReadback?: { nativeOperation: string; axis: number; axisLabel: string; position: number; sourceDimension: number; outputDimension: number; viewerClipping: boolean; levelSetCount: number; materialIdsByLevelSetIndex: number[] };
  mesh: { vtk?: string; pointCount: number; lineCount?: number; triangleCount?: number }; gridDelta: number; materialIdsByLevelSetIndex: number[]; noOp: boolean;
};
type SnapshotRow = { stepId: string; name: string; stepIndex: number; snapshot: string; vtk: string; pointCount: number; lineCount?: number; triangleCount?: number; materialIds: number[]; noOp?: boolean; geometryReadback?: Record<string, unknown>; sliceReadback?: Snapshot['sliceReadback']; nativeState?: DomainStateRef & { fileName: string } };
type RunSummary = { runId: string; kind: string; status: string; createdAt?: string; finishedAt?: string; requestedInputHash?: string; failureReason?: string };
type RunTermination = { reason?: string | null; returnCode?: number | null; rssLimitMiB?: number | null; peakObservedRssBytes?: number | null };
type IntermediateOutputRef = { stepId: string; fileName: string; url: string; format: string; pointCount: number | null; cellCounts: Record<string, number>; coordinateUnit: string | null; axisMap: Record<string, number> | null; fields: { name: string; association: 'pointData' | 'cellData'; components: number; tuples: number; nativeType?: string | null; nativeUnit: string | null; unitStatus?: string }[]; sizeBytes: number; sha256: string; associationPolicy?: string };
type RunInfo = {
  runId: string; kind: string; status: string; currentStepId: string | null; completedStepIds: string[]; startedAt?: string | null;
  elapsedSeconds: number; requestedInputHash: string; executionHash?: string; failureReason?: string | null; termination?: RunTermination | null; stepTotal?: number;
  runMode?: 'from-initial' | 'from-domain-state'; initialState?: DomainStateRef | null;
  log: { cursor: number; nextCursor: number; truncated: boolean; text: string }; snapshots: SnapshotRow[];
  actualExecution?: { stepId: string; model?: string; operation?: string; noOp?: boolean; actualEngine?: string; actualSeed?: number | null; actualRate?: number | null; actualParameters?: Record<string, unknown> }[];
  fluxResult?: { fileName: string; vtk: string; meshIdentity: string; pointCount: number; fieldCount: number; fieldNames: string[]; actualEngine: string; actualSeed: number };
  intermediateOutputs?: IntermediateOutputRef[];
};
type FluxField = { name: string; association: 'pointData' | 'cellData'; components: 1 | 3; tuples: number; nativeUnit: string | null; unitStatus: string };
type FluxResult = {
  schemaVersion: 1; source: string; dimension: 2 | 3; axisMap: Record<string, number>; coordinateUnit: string; fieldUnitPolicy: string;
  meshIdentity: string; mesh: { pointCount: number; cellCounts: Record<string, number>; vtk: string; format: string };
  points: number[][]; topology: Record<string, number[][]>; pointData: { scalars: Record<string, number[]>; vectors: Record<string, number[][]> };
  cellData: { scalars: Record<string, number[]>; vectors: Record<string, number[][]> }; fields: FluxField[];
  execution: { stepId: string; stepName: string; model: string; operation: string; actualRate: number | null; actualEngine: string; actualSeed: number; cpuThreads: number; rays: Record<string, unknown>; elapsedSeconds: number; processDuration: string; nativeModel?: Record<string, unknown> };
  versions: { viennaps: string; viennals: string };
};
type ProjectRow = { id: string; name: string; updatedAt: string };
type GpuMaterialScope = { kind: string; engineVersion: string; builtInLegacyIds: number[]; customLegacyIdRange: [number, number]; boundaries: string[]; roleKindsByGeometry: Record<string, string[][]>; bindingKeysByOperation: Record<string, string[]>; evidence: string[] };
type GpuSpeciesScope = { kind: string; evidence: string[] };
type GpuKnownFailure = { model: string; dimension: number; operation: string; boundaryXY: string; geometryKind: string; engineVersion: string; failure: string; engines?: string[]; testedProfiles?: string[]; evidence: string[] };
type GpuEngineLimitation = { model: string; engine: string; engineVersion: string; limit: string; maxIonSpecies: number; dimensions: number[]; extraIonBehavior: string; cpuVerifiedProductDirectCases: number; evidence: string[] };
type GpuCapabilityRow = { status: string; schemaVersions?: number[]; dimension: number; model: string; nativeModel?: string; operation: string; boundaryXY: string; engine: string; geometryKinds?: string[]; patternKinds?: string[]; finExtrusionMode?: 'direct' | 'extruded'; finConstruction?: 'native' | 'boolean'; materials?: string[]; materialRoles?: Record<string, string[]>; materialBindings?: Record<string, string>; speciesSignature?: { species: ParticleSpecies[]; rateCoefficients: MultiParticleRateCoefficients }; executionStrategy?: string; solverProfile?: { gridDelta: number; timeStepRatio: number; raysPerPoint: number; randomMode: 'fixed' | 'random'; advection?: AdvectionSettings; rayTracing?: RayTracingSettings }; materialScope?: GpuMaterialScope; speciesScope?: GpuSpeciesScope; gdsScope?: { kind: string; engineVersion: string; dimensions: number[]; maxLayerMappings: number; supports3DMask: boolean } };
type GpuProfileFailure = { dimension: number; model: string; operation: string; boundaryXY: string; engine: string; geometryKind: string; solverProfile: Record<string, unknown>; materials: string[]; materialRoles: Record<string, string[]>; materialBindings: Record<string, string>; speciesSignature?: { species: ParticleSpecies[]; rateCoefficients: MultiParticleRateCoefficients } | null; reason: string };
type Capabilities = { capabilities?: { gpuModelRows?: GpuCapabilityRow[]; knownGpuProfileFailures?: GpuProfileFailure[]; knownNativeFailures?: GpuKnownFailure[]; knownEngineLimitations?: GpuEngineLimitation[]; threeDimensional?: { rows?: GpuCapabilityRow[] }; engines?: { gpu?: { status?: string; exposed?: boolean; engine?: string } } }; resourcePolicy?: { run?: { maximumSteps?: number | null; cpuThreads?: { default?: number; minimum?: number; maximum?: number; limitSource?: string } } } };
type GpuDevice = { index: number; name: string; pciBusId: string; uuid: string; memoryTotalMiB: number | null; vendor: string; computeApi: string };
type GpuDeviceInventory = { status: 'available' | 'unavailable' | 'error'; devices: GpuDevice[]; preferredGpuDeviceId: string | null; reason: string | null; probe?: { bounded: boolean; returnCode?: number; elapsedSeconds?: number } };

const defaultProject: Project = {
  schemaVersion: 7,
  name: '未命名项目',
  dimension: 2,
  units: { length: 'um', time: 's', rate: 'um/s', angle: 'deg' },
  domain: { gridDelta: 0.1, xExtent: 5, xBoundary: 'reflective' },
  geometry: { kind: 'stack', substrateMaterial: 'Si', baseHeight: 0, layers: [
    { id: 'layer_sio2', name: 'SiO₂ 薄膜', material: 'SiO2', thickness: 0.6, role: 'film' },
    { id: 'layer_si3n4', name: 'Si₃N₄ 掩膜', material: 'Si3N4', thickness: 0.6, role: 'mask' },
  ], pattern: { kind: 'trench', width: 2, taperAngle: 0 } },
  steps: [],
  outputSettings: { levelSets: false, levelSetMeshes: false, surfaceMeshVtk: false, volumeMesh: false, nativeMetadata: false },
  solver: { timeStepRatio: 0.25, raysPerPoint: 256, cpuThreads: 1, random: { mode: 'fixed', seed: 314159 }, enginePreference: 'auto', timeoutSeconds: 0,
    advection: { spatialScheme: 'ENGQUIST_OSHER_1ST_ORDER', temporalScheme: 'FORWARD_EULER', adaptiveTimeStepping: false, adaptiveTimeStepSubdivisions: 20, calculateIntermediateVelocities: false, checkDissipation: true, dissipationAlpha: 1, ignoreVoids: false, velocityOutput: false },
    rayTracing: { diskRadius: 0, ignoreFluxBoundaries: false, maxBoundaryHits: 1000, maxReflections: 4294967295, minNodeDistanceFactor: 0.05, normalizationType: 'SOURCE', smoothingNeighbors: 1 } },
  view: { panX: 0, panH: 0, zoom: 1, showGrid: true },
};

type MaterialCatalogItem = { name: string; id: number; color: string; isCustom?: boolean; category?: string; densityGcm3?: number; conductive?: boolean; description?: string; customId?: number };

// Ray count is the dominant cost knob for stochastic flux steps: measured on
// the RTX 3060 host, 8/64/256 rays per point cost 1.42/2.26/4.87 s of flux
// engine time for a six-step 3D deposition. The numeric field stays available
// for fine tuning; the presets give the common trade-off points a name.
const RAY_PRESETS = [
  { value: 16, label: '预览', detail: '每点 16 条射线：最快，统计噪声明显，适合看趋势或快速试几何' },
  { value: 64, label: '标准', detail: '每点 64 条射线：射线耗时约为 256 的三分之一，噪声可接受' },
  { value: 256, label: '精确', detail: '每点 256 条射线：默认精度，统计噪声最低，单步耗时最长' },
] as const;

function DeleteMaterialDialog({ material, busy, error, onClose, onConfirm }: {
  material: MaterialCatalogItem; busy: boolean; error: string; onClose: () => void; onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    if (!dialog.open) dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  }, []);

  return <dialog ref={dialogRef} className="material-delete-dialog" aria-labelledby="material-delete-title" aria-describedby="material-delete-description"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="material-delete-card">
      <header className="material-delete-header">
        <span className="material-delete-mark" aria-hidden="true">!</span>
        <div><h2 id="material-delete-title">确认删除自定义材料？</h2><strong>{material.name}</strong></div>
        <button type="button" className="run-error-close" aria-label="关闭确认对话框" disabled={busy} onClick={onClose}>×</button>
      </header>
      <div className="material-delete-body">
        <p id="material-delete-description">删除后，该材料会从目录中移除。已分配的原生 ID {material.id} 会保留且不会复用。</p>
        <small>若项目或运行记录仍引用该材料，系统会阻止删除。</small>
        {error && <div className="material-delete-error" role="alert">{error}</div>}
      </div>
      <footer className="material-delete-footer">
        <button type="button" className="button" autoFocus disabled={busy} onClick={onClose}>取消</button>
        <button type="button" className="button danger material-delete-confirm" disabled={busy} onClick={onConfirm}>{busy ? '正在删除…' : '确认删除'}</button>
      </footer>
    </section>
  </dialog>;
}

const MATERIAL_CATEGORY_OPTIONS = [
  ['Generic', '通用'], ['Silicon', '硅'], ['OxideNitride', '氧化物 / 氮化物'], ['Hardmask', '硬掩膜'],
  ['Metal', '金属'], ['Silicide', '硅化物'], ['Compound', '化合物'], ['TwoD', '二维材料'],
  ['TCO', '透明导电氧化物'], ['Misc', '其他'],
] as const;
function materialCategoryLabel(value?: string): string {
  return MATERIAL_CATEGORY_OPTIONS.find(([key]) => key === value)?.[1] ?? value ?? '通用';
}
function initialVisibleMaterialIds(catalog: MaterialCatalogItem[], geometry: Geometry, preferred: number[] = []): number[] {
  const usedNames = geometry.kind === 'stack'
    ? [geometry.substrateMaterial, ...geometry.layers.map(layer => layer.material), ...(geometry.gdsMask ? [geometry.gdsMask.material] : [])]
    : geometry.kind === 'gds'
      ? geometry.layers.map(layer => layer.material)
      : [geometry.substrateMaterial, ...(geometry.kind === 'fin' ? [geometry.maskMaterial] : geometry.kind === 'trench' || geometry.kind === 'hole' ? [geometry.maskMaterial] : [])];
  const catalogIds = catalog.map(material => material.id);
  const usedIds = catalog.filter(material => usedNames.includes(material.name)).map(material => material.id);
  return [...new Set([...usedIds, ...preferred, ...catalogIds])].filter(id => catalogIds.includes(id)).slice(0, 16);
}
const SPECIALIZED_FLUX_MODELS = new Set(['sf6o2', 'cf4o2', 'hbro2', 'sf6c4f8', 'fluorocarbon', 'ionBeam', 'teosDeposition', 'teospecvd', 'ald', 'neutralTransport']);
const NATIVE_MAKEFIN_SIGSEGV_MODELS = new Set(['sf6o2', 'hbro2', 'sf6c4f8', 'neutralTransport']);
function hasMotion(step: Step): boolean {
  if (isGeometryStep(step)) return false;
  if (step.model === 'specialized') return true;
  if (step.model === 'directional') return (step.directionalRateMagnitude ?? 0) > 0 || (step.isotropicRateMagnitude ?? 0) > 0;
  if (step.model === 'multiParticle') {
    const table = step.rateCoefficients;
    return Object.values(table?.defaultBySpecies ?? {}).some(rate => rate > 0)
      || Object.values(table?.byMaterial ?? {}).some(row => Object.values(row).some(rate => rate > 0));
  }
  if (step.rateMode !== 'material') return (step.rateMagnitude ?? 0) > 0;
  return (step.rateMagnitude ?? 0) > 0 || Object.values(step.materialRates ?? {}).some(rate => rate > 0);
}
function usesFlux(step: Step): boolean { return step.enabled && ((step.model === 'specialized' && SPECIALIZED_FLUX_MODELS.has(step.nativeModel ?? '')) || (step.model === 'singleParticle' || step.model === 'multiParticle') && hasMotion(step)); }
function isGeometryStep(step: Step): boolean { return step.model === 'removeMaterial' || step.model === 'planarize' || step.model === 'boolean'; }
function stepKindLabel(step: Step): string {
  if (step.model === 'removeMaterial') return `移除材料 · ${step.material ?? '未选择'}`;
  if (step.model === 'planarize') return '平坦化';
  if (step.model === 'boolean') return `布尔几何 · ${{ union: '并集', intersection: '交集', difference: '差集' }[step.booleanOperation ?? 'difference']}`;
  if (step.model === 'specialized') return `ViennaPS 专用模型 · ${step.nativeModel ?? '未选择'}`;
  return `${step.operation === 'etch' ? '刻蚀' : '沉积'} · ${step.model === 'isotropic' ? '各向同性' : step.model === 'singleParticle' ? '单粒子' : step.model === 'multiParticle' ? '多粒子' : '定向过程'}${hasMotion(step) ? '' : ' · 零速率'}`;
}
function geometryMaterialNames(geometry: Geometry): string[] {
  const names = new Set<string>();
  if (geometry.kind === 'gds') geometry.layers.forEach(layer => names.add(layer.material));
  else {
    names.add(geometry.substrateMaterial);
    if (geometry.kind === 'stack') { geometry.layers.forEach(layer => names.add(layer.material)); if (geometry.gdsMask) names.add(geometry.gdsMask.material); }
    if ((geometry.kind === 'trench' || geometry.kind === 'hole' || geometry.kind === 'fin') && geometry.maskHeight > 0) names.add(geometry.maskMaterial ?? 'Mask');
  }
  return [...names].filter(Boolean).sort();
}
function specializedDefaults(spec: SpecializedModel, geometry: Geometry): Record<string, unknown> {
  const names = geometryMaterialNames(geometry);
  return Object.fromEntries(spec.fields.map(field => {
    if (field.kind === 'materialRates') {
      const required = spec.id === 'selectiveEpitaxy' ? ['Si', 'SiGe'] : spec.id === 'wetEtch' ? names : [];
      return [field.key, Object.fromEntries(required.map(name => [name, field.default]))];
    }
    if (field.kind === 'fluorocarbonMaterials') return [field.key, Object.fromEntries([...new Set([...names, 'Mask', 'Polymer'])].sort().map(name => [name, { ...(field.defaults ?? {}) }]))];
    return [field.key, structuredClone(field.default)];
  }));
}
function promoteProjectToV5(source: Project): Project {
  return {
    ...source,
    schemaVersion: source.schemaVersion >= 7 ? 7 : source.schemaVersion === 6 ? 6 : 5,
    steps: source.steps.map(step => step.model === 'directional' || step.model === 'multiParticle' || step.model === 'specialized' || isGeometryStep(step) ? step : { ...step, rateMode: step.rateMode ?? 'uniform' }),
    solver: { ...source.solver, enginePreference: 'cpu' },
  };
}
function promoteProjectToV7(source: Project): Project {
  const defaults = defaultProject.solver;
  return {
    ...source,
    schemaVersion: 7,
    steps: source.steps.map(step => step.model === 'directional' || step.model === 'multiParticle' || step.model === 'specialized' || isGeometryStep(step) ? step : { ...step, rateMode: step.rateMode ?? 'uniform' }),
    solver: { ...source.solver, enginePreference: 'auto', advection: { ...defaults.advection!, ...source.solver.advection }, rayTracing: { ...defaults.rayTracing!, ...source.solver.rayTracing } },
    outputSettings: source.outputSettings ?? { ...structuredClone(defaultProject.outputSettings!), volumeMesh: source.dimension === 3 },
  };
}
function normalizeGdsMaskLayer(source: Project): Project {
  if (source.geometry.kind !== 'stack' || !source.geometry.gdsMask || source.geometry.layers.some(layer => layer.source === 'gds-mask')) return source;
  const mask = source.geometry.gdsMask;
  const id = `gds_mask_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
  const geometry: StackGeometry = {
    ...source.geometry,
    layers: [...source.geometry.layers, { id, name: mask.name, material: mask.material, thickness: mask.height, role: 'mask', source: 'gds-mask' }],
    pattern: { kind: 'gds' },
    gdsMask: { ...mask, baseHeight: source.geometry.baseHeight + source.geometry.layers.reduce((sum, layer) => sum + layer.thickness, 0) },
  };
  return { ...source, schemaVersion: 7, dimension: 3, domain: { ...source.domain, yExtent: source.domain.yExtent ?? source.domain.xExtent, boundaryXY: source.domain.boundaryXY ?? 'reflective' }, geometry };
}
function defaultDirection(dimension: 2 | 3): [number, number, number] {
  return dimension === 2 ? [0, -1, 0] : [0, 0, -1];
}
function defaultIonSpecies(label = 'argonIon'): IonSpecies {
  return { kind: 'ion', label, sourcePower: 1, thetaRMin: 0, thetaRMax: 50, minAngle: 60, B_sp: -1,
    meanEnergy: 120, sigmaEnergy: 15, thresholdEnergy: 10, inflectAngle: 25, n: 1 };
}
function defaultNeutralSpecies(label = 'fluorineNeutral'): NeutralSpecies {
  return { kind: 'neutral', label, defaultStickingProbability: 0.4, stickingProbabilityByMaterial: {} };
}
function defaultMultiParticleFields(): Pick<Step, 'species' | 'rateCoefficients'> {
  return { species: [defaultIonSpecies(), defaultNeutralSpecies()],
    rateCoefficients: { defaultBySpecies: { argonIon: 0.025, fluorineNeutral: 0.025 }, byMaterial: {} } };
}
function configuredMaterialRates(project: Project, fallback: number): Record<string, number> {
  const roles = new Map<string, { seen: boolean; mask: boolean }>();
  const add = (name: string, mask: boolean) => {
    const current = roles.get(name) ?? { seen: false, mask: false };
    roles.set(name, { seen: true, mask: current.mask || mask });
  };
  if (project.geometry.kind === 'gds') project.geometry.layers.forEach(layer => add(layer.material, layer.mask ?? false));
  else {
    add(project.geometry.substrateMaterial, false);
    if (project.geometry.kind === 'stack') { project.geometry.layers.filter(layer => layer.source !== 'gds-mask').forEach(layer => add(layer.material, layer.role === 'mask')); if (project.geometry.gdsMask?.protectsEtch) add(project.geometry.gdsMask.material, true); }
    else if (project.geometry.kind === 'trench' || project.geometry.kind === 'hole' || project.geometry.kind === 'fin') add(project.geometry.maskMaterial ?? 'Mask', true);
  }
  return Object.fromEntries([...roles].map(([name, role]) => [name, role.mask ? 0 : fallback]));
}
function gdsLayerIds(asset: GDSAsset, name: string): number[] {
  const byName = new Map(asset.structures.map(item => [item.name, item]));
  const layers = new Set<number>(); const seen = new Set<string>(); const pending = [name];
  while (pending.length) {
    const current = pending.pop()!;
    if (seen.has(current)) continue;
    seen.add(current);
    const cell = byName.get(current);
    if (!cell) continue;
    cell.layers.forEach(layer => layers.add(layer));
    pending.push(...cell.references);
  }
  return [...layers].sort((a, b) => a - b);
}
function gdsPairs(asset: GDSAsset, name: string): [number, number][] {
  const byName = new Map(asset.structures.map(item => [item.name, item]));
  const pairs = new Set<string>(); const seen = new Set<string>(); const pending = [name];
  while (pending.length) {
    const current = pending.pop()!;
    if (seen.has(current)) continue;
    seen.add(current);
    const cell = byName.get(current);
    if (!cell) continue;
    (cell.layerDatatypes ?? cell.layers.map(layer => [layer, 0] as [number, number])).forEach(([layer, datatype]) => pairs.add(`${layer}/${datatype}`));
    pending.push(...cell.references);
  }
  return [...pairs].map(value => value.split('/').map(Number) as [number, number]).sort((a,b) => a[0]-b[0] || a[1]-b[1]);
}
function gdsMaskPairs(asset: GDSAsset, name: string): [number, number][] {
  const byName = new Map(asset.structures.map(item => [item.name, item]));
  const pairs = new Set<string>(); const seen = new Set<string>(); const pending = [name];
  while (pending.length) {
    const current = pending.pop()!;
    if (seen.has(current)) continue;
    seen.add(current);
    const cell = byName.get(current);
    if (!cell) continue;
    (cell.maskLayerDatatypes ?? []).forEach(([layer, datatype]) => pairs.add(`${layer}/${datatype}`));
    pending.push(...cell.references);
  }
  return [...pairs].map(value => value.split('/').map(Number) as [number, number]).sort((a,b) => a[0]-b[0] || a[1]-b[1]);
}
function gdsAssetIdFromProject(value: Project): string | null {
  if (value.geometry.kind === 'gds') return value.geometry.assetId;
  if (value.geometry.kind === 'stack') return value.geometry.gdsMask?.assetId ?? null;
  return null;
}
const terminalStates = new Set(['succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted']);
const maxPackageBytes = 512 * 1024 * 1024;
type PanelWidths = { structure: number; settings: number };
type ResizablePanel = keyof PanelWidths;
const panelWidthLimits = { structure: { min: 260, max: 420 }, settings: { min: 280, max: 420 }, viewerMin: 320, splitter: 10, workspacePadding: 20, rail: 64 } as const;
function fitPanelWidths(widths: PanelWidths, workspaceWidth: number): PanelWidths {
  const structure = Math.max(panelWidthLimits.structure.min, Math.min(panelWidthLimits.structure.max, widths.structure));
  const settings = Math.max(panelWidthLimits.settings.min, Math.min(panelWidthLimits.settings.max, widths.settings));
  const available = Math.max(panelWidthLimits.structure.min + panelWidthLimits.settings.min,
    workspaceWidth - panelWidthLimits.workspacePadding - panelWidthLimits.splitter * 2 - panelWidthLimits.viewerMin - panelWidthLimits.rail);
  const structureExtra = structure - panelWidthLimits.structure.min;
  const settingsExtra = settings - panelWidthLimits.settings.min;
  const totalExtra = structureExtra + settingsExtra;
  const remainingExtra = Math.max(0, available - panelWidthLimits.structure.min - panelWidthLimits.settings.min);
  if (totalExtra <= remainingExtra || totalExtra === 0) return { structure, settings };
  const factor = remainingExtra / totalExtra;
  return {
    structure: panelWidthLimits.structure.min + structureExtra * factor,
    settings: panelWidthLimits.settings.min + settingsExtra * factor,
  };
}
function maxPanelWidth(panel: ResizablePanel, workspaceWidth: number): number {
  const otherMinimum = panel === 'structure' ? panelWidthLimits.settings.min : panelWidthLimits.structure.min;
  const available = workspaceWidth - panelWidthLimits.workspacePadding - panelWidthLimits.splitter * 2 - panelWidthLimits.viewerMin - otherMinimum;
  return Math.max(panelWidthLimits[panel].min, Math.min(panelWidthLimits[panel].max, available));
}

function linePathsByMaterial(snapshot: Snapshot | null): Map<number, string> {
  const segments = new Map<number, string[]>();
  if (!snapshot) return new Map();
  for (let index = 0; index < (snapshot.lines?.length ?? 0); index++) {
    const materialId = snapshot.cellData.materialIds[index];
    const line = snapshot.lines![index];
    const a = snapshot.points[line[0]];
    const b = snapshot.points[line[1]];
    if (!a || !b) continue;
    const paths = segments.get(materialId) ?? [];
    paths.push(`M${Number(a[0].toPrecision(9))},${Number(a[1].toPrecision(9))}L${Number(b[0].toPrecision(9))},${Number(b[1].toPrecision(9))}`);
    segments.set(materialId, paths);
  }
  return new Map([...segments].map(([materialId, paths]) => [materialId, paths.join('')]));
}

class ApiError extends Error {
  detail: unknown;
  constructor(message: string, detail: unknown) { super(message); this.detail = detail; }
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers } });
  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === 'object' && data !== null && 'detail' in data ? (data as { detail: unknown }).detail : data;
    let message = response.statusText || `HTTP ${response.status}`;
    if (typeof detail === 'string') message = detail;
    else if (Array.isArray(detail) && detail[0]) message = `${detail[0].path ? `${detail[0].path}：` : ''}${detail[0].message ?? ''}`;
    else if (typeof detail === 'object' && detail !== null && 'message' in detail) message = String((detail as { message: unknown }).message);
    throw new ApiError(message, detail);
  }
  if (!contentType.toLowerCase().includes('json')) {
    throw new ApiError('本机工作台接口返回了非 JSON 内容，请检查后端地址或服务状态。', data);
  }
  return data as T;
}

function newStepId(): string { return `step_${crypto.randomUUID().replaceAll('-', '').slice(0, 24)}`; }
function saveBlob(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function prettyError(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function humanStatus(status?: string): string {
  const labels: Record<string, string> = { pending: '排队中', running: '运行中', cancelling: '正在取消', succeeded: '已完成', failed: '失败', timed_out: '超时', cancelled: '已取消', interrupted: '服务中断' };
  return labels[status ?? ''] ?? '未运行';
}
type RunFailureDialogData = { runId: string; status: string; reason: string; details: string; stepLabel?: string };
const errorRunStates = new Set(['failed', 'timed_out', 'interrupted']);
function cleanRunOutput(value?: string | null): string {
  return (value ?? '').replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '').trim();
}
function runFailureDialogData(info: RunInfo): RunFailureDialogData {
  const termination = info.termination;
  const returnCode = termination?.returnCode;
  const rawReason = cleanRunOutput(info.failureReason);
  let reason = '';
  if (termination?.reason === 'rss_limit') {
    const peakMiB = termination.peakObservedRssBytes == null
      ? null : termination.peakObservedRssBytes / (1024 * 1024);
    const limitMiB = termination.rssLimitMiB;
    reason = `进程组占用的系统内存超过 ${limitMiB ?? '未知'} MiB 上限，监督器终止了运行${peakMiB == null ? '' : `（峰值约 ${peakMiB.toFixed(1)} MiB）`}。`;
  } else if (returnCode === -11) {
    reason = '原生计算进程发生段错误（SIGSEGV，退出码 -11）并异常退出。';
  } else if (returnCode != null && returnCode < 0) {
    const signal = -returnCode;
    const signalName: Record<number, string> = { 6: 'SIGABRT', 9: 'SIGKILL', 11: 'SIGSEGV', 15: 'SIGTERM' };
    reason = `计算子进程被 ${signalName[signal] ?? `信号 ${signal}`} 终止（退出码 ${returnCode}）。`;
  } else if (info.status === 'timed_out') {
    reason = rawReason.length < 260 && !rawReason.includes('\n') ? rawReason : '运行超过配置的时间限制。';
  } else if (rawReason && rawReason.length < 260 && !rawReason.includes('\n')) {
    reason = rawReason;
  } else {
    const exceptionLine = rawReason.split(/\r?\n/).reverse().find(line => /(?:Error|Exception):/.test(line));
    reason = exceptionLine?.slice(0, 260) ?? (info.status === 'interrupted' ? '工作台服务中断，运行未能完成。' : '计算进程异常退出；请查看运行输出获取更多线索。');
  }

  const recentStep = info.actualExecution?.at(-1);
  const stepLabel = recentStep
    ? [recentStep.operation, recentStep.model, recentStep.stepId].filter(Boolean).join(' · ')
    : undefined;
  const logText = cleanRunOutput(info.log?.text);
  const details = [rawReason, logText && logText !== rawReason ? logText : '']
    .filter(Boolean).join('\n\n').slice(-16_000);
  return { runId: info.runId, status: info.status, reason, details, stepLabel };
}

function NumericField({ label, unit, value, onChange, step = 'any', min, max, disabled = false, hint }: {
  label: string; unit?: string; value: number; onChange: (value: number) => void; step?: string; min?: number; max?: number; disabled?: boolean; hint?: string;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return <label className={`field ${disabled ? 'field-disabled' : ''}`}>
    <span className="field-label">{label}{hint && <small>{hint}</small>}</span>
    <span className="number-wrap"><input type="number" aria-label={label} value={draft} step={step} min={min} max={max} disabled={disabled}
      onChange={event => setDraft(event.target.value)}
      onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}
      onBlur={() => { if (draft.trim() && Number.isFinite(Number(draft))) onChange(Number(draft)); else setDraft(String(value)); }} />
      {unit && <span>{unit}</span>}</span>
  </label>;
}

function SelectField({ label, value, options, onChange, hint }: { label: string; value: string; options: { value: string; label: string; disabled?: boolean }[]; onChange: (value: string) => void; hint?: string }) {
  return <label className="field"><span className="field-label">{label}{hint && <small>{hint}</small>}</span>
    <select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>{options.map(item => <option key={item.value} value={item.value} disabled={item.disabled}>{item.label}</option>)}</select>
  </label>;
}

function MaterialPropertyInspector({ material, busy, onSave }: { material: MaterialCatalogItem; busy: boolean; onSave: (name: string, event: FormEvent<HTMLFormElement>) => Promise<void> }) {
  return <div className="material-property-inspector" key={material.name}>
    <div className="material-inspector-head">
      <i className="material-swatch large" style={{ background: material.color }} />
      <div><strong>{material.name}</strong><span>{material.isCustom ? '自定义材料' : '预设材料'} · 原生 ID {material.id}{material.customId != null ? ` · customId ${material.customId}` : ''}</span></div>
    </div>
    {material.isCustom
      ? <form className="field-grid one-column" onSubmit={event => void onSave(material.name, event)}>
          <label className="field"><span className="field-label">材料类型</span><select aria-label={`${material.name} 类型`} name="category" defaultValue={material.category ?? 'Generic'}>{MATERIAL_CATEGORY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="field"><span className="field-label">显示颜色</span><input aria-label={`${material.name} 颜色`} name="color" type="color" defaultValue={material.color} /></label>
          <label className="field"><span className="field-label">密度</span><input aria-label={`${material.name} 密度`} name="densityGcm3" type="number" min="0" max="100" step="0.01" placeholder="g/cm³，可留空" defaultValue={material.densityGcm3 ?? ''} /></label>
          <label className="field"><span className="field-label">是否导电</span><span className="field-checkbox"><input aria-label={`${material.name} 是否导电`} name="conductive" type="checkbox" defaultChecked={material.conductive ?? false} /></span></label>
          <label className="field"><span className="field-label">备注</span><textarea aria-label={`${material.name} 备注`} name="description" maxLength={500} defaultValue={material.description ?? ''} /></label>
          <div className="inline-note">名称与原生 ID 固定；类型、颜色、密度和导电标记只影响 ViennaPS 元数据与显示，不会自动建立刻蚀、沉积或黏附物理模型。</div>
          <button className="button" type="submit" disabled={busy}>保存材料属性</button>
        </form>
      : <div className="field-grid one-column">
          <div className="material-property-row"><span>材料类型</span><strong>{materialCategoryLabel(material.category)}</strong></div>
          <div className="material-property-row"><span>显示颜色</span><strong><i className="material-swatch" style={{ background: material.color }} />{material.color}</strong></div>
          <div className="material-property-row"><span>密度</span><strong>{material.densityGcm3 != null ? `${material.densityGcm3} g/cm³` : '未设置'}</strong></div>
          <div className="material-property-row"><span>是否导电</span><strong>{material.conductive ? '是' : '否'}</strong></div>
          <div className="inline-note">预设材料的 MaterialInfo 由 ViennaPS 4.6.2 固定，不能修改。</div>
        </div>}
  </div>;
}

function SvgViewer({ current, compare, view, onViewChange, svgRef, catalog }: {
  current: Snapshot | null; compare: Snapshot | null; view: ViewSettings; onViewChange: (next: Partial<ViewSettings>) => void; svgRef: RefObject<SVGSVGElement | null>; catalog: MaterialCatalogItem[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 520 });
  const dragRef = useRef<{ x: number; y: number; panX: number; panH: number } | null>(null);
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(items => {
      const rect = items[0]?.contentRect;
      if (rect && rect.width > 0 && rect.height > 0) setSize({ width: Math.max(1, Math.round(rect.width)), height: Math.max(1, Math.round(rect.height)) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const allPoints = useMemo(() => [...(current?.points ?? []), ...(compare?.points ?? [])], [current, compare]);
  const bounds = useMemo(() => {
    if (!allPoints.length) return { minX: -2.5, maxX: 2.5, minH: -2.5, maxH: 0.5 };
    let minX = Infinity; let maxX = -Infinity; let minH = Infinity; let maxH = -Infinity;
    for (const point of allPoints) { minX = Math.min(minX, point[0]); maxX = Math.max(maxX, point[0]); minH = Math.min(minH, point[1]); maxH = Math.max(maxH, point[1]); }
    const dx = Math.max(0.4, maxX - minX) * 0.08; const dh = Math.max(0.4, maxH - minH) * 0.12;
    return { minX: minX - dx, maxX: maxX + dx, minH: minH - dh, maxH: maxH + dh };
  }, [allPoints]);
  const currentPaths = useMemo(() => linePathsByMaterial(current), [current]);
  const comparePaths = useMemo(() => linePathsByMaterial(compare), [compare]);
  const centerX = (bounds.minX + bounds.maxX) / 2 + view.panX;
  const centerH = (bounds.minH + bounds.maxH) / 2 + view.panH;
  const scale = Math.max(1e-3, Math.min((size.width - 112) / Math.max(0.01, bounds.maxX - bounds.minX), (size.height - 104) / Math.max(0.01, bounds.maxH - bounds.minH)) * view.zoom);
  const worldX = (x: number) => size.width / 2 + (x - centerX) * scale;
  const worldY = (h: number) => size.height / 2 - (h - centerH) * scale;
  const delta = current?.gridDelta ?? compare?.gridDelta ?? 0.1;
  const ids = useMemo(() => [...new Set([...(current?.cellData.materialIds ?? []), ...(compare?.cellData.materialIds ?? [])])].sort((a, b) => a - b), [current, compare]);
  const rawGridCount = Math.ceil((bounds.maxX - bounds.minX) / delta) + Math.ceil((bounds.maxH - bounds.minH) / delta);
  const gridEvery = Math.max(1, Math.ceil(rawGridCount / 260));
  const gridSpacing = delta * gridEvery;
  const gridX: number[] = []; const gridH: number[] = [];
  if (view.showGrid) {
    for (let value = Math.ceil(bounds.minX / gridSpacing) * gridSpacing; value <= bounds.maxX + delta * 0.01; value += gridSpacing) gridX.push(value);
    for (let value = Math.ceil(bounds.minH / gridSpacing) * gridSpacing; value <= bounds.maxH + delta * 0.01; value += gridSpacing) gridH.push(value);
  }
  const colorFor = (id: number) => current?.materials[String(id)]?.color ?? compare?.materials[String(id)]?.color ?? catalog.find(item => item.id === id)?.color ?? '#8064a2';
  const panStart = (event: PointerEvent<SVGSVGElement>) => {
    dragRef.current = { x: event.clientX, y: event.clientY, panX: view.panX, panH: view.panH };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const panMove = (event: PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current; if (!drag) return;
    onViewChange({ panX: drag.panX - (event.clientX - drag.x) / scale, panH: drag.panH + (event.clientY - drag.y) / scale });
  };
  const getScaleBar = () => {
    const micrometers = 100 / scale;
    const power = 10 ** Math.floor(Math.log10(Math.max(micrometers, 0.001)));
    const normalized = micrometers / power;
    const chosen = (normalized >= 5 ? 5 : normalized >= 2 ? 2 : 1) * power;
    return { length: chosen * scale, label: `${Number(chosen.toPrecision(3))} µm` };
  };
  const bar = getScaleBar();
  const left = 70; const right = size.width - 24; const top = 26; const bottom = size.height - 62;
  const axisNames = current?.materials ?? compare?.materials ?? {};
  const meshTransform = `translate(${size.width / 2 - centerX * scale} ${size.height / 2 + centerH * scale}) scale(${scale} ${-scale})`;
  return <div className="viewer-surface" ref={containerRef}>
    {current ? <svg ref={svgRef} className="mesh-svg" viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="none" role="img" aria-label="ViennaPS 二维材料界面查看器"
      onPointerDown={panStart} onPointerMove={panMove} onPointerUp={() => { dragRef.current = null; }} onPointerCancel={() => { dragRef.current = null; }}
      onWheel={event => { event.preventDefault(); onViewChange({ zoom: Math.min(20, Math.max(0.1, view.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12))) }); }}>
      <rect width={size.width} height={size.height} fill="#fbfcfd" />
      {view.showGrid && <g className="grid-lines">
        {gridX.map(x => <line key={`gx${x}`} x1={worldX(x)} y1={top} x2={worldX(x)} y2={bottom} />)}
        {gridH.map(h => <line key={`gh${h}`} x1={left} y1={worldY(h)} x2={right} y2={worldY(h)} />)}
      </g>}
      <line className="axis-line" x1={left} y1={worldY(0)} x2={right} y2={worldY(0)} />
      <line className="axis-line" x1={worldX(0)} y1={top} x2={worldX(0)} y2={bottom} />
      <g transform={meshTransform} fill="none">
        {compare && ids.map(id => <path key={`compare-${id}`} d={comparePaths.get(id) ?? ''} stroke={colorFor(id)} strokeWidth="2.2" strokeDasharray="7 4" opacity="0.48" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />)}
        {ids.map(id => <path key={`current-${id}`} d={currentPaths.get(id) ?? ''} stroke={colorFor(id)} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />)}
      </g>
      <text className="axis-label" x={right - 3} y={Math.min(bottom - 4, worldY(0) - 8)} textAnchor="end">h (µm)</text>
      <text className="axis-label" x={right - 4} y={bottom + 34} textAnchor="end">x (µm)</text>
      <line className="scale-bar" x1={left + 12} y1={bottom + 14} x2={left + 12 + bar.length} y2={bottom + 14} />
      <line className="scale-bar" x1={left + 12} y1={bottom + 9} x2={left + 12} y2={bottom + 19} />
      <line className="scale-bar" x1={left + 12 + bar.length} y1={bottom + 9} x2={left + 12 + bar.length} y2={bottom + 19} />
      <text className="scale-label" x={left + 12} y={bottom + 32}>{bar.label}</text>
    </svg> : <div className="empty-view"><div className="empty-icon">⌁</div><strong>还没有可显示的网格</strong><span>先生成初始结构预览，所有界面都来自本机 ViennaPS。</span></div>}
    {current && <div className="viewer-legend">
      {ids.map(id => <span key={id}><i style={{ background: colorFor(id) }} />{axisNames[String(id)]?.name ?? catalog.find(item => item.id === id)?.name ?? `Material ${id}`}</span>)}
      {compare && <span><i className="legend-dashed" />对比结果</span>}
    </div>}
    {current && <div className="viewer-grid-note">{view.showGrid ? `网格辅助线：${gridEvery} 格一条（Δ=${delta} µm）` : `计算网格间距 Δ=${delta} µm`}</div>}
  </div>;
}

export default function App() {
  const [project, setProject] = useState<Project>(() => structuredClone(defaultProject));
  const [historyView2D, setHistoryView2D] = useState<ViewSettings>(structuredClone(defaultProject.view!));
  const [historyView3D, setHistoryView3D] = useState<View3D>(structuredClone(defaultView3D));
  const [projectId, setProjectId] = useState<string | null>(null);
  const [savedProjectJson, setSavedProjectJson] = useState<string | null>(null);
  const [projectRows, setProjectRows] = useState<ProjectRow[]>([]);
  const [savedSelection, setSavedSelection] = useState('');
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [gpuInventory, setGpuInventory] = useState<GpuDeviceInventory | null>(null);
  const [materials, setMaterials] = useState<MaterialCatalogItem[]>([]);
  const [specializedCatalog, setSpecializedCatalog] = useState<SpecializedCatalog | null>(null);
  const [newSpecializedModel, setNewSpecializedModel] = useState('sf6o2');
  const [materialDraft, setMaterialDraft] = useState({ name: '', color: '#8b5cf6', category: 'Generic', densityGcm3: '', conductive: false, description: '' });
  const [materialBusy, setMaterialBusy] = useState(false);
  const [deletingMaterial, setDeletingMaterial] = useState<string | null>(null);
  const [deleteMaterialCandidate, setDeleteMaterialCandidate] = useState<MaterialCatalogItem | null>(null);
  const [deleteMaterialError, setDeleteMaterialError] = useState('');
  const [gdsAsset, setGdsAsset] = useState<GDSAsset | null>(null);
  const [gdsInspection, setGdsInspection] = useState<Record<string, unknown> | null>(null);
  const [gdsImportOpen, setGdsImportOpen] = useState(false);
  const [gdsImportPosition, setGdsImportPosition] = useState<{ top: number; left: number; side: 'right' | 'left' } | null>(null);
  const [pendingGdsAsset, setPendingGdsAsset] = useState<GDSAsset | null>(null);
  const [pendingGdsStructure, setPendingGdsStructure] = useState('');
  const [pendingGdsLayerDatatype, setPendingGdsLayerDatatype] = useState('');
  const [gdsImportMode, setGdsImportMode] = useState<'etch-mask' | 'material-geometry'>('etch-mask');
  const [pendingMaskName, setPendingMaskName] = useState('GDS 刻蚀掩膜');
  const [pendingMaskMaterial, setPendingMaskMaterial] = useState('Mask');
  const [pendingMaskThickness, setPendingMaskThickness] = useState(0.3);
  const [pendingGdsScale, setPendingGdsScale] = useState(1);
  const [pendingMaskOffset, setPendingMaskOffset] = useState<[number, number]>([0, 0]);
  const [pendingMaskPolarity, setPendingMaskPolarity] = useState<'inside' | 'outside'>('inside');
  const [pendingProtectsEtch, setPendingProtectsEtch] = useState(true);
  const [gdsImportError, setGdsImportError] = useState('');
  const [slicePosition, setSlicePosition] = useState(0);
  const [rateRowMaterial, setRateRowMaterial] = useState('');
  const [multiRateMaterial, setMultiRateMaterial] = useState('');
  const [multiStickingMaterial, setMultiStickingMaterial] = useState('');
  const [health, setHealth] = useState<'checking' | 'ready' | 'offline'>('checking');
  const [inputHash, setInputHash] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [validationMessage, setValidationMessage] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false);
  const [latestRunId, setLatestRunId] = useState<string | null>(null);
  const [runInfo, setRunInfo] = useState<RunInfo | null>(null);
  const [displayInfo, setDisplayInfo] = useState<RunInfo | null>(null);
  const [runErrorDialog, setRunErrorDialog] = useState<RunFailureDialogData | null>(null);
  const [fluxResult, setFluxResult] = useState<FluxResult | null>(null);
  const [fluxResultRunId, setFluxResultRunId] = useState<string | null>(null);
  const [selectedFluxField, setSelectedFluxField] = useState('');
  const [runLog, setRunLog] = useState('');
  const [logCursor, setLogCursor] = useState(0);
  const [displayRunId, setDisplayRunId] = useState<string | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<Snapshot | null>(null);
  const [compareRunId, setCompareRunId] = useState('');
  const [compareInfo, setCompareInfo] = useState<RunInfo | null>(null);
  const [compareSnapshot, setCompareSnapshot] = useState<Snapshot | null>(null);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [activity, setActivity] = useState<Activity>('structure');
  const [selection, setSelection] = useState<Selection>({ kind: 'none' });
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<'basic' | 'physics' | 'native'>('basic');
  const [dockTab, setDockTab] = useState<DockTab>('log');
  const [dockOpen, setDockOpen] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<'structure' | 'viewer' | 'settings'>('viewer');
  const [panelWidths, setPanelWidths] = useState<PanelWidths>({ structure: 300, settings: 320 });
  const [isResizingPanels, setIsResizingPanels] = useState(false);
  const workspaceRef = useRef<HTMLElement>(null);
  const panelResizeRef = useRef<{ panel: ResizablePanel; pointerX: number; width: number } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const gdsInputRef = useRef<HTMLInputElement>(null);
  const gdsImportButtonRef = useRef<HTMLButtonElement>(null);
  const gdsImportCardRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const cursorRef = useRef(0);
  const shownRunErrorRef = useRef<string | null>(null);
  const snapshotSequence = useRef(0);
  const history3DRun = useRef<string | null>(null);
  const comparisonSequence = useRef(0);
  const validationSequence = useRef(0);
  const polling = useRef(false);

  useEffect(() => {
    if (!gdsImportOpen) return undefined;
    const updatePosition = () => {
      const anchor = gdsImportButtonRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const cardWidth = Math.min(360, Math.max(280, window.innerWidth - 24));
      const openRight = anchor.right + 12 + cardWidth <= window.innerWidth - 12;
      const left = openRight ? anchor.right + 12 : Math.max(12, anchor.left - cardWidth - 12);
      const maxTop = Math.max(12, window.innerHeight - 480);
      setGdsImportPosition({ top: Math.max(12, Math.min(anchor.top - 4, maxTop)), left, side: openRight ? 'right' : 'left' });
    };
    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [gdsImportOpen]);

  useEffect(() => {
    const closeProjectMenusOnMobile = () => {
      if (!window.matchMedia('(max-width: 900px)').matches) return;
      document.querySelectorAll<HTMLDetailsElement>('.topbar-shell details.ui-menu[open]')
        .forEach(menu => { menu.open = false; });
    };
    closeProjectMenusOnMobile();
    window.addEventListener('resize', closeProjectMenusOnMobile);
    return () => window.removeEventListener('resize', closeProjectMenusOnMobile);
  }, []);

  useEffect(() => {
    if (selectedStep) {
      setSelection({ kind: 'step', id: selectedStep });
      setInspectorOpen(true);
      setInspectorTab('basic');
    } else {
      setSelection(previous => previous.kind === 'step' ? { kind: 'none' } : previous);
    }
  }, [selectedStep]);

  useEffect(() => {
    if (selection.kind !== 'none') setInspectorOpen(true);
  }, [selection]);

  useEffect(() => {
    const selectionExists = selection.kind === 'none'
      || selection.kind === 'step' && project.steps.some(step => step.id === selection.id)
      || selection.kind === 'material' && materials.some(material => material.name === selection.name)
      || selection.kind === 'run' && (displayRunId === selection.id || runs.some(run => run.runId === selection.id))
      || selection.kind === 'artifact' && !!selectedSnapshot?.outputs?.some(artifact => artifact.fileName === selection.key);
    if (!selectionExists) setSelection({ kind: 'none' });
  }, [selection, project.steps, materials, runs, selectedSnapshot, displayRunId]);

  useEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return undefined;
    const fitToWorkspace = () => {
      if (window.matchMedia('(max-width: 900px)').matches) return;
      setPanelWidths(previous => {
        const next = fitPanelWidths(previous, workspace.clientWidth);
        return Math.abs(next.structure - previous.structure) < 0.5 && Math.abs(next.settings - previous.settings) < 0.5 ? previous : next;
      });
    };
    const observer = new ResizeObserver(fitToWorkspace);
    observer.observe(workspace);
    window.addEventListener('resize', fitToWorkspace);
    fitToWorkspace();
    return () => { observer.disconnect(); window.removeEventListener('resize', fitToWorkspace); };
  }, []);

  useEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    workspace.style.setProperty('--left-panel-width', `${panelWidths.structure}px`);
    workspace.style.setProperty('--right-panel-width', `${panelWidths.settings}px`);
  }, [panelWidths]);

  const beginPanelResize = (event: PointerEvent<HTMLDivElement>, panel: ResizablePanel) => {
    if (window.matchMedia('(max-width: 900px)').matches) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    panelResizeRef.current = { panel, pointerX: event.clientX, width: panelWidths[panel] };
    setIsResizingPanels(true);
  };
  const movePanelResize = (event: PointerEvent<HTMLDivElement>) => {
    const drag = panelResizeRef.current;
    const workspace = workspaceRef.current;
    if (!drag || !workspace) return;
    const delta = event.clientX - drag.pointerX;
    const nextWidth = drag.width + (drag.panel === 'structure' ? delta : -delta);
    setPanelWidths(previous => fitPanelWidths({ ...previous, [drag.panel]: nextWidth }, workspace.clientWidth));
  };
  const endPanelResize = () => {
    panelResizeRef.current = null;
    setIsResizingPanels(false);
  };
  const adjustPanelWidth = (event: KeyboardEvent<HTMLDivElement>, panel: ResizablePanel) => {
    const grows = panel === 'structure' ? event.key === 'ArrowRight' : event.key === 'ArrowLeft';
    const shrinks = panel === 'structure' ? event.key === 'ArrowLeft' : event.key === 'ArrowRight';
    if (!grows && !shrinks) return;
    event.preventDefault();
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const delta = grows ? 12 : -12;
    setPanelWidths(previous => fitPanelWidths({ ...previous, [panel]: previous[panel] + delta }, workspace.clientWidth));
  };

  const updateProject = useCallback((updater: (previous: Project) => Project) => setProject(previous => {
    const next = updater(previous);
    if ((next.geometry.kind === 'fin' || next.geometry.kind === 'gds') && next.schemaVersion < 7) return { ...promoteProjectToV7(next), geometry: next.geometry };
    if (previous.schemaVersion < 3 && (JSON.stringify(previous.geometry) !== JSON.stringify(next.geometry) || JSON.stringify(previous.steps) !== JSON.stringify(next.steps))) return { ...next, schemaVersion: Math.max(3, next.schemaVersion) as Project["schemaVersion"], solver: { ...next.solver, enginePreference: 'cpu' }, ...(next.dimension === 3 ? { view3D: { ...next.view3D!, visibleMaterialIds: materials.map(m => m.id) } } : {}) };
    return next;
  }), [materials]);
  const editAdvection = (key: keyof AdvectionSettings, value: AdvectionSettings[keyof AdvectionSettings]) => setProject(previous => {
    const promoted = promoteProjectToV7(previous);
    return { ...promoted, solver: { ...promoted.solver, advection: { ...promoted.solver.advection!, [key]: value } as AdvectionSettings } };
  });
  const editRayTracing = (key: keyof RayTracingSettings, value: RayTracingSettings[keyof RayTracingSettings]) => setProject(previous => {
    const promoted = promoteProjectToV7(previous);
    return { ...promoted, solver: { ...promoted.solver, rayTracing: { ...promoted.solver.rayTracing!, [key]: value } as RayTracingSettings } };
  });
  const editOutputSetting = (key: keyof OutputSettings, value: boolean) => setProject(previous => {
    const promoted = promoteProjectToV7(previous);
    return { ...promoted, outputSettings: { ...promoted.outputSettings!, [key]: value } };
  });
  const editGdsGeometry = (updater: (geometry: GDSGeometry) => GDSGeometry) => updateProject(previous =>
    previous.geometry.kind === 'gds' ? { ...previous, geometry: updater(previous.geometry) } : previous);
  const syncGdsMaskLayer = (geometry: StackGeometry): StackGeometry => {
    const index = geometry.layers.findIndex(layer => layer.source === 'gds-mask');
    if (index < 0 || !geometry.gdsMask) return geometry;
    const layer = geometry.layers[index];
    const baseHeight = geometry.baseHeight + geometry.layers.slice(0, index).reduce((sum, item) => sum + item.thickness, 0);
    return { ...geometry, gdsMask: { ...geometry.gdsMask, name: layer.name, material: layer.material, height: layer.thickness, baseHeight } };
  };
  const editStackGdsMask = (updater: (mask: GDSMaskPattern) => GDSMaskPattern) => updateProject(previous => {
    if (previous.geometry.kind !== 'stack' || !previous.geometry.gdsMask) return previous;
    return { ...previous, geometry: syncGdsMaskLayer({ ...previous.geometry, gdsMask: updater(previous.geometry.gdsMask) }) };
  });
  const active = !!runInfo && !terminalStates.has(runInfo.status);
  const changeActivity = (next: Activity) => {
    setActivity(next);
    setMobilePanel('structure');
  };
  useEffect(() => {
    const panel = document.getElementById(`${activity}-context`);
    if (panel) panel.scrollIntoView({ block: 'start' });
  }, [activity]);
  useEffect(() => {
    if (selection.kind === 'none') return;
    const selectionBelongsToActivity =
      activity === 'recipe' && selection.kind === 'step' && project.steps.some(step => step.id === selection.id)
      || activity === 'materials' && selection.kind === 'material' && materials.some(material => material.name === selection.name)
      || activity === 'results' && selection.kind === 'run' && (displayRunId === selection.id || runs.some(run => run.runId === selection.id))
      || activity === 'results' && selection.kind === 'artifact' && !!selectedSnapshot?.outputs?.some(artifact => artifact.fileName === selection.key);
    if (!selectionBelongsToActivity) {
      if (selection.kind === 'step') setSelectedStep(null);
      setSelection({ kind: 'none' });
    }
  }, [activity, selection, project.steps, materials, displayRunId, runs, selectedSnapshot]);
  const particleSteps = project.steps.filter(usesFlux).length;
  const processSteps = project.steps.filter(step => !isGeometryStep(step));
  const selectedStepObject = project.steps.find(step => step.id === selectedStep) ?? null;
  const selectedMaterial = selection.kind === 'material' ? materials.find(item => item.name === selection.name) : undefined;
  const selectedRunHash = displayRunId === latestRunId ? runInfo?.requestedInputHash : runs.find(item => item.runId === displayRunId)?.requestedInputHash;
  const isStale = !!selectedRunHash && !!inputHash && selectedRunHash !== inputHash;
  const visibleLog = displayRunId === latestRunId ? runLog : displayInfo?.log.text ?? '';
  const selectedExecution = displayInfo?.actualExecution?.find(item => item.stepId === selectedSnapshot?.stepId);
  const selectedExecutionGpu = selectedExecution?.actualParameters?.cudaDeviceReadback as {
    uuid?: string; name?: string; pciBusId?: string;
    afterProcessApply?: { readbackStatus?: string; synchronizeStatus?: number };
    process?: { processListed?: boolean; processListStatus?: string };
  } | undefined;
  const directionalActual = selectedExecution?.model === 'directional' ? selectedExecution.actualParameters ?? {} : null;
  const multiParticleActual = selectedExecution?.model === 'multiParticle' ? selectedExecution.actualParameters ?? {} : null;
  const specializedActual = selectedExecution?.model === 'specialized' ? selectedExecution.actualParameters ?? {} : null;
  const geometryActual = selectedExecution && ['removeMaterial', 'planarize', 'boolean'].includes(selectedExecution.model ?? '') ? selectedExecution.actualParameters ?? {} : null;
  const displayFlux = fluxResultRunId === displayRunId ? fluxResult : null;
  const fluxField = displayFlux?.fields.find(item => `${item.association}:${item.name}` === selectedFluxField) ?? displayFlux?.fields[0];
  const fluxValues = displayFlux && fluxField
    ? fluxField.components === 1 ? displayFlux[fluxField.association].scalars[fluxField.name] : displayFlux[fluxField.association].vectors[fluxField.name]
    : undefined;

  useEffect(() => {
    if (displayInfo?.kind === 'flux') { setDockTab('flux'); setDockOpen(true); }
    else if ((displayInfo?.intermediateOutputs?.length ?? 0) > 0) { setDockTab('intermediate'); setDockOpen(true); }
    else if ((selectedSnapshot?.outputs?.length ?? 0) > 0) { setDockTab('artifacts'); setDockOpen(true); }
    else if (directionalActual || multiParticleActual || specializedActual || geometryActual) { setDockTab('native'); setDockOpen(true); }
    else if (visibleLog) { setDockTab('log'); setDockOpen(true); }
  }, [displayInfo?.kind, displayInfo?.intermediateOutputs?.length, selectedSnapshot?.outputs?.length, directionalActual, multiParticleActual, specializedActual, geometryActual, visibleLog]);

  const refreshLists = useCallback(async () => {
    try {
      const [projectData, runData] = await Promise.all([api<{ projects: ProjectRow[] }>('/api/projects'), api<{ runs: RunSummary[] }>('/api/runs')]);
      if (!Array.isArray(projectData.projects) || !Array.isArray(runData.runs)) throw new ApiError('本机工作台返回的项目或运行记录格式无效。', null);
      setProjectRows(projectData.projects); setRuns(runData.runs);
    } catch (error) { setNotice(prettyError(error)); }
  }, []);

  useEffect(() => {
    let mounted = true;
    void Promise.all([
      api<Record<string, unknown>>('/api/health'),
      api<Capabilities>('/api/capabilities'),
      api<GpuDeviceInventory>('/api/gpu-devices'),
      api<SpecializedCatalog>('/api/specialized-models'),
    ]).then(([, cap, gpuInfo, specialized]) => {
      if (!Array.isArray(specialized.models)) throw new ApiError('本机工作台返回的专用模型目录格式无效。', null);
      if (mounted) { setHealth('ready'); setCapabilities(cap); setGpuInventory(gpuInfo); setSpecializedCatalog(specialized); if (specialized.models.length) setNewSpecializedModel(specialized.models[0].id); }
    })
      .catch(() => { if (mounted) setHealth('offline'); });
    void refreshLists();
    void api<{ materials: MaterialCatalogItem[] }>('/api/materials').then(result => {
      if (!Array.isArray(result.materials)) throw new ApiError('本机工作台返回的材料目录格式无效。', null);
      setMaterials(result.materials);
    }).catch(error => setNotice(`材料目录加载失败：${prettyError(error)}`));
    return () => { mounted = false; };
  }, [refreshLists]);

  useEffect(() => {
    const sequence = ++validationSequence.current;
    const timeout = window.setTimeout(async () => {
      try {
        const result = await api<{ valid: boolean; warnings: string[]; requestedInputHash: string }>('/api/projects/validate', { method: 'POST', body: JSON.stringify(project) });
        if (sequence === validationSequence.current) {
          setInputHash(result.requestedInputHash); setWarnings(result.warnings); setValidationMessage('输入通过校验');
        }
      } catch (error) {
        if (sequence === validationSequence.current) { setInputHash(''); setWarnings([]); setValidationMessage(prettyError(error)); }
      }
    }, 350);
    return () => window.clearTimeout(timeout);
  }, [project]);

  const fetchSnapshot = useCallback(async (runId: string, stepId: string) => {
    try {
      const sequence = ++snapshotSequence.current;
      const snapshot = await api<Snapshot>(`/api/runs/${runId}/snapshots/${encodeURIComponent(stepId)}`);
      const dimension = snapshotDimension(snapshot);
      if (sequence !== snapshotSequence.current) return;
      if (dimension === 3 && history3DRun.current !== runId) {
        // A reopened history result owns its view. The current editor can be
        // 2D or have an unrelated material filter and camera.
        history3DRun.current = runId;
        setHistoryView3D({
          ...structuredClone(defaultView3D),
          visibleMaterialIds: [...new Set(snapshot.cellData.materialIds)],
        });
      }
      setSelectedSnapshot(snapshot); setSelectedStepId(stepId); setDisplayRunId(runId);
    } catch (error) { setNotice(prettyError(error)); }
  }, []);

  const fetchRunInfo = useCallback(async (runId: string, cursor = 0) => {
    return api<RunInfo>(`/api/runs/${runId}?cursor=${cursor}`);
  }, []);

  const fetchFluxResult = useCallback(async (runId: string, expectedMeshIdentity?: string) => {
    const result = await api<FluxResult>(`/api/runs/${runId}/flux-result`);
    if (expectedMeshIdentity && result.meshIdentity !== expectedMeshIdentity) throw new Error('通量数据的网格身份与运行记录不一致。');
    setFluxResult(result); setFluxResultRunId(runId);
    setSelectedFluxField(previous => result.fields.some(field => `${field.association}:${field.name}` === previous) ? previous : result.fields[0] ? `${result.fields[0].association}:${result.fields[0].name}` : '');
  }, []);

  useEffect(() => {
    if (!latestRunId || !runInfo || terminalStates.has(runInfo.status)) return undefined;
    let mounted = true;
    const poll = async () => {
      if (polling.current) return;
      polling.current = true;
      try {
        const info = await fetchRunInfo(latestRunId, cursorRef.current);
        if (!mounted) return;
        setRunInfo(info);
        if (displayRunId === latestRunId) setDisplayInfo(info);
        if (info.log.nextCursor > cursorRef.current) {
          cursorRef.current = info.log.nextCursor; setLogCursor(info.log.nextCursor);
          if (info.log.text) setRunLog(previous => (previous + info.log.text).slice(-250_000));
        }
        if (displayRunId === latestRunId && autoFollow && info.snapshots.length) {
          const newest = info.snapshots[info.snapshots.length - 1];
          if (selectedStepId !== newest.stepId) await fetchSnapshot(latestRunId, newest.stepId);
        }
        if (displayRunId === latestRunId && info.kind === 'flux' && info.status === 'succeeded' && info.fluxResult && fluxResultRunId !== latestRunId) {
          await fetchFluxResult(latestRunId, info.fluxResult.meshIdentity);
        }
        if (terminalStates.has(info.status)) { setHealth('ready'); void refreshLists(); }
      } catch (error) { if (mounted) setNotice(prettyError(error)); }
      finally { polling.current = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [latestRunId, runInfo?.status, fetchRunInfo, fetchSnapshot, fetchFluxResult, refreshLists, displayRunId, autoFollow, selectedStepId, fluxResultRunId]);

  useEffect(() => {
    if (!displayInfo || !errorRunStates.has(displayInfo.status)) return;
    const key = `${displayInfo.runId}:${displayInfo.status}`;
    if (shownRunErrorRef.current === key) return;
    shownRunErrorRef.current = key;
    setRunErrorDialog(runFailureDialogData(displayInfo));
  }, [displayInfo]);

  const loadRun = useCallback(async (runId: string, preferLatest = false) => {
    try {
      const info = await fetchRunInfo(runId, 0);
      if (runId === latestRunId) setRunInfo(info);
      setDisplayInfo(info);
      setDisplayRunId(runId); setAutoFollow(false);
      if (info.kind === 'flux' && info.status === 'succeeded' && info.fluxResult) await fetchFluxResult(runId, info.fluxResult.meshIdentity);
      else if (fluxResultRunId !== runId) { setFluxResult(null); setFluxResultRunId(null); }
      const row = preferLatest ? info.snapshots.at(-1) : info.snapshots[0];
      if (row) await fetchSnapshot(runId, row.stepId);
      else setSelectedSnapshot(null);
    } catch (error) { setNotice(prettyError(error)); }
  }, [fetchRunInfo, fetchSnapshot, fetchFluxResult, latestRunId, fluxResultRunId]);

  const startRun = async (kind: 'preview' | 'simulate') => {
    setLoading(true); setNotice(''); setFluxResult(null); setFluxResultRunId(null);
    try {
      await api('/api/projects/validate', { method: 'POST', body: JSON.stringify(project) });
      const started = await api<{ runId: string; status: string; requestedInputHash: string }>('/api/runs', { method: 'POST', body: JSON.stringify({ kind, project }) });
      const initialInfo: RunInfo = { runId: started.runId, kind, status: started.status, currentStepId: null, completedStepIds: [], elapsedSeconds: 0, requestedInputHash: started.requestedInputHash, stepTotal: project.steps.filter(step => step.enabled).length, log: { cursor: 0, nextCursor: 0, truncated: false, text: '' }, snapshots: [] };
      setLatestRunId(started.runId); setDisplayRunId(started.runId); setRunInfo(initialInfo); setDisplayInfo(initialInfo);
      setRunLog(''); cursorRef.current = 0; setLogCursor(0); setSelectedSnapshot(null); setSelectedStepId(null); setAutoFollow(true);
      setNotice(kind === 'preview' ? '初始结构预览已提交。' : '工艺仿真已提交。');
      void refreshLists();
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); }
  };

  const startNativeSlice = async () => {
    if (project.dimension !== 3 || project.schemaVersion !== 7) return;
    setLoading(true); setNotice(''); setFluxResult(null); setFluxResultRunId(null);
    try {
      const started = await api<{ runId: string; status: string; requestedInputHash: string }>('/api/runs/slice', {
        method: 'POST', body: JSON.stringify({ project, sliceRequest: { axis: 1, position: slicePosition } }),
      });
      const initialInfo: RunInfo = { runId: started.runId, kind: 'slice', status: started.status, currentStepId: null,
        completedStepIds: [], elapsedSeconds: 0, requestedInputHash: started.requestedInputHash, stepTotal: 0,
        log: { cursor: 0, nextCursor: 0, truncated: false, text: '' }, snapshots: [] };
      setLatestRunId(started.runId); setDisplayRunId(started.runId); setRunInfo(initialInfo); setDisplayInfo(initialInfo);
      setRunLog(''); cursorRef.current = 0; setLogCursor(0); setSelectedSnapshot(null); setSelectedStepId(null); setAutoFollow(true);
      setNotice('ViennaPS Slice 真实切片已提交；运行记录将同时保留 3D 源结构和输出 2D 网格。'); void refreshLists();
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); }
  };

  const startFlux = async (stepId: string) => {
    setLoading(true); setNotice(''); setFluxResult(null); setFluxResultRunId(null);
    try {
      await api('/api/projects/validate', { method: 'POST', body: JSON.stringify(project) });
      const started = await api<{ runId: string; status: string; requestedInputHash: string }>('/api/runs/flux', { method: 'POST', body: JSON.stringify({ project, stepId }) });
      const initialInfo: RunInfo = { runId: started.runId, kind: 'flux', status: started.status, currentStepId: null, completedStepIds: [], elapsedSeconds: 0,
        requestedInputHash: started.requestedInputHash, stepTotal: 0, log: { cursor: 0, nextCursor: 0, truncated: false, text: '' }, snapshots: [] };
      setLatestRunId(started.runId); setDisplayRunId(started.runId); setRunInfo(initialInfo); setDisplayInfo(initialInfo);
      setRunLog(''); cursorRef.current = 0; setLogCursor(0); setSelectedSnapshot(null); setSelectedStepId(null); setAutoFollow(true);
      setNotice('ViennaPS Process.calculateFlux 单次通量计算已提交；不会推进几何。'); void refreshLists();
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); }
  };

  const exportFluxCsv = () => {
    if (!fluxResult) return;
    const field = fluxResult.fields.find(item => `${item.association}:${item.name}` === selectedFluxField) ?? fluxResult.fields[0];
    if (!field) return;
    const values = field.components === 1 ? fluxResult[field.association].scalars[field.name] : fluxResult[field.association].vectors[field.name];
    if (!values) return;
    const quote = (value: unknown) => `"${String(value).replaceAll('"', '""')}"`;
    const heading = field.nativeUnit ? `${field.name} (${field.nativeUnit})` : `${field.name} (native unit not declared)`;
    const rows = [['tupleIndex', heading], ...values.map((value, index) => [index, Array.isArray(value) ? value.join(';') : value])];
    saveBlob(`viennaps-flux-${fluxResult.execution.stepId}-${field.name}.csv`, new Blob([rows.map(row => row.map(quote).join(',')).join('\n') + '\n'], { type: 'text/csv;charset=utf-8' }));
  };

  const cancelRun = async () => {
    if (!latestRunId) return;
    try { const result = await api<{ status: string }>(`/api/runs/${latestRunId}/cancel`, { method: 'POST' }); setRunInfo(previous => previous ? { ...previous, status: result.status } : previous); }
    catch (error) { setNotice(prettyError(error)); }
  };

  const saveProject = async () => {
    setLoading(true); setNotice('');
    try {
      const result = projectId
        ? await api<{ id: string; project: Project }>(`/api/projects/${projectId}`, { method: 'PUT', body: JSON.stringify(project) })
        : await api<{ id: string; project: Project }>('/api/projects', { method: 'POST', body: JSON.stringify(project) });
      setProject(result.project); setProjectId(result.id); setSavedProjectJson(JSON.stringify(result.project)); setNotice('项目已保存到本机工作台。'); await refreshLists();
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); }
  };

  const createCustomMaterial = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setMaterialBusy(true);
    try {
      const result = await api<MaterialCatalogItem>('/api/materials', { method: 'POST', body: JSON.stringify({
        name: materialDraft.name, color: materialDraft.color, category: materialDraft.category,
        densityGcm3: materialDraft.densityGcm3.trim() ? Number(materialDraft.densityGcm3) : null,
        conductive: materialDraft.conductive, description: materialDraft.description,
      }) });
      setMaterials(previous => [...previous, result]);
      setProject(previous => previous.dimension === 3 && previous.view3D
        ? { ...previous, view3D: { ...previous.view3D, visibleMaterialIds: initialVisibleMaterialIds(
          [...materials, result], previous.geometry, [result.id, ...previous.view3D.visibleMaterialIds]) } }
        : previous);
      setMaterialDraft({ name: '', color: '#8b5cf6', category: 'Generic', densityGcm3: '', conductive: false, description: '' });
      setNotice(`已保存自定义材料 ${result.name}（原生 ID ${result.id}）。材料名称和元数据不会自动建立工艺物理模型。`);
    } catch (error) { setNotice(prettyError(error)); }
    finally { setMaterialBusy(false); }
  };

  const saveCustomMaterialMetadata = async (name: string, event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setMaterialBusy(true);
    const data = new FormData(event.currentTarget);
    const density = String(data.get('densityGcm3') ?? '').trim();
    try {
      const result = await api<MaterialCatalogItem>(`/api/materials/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify({
        color: String(data.get('color') ?? '#ffffff'), category: String(data.get('category') ?? 'Generic'), densityGcm3: density ? Number(density) : null,
        conductive: data.has('conductive'), description: String(data.get('description') ?? ''),
      }) });
      setMaterials(previous => previous.map(item => item.name === name ? result : item));
      setNotice(`已更新 ${name} 的元数据。该材料的工艺行为仍由配方模型和速率参数决定。`);
    } catch (error) { setNotice(prettyError(error)); }
    finally { setMaterialBusy(false); }
  };

  const deleteCustomMaterial = async (material: MaterialCatalogItem) => {
    setDeletingMaterial(material.name); setDeleteMaterialError(''); setNotice('');
    try {
      await api(`/api/materials/${encodeURIComponent(material.name)}`, { method: 'DELETE', body: JSON.stringify({ project }) });
      setMaterials(previous => previous.filter(item => item.name !== material.name));
      setProject(previous => previous.view3D
        ? { ...previous, view3D: { ...previous.view3D, visibleMaterialIds: previous.view3D.visibleMaterialIds.filter(id => id !== material.id) } }
        : previous);
      setSelection(previous => previous.kind === 'material' && previous.name === material.name ? { kind: 'none' } : previous);
      setDeleteMaterialCandidate(null);
      setNotice(`已删除自定义材料 ${material.name}；原生 ID ${material.id} 已保留，不会重新分配。`);
    } catch (error) { setDeleteMaterialError(prettyError(error)); }
    finally { setDeletingMaterial(null); }
  };

  const loadGdsAsset = async (assetId: string) => {
    const asset = await api<GDSAsset>(`/api/gds-assets/${assetId}`);
    setGdsAsset(asset);
    return asset;
  };

  const uploadGdsFile = async (file: File) => {
    setLoading(true); setNotice(''); setGdsImportError('');
    try {
      const response = await fetch(`/api/gds-assets?file_name=${encodeURIComponent(file.name)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
      });
      const result = await response.json() as GDSAsset | { detail?: unknown };
      if (!response.ok) {
        const detail = 'detail' in result ? result.detail : undefined;
        throw new Error(typeof detail === 'string' ? detail : `GDS 上传失败：HTTP ${response.status}`);
      }
      const asset = result as GDSAsset;
      const firstUsableStructure = asset.structures.find(item => gdsMaskPairs(asset, item.name).length > 0)?.name
        ?? asset.structures.find(item => gdsLayerIds(asset, item.name).length > 0)?.name;
      if (!firstUsableStructure) throw new Error('GDS 文件不包含可用的结构单元或图层。');
      const firstPair = (gdsImportMode === 'etch-mask' ? gdsMaskPairs(asset, firstUsableStructure) : gdsPairs(asset, firstUsableStructure))[0];
      setPendingGdsScale(1);
      setPendingMaskOffset([0, 0]);
      setPendingGdsAsset(asset);
      setPendingGdsStructure(firstUsableStructure);
      setPendingGdsLayerDatatype(firstPair ? `${firstPair[0]}/${firstPair[1]}` : '');
      setGdsImportError('');
      setNotice(gdsMaskPairs(asset, firstUsableStructure).length
        ? '已读取 GDS 文件。掩膜列表只显示含闭合边界多边形的 layer/datatype 组合。'
        : '已读取 GDS 文件，但没有发现闭合边界多边形；请切换到高级材料几何导入，或选择其他 GDS 文件。');
    } catch (error) { setGdsImportError(prettyError(error)); }
    finally { setLoading(false); if (gdsInputRef.current) gdsInputRef.current.value = ''; }
  };

  const makeGdsMaskProject = (
    baseProject: Project,
    source: GDSGeometry,
    pair: [number, number],
    settings: { maskName: string; maskMaterial: string; maskThickness: number; polarity: 'inside' | 'outside'; protectsEtch: boolean },
  ): Project => {
    const existingStack = baseProject.geometry.kind === 'stack'
      ? baseProject.geometry
      : { kind: 'stack', substrateMaterial: 'Si', baseHeight: 0, layers: [], pattern: { kind: 'none' } } as StackGeometry;
    const layers = existingStack.layers.filter(layer => layer.source !== 'gds-mask');
    const maskLayer: Layer = {
      id: `gds_mask_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`,
      name: settings.maskName.trim() || 'GDS 掩膜', material: settings.maskMaterial,
      thickness: settings.maskThickness, role: 'mask', source: 'gds-mask',
    };
    const stack: StackGeometry = {
      ...existingStack,
      layers: [...layers, maskLayer],
      pattern: { kind: 'gds' },
      gdsMask: {
        name: maskLayer.name,
        assetId: source.assetId, fileName: source.fileName, structure: source.structure,
        layer: pair[0], datatype: pair[1], unitScale: source.unitScale, position: source.position,
        baseHeight: existingStack.baseHeight + layers.reduce((sum, layer) => sum + layer.thickness, 0),
        height: maskLayer.thickness, material: maskLayer.material,
        retainedRegion: settings.polarity, protectsEtch: settings.protectsEtch,
      },
    };
    const next: Project = {
      ...baseProject,
      schemaVersion: 7,
      dimension: 3,
      domain: { ...baseProject.domain, yExtent: baseProject.domain.yExtent ?? baseProject.domain.xExtent, boundaryXY: baseProject.domain.boundaryXY ?? 'reflective' },
      geometry: stack,
      outputSettings: { ...(baseProject.outputSettings ?? defaultProject.outputSettings!), volumeMesh: true },
      view3D: { ...structuredClone(defaultView3D), visibleMaterialIds: initialVisibleMaterialIds(materials, stack) },
    };
    delete next.view;
    return next;
  };

  const importPendingGds = async () => {
    if (!pendingGdsAsset || !pendingGdsStructure) return;
    if (gdsImportMode === 'etch-mask' && project.dimension !== 3) {
      setGdsImportError('GDS 掩膜刻蚀需要 3D 工程。请先将工程切换到 3D 模式，再导入掩膜。');
      return;
    }
    const layerIds = gdsLayerIds(pendingGdsAsset, pendingGdsStructure);
    if (!layerIds.length) {
      setGdsImportError('所选结构没有可映射的图层，请换一个结构单元。');
      return;
    }
    const defaultMaterial = materials.find(item => item.name === 'SiO2')?.name ?? materials[0]?.name ?? 'Si';
    const projectV7 = promoteProjectToV7(project);
    const gdsGeometry: GDSGeometry = {
      kind: 'gds', assetId: pendingGdsAsset.assetId, fileName: pendingGdsAsset.fileName, structure: pendingGdsStructure,
      position: gdsImportMode === 'etch-mask' ? [pendingMaskOffset[0], pendingMaskOffset[1], 0] : [0, 0, 0],
      unitScale: gdsImportMode === 'etch-mask' ? pendingGdsScale : 1,
      layers: layerIds.slice(0, 64).map(layer => ({
        layer, material: defaultMaterial,
        ...(project.dimension === 3 ? { baseHeight: 0, height: Math.max(0.5, 2 * project.domain.gridDelta), mask: false } : {}),
      })),
    };
    let importedProject: Project = { ...projectV7, geometry: gdsGeometry };
    let nativeMaskInspection: Record<string, unknown> | null = null;
    if (gdsImportMode === 'etch-mask') {
      const pair = gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).find(([layer, datatype]) => `${layer}/${datatype}` === pendingGdsLayerDatatype);
      if (!pair) {
        setGdsImportError('请选择一个有效的 GDS 图层 / datatype 组合。');
        return;
      }
      if (!pendingMaskName.trim() || !Number.isFinite(pendingGdsScale) || pendingGdsScale <= 0 || pendingGdsScale > 1_000_000
          || !Number.isFinite(pendingMaskThickness) || pendingMaskThickness < 2 * project.domain.gridDelta || pendingMaskThickness > 100
          || pendingMaskOffset.some(value => !Number.isFinite(value) || Math.abs(value) > 40)) {
        setGdsImportError('请检查版图比例、偏移和掩膜厚度。掩膜厚度需至少跨越两个网格间距。');
        return;
      }
      importedProject = makeGdsMaskProject(projectV7, gdsGeometry, pair, {
        maskName: pendingMaskName,
        maskMaterial: pendingMaskMaterial, maskThickness: pendingMaskThickness,
        polarity: pendingMaskPolarity, protectsEtch: pendingProtectsEtch,
      });
    }
    setLoading(true); setGdsImportError('');
    try {
      if (gdsImportMode === 'etch-mask') {
        const pair = gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).find(([layer, datatype]) => `${layer}/${datatype}` === pendingGdsLayerDatatype)!;
        const result = await api<{ inspection: Record<string, unknown> }>(
          `/api/gds-assets/${pendingGdsAsset.assetId}/inspect`, { method: 'POST', body: JSON.stringify({
            structure: pendingGdsStructure, selectedLayers: [pair[0]], selectedLayerDatatypes: [[pair[0], pair[1]]],
            dimension: 2, gridDelta: project.domain.gridDelta, xExtent: project.domain.xExtent,
            boundary: 'reflective', unitScale: pendingGdsScale, position: pendingMaskOffset,
          }) });
        const selectedNativeLayers = result.inspection.selectedNativeLayers as number[] | undefined;
        const pointCounts = result.inspection.levelSetPointCounts as Record<string, number> | undefined;
        if (!selectedNativeLayers?.includes(pair[0]) || !Number.isFinite(pointCounts?.[String(pair[0])]) || Number(pointCounts?.[String(pair[0])]) <= 0) {
          throw new Error(`Layer ${pair[0]} · datatype ${pair[1]} 没有生成可用的 ViennaPS 掩膜网格，请选择含闭合版图图形的组合。`);
        }
        nativeMaskInspection = result.inspection;
      }
      await api('/api/projects/validate', { method: 'POST', body: JSON.stringify(importedProject) });
    } catch (error) {
      const message = prettyError(error);
      setGdsImportError(message.includes('native GDS layer readback') || message.includes('level-set')
        ? `ViennaPS 无法从所选 layer/datatype 读取掩膜图形，请换一个含闭合版图图形的组合。${message}`
        : message);
      return;
    } finally { setLoading(false); }
    setGdsAsset(pendingGdsAsset); setGdsInspection(null); setProject(importedProject); setProjectId(null);
    if (gdsImportMode === 'etch-mask') { setSelectedStep(importedProject.steps[0]?.id ?? null); setActivity('structure'); }
    setGdsImportOpen(false); setPendingGdsAsset(null); setPendingGdsStructure(''); setGdsImportError('');
    setPendingGdsLayerDatatype('');
    setNotice(gdsImportMode === 'etch-mask'
      ? `已导入 ${pendingGdsAsset.fileName} · ${pendingGdsStructure}，并将“${pendingMaskName.trim()}”加入“多层”顶层掩膜。ViennaPS 原生检查通过：所选掩膜网格点数 ${JSON.stringify(nativeMaskInspection?.levelSetPointCounts)}。${projectV7.geometry.kind === 'stack' ? '现有多层和工艺步骤已保留。' : '原工程没有多层结构，已建立最小 Si 基底；请在“多层”中确认基底并添加需要刻蚀的薄膜。'} 请生成结构预览并核对。`
      : `已导入 ${pendingGdsAsset.fileName} · ${pendingGdsStructure}。请为可用图层指定材料，再运行 ViennaPS 原生预览。${layerIds.length > 64 ? ' 默认只选前 64 个图层，最多映射 64 层。' : ''}`);
  };

  const startGdsMaskTransfer = () => {
    if (project.geometry.kind !== 'gds' || !gdsAsset) return;
    const structure = project.geometry.structure;
    const pair = gdsMaskPairs(gdsAsset, structure)[0] ?? gdsPairs(gdsAsset, structure)[0];
    if (!pair) { setNotice('所选结构没有可用的 layer/datatype 图形。'); return; }
    setPendingGdsAsset(gdsAsset);
    setPendingGdsStructure(structure);
    setPendingGdsScale(project.geometry.unitScale);
    setPendingMaskOffset([project.geometry.position[0], project.geometry.position[1]]);
    setPendingGdsLayerDatatype(`${pair[0]}/${pair[1]}`);
    setGdsImportMode('etch-mask');
    setGdsImportError('');
    setGdsImportOpen(true);
    setNotice('请选择 GDS 掩膜图层。导入后掩膜会作为“多层”的顶层图案；基底、薄膜和刻蚀步骤由当前项目设置。');
  };

  const inspectGds = async () => {
    if (project.geometry.kind !== 'gds') return;
    const geometry = project.geometry;
    setLoading(true); setGdsInspection(null); setNotice('正在 bounded_child.py 保护下读取 ViennaPS GDSGeometry…');
    try {
      const result = await api<{ inspection: Record<string, unknown>; boundedChild: Record<string, unknown> }>(
        `/api/gds-assets/${geometry.assetId}/inspect`, { method: 'POST', body: JSON.stringify({
          structure: geometry.structure, selectedLayers: geometry.layers.map(layer => layer.layer),
          dimension: project.dimension, gridDelta: project.domain.gridDelta,
          xExtent: project.domain.xExtent, ...(project.dimension === 3 ? { yExtent: project.domain.yExtent } : {}),
          boundary: project.dimension === 3 ? project.domain.boundaryXY : project.domain.xBoundary,
          unitScale: geometry.unitScale, position: geometry.position.slice(0, 2),
          probeHeight: Math.max(project.domain.gridDelta * 2, geometry.layers.find(layer => layer.height)?.height ?? 1),
        }) });
      setGdsInspection(result.inspection);
      setNotice(`ViennaPS 原生结构检查完成：${geometry.structure}，已选图层 ${JSON.stringify(result.inspection.selectedNativeLayers)}。`);
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); }
  };

  const openProject = async () => {
    if (!savedSelection) return;
    try {
      const result = await api<{ id: string; project: Project }>(`/api/projects/${savedSelection}`);
      const loadedProject = normalizeGdsMaskLayer(result.project);
      const firstStep = loadedProject.steps[0] ?? null;
      setProject(loadedProject); setProjectId(result.id); setSavedProjectJson(JSON.stringify(loadedProject)); setSelectedStep(firstStep?.id ?? null); setSelection(firstStep ? { kind: 'step', id: firstStep.id } : { kind: 'none' }); setActivity(firstStep ? 'recipe' : 'structure'); setMobilePanel(firstStep ? 'settings' : 'structure'); setInspectorOpen(true); setGdsInspection(null);
      const projectAsset = gdsAssetIdFromProject(loadedProject); if (projectAsset) await loadGdsAsset(projectAsset); else setGdsAsset(null);
      setNotice('项目已从本机工作台打开。');
    } catch (error) { setNotice(prettyError(error)); }
  };

  const downloadContinuationPackage = async (value: { project: Project; replay?: unknown }, filename: string) => {
    const response = await fetch('/api/continuation-package/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
    if (!response.ok) {
      const text = await response.text();
      let message = `导出状态包失败：HTTP ${response.status}`;
      try { const data = JSON.parse(text) as { detail?: unknown }; if (typeof data.detail === 'string') message = data.detail; } catch { /* keep HTTP status */ }
      throw new Error(message);
    }
    saveBlob(filename, await response.blob());
  };

  const exportProject = async () => {
    try {
      const validated = await api<{ normalizedProject: Project }>('/api/projects/validate', { method: 'POST', body: JSON.stringify(project) });
      const exportReady = validated.normalizedProject;
      if (exportReady.continuationState || exportReady.geometry.kind === 'gds') await downloadContinuationPackage({ project: exportReady }, `${exportReady.name || 'project'}.zip`);
      else saveBlob(`${exportReady.name || 'project'}.json`, new Blob([JSON.stringify(exportReady, null, 2)], { type: 'application/json' }));
    } catch (error) { setNotice(prettyError(error)); }
  };

  const startReplay = async (replay: unknown) => {
    const started = await api<{ runId: string; status: string; requestedInputHash: string }>('/api/runs/replay', { method: 'POST', body: JSON.stringify(replay) });
    const replayData = replay as { normalizedProject: Project; analysisMode?: 'process' | 'flux' | 'slice' };
    const replayProject = replayData.normalizedProject;
    setProject(replayProject); setProjectId(null); setSelectedStep(null); setSelection({ kind: 'run', id: started.runId }); setActivity('results'); setMobilePanel('structure'); setInspectorOpen(true);
    setGdsInspection(null);
    const replayAsset = gdsAssetIdFromProject(replayProject); if (replayAsset) await loadGdsAsset(replayAsset); else setGdsAsset(null);
    const replayKind = replayData.analysisMode === 'flux' ? 'flux' : replayData.analysisMode === 'slice' ? 'slice' : replayProject.steps.some(step => step.enabled) ? 'simulate' : 'preview';
    const initialInfo: RunInfo = { runId: started.runId, kind: replayKind, status: started.status, currentStepId: null, completedStepIds: [], elapsedSeconds: 0, requestedInputHash: started.requestedInputHash, stepTotal: replayKind === 'simulate' ? replayProject.steps.filter(step => step.enabled).length : 0, log: { cursor: 0, nextCursor: 0, truncated: false, text: '' }, snapshots: [] };
    setFluxResult(null); setFluxResultRunId(null);
    setLatestRunId(started.runId); setDisplayRunId(started.runId); setRunInfo(initialInfo); setDisplayInfo(initialInfo);
    setRunLog(''); cursorRef.current = 0; setSelectedSnapshot(null); setSelectedStepId(null); setAutoFollow(true); setNotice('重放任务已提交；页面显示其起点是初始结构还是保存的 ViennaPS Domain 状态。');
  };

  const importFile = async (file: File) => {
    try {
      setLoading(true);
      if (file.name.toLowerCase().endsWith('.zip')) {
        if (file.size > maxPackageBytes) throw new Error('状态包不能超过 512 MiB。');
        const imported = await api<{ project: Project; replay?: unknown | null }>('/api/continuation-package/import', { method: 'POST', body: file, headers: { 'Content-Type': 'application/zip' } });
        if (imported.replay) await startReplay(imported.replay);
        else {
          const importedProject = normalizeGdsMaskLayer(imported.project);
          setProject(importedProject); setProjectId(null); setSelectedStep(null); setSelection({ kind: 'none' }); setActivity('structure'); setMobilePanel('structure'); setInspectorOpen(true); setGdsInspection(null);
          const projectAsset = gdsAssetIdFromProject(importedProject); if (projectAsset) await loadGdsAsset(projectAsset); else setGdsAsset(null);
          setNotice(projectAsset ? 'GDS 项目包和源布局已校验并导入。' : 'ViennaPS Domain 状态包已校验并导入，可将该状态用作新工艺的起点。');
        }
        return;
      }
      const parsed: unknown = JSON.parse(await file.text());
      if (typeof parsed === 'object' && parsed !== null && 'format' in parsed && (parsed as { format?: unknown }).format === 'viennaps-replay') {
        await startReplay(parsed);
      } else {
        const valid = await api<{ normalizedProject: Project }>('/api/projects/validate', { method: 'POST', body: JSON.stringify(parsed) });
        const importedProject = normalizeGdsMaskLayer(valid.normalizedProject);
        setProject(importedProject); setProjectId(null); setSelectedStep(null); setSelection({ kind: 'none' }); setActivity('structure'); setMobilePanel('structure'); setInspectorOpen(true); setGdsInspection(null);
        const projectAsset = gdsAssetIdFromProject(importedProject); if (projectAsset) await loadGdsAsset(projectAsset); else setGdsAsset(null);
        setNotice('项目 JSON 已导入。');
      }
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); if (fileInputRef.current) fileInputRef.current.value = ''; }
  };

  const createContinuation = async (runId: string, stepId: string) => {
    try {
      setLoading(true);
      const result = await api<{ project: Project; continuation: { sourceRunId: string; sourceStepId: string; engineVersions: { viennaps: string; viennals: string } } }>(`/api/runs/${runId}/continuation-project/${encodeURIComponent(stepId)}`);
      const continuationProject = result.project.continuationState
        ? { ...result.project, continuationState: { ...result.project.continuationState, sourceRunId: result.continuation.sourceRunId, sourceStepId: result.continuation.sourceStepId } }
        : result.project;
      setProject(continuationProject); setProjectId(null); setSavedSelection(''); setSelectedStep(null); setSelection({ kind: 'none' }); setActivity('structure'); setMobilePanel('structure'); setInspectorOpen(true); clearDisplayedRun();
      setNotice('已从选中快照创建新工艺；起始 Domain 来自保存的 ViennaPS 状态。');
    } catch (error) { setNotice(prettyError(error)); }
    finally { setLoading(false); }
  };

  const discardContinuationState = () => {
    if (!project.continuationState) return;
    const { continuationState: _state, ...fromInitial } = project;
    setProject({ ...fromInitial, schemaVersion: project.schemaVersion >= 7 ? 7 : 6 }); setNotice('已移除保存状态引用；后续运行将从项目中的初始结构开始。');
  };

  const downloadRun = async (format: 'zip' | 'replay') => {
    if (!displayRunId) return;
    try {
      if (format === 'replay') {
        const replay = await api<Record<string, unknown>>(`/api/runs/${displayRunId}/replay-project`);
        const normalizedReplay = (replay as { normalizedProject?: Project }).normalizedProject;
        if (normalizedReplay?.continuationState) await downloadContinuationPackage({ project: normalizedReplay, replay }, `replay-project-${displayRunId}.zip`);
        else saveBlob(`replay-project-${displayRunId}.json`, new Blob([JSON.stringify(replay, null, 2)], { type: 'application/json' }));
      } else {
        const response = await fetch(`/api/runs/${displayRunId}/download`);
        if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`);
        saveBlob(`viennaps-run-${displayRunId}.zip`, await response.blob());
      }
    } catch (error) { setNotice(prettyError(error)); }
  };

  const exportSvg = () => {
    const svg = svgRef.current;
    if (!svg) { setNotice('请先加载一个网格结果。'); return; }
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', String(Math.round(svg.viewBox.baseVal.width)));
    clone.setAttribute('height', String(Math.round(svg.viewBox.baseVal.height)));
    saveBlob(`viennaps-view-${displayRunId ?? 'snapshot'}.svg`, new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' }));
  };

  const loadComparison = async (runId: string, stepId?: string) => {
    setCompareRunId(runId);
    if (!runId) { comparisonSequence.current++; setCompareInfo(null); setCompareSnapshot(null); setSelectedStepId(selectedSnapshot?.stepId ?? null); return; }
    try {
      const info = await fetchRunInfo(runId, 0); setCompareInfo(info);
      const row = info.snapshots.find(item => item.stepId === stepId) ?? info.snapshots.at(-1);
      const sequence = ++comparisonSequence.current;
      if (row) {
        const snapshot = await api<Snapshot>(`/api/runs/${runId}/snapshots/${row.stepId}`);
        snapshotDimension(snapshot);
        if (sequence === comparisonSequence.current) setCompareSnapshot(snapshot);
      }
    } catch (error) { setNotice(prettyError(error)); setCompareSnapshot(null); }
  };

  const addStep = (model: Step['model'] = 'isotropic') => {
    const id = newStepId();
    if (model === 'specialized') {
      const spec = specializedCatalog?.models.find(row => row.id === newSpecializedModel);
      if (!spec) { setNotice('ViennaPS 专用模型目录尚未加载完成。'); return; }
      const step: Step = { id, name: `步骤 ${project.steps.length + 1}`, enabled: true, model: 'specialized',
        nativeModel: spec.id, operation: spec.operation, nativeParameters: specializedDefaults(spec, project.geometry),
        ...(spec.id === 'oxidation' ? {} : { duration: 0.2 }),
        ...(spec.depositMaterial ? { depositMaterial: spec.depositMaterial } : spec.depositMaterialSelectable ? { depositMaterial: materials.find(item => item.name === 'Al2O3')?.name ?? materials[0]?.name ?? 'Al2O3' } : {}) };
      setProject(previous => { const promoted = promoteProjectToV7(previous); return { ...promoted, steps: [...promoted.steps, step] }; });
      setSelectedStep(id); return;
    }
    if (model === 'multiParticle') {
      const step: Step = { id, name: `步骤 ${project.steps.length + 1}`, enabled: true, operation: 'etch', model,
        duration: 0.2, ...defaultMultiParticleFields() };
      setProject(previous => {
        const promoted = promoteProjectToV7(previous);
        return { ...promoted, steps: [...promoted.steps, step] };
      });
      setSelectedStep(id);
      return;
    }
    if (model === 'directional') {
      const step: Step = { id, name: `步骤 ${project.steps.length + 1}`, enabled: true, operation: 'etch', model,
        direction: defaultDirection(project.dimension), directionalRateMagnitude: 0.2, isotropicRateMagnitude: 0,
        calculateVisibility: true, duration: 0.2 };
      setProject(previous => {
        const promoted = promoteProjectToV5(previous);
        return { ...promoted, steps: [...promoted.steps, step] };
      });
      setSelectedStep(id);
      return;
    }
    const step: Step = { id, name: `步骤 ${project.steps.length + 1}`, enabled: true, operation: 'etch', model, rateMagnitude: 0.2, duration: 0.2,
      ...(project.schemaVersion >= 4 ? { rateMode: 'uniform' as const } : {}),
      ...(model === 'singleParticle' ? { stickingProbability: 0.5, sourceExponent: 1 } : {}) };
    updateProject(previous => ({ ...previous, steps: [...previous.steps, step] })); setSelectedStep(id);
  };
  const changeSpecializedModel = (stepId: string, nativeModel: string) => {
    const spec = specializedCatalog?.models.find(row => row.id === nativeModel);
    if (!spec) return;
    setProject(previous => {
      const promoted = promoteProjectToV7(previous);
      return { ...promoted, steps: promoted.steps.map(step => {
        if (step.id !== stepId) return step;
        return { id: step.id, name: step.name, enabled: step.enabled, model: 'specialized', nativeModel: spec.id,
          operation: spec.operation, nativeParameters: specializedDefaults(spec, previous.geometry),
          ...(spec.id === 'oxidation' ? {} : { duration: step.duration ?? 0.2 }),
          ...(spec.depositMaterial ? { depositMaterial: spec.depositMaterial } : spec.depositMaterialSelectable ? { depositMaterial: step.depositMaterial ?? materials.find(item => item.name === 'Al2O3')?.name ?? 'Al2O3' } : {}) };
      }) };
    });
  };
  const editSpecializedParameter = (stepId: string, key: string, value: unknown) => editStep(stepId, step => step.model === 'specialized'
    ? { ...step, nativeParameters: { ...(step.nativeParameters ?? {}), [key]: value } } : step);
  const addGeometryStep = (model: 'removeMaterial' | 'planarize' | 'boolean') => {
    const id = newStepId();
    const name = model === 'removeMaterial' ? '移除材料' : model === 'planarize' ? '平坦化' : '布尔几何';
    const step: Step = model === 'removeMaterial'
      ? { id, name, enabled: true, model, material: materials.find(item => item.name === 'SiO2')?.name ?? materials[0]?.name ?? 'SiO2' }
      : model === 'planarize'
        ? { id, name, enabled: true, model, cutoffHeight: 0.6 }
        : { id, name, enabled: true, model, booleanOperation: 'difference', position: [0, 0, project.dimension === 2 ? 0 : 0.2], width: 1, height: 0.8, ...(project.dimension === 3 ? { depth: 1 } : {}), angle: 0, applyToAll: true };
    setProject(previous => {
      const promoted = promoteProjectToV5(previous);
      return { ...promoted, steps: [...promoted.steps, step] };
    });
    setSelectedStep(id);
  };
  const editStep = (stepId: string, transform: (step: Step) => Step) => updateProject(previous => ({ ...previous, steps: previous.steps.map(step => step.id === stepId ? transform(step) : step) }));
  const editParticleSpecies = (stepId: string, index: number, transform: (species: ParticleSpecies) => ParticleSpecies) => updateProject(previous => ({
    ...previous,
    steps: previous.steps.map(step => {
      if (step.id !== stepId || step.model !== 'multiParticle') return step;
      const species = [...(step.species ?? [])];
      const oldLabel = species[index]?.label;
      if (!species[index] || !step.rateCoefficients) return step;
      species[index] = transform(species[index]);
      const newLabel = species[index].label;
      const rateCoefficients = structuredClone(step.rateCoefficients);
      if (oldLabel !== newLabel) {
        rateCoefficients.defaultBySpecies[newLabel] = rateCoefficients.defaultBySpecies[oldLabel] ?? 0.025;
        delete rateCoefficients.defaultBySpecies[oldLabel];
        for (const row of Object.values(rateCoefficients.byMaterial)) {
          if (Object.hasOwn(row, oldLabel)) { row[newLabel] = row[oldLabel]; delete row[oldLabel]; }
        }
      }
      return { ...step, species, rateCoefficients };
    }),
  }));
  const addParticleSpecies = (stepId: string, kind: ParticleSpecies['kind']) => updateProject(previous => ({
    ...previous,
    steps: previous.steps.map(step => {
      if (step.id !== stepId || step.model !== 'multiParticle' || !step.rateCoefficients || (step.species?.length ?? 0) >= 8) return step;
      const used = new Set((step.species ?? []).map(species => species.label));
      const prefix = kind === 'ion' ? 'ion' : 'neutral';
      let ordinal = 1; while (used.has(`${prefix}${ordinal}`)) ordinal++;
      const label = `${prefix}${ordinal}`;
      const species = kind === 'ion' ? defaultIonSpecies(label) : defaultNeutralSpecies(label);
      const rateCoefficients = structuredClone(step.rateCoefficients);
      rateCoefficients.defaultBySpecies[label] = 0.025;
      for (const row of Object.values(rateCoefficients.byMaterial)) row[label] = 0.025;
      return { ...step, species: [...(step.species ?? []), species], rateCoefficients };
    }),
  }));
  const removeParticleSpecies = (stepId: string, index: number) => updateProject(previous => ({
    ...previous,
    steps: previous.steps.map(step => {
      if (step.id !== stepId || step.model !== 'multiParticle' || (step.species?.length ?? 0) <= 1 || !step.rateCoefficients) return step;
      const label = step.species?.[index]?.label;
      if (!label) return step;
      const rateCoefficients = structuredClone(step.rateCoefficients);
      delete rateCoefficients.defaultBySpecies[label];
      for (const row of Object.values(rateCoefficients.byMaterial)) delete row[label];
      return { ...step, species: step.species?.filter((_, position) => position !== index), rateCoefficients };
    }),
  }));
  const changeStepModel = (stepId: string, model: Step['model']) => setProject(previous => {
    const current = previous.steps.find(step => step.id === stepId);
    if (!current || isGeometryStep(current) || isGeometryStep({ model, id: '', name: '', enabled: true }) || current.model === model) return previous;
    if (current.model === 'multiParticle') {
      const promoted = promoteProjectToV7(previous);
      return { ...promoted, steps: promoted.steps.map(step => {
        if (step.id !== stepId) return step;
        const rate = Math.min(2, Object.values(step.rateCoefficients?.defaultBySpecies ?? {}).reduce((sum, value) => sum + value, 0));
        const { species: _species, rateCoefficients: _rateCoefficients, ...common } = step;
        if (model === 'directional') return { ...common, model, direction: defaultDirection(previous.dimension), directionalRateMagnitude: rate, isotropicRateMagnitude: 0, calculateVisibility: true };
        const converted: Step = { ...common, model, rateMagnitude: Math.max(rate, 0.2), rateMode: 'uniform' };
        if (model === 'singleParticle') { converted.stickingProbability = 0.5; converted.sourceExponent = 1; }
        else { delete converted.stickingProbability; delete converted.sourceExponent; }
        return converted;
      }) };
    }
    if (model === 'multiParticle') {
      return { ...promoteProjectToV7(previous), steps: promoteProjectToV7(previous).steps.map(step => {
        if (step.id !== stepId) return step;
        const { rateMagnitude: _rate, rateMode: _mode, materialRates: _materials, selectivityReference: _reference,
          stickingProbability: _sticking, sourceExponent: _exponent, direction: _direction,
          directionalRateMagnitude: _directional, isotropicRateMagnitude: _isotropic,
          calculateVisibility: _visibility, ...common } = step;
        return { ...common, model, ...defaultMultiParticleFields() };
      }) };
    }
    if (model === 'directional') {
      const promoted = promoteProjectToV5(previous);
      return { ...promoted, steps: promoted.steps.map(step => {
        if (step.id !== stepId) return step;
        const availableRates = [step.rateMagnitude ?? 0, ...Object.values(step.materialRates ?? {})];
        const rate = Math.max(...availableRates);
        const { rateMagnitude: _rateMagnitude, rateMode: _rateMode, materialRates: _materialRates, selectivityReference: _selectivityReference,
          stickingProbability: _stickingProbability, sourceExponent: _sourceExponent, ...common } = step;
        return { ...common, model: 'directional', direction: defaultDirection(previous.dimension), directionalRateMagnitude: rate,
          isotropicRateMagnitude: 0, calculateVisibility: true };
      }) };
    }
    if (current.model === 'directional') {
      const promoted = promoteProjectToV5(previous);
      return { ...promoted, steps: promoted.steps.map(step => {
        if (step.id !== stepId) return step;
        const combinedRate = Math.min(2, (step.directionalRateMagnitude ?? 0) + (step.isotropicRateMagnitude ?? 0));
        const { direction: _direction, directionalRateMagnitude: _directionalRate, isotropicRateMagnitude: _isotropicRate,
          calculateVisibility: _visibility, ...common } = step;
        const converted: Step = { ...common, model, rateMagnitude: combinedRate, rateMode: 'uniform' };
        if (model === 'singleParticle') { converted.stickingProbability = 0.5; converted.sourceExponent = 1; }
        else { delete converted.stickingProbability; delete converted.sourceExponent; }
        return converted;
      }) };
    }
    return { ...previous, steps: previous.steps.map(step => {
      if (step.id !== stepId) return step;
      if (model === 'singleParticle') return { ...step, model, rateMagnitude: step.rateMagnitude ?? 0.2, stickingProbability: 0.5, sourceExponent: 1 };
      const converted: Step = { ...step, model, rateMagnitude: step.rateMagnitude ?? 0.2 };
      delete converted.stickingProbability; delete converted.sourceExponent;
      return converted;
    }) };
  });
  const editRateProfile = (stepId: string, transform: (step: Step, project: Project) => Step) => updateProject(previous => ({
    ...previous,
    schemaVersion: previous.schemaVersion >= 7 ? 7 : previous.schemaVersion === 6 ? 6 : previous.schemaVersion >= 5 ? 5 : 4,
    solver: { ...previous.solver, enginePreference: 'cpu' },
    steps: previous.steps.map(step => step.model === 'directional' || step.model === 'specialized' ? step : step.id === stepId ? transform({ ...step, rateMode: step.rateMode ?? 'uniform' }, previous) : { ...step, rateMode: step.rateMode ?? 'uniform' }),
  }));
  const moveStep = (stepId: string, offset: -1 | 1) => updateProject(previous => {
    const index = previous.steps.findIndex(step => step.id === stepId); const nextIndex = index + offset;
    if (index < 0 || nextIndex < 0 || nextIndex >= previous.steps.length) return previous;
    const items = [...previous.steps]; [items[index], items[nextIndex]] = [items[nextIndex], items[index]];
    return { ...previous, steps: items };
  });
  const copyStep = (step: Step) => {
    const duplicate = { ...step, id: newStepId(), name: `${step.name} 副本` };
    updateProject(previous => { const index = previous.steps.findIndex(item => item.id === step.id); const items = [...previous.steps]; items.splice(index + 1, 0, duplicate); return { ...previous, steps: items }; });
    setSelectedStep(duplicate.id);
  };
  const setRateMode = (stepId: string, mode: 'uniform' | 'material') => editRateProfile(stepId, (step, source) => {
    if (mode === 'material' && step.operation === 'etch') return {
      ...step, rateMode: 'material',
      materialRates: step.rateMode === 'material' ? { ...(step.materialRates ?? {}) } : configuredMaterialRates(source, step.rateMagnitude ?? 0.2),
    };
    const next = { ...step, rateMode: 'uniform' as const };
    delete next.materialRates; delete next.selectivityReference;
    return next;
  });
  const setMaterialRate = (stepId: string, name: string, rate: number) => editRateProfile(stepId, step => ({ ...step, rateMode: 'material', materialRates: { ...(step.materialRates ?? {}), [name]: rate } }));
  const removeMaterialRate = (stepId: string, name: string) => editRateProfile(stepId, step => {
    const materialRates = { ...(step.materialRates ?? {}) }; delete materialRates[name];
    return { ...step, rateMode: 'material', materialRates };
  });
  const addMaterialRate = (stepId: string, name: string) => {
    if (!name) return;
    editRateProfile(stepId, step => ({ ...step, rateMode: 'material', materialRates: { ...(step.materialRates ?? {}), [name]: step.rateMagnitude ?? 0.2 } }));
    setRateRowMaterial('');
  };
  const setSelectivityReference = (stepId: string, name: string) => editRateProfile(stepId, step => {
    const next = { ...step, rateMode: 'material' as const };
    if (name) next.selectivityReference = name; else delete next.selectivityReference;
    return next;
  });
  const updatePattern = (part: Partial<Exclude<StackGeometry['pattern'], { kind: 'none' }>>) => updateProject(previous => {
    if (previous.geometry.kind !== 'stack' || previous.geometry.pattern.kind === 'none') return previous;
    return { ...previous, geometry: { ...previous.geometry, pattern: { ...previous.geometry.pattern, ...part } as StackGeometry['pattern'] } };
  });
  const editLayer = (id: string, patch: Partial<Layer>) => updateProject(previous => {
    if (previous.geometry.kind !== 'stack') return previous;
    const layers = previous.geometry.layers.map(layer => layer.id === id ? { ...layer, ...patch } : layer);
    if (previous.geometry.pattern.kind === 'gds') return { ...previous, geometry: syncGdsMaskLayer({ ...previous.geometry, layers }) };
    if (previous.geometry.pattern.kind !== 'none' && layers.at(-1)?.role !== 'mask') setNotice('已关闭顶层图案，因为新的顶层不是掩膜层；已有层的材料和角色保持不变。');
    return { ...previous, geometry: syncGdsMaskLayer({ ...previous.geometry, layers, pattern: previous.geometry.pattern.kind !== 'none' && layers.at(-1)?.role !== 'mask' ? { kind: 'none' } : previous.geometry.pattern }) };
  });
  const addLayer = () => updateProject(previous => {
    if (previous.geometry.kind !== 'stack' || previous.geometry.layers.length >= 16) return previous;
    const oldLayers = previous.geometry.layers;
    const layers = [...oldLayers];
    const gdsIndex = layers.findIndex(layer => layer.source === 'gds-mask');
    const newLayer = { id: `layer_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, name: `新层 ${oldLayers.length + 1}`, material: materials.find(m => m.name === 'SiO2')?.name ?? 'SiO2', thickness: Math.max(.6, 2 * previous.domain.gridDelta), role: 'film' as const };
    if (gdsIndex >= 0) layers.splice(gdsIndex, 0, newLayer); else layers.push(newLayer);
    if (previous.geometry.pattern.kind !== 'none' && previous.geometry.pattern.kind !== 'gds') setNotice('已关闭顶层图案，因为新增薄膜后顶层不再是原掩膜；已有层的材料和角色保持不变。');
    return { ...previous, geometry: syncGdsMaskLayer({ ...previous.geometry, layers, pattern: previous.geometry.pattern.kind === 'gds' ? previous.geometry.pattern : { kind: 'none' } }) };
  });
  const removeLayer = (id: string) => updateProject(previous => {
    if (previous.geometry.kind !== 'stack') return previous;
    const removingGdsMask = previous.geometry.layers.some(layer => layer.id === id && layer.source === 'gds-mask');
    const layers = previous.geometry.layers.filter(layer => layer.id !== id);
    const closePattern = previous.geometry.pattern.kind !== 'none' && (layers.at(-1)?.role !== 'mask' || !layers.length);
    if (closePattern) setNotice('已关闭顶层图案，因为删除层后顶层不再是掩膜层；其他已有层的材料和角色保持不变。');
    const geometry = { ...previous.geometry, layers, pattern: removingGdsMask || closePattern ? { kind: 'none' as const } : previous.geometry.pattern, ...(removingGdsMask ? { gdsMask: undefined } : {}) };
    return { ...previous, geometry: syncGdsMaskLayer(geometry) };
  });
  const moveLayer = (id: string, offset: -1 | 1) => updateProject(previous => {
    if (previous.geometry.kind !== 'stack') return previous;
    const layers = [...previous.geometry.layers], index = layers.findIndex(layer => layer.id === id), target = index + offset;
    if (index < 0 || target < 0 || target >= layers.length) return previous;
    const gdsIndex = layers.findIndex(layer => layer.source === 'gds-mask');
    if (index === gdsIndex || (gdsIndex >= 0 && target >= gdsIndex)) return previous;
    [layers[index], layers[target]] = [layers[target], layers[index]];
    if (previous.geometry.pattern.kind !== 'none' && layers.at(-1)?.role !== 'mask') setNotice('已关闭顶层图案，因为换序后顶层不再是掩膜层；已有层的材料和角色保持不变。');
    return { ...previous, geometry: syncGdsMaskLayer({ ...previous.geometry, layers, pattern: previous.geometry.pattern.kind !== 'none' && previous.geometry.pattern.kind !== 'gds' && layers.at(-1)?.role !== 'mask' ? { kind: 'none' } : previous.geometry.pattern }) };
  });
  const copyLayer = (id: string) => updateProject(previous => {
    if (previous.geometry.kind !== 'stack' || previous.geometry.layers.length >= 16) return previous;
    const index = previous.geometry.layers.findIndex(layer => layer.id === id), original = previous.geometry.layers[index];
    if (!original || original.source === 'gds-mask') return previous;
    const layers = [...previous.geometry.layers];
    layers.splice(index + 1, 0, { ...original, id: `layer_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, name: `${original.name} 副本` });
    const closePattern = previous.geometry.pattern.kind !== 'none' && previous.geometry.pattern.kind !== 'gds' && layers.at(-1)?.role !== 'mask';
    if (closePattern) setNotice('已关闭顶层图案，因为复制后顶层不再是掩膜层；已有层的材料和角色保持不变。');
    return { ...previous, geometry: syncGdsMaskLayer({ ...previous.geometry, layers, pattern: closePattern ? { kind: 'none' } : previous.geometry.pattern }) };
  });

  const loadServerRun = async (runId: string, preferLatest: boolean) => {
    setSelectedStep(null);
    setSelection({ kind: 'run', id: runId });
    setActivity('results'); setMobilePanel('structure'); setInspectorOpen(true);
    await loadRun(runId, preferLatest);
  };
  const view2D = project.dimension === 2 ? project.view ?? defaultProject.view! : historyView2D;
  const showingHistory3D = selectedSnapshot && snapshotDimension(selectedSnapshot) === 3 && displayRunId !== latestRunId;
  const view3D = showingHistory3D ? historyView3D : project.dimension === 3 ? project.view3D ?? defaultView3D : historyView3D;
  const changeView = (partial: Partial<ViewSettings>) => {
    if (project.dimension === 2) updateProject(previous => ({ ...previous, view: { ...view2D, ...partial } }));
    else setHistoryView2D(previous => ({ ...previous, ...partial }));
  };
  const changeView3D = (next: View3D) => {
    if (project.dimension === 3 && !showingHistory3D) updateProject(previous => ({ ...previous, view3D: next }));
    else setHistoryView3D(next);
  };
  const displayDimension = selectedSnapshot ? snapshotDimension(selectedSnapshot) : project.dimension;
  const layerCount = project.geometry.kind === 'stack' ? project.geometry.layers.length : 0;
  const selectedGds = project.geometry.kind === 'gds' ? project.geometry : null;
  const gdsAvailableLayers = selectedGds && gdsAsset
    ? gdsLayerIds(gdsAsset, selectedGds.structure) : [];
  const compatibleCompare = selectedSnapshot && compareSnapshot && snapshotDimension(selectedSnapshot) === snapshotDimension(compareSnapshot) ? compareSnapshot : null;
  const clearDisplayedRun = () => {
    snapshotSequence.current++; comparisonSequence.current++;
    setLatestRunId(null); setRunInfo(null); setRunLog(''); setLogCursor(0);
    setDisplayInfo(null); setDisplayRunId(null); setSelectedSnapshot(null); setSelectedStepId(null);
    setFluxResult(null); setFluxResultRunId(null); setSelectedFluxField('');
    setCompareRunId(''); setCompareInfo(null); setCompareSnapshot(null); setAutoFollow(false);
  };
  const newProject = (dimension: 2 | 3) => {
    const next: Project = dimension === 2 ? structuredClone(defaultProject) : {
      ...structuredClone(defaultProject), name: '三维工艺项目', schemaVersion: 7, dimension: 3,
      domain: { gridDelta: .2, xExtent: 6, yExtent: 6, boundaryXY: 'reflective' },
      outputSettings: { ...structuredClone(defaultProject.outputSettings!), volumeMesh: true },
      geometry: { kind: 'stack', substrateMaterial: 'Si', baseHeight: 0, layers: [
        { id: 'layer_sio2', name: 'SiO₂ 薄膜', material: 'SiO2', thickness: .6, role: 'film' },
        { id: 'layer_si3n4', name: 'Si₃N₄ 掩膜', material: 'Si3N4', thickness: .6, role: 'mask' },
      ], pattern: { kind: 'hole', radius: 1, taperAngle: 0 } }, view3D: { ...structuredClone(defaultView3D), visibleMaterialIds: initialVisibleMaterialIds(materials, defaultProject.geometry) },
    };
    if (dimension === 3) delete next.view;
    setProject(next); setProjectId(null); setSavedSelection(''); setSelectedStep(null); clearDisplayedRun();
    setNotice(`已创建新的${dimension === 3 ? '三' : '二'}维工程，原工程和运行记录仍保留。`);
  };
  const switchProjectDimension = (dimension: 2 | 3) => {
    if (dimension === project.dimension || loading || active) return;
    const source = structuredClone(project);
    const sourceBaseName = source.name.replace(/（[23]D 副本）$/, '');
    const targetName = projectId || source.name !== sourceBaseName ? `${sourceBaseName}（${dimension}D 副本）` : source.name;
    let next: Project;
    let convertedHole = false;
    let directionFallback = false;
    let geometry: Geometry = source.geometry;
    if (source.geometry.kind === 'gds') {
      const value = source.geometry;
      geometry = dimension === 3
        ? { ...value, position: [value.position[0], 0, value.position[1]], layers: value.layers.map(layer => ({
            layer: layer.layer, material: layer.material, baseHeight: layer.baseHeight ?? 0,
            height: layer.height ?? Math.max(0.5, 2 * source.domain.gridDelta), mask: layer.mask ?? false,
          })) }
        : { ...value, position: [value.position[0], value.position[2], 0], layers: value.layers.map(layer => ({ layer: layer.layer, material: layer.material })) };
    } else if (source.geometry.kind === 'fin' && dimension === 2 && source.geometry.extrusion) {
      const { extrusion: _extrusion, ...fin } = source.geometry;
      geometry = fin;
    }
    const convertedSteps = source.steps.map(step => {
      if (step.model === 'boolean') {
        const position = step.position ?? [0, 0, 0];
        if (dimension === 3) return { ...step, position: [position[0], 0, position[1]] as [number, number, number], depth: step.depth ?? step.width ?? 1 };
        const converted = { ...step, position: [position[0], position[2], 0] as [number, number, number] };
        delete converted.depth;
        return converted;
      }
      if (step.model !== 'directional') return step;
      const direction = step.direction ?? defaultDirection(source.dimension);
      if (dimension === 3) return { ...step, direction: [direction[0], 0, direction[1]] as [number, number, number] };
      const mapped: [number, number, number] = [direction[0], direction[2], 0];
      if (Math.hypot(mapped[0], mapped[1]) <= 1e-12) {
        directionFallback = true;
        return { ...step, direction: defaultDirection(2) };
      }
      return { ...step, direction: mapped };
    });
    if (dimension === 3) {
      next = {
        ...source,
        name: targetName,
        schemaVersion: source.schemaVersion >= 7 ? 7 : source.schemaVersion === 6 ? 6 : source.schemaVersion === 5 ? 5 : source.schemaVersion >= 4 ? 4 : 3,
        dimension: 3,
        steps: convertedSteps,
        geometry,
        outputSettings: { ...(source.outputSettings ?? defaultProject.outputSettings!), volumeMesh: true },
        domain: { gridDelta: source.domain.gridDelta, xExtent: source.domain.xExtent, yExtent: source.domain.yExtent ?? source.domain.xExtent, boundaryXY: source.domain.boundaryXY ?? source.domain.xBoundary ?? 'reflective' },
        view3D: {
          ...(source.view3D ? structuredClone(source.view3D) : structuredClone(historyView3D)),
          visibleMaterialIds: initialVisibleMaterialIds(materials, geometry, source.view3D?.visibleMaterialIds ?? historyView3D.visibleMaterialIds),
        },
      };
      delete next.view;
      setHistoryView2D(structuredClone(source.view ?? defaultProject.view!));
    } else {
      const boundary = source.domain.boundaryXY ?? source.domain.xBoundary ?? 'reflective';
      geometry = source.geometry.kind === 'hole' ? {
        kind: 'trench' as const, width: source.geometry.radius * 2, depth: source.geometry.depth,
        taperAngle: source.geometry.taperAngle, maskHeight: source.geometry.maskHeight,
        maskTaperAngle: source.geometry.maskTaperAngle, substrateMaterial: source.geometry.substrateMaterial,
      } : source.geometry.kind === 'gds' || source.geometry.kind === 'fin' ? geometry : source.geometry.kind === 'stack' && source.geometry.pattern.kind === 'hole'
        ? { ...source.geometry, pattern: { kind: 'trench' as const, width: source.geometry.pattern.radius * 2, taperAngle: source.geometry.pattern.taperAngle } }
        : source.geometry;
      convertedHole = source.geometry.kind === 'hole' || (source.geometry.kind === 'stack' && source.geometry.pattern.kind === 'hole');
      next = {
        ...source,
        name: targetName,
        schemaVersion: source.schemaVersion >= 7 ? 7 : source.schemaVersion === 6 ? 6 : source.schemaVersion === 5 ? 5 : source.schemaVersion >= 4 ? 4 : 3,
        dimension: 2,
        steps: convertedSteps,
        domain: { gridDelta: source.domain.gridDelta, xExtent: source.domain.xExtent, xBoundary: boundary },
        geometry,
        view: structuredClone(source.view ?? historyView2D),
      };
      delete next.view3D;
      setHistoryView3D(structuredClone(source.view3D ?? historyView3D));
    }
    setProject(next); setProjectId(null); setSavedSelection(''); clearDisplayedRun();
    setNotice(directionFallback
      ? '已转换到 2D：3D 方向若只有 y 分量则没有对应二维方向，已改为 [0, -1, 0]（向下）。'
      : convertedHole
      ? '已新建对应 2D 工程：圆孔按 x 截面转换为宽度等于孔径的沟槽；原工程和历史结果保持不变。'
      : `已新建对应 ${dimension}D 工程；原工程和历史结果保持不变。`);
  };
  const snapshotRows = displayInfo?.runId === displayRunId ? displayInfo.snapshots : [];
  const gpuStatus = capabilities?.capabilities?.engines?.gpu?.status ?? 'unverified-outside-sandbox';
  const gpuRows3D = capabilities?.capabilities?.threeDimensional?.rows?.filter(r => r.status === 'verified' && r.dimension === 3 && r.engine.startsWith('GPU_')) ?? [];
  const enabledFluxSteps = project.steps.filter(usesFlux);
  const finFallbackModels = project.geometry.kind === 'fin' && (project.geometry.construction ?? 'native') === 'native'
    ? [...new Set(enabledFluxSteps.filter(step => step.model === 'specialized' && NATIVE_MAKEFIN_SIGSEGV_MODELS.has(step.nativeModel ?? '')).map(step => step.nativeModel!))].sort()
    : [];
  const masked3DFinFallbackModels = project.geometry.kind === 'fin' && project.dimension === 3
    && project.geometry.maskHeight > 0
    && (project.geometry.construction ?? 'native') === 'native'
    ? [...new Set(enabledFluxSteps.filter(step => step.model === 'singleParticle' || step.model === 'multiParticle').map(step => step.model))].sort()
    : [];
  const finExtrusionFallbackModels = project.geometry.kind === 'fin' && project.dimension === 3
    && !!project.geometry.extrusion && (project.domain.boundaryXY ?? project.domain.xBoundary) === 'periodic'
    ? [...new Set(enabledFluxSteps.filter(step =>
      (step.model === 'specialized' && NATIVE_MAKEFIN_SIGSEGV_MODELS.has(step.nativeModel ?? ''))
      || (project.geometry.kind === 'fin' && project.geometry.maskHeight > 0
        && (step.model === 'singleParticle' || step.model === 'multiParticle'))
    ).map(step => step.model === 'specialized' ? step.nativeModel! : step.model))].sort()
    : [];
  const effectiveFinConstruction = project.geometry.kind === 'fin'
    ? (project.geometry.construction === 'boolean' || finFallbackModels.length > 0 || masked3DFinFallbackModels.length > 0 ? 'boolean' : 'native')
    : 'native';
  const boundary = project.domain.boundaryXY ?? project.domain.xBoundary;
  const schemaGpuRows = (capabilities?.capabilities?.gpuModelRows ?? []).filter(r => r.status === 'verified' && r.dimension === project.dimension && r.schemaVersions?.includes(project.schemaVersion));
  const gpuMaterialRoles = () => {
    const roles = new Map<string, Set<string>>();
    const add = (name: string | undefined, role: string) => { if (name) { const set = roles.get(name) ?? new Set<string>(); set.add(role); roles.set(name, set); } };
    const geometry = project.geometry;
    if (geometry.kind === 'gds') geometry.layers.forEach(layer => add(layer.material, layer.mask ? 'mask' : 'film'));
    else {
      add(geometry.substrateMaterial, 'bulk');
      if (geometry.kind === 'stack') geometry.layers.forEach(layer => add(layer.material, layer.role));
      else if ((geometry.kind === 'trench' || geometry.kind === 'hole' || geometry.kind === 'fin') && geometry.maskHeight > 0) add(geometry.maskMaterial ?? 'Mask', 'mask');
    }
    return Object.fromEntries([...roles.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, values]) => [name, [...values].sort()]));
  };
  const gpuMaterialSignature = (step: Step) => {
    const names = new Set(geometryMaterialNames(project.geometry));
    const addKeys = (value: unknown) => {
      if (value && typeof value === 'object' && !Array.isArray(value)) Object.keys(value).forEach(name => names.add(name));
    };
    addKeys(step.materialRates);
    addKeys(step.rateCoefficients?.byMaterial);
    (step.species ?? []).forEach(species => { if (species.kind === 'neutral') addKeys(species.stickingProbabilityByMaterial); });
    const native = step.nativeParameters ?? {};
    ['materialRates', 'materialParametersByMaterial', 'materialPlaneWaferRateByMaterial'].forEach(key => addKeys(native[key]));
    if (step.depositMaterial) names.add(step.depositMaterial);
    return [...names].filter(Boolean).sort();
  };
  const gpuMaterialBindings = (step: Step) => {
    const bindings: Record<string, string> = {};
    if (step.depositMaterial) bindings.depositMaterial = step.depositMaterial;
    const native = step.nativeParameters ?? {};
    ['maskMaterial', 'siliconMaterial', 'oxideMaterial', 'etchFrontMaterial'].forEach(key => {
      const value = native[key];
      if (typeof value === 'string' && value) bindings[key] = value;
    });
    return Object.fromEntries(Object.entries(bindings).sort(([a], [b]) => a.localeCompare(b)));
  };
  const gpuSpeciesSignature = (step: Step) => step.model === 'multiParticle'
    ? { species: step.species ?? [], rateCoefficients: step.rateCoefficients ?? { defaultBySpecies: {}, byMaterial: {} } }
    : undefined;
  const stableJson = (value: unknown) => JSON.stringify(value, (_key, child) => {
    if (child && typeof child === 'object' && !Array.isArray(child)) return Object.fromEntries(Object.entries(child).sort(([a], [b]) => a.localeCompare(b)));
    return child;
  });
  const gpuSolverProfile = () => ({
    gridDelta: project.domain.gridDelta,
    timeStepRatio: project.solver.timeStepRatio,
    raysPerPoint: project.solver.raysPerPoint,
    randomMode: project.solver.random.mode,
    advection: project.solver.advection,
    rayTracing: project.solver.rayTracing,
  });
  const matchesKnownGpuProfileFailure = (failure: GpuProfileFailure, step: Step) => {
    const model = step.model === 'specialized' ? `specialized:${step.nativeModel ?? ''}` : step.model;
    const profile = gpuSolverProfile() as Record<string, unknown>;
    return failure.dimension === project.dimension && failure.model === model
      && failure.operation === step.operation && failure.boundaryXY === boundary
      && failure.engine === 'GPU_DISK' && failure.geometryKind === project.geometry.kind
      && stableJson(failure.materials) === stableJson([...gpuMaterialSignature(step)].sort())
      && stableJson(failure.materialRoles) === stableJson(gpuMaterialRoles())
      && stableJson(failure.materialBindings) === stableJson(gpuMaterialBindings(step))
      && stableJson(failure.speciesSignature ?? null) === stableJson(gpuSpeciesSignature(step) ?? null)
      && Object.entries(failure.solverProfile ?? {}).every(([key, value]) => profile[key] === value);
  };
  const knownGpuProfileForStep = (step: Step) =>
    (capabilities?.capabilities?.knownGpuProfileFailures ?? []).find(failure => matchesKnownGpuProfileFailure(failure, step));
  const gpuMaterialScopeMatches = (row: GpuCapabilityRow, step: Step) => {
    const scope = row.materialScope;
    if (!scope || scope.kind !== 'viennaps-native-material-registry-v1' || scope.engineVersion !== '4.6.2'
      || !boundary || !scope.boundaries.includes(boundary)) return false;
    const geometryKind = project.geometry.kind;
    const roleKinds = [...new Set(Object.values(gpuMaterialRoles()).flat())].sort();
    const allowedRoleKinds = scope.roleKindsByGeometry[geometryKind] ?? [];
    if (!allowedRoleKinds.some(candidate => stableJson([...candidate].sort()) === stableJson(roleKinds))) return false;
    const expectedBindings = scope.bindingKeysByOperation[step.operation ?? 'etch'];
    const bindings = gpuMaterialBindings(step);
    if (!expectedBindings || stableJson([...expectedBindings].sort()) !== stableJson(Object.keys(bindings).sort())) return false;
    const materialByName = new Map(materials.map(material => [material.name, material]));
    const names = gpuMaterialSignature(step);
    const selected = names.map(name => materialByName.get(name));
    if (selected.some(material => !material)) return false;
    const ids = selected.map(material => material!.id);
    if (new Set(ids).size !== ids.length) return false;
    const builtinIds = new Set(scope.builtInLegacyIds);
    const [customMin, customMax] = scope.customLegacyIdRange;
    if (ids.some((id, index) => {
      const material = selected[index]!;
      return material.isCustom ? id < customMin || id > customMax : !builtinIds.has(id);
    })) return false;
    if (Object.keys(gpuMaterialRoles()).some(name => !names.includes(name))) return false;
    return Object.values(bindings).every(name => names.includes(name));
  };
  const gpuSpeciesTableScopeMatches = (row: GpuCapabilityRow, step: Step) => {
    if (row.speciesScope?.kind !== 'material-tables-v1' || !row.speciesSignature) return false;
    const normalize = (signature: NonNullable<GpuCapabilityRow['speciesSignature']>) => {
      const species = signature.species ?? [];
      const labels = species.map(item => item.label);
      const defaults = signature.rateCoefficients?.defaultBySpecies ?? {};
      if (!species.length || new Set(labels).size !== labels.length || labels.some(label => !Object.hasOwn(defaults, label))) return null;
      return {
        species: species.map(item => {
          if (item.kind === 'neutral') {
            const { label: _label, stickingProbabilityByMaterial: _sticking, ...native } = item;
            return native;
          }
          const { label: _label, ...native } = item;
          return native;
        }),
        defaultRatesByOrder: labels.map(label => defaults[label]),
      };
    };
    const baseline = normalize(row.speciesSignature);
    const candidate = normalize(gpuSpeciesSignature(step)!);
    return baseline !== null && candidate !== null && stableJson(baseline) === stableJson(candidate);
  };
  const rowMatchesStep = (row: GpuCapabilityRow, step: Step) => {
    if (row.operation !== step.operation || row.boundaryXY !== boundary ||
        row.model !== (step.model === 'specialized' ? `specialized:${step.nativeModel ?? ''}` : step.model)) return false;
    if (knownGpuProfileForStep(step)) return false;
    // Stage 9 rows are scoped to the geometry and material set actually
    // compared against ViennaPS. Legacy rows keep their earlier host scope.
    if (row.geometryKinds && !row.geometryKinds.includes(project.geometry.kind)) return false;
    if (project.geometry.kind === 'gds') {
      const scope = row.gdsScope;
      const has3dMask = project.dimension === 3 && project.geometry.layers.some(layer => layer.mask ?? false);
      if (!scope || scope.kind !== 'viennaps-gds-process-v1' || scope.engineVersion !== '4.6.2'
        || !scope.dimensions.includes(project.dimension)
        || project.geometry.layers.length < 1 || project.geometry.layers.length > scope.maxLayerMappings
        || (has3dMask && !scope.supports3DMask)) return false;
    }
    if (row.finExtrusionMode && (project.geometry.kind !== 'fin' || row.finExtrusionMode !== (project.geometry.extrusion ? 'extruded' : 'direct'))) return false;
    if (project.geometry.kind === 'fin' && row.geometryKinds?.includes('fin') &&
        (row.finExtrusionMode !== (project.geometry.extrusion ? 'extruded' : 'direct') ||
         row.finConstruction !== effectiveFinConstruction)) return false;
    if (row.patternKinds) {
      const pattern = project.geometry.kind === 'stack' ? project.geometry.pattern.kind : null;
      if (!pattern || !row.patternKinds.includes(pattern)) return false;
    }
    if (row.materialScope) {
      if (!gpuMaterialScopeMatches(row, step)) return false;
    } else {
      if (row.materials && row.materials.join('\u0000') !== gpuMaterialSignature(step).join('\u0000')) return false;
      if (row.materialRoles && stableJson(row.materialRoles) !== stableJson(gpuMaterialRoles())) return false;
      if (row.materialBindings && stableJson(row.materialBindings) !== stableJson(gpuMaterialBindings(step))) return false;
    }
    if (step.model === 'multiParticle' && (row.speciesScope?.kind === 'material-tables-v1'
      ? !gpuSpeciesTableScopeMatches(row, step)
      : (!row.speciesSignature || stableJson(row.speciesSignature) !== stableJson(gpuSpeciesSignature(step))))) return false;
    return true;
  };
  const nativeKnownFailure = (capabilities?.capabilities?.knownNativeFailures ?? []).find(failure =>
    failure.failure === 'native-viennaps-process-apply-segfault'
    && failure.engineVersion === '4.6.2'
    && enabledFluxSteps.some(step => failure.model === (step.model === 'specialized' ? `specialized:${step.nativeModel ?? ''}` : step.model)
      && failure.dimension === project.dimension
      && failure.operation === step.operation
      && failure.boundaryXY === boundary
      && failure.geometryKind === project.geometry.kind));
  const nativeFailureNotice = nativeKnownFailure
    ? `该模型和几何组合曾触发 ViennaPS 4.6.2 原生崩溃；运行失败时可在历史记录中查看错误。`
    : null;
  const schemaGpuStepsVerified = enabledFluxSteps.length > 0 && enabledFluxSteps.every(step => schemaGpuRows.some(row => rowMatchesStep(row, step)));
  const legacyGpuRows = project.dimension === 3 ? gpuRows3D.filter(row => row.dimension === 3 && row.boundaryXY === boundary) : [];
  const gpuCoverageVerified = project.schemaVersion < 3
    ? (project.dimension === 2 ? capabilities?.capabilities?.engines?.gpu?.exposed === true : enabledFluxSteps.length > 0 && enabledFluxSteps.every(step => legacyGpuRows.some(row => rowMatchesStep(row, step))))
    : schemaGpuStepsVerified;
  const gpuDeviceSelectable = (gpuInventory?.devices.length ?? 0) > 0;
  const gpuExposed = gpuDeviceSelectable;
  const defaultGpuDevice = gpuInventory?.devices.find(device => device.uuid === gpuInventory.preferredGpuDeviceId);

  return <div className={`app-shell ${isResizingPanels ? 'columns-resizing' : ''}`}>
    <TopBar>
      <header className="topbar topbar-shell">
        <div className="brand-block"><div className="brand-mark">V<span>PS</span></div><div className="brand-copy"><strong>ViennaPS 工艺工作台</strong><span>二维 / 三维本机仿真 · µm · s</span></div></div>
        <div className="topbar-project"><span className="muted-label">项目</span><input value={project.name} aria-label="项目名称" onChange={event => updateProject(previous => ({ ...previous, name: event.target.value }))} /><span className="save-state">{!projectId ? '未保存' : savedProjectJson === JSON.stringify(project) ? '已保存' : '有未保存修改'}</span></div>
        <nav className="toolbar" aria-label="项目操作">
          <Menu label="项目">
            <button className="button" onClick={() => newProject(project.dimension)} disabled={loading || active}>新建工程</button>
            <button className="button primary" onClick={saveProject} disabled={loading || health !== 'ready'}>保存项目</button>
            <select aria-label="已保存项目" value={savedSelection} onChange={event => setSavedSelection(event.target.value)}>
              <option value="">打开已保存项目…</option>{projectRows.map(row => <option key={row.id} value={row.id}>{row.name} · {new Date(row.updatedAt).toLocaleString()}</option>)}
            </select>
            <button className="button" onClick={openProject} disabled={!savedSelection}>打开</button>
            <button className="button" onClick={() => fileInputRef.current?.click()}>导入项目 / 状态包</button>
            <button className="button" onClick={exportProject}>导出项目</button>
            <input ref={fileInputRef} type="file" accept="application/json,.json,application/zip,.zip" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void importFile(file); }} />
          </Menu>
          <div className="toolbar-group toolbar-run">
            <button className="button" onClick={() => void startRun('preview')} disabled={loading || health !== 'ready' || active}>生成结构预览</button>
            {active
              ? <button className="button danger" onClick={() => void cancelRun()}>取消运行</button>
              : <button className="button primary run-button" onClick={() => void startRun('simulate')} disabled={loading || health !== 'ready' || !project.steps.some(step => step.enabled)}>运行工艺</button>}
            {project.schemaVersion >= 7 && selectedStepObject && usesFlux(selectedStepObject) && <button className="button" onClick={() => void startFlux(selectedStepObject.id)} disabled={loading || health !== 'ready' || active}>单次计算所选粒子过程通量</button>}
          </div>
        </nav>
        <div className="topbar-status"><div className={`server-pill ${health}`} aria-label={health === 'ready' ? '本机服务已连接' : health === 'checking' ? '正在连接' : '本机服务离线'} title={health === 'ready' ? '本机服务已连接' : health === 'checking' ? '正在连接' : '本机服务离线'}><i /></div><div className={`run-pill ${runInfo?.status ?? ''}`}><i />{humanStatus(runInfo?.status)}</div><button className="inspector-toggle" aria-label={inspectorOpen ? '关闭属性检查器' : '打开属性检查器'} title={inspectorOpen ? '关闭属性检查器' : '打开属性检查器'} onClick={() => setInspectorOpen(value => !value)}><Icon name="settings"/></button></div>
      </header>
    </TopBar>

    <div className="mobile-tabs">
      {([{ id: 'structure', label: '结构' }, { id: 'recipe', label: '配方' }, { id: 'materials', label: '材料' }, { id: 'results', label: '结果' }, { id: 'settings', label: '设置' }] as const).map(item => <button key={item.id} className={activity === item.id ? 'selected' : ''} onClick={() => changeActivity(item.id)}>{item.label}</button>)}
      <button className={`mobile-canvas-toggle ${mobilePanel === 'viewer' ? 'selected' : ''}`} onClick={() => setMobilePanel('viewer')}>画布</button>
    </div>

    <main className={`workspace ${inspectorOpen ? '' : 'inspector-hidden'}`} ref={workspaceRef}>
      <Rail activity={activity} onChange={changeActivity} gpuStatus={gpuExposed ? 'GPU_DISK · 可选择' : gpuStatus === 'unverified-outside-sandbox' ? '设备不可用' : gpuStatus} />
      <aside id="structure-panel" className={`side-panel structure-panel ${mobilePanel === 'structure' ? 'mobile-visible' : ''}`}>
        <ContextPanel>
        {activity === 'materials' && <MaterialsPanel><section id="materials-context" className="panel-section material-admin-panel" aria-label="材料目录与自定义材料">
          <div className="section-heading"><div><h2>材料目录</h2></div><span className="tag">{materials.length} 种</span></div>
          <div className="panel-card material-registration-card">
            <form className="field-grid one-column material-registration-form" onSubmit={event => void createCustomMaterial(event)}>
              <label className="field"><span className="field-label">新材料名称</span><input aria-label="新材料名称" type="text" required maxLength={64} value={materialDraft.name} onChange={event => setMaterialDraft(previous => ({ ...previous, name: event.target.value }))} /></label>
              <SelectField label="材料类型" value={materialDraft.category} options={MATERIAL_CATEGORY_OPTIONS.map(([value, label]) => ({ value, label }))} onChange={value => setMaterialDraft(previous => ({ ...previous, category: value }))} />
              <label className="field"><span className="field-label">显示颜色</span><input aria-label="新材料颜色" type="color" value={materialDraft.color} onChange={event => setMaterialDraft(previous => ({ ...previous, color: event.target.value }))} /></label>
              <label className="field"><span className="field-label">密度</span><input aria-label="新材料密度" type="number" min="0" max="100" step="0.01" placeholder="g/cm³，可留空" value={materialDraft.densityGcm3} onChange={event => setMaterialDraft(previous => ({ ...previous, densityGcm3: event.target.value }))} /></label>
              <label className="field"><span className="field-label">是否导电</span><span className="field-checkbox"><input aria-label="新材料是否导电" type="checkbox" checked={materialDraft.conductive} onChange={event => setMaterialDraft(previous => ({ ...previous, conductive: event.target.checked }))} /></span></label>
              <label className="field"><span className="field-label">备注</span><textarea aria-label="新材料备注" maxLength={500} value={materialDraft.description} onChange={event => setMaterialDraft(previous => ({ ...previous, description: event.target.value }))} /></label>
              <button className="button primary" type="submit" disabled={materialBusy || health !== 'ready'}>注册自定义材料</button>
            </form>
          </div>
          {[{ label: '预设材料', rows: materials.filter(item => !item.isCustom) }, { label: '自定义材料', rows: materials.filter(item => item.isCustom) }].map(group => <div className="material-catalog-group" key={group.label}>
            <div className="field-label material-catalog-heading">{group.label}</div>
            <div className="material-catalog-list">{group.rows.map(item => <div key={item.id} className={`material-catalog-item ${item.isCustom ? 'custom' : ''}`}>
              <button type="button" className={`material-catalog-row ${selection.kind === 'material' && selection.name === item.name ? 'selected' : ''}`} onClick={() => { setSelection({ kind: 'material', name: item.name }); setActivity('materials'); }}>
                <i className="material-swatch" style={{ background: item.color }} />
                <span className="material-catalog-name">{item.name}</span>
                <span className="material-catalog-meta">{materialCategoryLabel(item.category)} · ID {item.id}</span>
              </button>
              {item.isCustom && <button type="button" className="material-catalog-delete" aria-label={`删除自定义材料 ${item.name}`} title="删除自定义材料" disabled={deletingMaterial !== null || materialBusy} onClick={() => { setDeleteMaterialCandidate(item); setDeleteMaterialError(''); }}>×</button>}
            </div>)}</div>
          </div>)}
        </section></MaterialsPanel>}
        {activity === 'structure' && <StructurePanel><section id="structure-context" className="panel-section">
          <div className="section-heading structure-section-heading"><div><h2>结构与网格</h2></div>
            <div className="segmented dimension-switch" role="group" aria-label="工程维度">
              <button type="button" aria-label="切换到 2D" aria-pressed={project.dimension === 2} className={project.dimension === 2 ? 'active' : ''} disabled={loading || active || !!project.continuationState} onClick={() => switchProjectDimension(2)}>2D</button>
              <button type="button" aria-label="切换到 3D" aria-pressed={project.dimension === 3} className={project.dimension === 3 ? 'active' : ''} disabled={loading || active || !!project.continuationState} onClick={() => switchProjectDimension(3)}>3D</button>
            </div>
          </div>
          {project.continuationState && <div className="inline-note warning continuation-note">
            <div className="continuation-heading"><strong>新工艺起始状态 · {project.continuationState.sourceStepId ?? '导入状态'}</strong><button className="button" aria-label="移除起始 Domain 状态" onClick={discardContinuationState}>移除</button></div>
            <details><summary>兼容性要求</summary><p>新工艺从该原生 Domain 状态开始。网格、边界、材料映射及 ViennaPS/ViennaLS 版本需匹配；修改结构参数后将无法运行。</p></details>
          </div>}
          <div className="field-grid one-column">
            <NumericField label="网格间距" unit="µm" value={project.domain.gridDelta} max={0.5} onChange={value => updateProject(previous => ({ ...previous, domain: { ...previous.domain, gridDelta: value } }))} />
            <NumericField label="x 横向总宽度" unit="µm" value={project.domain.xExtent} min={1} max={40} step="0.1" onChange={value => updateProject(previous => ({ ...previous, domain: { ...previous.domain, xExtent: value } }))} />
            {project.dimension === 3 && <NumericField label="y 横向总宽度" unit="µm" value={project.domain.yExtent ?? 6} min={1} max={40} step=".2" onChange={value => updateProject(previous => ({ ...previous, domain: { ...previous.domain, yExtent: value } }))} />}
            <SelectField label="横向边界" value={project.dimension === 3 ? project.domain.boundaryXY ?? 'reflective' : project.domain.xBoundary ?? 'reflective'} options={[{ value: 'reflective', label: '反射' }, { value: 'periodic', label: '周期' }]} onChange={value => updateProject(previous => ({ ...previous, domain: previous.dimension === 3 ? { ...previous.domain, boundaryXY: value as 'reflective' | 'periodic' } : { ...previous.domain, xBoundary: value as Domain['xBoundary'] } }))} />
          </div>
          <div className="structure-select">
            <span className="field-label">初始结构</span>
            <div className="segmented">
              <button className={project.geometry.kind === 'stack' ? 'active' : ''} onClick={() => updateProject(previous => ({ ...previous, geometry: structuredClone(defaultProject.geometry) }))}>多层</button>
              <button className={project.geometry.kind === 'plane' ? 'active' : ''} onClick={() => updateProject(previous => ({ ...previous, geometry: { kind: 'plane', height: 0, substrateMaterial: 'Si' } }))}>平面</button>
              <button className={project.geometry.kind === 'trench' ? 'active' : ''} onClick={() => updateProject(previous => ({ ...previous, geometry: { kind: 'trench', width: 2, depth: 2.2, taperAngle: 0, maskHeight: 0, maskTaperAngle: 0, substrateMaterial: 'Si', maskMaterial: 'Mask' } }))}>沟槽</button>
              {project.dimension === 3 && <button className={project.geometry.kind === 'hole' ? 'active' : ''} onClick={() => updateProject(previous => ({ ...previous, geometry: { kind: 'hole', radius: 1, depth: 2, taperAngle: 0, maskHeight: 0, maskTaperAngle: 0, substrateMaterial: 'Si', maskMaterial: 'Mask' } }))}>圆孔</button>}
              <button className={project.geometry.kind === 'fin' ? 'active' : ''} onClick={() => updateProject(previous => ({ ...previous, geometry: { kind: 'fin', width: 0.8, height: 1.2, taperAngle: 0, maskHeight: 0, maskTaperAngle: 0, substrateMaterial: 'Si', maskMaterial: 'Mask', construction: 'boolean' } }))}>Fin</button>
              <button
                ref={gdsImportButtonRef}
                type="button"
                className={gdsImportOpen || project.geometry.kind === 'gds' ? 'active' : ''}
                aria-haspopup="dialog"
                aria-expanded={gdsImportOpen}
                aria-controls="gds-import-popover"
                onClick={() => { setGdsImportError(''); setGdsImportOpen(open => !open); }}
              >导入 GDS</button>
            </div>
            <input ref={gdsInputRef} type="file" accept=".gds,.gdsii,application/octet-stream" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void uploadGdsFile(file); }} />
            {gdsImportOpen && gdsImportPosition && createPortal(
              <div
                ref={gdsImportCardRef}
                id="gds-import-popover"
                role="dialog"
                aria-label="导入 GDS 版图"
                className={`gds-import-popover ${gdsImportPosition.side === 'left' ? 'opens-left' : ''}`}
                style={{ top: gdsImportPosition.top, left: gdsImportPosition.left }}
              >
                <div className="gds-import-card-header">
                  <div><strong>导入 GDS 版图</strong><span>选择单元和掩膜层，将其加入当前多层结构。</span></div>
                  <button type="button" className="icon-button" aria-label="关闭 GDS 导入卡片" onClick={() => setGdsImportOpen(false)}>×</button>
                </div>
                <button type="button" className="button gds-import-file-button" disabled={loading} onClick={() => gdsInputRef.current?.click()}>
                  {loading ? '正在读取文件…' : pendingGdsAsset ? '更换 GDS 文件' : '选择 GDS 文件'}
                </button>
                {pendingGdsAsset && <>
                  <div className="gds-import-file-summary">
                    <strong title={pendingGdsAsset.fileName}>{pendingGdsAsset.fileName}</strong>
                    <span>{pendingGdsAsset.structures.length} 个结构单元 · {pendingGdsAsset.fileSizeBytes.toLocaleString()} 字节</span>
                    <span>原生单位：1 user unit = {pendingGdsAsset.units.micrometersPerNativeUserUnit.toPrecision(5)} µm</span>
                  </div>
                  <div className="gds-import-mode" role="radiogroup" aria-label="GDS 导入用途">
                    <label className={gdsImportMode === 'etch-mask' ? 'selected' : ''}>
                      <input type="radio" name="gds-import-mode" value="etch-mask" checked={gdsImportMode === 'etch-mask'} onChange={() => {
                        setGdsImportMode('etch-mask');
                        if (pendingGdsAsset) {
                          const structure = pendingGdsAsset.structures.find(item => gdsMaskPairs(pendingGdsAsset, item.name).length > 0)?.name ?? pendingGdsStructure;
                          setPendingGdsStructure(structure);
                          const pair = gdsMaskPairs(pendingGdsAsset, structure)[0];
                          setPendingGdsLayerDatatype(pair ? `${pair[0]}/${pair[1]}` : '');
                        }
                      }} />
                      <span><strong>作为刻蚀掩膜</strong><small>把所选图形加入当前多层结构的顶层。</small></span>
                    </label>
                    <label className={gdsImportMode === 'material-geometry' ? 'selected' : ''}>
                      <input type="radio" name="gds-import-mode" value="material-geometry" checked={gdsImportMode === 'material-geometry'} onChange={() => {
                        setGdsImportMode('material-geometry');
                        if (pendingGdsAsset) {
                          const pair = gdsPairs(pendingGdsAsset, pendingGdsStructure)[0];
                          setPendingGdsLayerDatatype(pair ? `${pair[0]}/${pair[1]}` : '');
                        }
                      }} />
                      <span><strong>导入为材料几何（高级）</strong><small>每个 GDS 图层都要指定仿真材料。</small></span>
                    </label>
                  </div>
                  <label className="gds-import-field">
                    <span>1 · 选择版图单元（Cell）</span>
                    <select value={pendingGdsStructure} onChange={event => {
                      const structure = event.target.value;
                      const pair = (gdsImportMode === 'etch-mask' ? gdsMaskPairs(pendingGdsAsset, structure) : gdsPairs(pendingGdsAsset, structure))[0];
                      setPendingGdsStructure(structure);
                      setPendingGdsLayerDatatype(pair ? `${pair[0]}/${pair[1]}` : '');
                      setGdsImportError('');
                    }}>
                      {pendingGdsAsset.structures.filter(item => gdsLayerIds(pendingGdsAsset, item.name).length > 0).map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
                    </select>
                  </label>
                  <div className="gds-import-layer-summary">
                    {gdsLayerIds(pendingGdsAsset, pendingGdsStructure).length} 个图层 · {gdsImportMode === 'etch-mask' ? gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).length : gdsPairs(pendingGdsAsset, pendingGdsStructure).length} 种{gdsImportMode === 'etch-mask' ? '闭合多边形掩膜' : 'layer/datatype'}组合
                  </div>
                  {gdsImportMode === 'etch-mask' && !gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).length && <div className="gds-import-help">当前 Cell 没有闭合边界多边形可用作掩膜。可换 Cell 或切换到高级材料几何导入。</div>}
                  {gdsImportMode === 'etch-mask' && <label className="gds-import-field">
                    <span>2 · 作为掩膜的闭合 GDS 图层</span>
                    <select value={pendingGdsLayerDatatype} onChange={event => { setPendingGdsLayerDatatype(event.target.value); setGdsImportError(''); }}>
                      {gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).length === 0 && <option value="">无闭合掩膜图形</option>}
                      {gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).map(([layer, datatype]) => <option key={`${layer}/${datatype}`} value={`${layer}/${datatype}`}>Layer {layer} · datatype {datatype}</option>)}
                    </select>
                  </label>}
                  {gdsImportMode === 'etch-mask' && <>
                    <div className="gds-import-section-heading"><span>3</span><div><strong>掩膜图层</strong><small>这些属性会填入“多层”中的新顶层掩膜；基底与薄膜保持当前项目设置。</small></div></div>
                    <label className="gds-import-field"><span>掩膜名称</span><input type="text" maxLength={80} value={pendingMaskName} onChange={event => setPendingMaskName(event.target.value)} /></label>
                    <div className="gds-import-data-grid">
                      <label className="gds-import-field"><span>掩膜材料</span><select value={pendingMaskMaterial} onChange={event => setPendingMaskMaterial(event.target.value)}>{materials.map(item => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label>
                      <label className="gds-import-field"><span>掩膜厚度 · µm</span><input type="number" min={2 * project.domain.gridDelta} max="100" step="0.05" value={pendingMaskThickness} onChange={event => setPendingMaskThickness(Number(event.target.value))} /></label>
                      <label className="gds-import-field"><span>额外尺寸比例（自动换算后 ×）</span><input type="number" min="0.000001" max="1000000" step="0.1" value={pendingGdsScale} onChange={event => setPendingGdsScale(Number(event.target.value))} /></label>
                      <label className="gds-import-field"><span>GDS X 偏移 · µm</span><input type="number" min="-40" max="40" step="0.05" value={pendingMaskOffset[0]} onChange={event => setPendingMaskOffset([Number(event.target.value), pendingMaskOffset[1]])} /></label>
                      <label className="gds-import-field"><span>GDS Y 偏移 · µm</span><input type="number" min="-40" max="40" step="0.05" value={pendingMaskOffset[1]} onChange={event => setPendingMaskOffset([Number(event.target.value), pendingMaskOffset[1]])} /></label>
                    </div>
                    <label className="gds-import-field"><span>保留版图区域</span><select value={pendingMaskPolarity} onChange={event => setPendingMaskPolarity(event.target.value as 'inside' | 'outside')}><option value="inside">GDS 图形内部</option><option value="outside">GDS 图形外部</option></select></label>
                    <label className="gds-import-protection"><input type="checkbox" checked={pendingProtectsEtch} onChange={event => setPendingProtectsEtch(event.target.checked)} /><span><strong>刻蚀时保护掩膜材料</strong><small>关闭后，掩膜材料会按工艺步骤的材料速率参与刻蚀。</small></span></label>
                    <div className="gds-import-help">沿用当前计算域和已有多层结构。若原工程只有裸 GDS，导入后会显示最小起始结构；请在“多层”中确认基底并添加所需薄膜，再到工艺步骤区设置刻蚀参数。</div>
                  </>}
                  <div className="gds-import-help">
                    {gdsImportMode === 'etch-mask'
                      ? '确认后只会把所选 GDS 图形作为一个顶层掩膜加入“多层”；已有基底、薄膜和工艺步骤会保留。导入前会运行 ViennaPS 原生图层检查。'
                      : 'GDS 图层编号本身不代表材料；导入后需要把图层映射为硅、氧化物等仿真材料。'}
                  </div>
                </>}
                {gdsImportError && <div className="gds-import-error" role="alert">{gdsImportError}</div>}
                <div className="gds-import-card-actions">
                  <button type="button" className="button" onClick={() => setGdsImportOpen(false)}>稍后</button>
                  <button
                    type="button"
                    className="button primary"
                    disabled={loading || !pendingGdsAsset || !gdsLayerIds(pendingGdsAsset, pendingGdsStructure).length || (gdsImportMode === 'etch-mask' && !gdsMaskPairs(pendingGdsAsset, pendingGdsStructure).length)}
                    onClick={() => void importPendingGds()}
                  >{loading ? '正在检查…' : '导入并准备预览'}</button>
                </div>
              </div>,
              document.body,
            )}
          </div>
          {project.geometry.kind === 'stack' ? <div className="field-grid one-column geometry-fields">
            <SelectField label="基底材料" value={project.geometry.substrateMaterial} options={materials.map(m => ({ value: m.name, label: m.name }))} onChange={value => updateProject(previous => previous.geometry.kind === 'stack' ? { ...previous, geometry: { ...previous.geometry, substrateMaterial: value } } : previous)} />
            <NumericField label="基底表面高度" unit="µm" value={project.geometry.baseHeight} onChange={value => updateProject(previous => previous.geometry.kind === 'stack' ? { ...previous, geometry: { ...previous.geometry, baseHeight: value } } : previous)} />
            {project.geometry.layers.map((layer, index) => layer.source === 'gds-mask' && project.geometry.kind === 'stack' && project.geometry.gdsMask ? <div className="layer-card gds-mask-card" key={layer.id}>
              <div className="section-heading gds-mask-section-heading"><div className="gds-mask-heading-copy"><span className="field-label">第 {index + 1} 层 · {layer.name}</span><span className="gds-mask-heading-hint">GDS 掩膜 · 顶层图案直接使用此版图图形</span></div><button type="button" className="button danger-quiet" onClick={() => removeLayer(layer.id)}>移除</button></div>
              <label className="field"><span className="field-label">掩膜名称</span><input type="text" maxLength={80} value={layer.name} onChange={event => editLayer(layer.id, { name: event.target.value })} /></label>
              <SelectField label="掩膜材料" value={layer.material} options={materials.map(item => ({ value: item.name, label: item.name }))} onChange={value => editLayer(layer.id, { material: value })} />
              <NumericField label="掩膜厚度" unit="µm" value={layer.thickness} min={2 * project.domain.gridDelta} max={100} step=".05" onChange={value => editLayer(layer.id, { thickness: value })} />
              {gdsAsset && <>
                <SelectField label="版图结构" value={project.geometry.gdsMask.structure} options={gdsAsset.structures.filter(item => gdsMaskPairs(gdsAsset, item.name).length > 0).map(item => ({ value: item.name, label: item.name }))} onChange={value => {
                  const pair = gdsMaskPairs(gdsAsset, value)[0]; if (!pair) return;
                  editStackGdsMask(mask => ({ ...mask, structure: value, layer: pair[0], datatype: pair[1] }));
                }} />
                <SelectField label="GDS 掩膜图层 / datatype" value={`${project.geometry.gdsMask.layer}/${project.geometry.gdsMask.datatype}`} options={gdsMaskPairs(gdsAsset, project.geometry.gdsMask.structure).map(([gdsLayer, datatype]) => ({ value: `${gdsLayer}/${datatype}`, label: `Layer ${gdsLayer} · datatype ${datatype}` }))} onChange={value => { const [gdsLayer, datatype] = value.split('/').map(Number); editStackGdsMask(mask => ({ ...mask, layer: gdsLayer, datatype })); }} />
                <NumericField label="版图尺寸比例" value={project.geometry.gdsMask.unitScale} min={.000001} max={1_000_000} step="0.1" onChange={value => editStackGdsMask(mask => ({ ...mask, unitScale: value }))} />
                <SelectField label="保留图形区域" value={project.geometry.gdsMask.retainedRegion} options={[{ value: 'inside', label: 'GDS 图形内部' }, { value: 'outside', label: 'GDS 图形外部' }]} onChange={value => editStackGdsMask(mask => ({ ...mask, retainedRegion: value as 'inside' | 'outside' }))} />
                <NumericField label="GDS X 偏移" unit="µm" value={project.geometry.gdsMask.position[0]} min={-40} max={40} step="0.05" onChange={value => editStackGdsMask(mask => { const position: [number, number, number] = [...mask.position]; position[0] = value; return { ...mask, position }; })} />
                <NumericField label="GDS Y 偏移" unit="µm" value={project.geometry.gdsMask.position[1]} min={-40} max={40} step="0.05" onChange={value => editStackGdsMask(mask => { const position: [number, number, number] = [...mask.position]; position[1] = value; return { ...mask, position }; })} />
                <label className="visibility-option"><input type="checkbox" checked={project.geometry.gdsMask.protectsEtch} onChange={event => editStackGdsMask(mask => ({ ...mask, protectsEtch: event.target.checked }))} /><span><strong>保护掩膜材料</strong><small>关闭后，掩膜材料按工艺步骤中的材料速率参与刻蚀。</small></span></label>
                <div className="inline-note">来源：{project.geometry.gdsMask.fileName} · {project.geometry.gdsMask.layer}/{project.geometry.gdsMask.datatype}。掩膜底面自动贴合其下方多层的顶部。</div>
              </>}
            </div> : <div className="layer-card" key={layer.id}>
              <label className="field"><span className="field-label">第 {index + 1} 层名称</span><input type="text" value={layer.name} onChange={event => editLayer(layer.id, { name: event.target.value })} /></label>
              <SelectField label="材料" value={layer.material} options={materials.map(m => ({ value: m.name, label: m.name }))} onChange={value => editLayer(layer.id, { material: value })} />
              <NumericField label="厚度" unit="µm" value={layer.thickness} min={.01} max={100} step=".05" onChange={value => editLayer(layer.id, { thickness: value })} />
              <SelectField label="角色" value={layer.role} options={[{ value: 'film', label: '薄膜' }, { value: 'mask', label: '掩膜' }]} onChange={value => editLayer(layer.id, { role: value as Layer['role'] })} />
              <div className="layer-actions"><button className="button" disabled={index === 0} onClick={() => moveLayer(layer.id, -1)}>↑</button><button className="button" disabled={index === layerCount - 1} onClick={() => moveLayer(layer.id, 1)}>↓</button><button className="button" onClick={() => copyLayer(layer.id)}>复制</button><button className="button danger-quiet" onClick={() => removeLayer(layer.id)}>删除</button></div>
            </div>)}
            <button className="button" disabled={project.geometry.layers.length >= 16} onClick={addLayer}>＋ 添加层</button>
            <SelectField label="顶层图案" value={project.geometry.pattern.kind} options={project.geometry.gdsMask ? [{ value: 'gds', label: 'GDS 掩膜图案' }] : [{ value: 'none', label: '无' }, { value: 'trench', label: '沟槽' }, ...(project.dimension === 3 ? [{ value: 'hole', label: '圆孔' }] : [])]} hint={project.geometry.gdsMask ? '由上方 GDS 掩膜层提供图案。' : undefined} onChange={value => updateProject(previous => { if (previous.geometry.kind !== 'stack' || previous.geometry.gdsMask) return previous; const pattern: StackGeometry['pattern'] = value === 'trench' ? { kind: 'trench', width: 2, taperAngle: 0 } : value === 'hole' && previous.dimension === 3 ? { kind: 'hole', radius: 1, taperAngle: 0 } : { kind: 'none' }; return { ...previous, geometry: { ...previous.geometry, pattern } }; })} />
            {project.geometry.pattern.kind === 'trench' && <NumericField label="沟槽宽度" unit="µm" value={project.geometry.pattern.width} min={.05} onChange={value => updatePattern({ width: value })} />}
            {project.geometry.pattern.kind === 'hole' && <NumericField label="孔半径" unit="µm" value={project.geometry.pattern.radius} min={.05} onChange={value => updatePattern({ radius: value })} />}
            {(project.geometry.pattern.kind === 'trench' || project.geometry.pattern.kind === 'hole') && <NumericField label="图案锥角" unit="°" value={project.geometry.pattern.taperAngle} min={-45} max={45} onChange={value => updatePattern({ taperAngle: value })} />}
            {project.geometry.gdsMask && <div className="inline-note">当前为 GDS 图形转移模式。运行前可先用“生成结构预览”检查掩膜、膜层和基底的相对位置。</div>}
          </div> : project.geometry.kind === 'gds' ? <div className="field-grid one-column geometry-fields gds-fields">
            <div className="gds-file-summary"><div><strong>{project.geometry.fileName}</strong><span>{gdsAsset ? `${gdsAsset.structures.length} 个结构单元 · ${gdsAvailableLayers.length} 个图层` : '正在读取布局…'}</span></div>
              <details><summary>文件与坐标信息</summary><div className="gds-file-details"><span>SHA-256：{project.geometry.assetId}</span>
                {gdsAsset && <span>文件单位：{gdsAsset.units.metersPerDatabaseUnit} m/数据库单位；{gdsAsset.units.userUnitsPerDatabaseUnit} 数据库单位/用户单位；1 原生单位 = {gdsAsset.units.micrometersPerNativeUserUnit} µm。</span>}
                <span>ViennaPS 将 XY 映射为 {project.dimension === 2 ? 'x/h' : 'x/y'}；3D 图层高度沿 z。</span>
              </div></details>
            </div>
            <button type="button" className="button primary" disabled={!gdsAsset || loading} onClick={startGdsMaskTransfer}>从此版图建立刻蚀流程…</button>
            <SelectField label="GDS 结构单元" value={project.geometry.structure} options={(gdsAsset?.structures ?? []).map(item => ({ value: item.name, label: item.name }))} onChange={value => {
              if (!gdsAsset) return;
              const ids = gdsLayerIds(gdsAsset, value); const material = materials.find(item => item.name === 'SiO2')?.name ?? materials[0]?.name ?? 'Si';
              editGdsGeometry(current => ({ ...current, structure: value, layers: ids.slice(0, 64).map(layer => ({ layer, material, ...(project.dimension === 3 ? { baseHeight: 0, height: Math.max(0.5, 2 * project.domain.gridDelta), mask: false } : {}) })) }));
              setGdsInspection(null);
            }} />
            <NumericField label="声明单位到 µm 的手动比例" value={project.geometry.unitScale} min={0.000001} max={1000000} step="0.1" hint="自动换算后再乘此比例，默认 1。" onChange={value => editGdsGeometry(current => ({ ...current, unitScale: value }))} />
            {(project.dimension === 2 ? [{ index: 0, label: 'GDS x 位置', unit: 'µm' }, { index: 1, label: 'GDS h 位置', unit: 'µm' }] : [{ index: 0, label: 'GDS x 位置', unit: 'µm' }, { index: 1, label: 'GDS y 位置', unit: 'µm' }, { index: 2, label: '层高附加偏移 z', unit: 'µm' }]).map(axis => <NumericField key={axis.label} label={axis.label} unit={axis.unit} value={selectedGds?.position[axis.index] ?? 0} min={-40} max={40} step="0.05" onChange={value => editGdsGeometry(current => { const position: [number, number, number] = [...current.position]; position[axis.index] = value; if (project.dimension === 2) position[2] = 0; return { ...current, position }; })} />)}
            <div className="gds-layer-list"><span className="field-label">结构中可用图层与材料映射</span>
              {gdsAvailableLayers.map(layerId => {
                const mapping = selectedGds?.layers.find(item => item.layer === layerId);
                return <div className="layer-card" key={layerId}>
                  <label className="visibility-option"><input type="checkbox" aria-label={`包含 GDS 图层 ${layerId}`} checked={!!mapping} onChange={event => editGdsGeometry(current => {
                    if (event.target.checked) {
                      if (current.layers.length >= 64 || current.layers.some(item => item.layer === layerId)) return current;
                      const material = materials.find(item => item.name === 'SiO2')?.name ?? materials[0]?.name ?? 'Si';
                      return { ...current, layers: [...current.layers, { layer: layerId, material, ...(project.dimension === 3 ? { baseHeight: 0, height: Math.max(0.5, 2 * project.domain.gridDelta), mask: false } : {}) }].sort((a,b) => a.layer-b.layer) };
                    }
                    return { ...current, layers: current.layers.filter(item => item.layer !== layerId) };
                  })} /><span><strong>GDS 图层 {layerId}</strong></span></label>
                  {mapping && <>
                    <SelectField label={`图层 ${layerId} 的材料`} value={mapping.material} options={materials.map(item => ({ value: item.name, label: item.name }))} onChange={value => editGdsGeometry(current => ({ ...current, layers: current.layers.map(item => item.layer === layerId ? { ...item, material: value } : item) }))} />
                    {project.dimension === 3 && <>
                      <NumericField label={`图层 ${layerId} 底面高度`} unit="µm" value={mapping.baseHeight ?? 0} step="0.05" onChange={value => editGdsGeometry(current => ({ ...current, layers: current.layers.map(item => item.layer === layerId ? { ...item, baseHeight: value } : item) }))} />
                      <NumericField label={`图层 ${layerId} 厚度`} unit="µm" value={mapping.height ?? Math.max(0.5, 2 * project.domain.gridDelta)} min={2 * project.domain.gridDelta} step="0.05" onChange={value => editGdsGeometry(current => ({ ...current, layers: current.layers.map(item => item.layer === layerId ? { ...item, height: value } : item) }))} />
                      <label className="visibility-option"><input type="checkbox" aria-label={`GDS 图层 ${layerId} 是掩膜`} checked={mapping.mask ?? false} onChange={event => editGdsGeometry(current => ({ ...current, layers: current.layers.map(item => item.layer === layerId ? { ...item, mask: event.target.checked } : item) }))} /><span><strong>作为掩膜材料</strong><small>图层厚度由上方高度设置。</small></span></label>
                    </>}
                  </>}
                </div>;
              })}
              {gdsAvailableLayers.length > 64 && <div className="inline-note warning">schema 7 最多允许映射 64 个 GDS 图层；请勾选要保留的层。</div>}
            </div>
            <button className="button gds-check-button" aria-label="检查 ViennaPS 原生 GDS 读入" disabled={loading || !project.geometry.layers.length || !gdsAsset} onClick={() => void inspectGds()}>检查原生 GDS 布局</button>
            {gdsInspection && <div className="inline-note gds-inspection-result"><strong>GDSGeometry 布局读取边界</strong>
              {gdsInspection.supportPurpose === 'align-native-periodic-gds-bounds-with-project-domain' && <span>周期边界使用未映射的隐藏 GDS 支持图层 {String(gdsInspection.supportLayer)} 对齐 ViennaPS 周期范围。</span>}
              <details className="gds-inspection"><summary>诊断详情</summary><div className="gds-inspection-content"><span>边界 {JSON.stringify(gdsInspection.nativeBounds)}；ViennaPS 读取 {String(gdsInspection.nativeStructureCount)} 个单元。</span><span>已选原生图层 {JSON.stringify(gdsInspection.selectedNativeLayers)} · 隐藏支持图层 {JSON.stringify(gdsInspection.supportLayers)} · Level-set 点数 {JSON.stringify(gdsInspection.levelSetPointCounts)}</span>{gdsInspection.supportPurpose === 'align-native-periodic-gds-bounds-with-project-domain' && <span>支持图层不会进入材料 Level-set 或工艺结果。</span>}<span>未选结构仅用于初始化布局边界，不会插入仿真 Domain。结构预览显示所选图层的实际网格。</span></div></details>
            </div>}
          </div> : project.geometry.kind === 'fin' ? <div className="field-grid one-column geometry-fields">
            <SelectField label="Fin 材料" value={project.geometry.substrateMaterial} options={materials.map(item => ({ value: item.name, label: item.name }))} onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, substrateMaterial: value } } : previous)} />
            <SelectField label="Fin 构造方式" value={project.geometry.construction ?? 'native'} options={[{ value: 'boolean', label: 'ViennaPS 布尔几何（推荐）' }, { value: 'native', label: '原生 MakeFin（旧项目兼容）' }]} onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, construction: value as 'native' | 'boolean' } } : previous)} />
            <NumericField label="Fin 宽度" unit="µm" value={project.geometry.width} min={2 * project.domain.gridDelta} max={40} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, width: value } } : previous)} />
            <NumericField label="Fin 高度" unit="µm" value={project.geometry.height} min={2 * project.domain.gridDelta} max={100} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, height: value } } : previous)} />
            <NumericField label="Fin 锥角" unit="°" value={project.geometry.taperAngle} min={-45} max={45} step="1" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, taperAngle: value } } : previous)} />
            <SelectField label="掩膜材料" value={project.geometry.maskMaterial} options={materials.map(item => ({ value: item.name, label: item.name }))} onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, maskMaterial: value } } : previous)} />
            <NumericField label="掩膜厚度" unit="µm" value={project.geometry.maskHeight} min={0} max={20} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, maskHeight: value } } : previous)} />
            {project.geometry.maskHeight > 0 && <NumericField label="掩膜锥角" unit="°" value={project.geometry.maskTaperAngle} min={-45} max={45} step="1" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, maskTaperAngle: value } } : previous)} />}
            {project.dimension === 3 && <>
              <label className="visibility-option"><input type="checkbox" aria-label="使用 ViennaPS Fin 挤出" checked={!!project.geometry.extrusion} onChange={event => updateProject(previous => previous.geometry.kind === 'fin' ? { ...previous, geometry: { ...previous.geometry, extrusion: event.target.checked ? { extent: [-1, 1], axis: 1 } : undefined } } : previous)} /><span><strong>用原生 Extrude 沿 y 方向挤出 2D Fin</strong><small>关闭时直接调用 d3.MakeFin；打开时调用 d2.MakeFin + ViennaPS Extrude。</small></span></label>
              {project.geometry.extrusion && <>
                <NumericField label="挤出 y 最小位置" unit="µm" value={project.geometry.extrusion.extent[0]} min={-(project.domain.yExtent ?? 6)/2} max={(project.domain.yExtent ?? 6)/2} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' && previous.geometry.extrusion ? { ...previous, geometry: { ...previous.geometry, extrusion: { ...previous.geometry.extrusion, extent: [value, previous.geometry.extrusion.extent[1]] } } } : previous)} />
                <NumericField label="挤出 y 最大位置" unit="µm" value={project.geometry.extrusion.extent[1]} min={-(project.domain.yExtent ?? 6)/2} max={(project.domain.yExtent ?? 6)/2} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'fin' && previous.geometry.extrusion ? { ...previous, geometry: { ...previous.geometry, extrusion: { ...previous.geometry.extrusion, extent: [previous.geometry.extrusion.extent[0], value] } } } : previous)} />
              </>}
            </>}
            {finFallbackModels.length > 0 || masked3DFinFallbackModels.length > 0 || finExtrusionFallbackModels.length > 0
              ? <div className="inline-note warning"><strong>已为已安装引擎启用安全构造回退</strong>
                  {finFallbackModels.length > 0 && <span>{finFallbackModels.join('、')} 与 ViennaPS 4.6.2 原生 MakeFin 组合会导致 ViennaPS 进程崩溃。运行时将使用经 GPU_DISK 与 CPU_DISK 独立参考验证的 ViennaPS 布尔 Fin；已保存工程仍保留原始构造选择。</span>}
                  {masked3DFinFallbackModels.length > 0 && <span>三维带遮罩 Fin 的原生构造与通用粒子过程会产生不一致的最终网格。运行时改用经产品与独立 ViennaPS GPU_DISK 参考对照的布尔构造；保存的项目仍保留原构造选择，初始快照记录实际路径。</span>}
                  {finExtrusionFallbackModels.length > 0 && <span>周期边界下，{finExtrusionFallbackModels.join('、')} 的 3D 挤出 Fin 改用 ViennaPS 3D 布尔盒体构造，保留所设 y 范围。该输入已与独立 ViennaPS 参考核对，初始快照记录实际构造路径。</span>}
                </div>
              : <div className="inline-note">布尔构造使用 ViennaPS 平面、盒体、BooleanOperation 和材料 level set 生成 Fin 及顶部遮罩；原生 MakeFin 保留既有项目结果。3D 平面切片可在 ViennaPS Slice 中另行计算。</div>}
          </div> : project.geometry.kind !== 'plane' ? <div className="field-grid one-column geometry-fields">
            <SelectField label="基底材料" value={project.geometry.substrateMaterial} options={materials.map(m => ({ value: m.name, label: m.name }))} onChange={value => updateProject(previous => previous.geometry.kind !== 'stack' ? { ...previous, geometry: { ...previous.geometry, substrateMaterial: value } } : previous)} />
            <SelectField label="掩膜材料" value={project.geometry.maskMaterial ?? 'Mask'} options={materials.map(m => ({ value: m.name, label: m.name }))} onChange={value => updateProject(previous => previous.geometry.kind === 'trench' || previous.geometry.kind === 'hole' ? { ...previous, geometry: { ...previous.geometry, maskMaterial: value } } : previous)} />
            {project.geometry.kind === 'trench' && <NumericField label="沟槽宽度" unit="µm" value={project.geometry.width} min={0.05} max={40} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'trench' ? { ...previous, geometry: { ...previous.geometry, width: value } } : previous)} />}
            {project.geometry.kind === 'hole' && <NumericField label="孔半径" unit="µm" value={project.geometry.radius} min={.05} step=".05" onChange={value => updateProject(previous => previous.geometry.kind === 'hole' ? { ...previous, geometry: { ...previous.geometry, radius: value } } : previous)} />}

            <NumericField label="深度" unit="µm" value={project.geometry.depth} min={0.05} max={100} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind !== 'plane' ? { ...previous, geometry: { ...previous.geometry, depth: value } } : previous)} />
            <NumericField label="锥角" unit="°" value={project.geometry.taperAngle} min={-45} max={45} step="1" hint="正值表示底部收窄" onChange={value => updateProject(previous => previous.geometry.kind !== 'plane' ? { ...previous, geometry: { ...previous.geometry, taperAngle: value } } : previous)} />
            <NumericField label="掩膜厚度" unit="µm" value={project.geometry.maskHeight} min={0} max={20} step="0.05" hint="0 表示不建立掩膜" onChange={value => updateProject(previous => previous.geometry.kind !== 'plane' ? { ...previous, geometry: { ...previous.geometry, maskHeight: value } } : previous)} />
            {project.geometry.maskHeight > 0 && <NumericField label="掩膜锥角" unit="°" value={project.geometry.maskTaperAngle} min={-45} max={45} step="1" onChange={value => updateProject(previous => previous.geometry.kind !== 'plane' ? { ...previous, geometry: { ...previous.geometry, maskTaperAngle: value } } : previous)} />}
          </div> : <div className="field-grid one-column geometry-fields"><SelectField label="基底材料" value={project.geometry.substrateMaterial} options={materials.map(m => ({ value: m.name, label: m.name }))} onChange={value => updateProject(previous => previous.geometry.kind === 'plane' ? { ...previous, geometry: { ...previous.geometry, substrateMaterial: value } } : previous)} /><NumericField label="平面高度" unit="µm" value={project.geometry.height} step="0.05" onChange={value => updateProject(previous => previous.geometry.kind === 'plane' ? { ...previous, geometry: { ...previous.geometry, height: value } } : previous)} /></div>}
          {project.dimension === 3 && project.schemaVersion === 7 && (project.geometry.kind === 'fin' || project.geometry.kind === 'gds') && <div className="field-grid one-column geometry-fields">
            <NumericField label="真实切片位置 y" unit="µm" value={slicePosition} min={-(project.domain.yExtent ?? 6)/2} max={(project.domain.yExtent ?? 6)/2} step="0.05" onChange={setSlicePosition} />
            <button className="button" disabled={loading || health !== 'ready' || active} onClick={() => void startNativeSlice()}>运行 ViennaPS Slice 并生成 2D 网格</button>
            <details className="compact-help"><summary>切片说明</summary><p>从 3D Domain 生成真实几何切片，并保存为独立 2D 快照。查看器裁剪不会生成几何。</p></details>
          </div>}
        </section></StructurePanel>}

        {activity === 'recipe' && <RecipePanel><section id="recipe-context" className="panel-section process-section">
          <div className="section-heading"><div><h2>工艺步骤</h2></div><span className="step-count">{project.steps.length} 步</span></div>
          <div className="panel-card recipe-add-card">
            <div className="recipe-add-heading">添加工艺步骤</div>
            <div className="add-step-groups">
              <div className="add-step-group"><strong>工艺过程</strong><div className="add-step-row">
                <button className="button add-step" onClick={() => addStep('isotropic')}>＋ 添加各向同性步骤</button>
                <button className="button add-step" onClick={() => addStep('singleParticle')}>＋ 添加单粒子步骤</button>
                <button className="button add-step" onClick={() => addStep('multiParticle')}>＋ 添加多粒子步骤</button>
                <button className="button add-step" onClick={() => addStep('directional')}>＋ 添加定向步骤</button>
              </div></div>
              <div className="add-step-group"><strong>ViennaPS 专用模型</strong><div className="specialized-add-row"><select aria-label="选择 ViennaPS 专用模型" value={newSpecializedModel} onChange={event => setNewSpecializedModel(event.target.value)}>{(specializedCatalog?.models ?? []).map(spec => <option key={spec.id} value={spec.id}>{spec.label}</option>)}</select><button className="button add-step" disabled={!specializedCatalog} onClick={() => addStep('specialized')}>＋ 添加专用模型步骤</button></div></div>
              <div className="add-step-group"><strong>几何操作</strong><div className="add-step-row">
                <button className="button add-step" onClick={() => addGeometryStep('removeMaterial')}>＋ 移除材料</button>
                <button className="button add-step" onClick={() => addGeometryStep('planarize')}>＋ 平坦化</button>
                <button className="button add-step" onClick={() => addGeometryStep('boolean')}>＋ 布尔几何</button>
              </div></div>
            </div>
          </div>
          <div className="step-list">
            {project.steps.map((step, index) => <div key={step.id} className={`step-card ${selectedStep === step.id ? 'selected' : ''} ${!step.enabled ? 'disabled-step' : ''}`}>
              <button className="step-main" onClick={() => { setSelectedStep(step.id); setSelection({ kind: 'step', id: step.id }); setActivity('recipe'); setInspectorOpen(true); setMobilePanel('settings'); }}>
                <span className="step-number">{String(index + 1).padStart(2, '0')}</span>
                <span className={`step-symbol ${isGeometryStep(step) ? 'geometry' : step.operation}`}>{isGeometryStep(step) ? '◇' : step.operation === 'etch' ? '−' : '+'}</span>
                <span className="step-title"><strong>{step.name}</strong><small>{stepKindLabel(step)}</small></span>
              </button>
              <label className="toggle" aria-label="启用步骤"><input type="checkbox" checked={step.enabled} onChange={event => editStep(step.id, value => ({ ...value, enabled: event.target.checked }))} /><span /></label>
            </div>)}
            {!project.steps.length && <div className="steps-empty"><span>＋</span><strong>还没有工艺步骤</strong><small>可以先预览结构，也可以添加第一步工艺。</small></div>}
          </div>
        </section></RecipePanel>}

        {activity === 'results' && <ResultsPanel><section id="results-context" className="panel-section history-section">
          <div className="section-heading"><div><h2>运行记录</h2></div><button className="icon-button" title="刷新记录" onClick={() => void refreshLists()}>↻</button></div>
          {runs.length ? <div className="history-list">{runs.map(row => <button key={row.runId} className={`history-item ${displayRunId === row.runId ? 'selected' : ''}`} onClick={() => void loadServerRun(row.runId, true)}>
            <span className={`history-status ${row.status}`} /><span className="history-copy"><strong>{row.kind === 'preview' ? '结构预览' : row.kind === 'flux' ? '单次通量' : row.kind === 'slice' ? 'ViennaPS 真实切片' : '工艺运行'}</strong><small>{new Date(row.createdAt ?? '').toLocaleString()} · {row.runId.slice(0, 8)}</small></span><span className="history-state">{humanStatus(row.status)}</span>
          </button>)}</div> : <div className="inline-note">运行记录会保存在本机工作台。</div>}
        </section></ResultsPanel>}
        {activity === 'settings' && <SettingsPanel><section id="settings-context" className="panel-section settings-context">
          <div className="section-heading"><div><h2>求解器与输出</h2></div></div>
          <div className="field-grid one-column solver-fields">
            {processSteps.length > 0 && <NumericField label="时间步比例" value={project.solver.timeStepRatio} min={0.05} max={0.5} step="0.01" hint="显式传入 AdvectionParameters" onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, timeStepRatio: value } }))} />}
            {particleSteps > 0 ? <>
              <div className="field">
                <span className="field-label">射线档位<small>决定统计噪声与单步耗时；同一种子仍可复现</small></span>
                <div className="segmented ray-presets" role="group" aria-label="射线档位">
                  {RAY_PRESETS.map(preset => <button key={preset.value} type="button" title={preset.detail}
                    aria-label={`射线档位 ${preset.label} ${preset.value}`}
                    aria-pressed={project.solver.raysPerPoint === preset.value}
                    className={project.solver.raysPerPoint === preset.value ? 'active' : ''}
                    onClick={() => updateProject(previous => ({ ...previous, solver: { ...previous.solver, raysPerPoint: preset.value } }))}>
                    {preset.label} · {preset.value}
                  </button>)}
                </div>
              </div>
              <NumericField label="每点射线数" value={project.solver.raysPerPoint} min={16} max={4096} step="16" hint="16–4096，可微调" onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, raysPerPoint: Math.trunc(value) } }))} />
              <SelectField label="随机策略" value={project.solver.random.mode} options={[{ value: 'fixed', label: '固定种子（可重放）' }, { value: 'random', label: '每次运行生成' }]} onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, random: value === 'fixed' ? { mode: 'fixed', seed: previous.solver.random.mode === 'fixed' ? previous.solver.random.seed : 314159 } : { mode: 'random' } } }))} />
              {project.solver.random.mode === 'fixed' && <NumericField label="随机种子" value={project.solver.random.seed} min={0} max={4294967295} step="1" onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, random: { mode: 'fixed', seed: Math.trunc(value) } } }))} />}
              <SelectField label="引擎偏好" value={project.solver.enginePreference} options={[{ value: 'auto', label: '自动 · GPU 优先' }, { value: 'cpu', label: 'CPU · CPU_DISK' }, { value: 'gpu', label: 'GPU · GPU_DISK', disabled: !gpuDeviceSelectable }]} onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, enginePreference: value as Solver['enginePreference'] } }))} />
              <div className="capability-note"><i />{gpuExposed
                ? nativeFailureNotice ?? `自动模式优先使用所选 CUDA GPU${defaultGpuDevice ? `（默认 ${defaultGpuDevice.name}）` : ''}；GPU 运行失败会保留原生错误。${gpuCoverageVerified ? ' 当前类别已有代表性运行记录。' : ''}`
                : '未发现可用的 CUDA 计算设备。'}</div>
            </> : <div className="not-applicable"><strong>射线 / 随机种子 / GPU 通量：不适用</strong><span>当前启用步骤没有非零速率的单粒子模型。</span></div>}
            {project.schemaVersion >= 7 && <>
              <SelectField label="计算 GPU" value={project.solver.gpuDeviceId ?? 'auto'} options={[
                { value: 'auto', label: gpuInventory?.devices.find(device => device.uuid === gpuInventory.preferredGpuDeviceId)?.name ? `自动 · 优先 ${gpuInventory.devices.find(device => device.uuid === gpuInventory.preferredGpuDeviceId)!.name}` : gpuInventory?.status === 'unavailable' ? '自动 · 未发现 CUDA GPU' : '自动 · 正在检测 CUDA GPU' },
                ...(gpuInventory?.devices ?? []).map(device => ({ value: device.uuid, label: `${device.name} · ${device.memoryTotalMiB ?? '未知'} MiB · ${device.pciBusId}` })),
              ]} onChange={value => updateProject(previous => { const solver = { ...previous.solver }; if (value === 'auto') delete solver.gpuDeviceId; else solver.gpuDeviceId = value; return { ...previous, solver }; })} />
              <div className="inline-note">该选择只作用于使用 ViennaPS CUDA 通量引擎的步骤；各向同性、定向及几何操作不使用 CUDA 通量引擎。三维查看器通过浏览器 WebGL 请求高性能适配器，具体渲染卡由浏览器和驱动选择。</div>
              {(gpuInventory?.devices.length ?? 0) === 0 && <div className="inline-note warning">本机没有检测到可用于 ViennaPS CUDA 计算的 NVIDIA GPU；显式 GPU 运行会失败，不会静默切换设备。</div>}
            </>}
            {project.schemaVersion < 7 && <div className="not-applicable"><strong>高级原生求解器参数</strong><span>旧项目格式仍可运行。升级后可编辑 ViennaLS 离散、自适应步进、射线跟踪和单次通量参数。</span><button className="button" onClick={() => setProject(promoteProjectToV7(project))}>升级到扩展项目格式 7</button></div>}
            {project.schemaVersion >= 7 && <details className="solver-advanced">
              <summary>ViennaLS 离散与自适应参数</summary>
              {processSteps.length > 0 ? <>
                <SelectField label="空间离散" value={project.solver.advection?.spatialScheme ?? 'ENGQUIST_OSHER_1ST_ORDER'} options={[
                  ['ENGQUIST_OSHER_1ST_ORDER','Engquist–Osher 1 阶'],['ENGQUIST_OSHER_2ND_ORDER','Engquist–Osher 2 阶'],
                  ['LAX_FRIEDRICHS_1ST_ORDER','Lax–Friedrichs 1 阶'],['LAX_FRIEDRICHS_2ND_ORDER','Lax–Friedrichs 2 阶'],
                  ['LOCAL_LAX_FRIEDRICHS_ANALYTICAL_1ST_ORDER','解析 Local Lax–Friedrichs 1 阶'],
                  ['LOCAL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER','Local Local Lax–Friedrichs 1 阶'],['LOCAL_LOCAL_LAX_FRIEDRICHS_2ND_ORDER','Local Local Lax–Friedrichs 2 阶'],
                  ['LOCAL_LAX_FRIEDRICHS_1ST_ORDER','Local Lax–Friedrichs 1 阶'],['LOCAL_LAX_FRIEDRICHS_2ND_ORDER','Local Lax–Friedrichs 2 阶'],
                  ['STENCIL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER','Stencil Local Lax–Friedrichs 1 阶'],['WENO_3RD_ORDER','WENO 3 阶'],['WENO_5TH_ORDER','WENO 5 阶'],
                ].map(([value,label]) => ({ value, label }))} onChange={value => editAdvection('spatialScheme', value)} />
                <SelectField label="时间离散" value={project.solver.advection?.temporalScheme ?? 'FORWARD_EULER'} options={[
                  { value: 'FORWARD_EULER', label: 'Forward Euler' },{ value: 'RUNGE_KUTTA_2ND_ORDER', label: 'Runge–Kutta 2 阶' },{ value: 'RUNGE_KUTTA_3RD_ORDER', label: 'Runge–Kutta 3 阶' },
                ]} onChange={value => editAdvection('temporalScheme', value)} />
                <label className="visibility-option"><input type="checkbox" checked={project.solver.advection?.adaptiveTimeStepping ?? false} onChange={event => editAdvection('adaptiveTimeStepping', event.target.checked)} /><span><strong>自适应时间步</strong><small>传入 AdvectionParameters.adaptiveTimeStepping</small></span></label>
                {project.solver.advection?.adaptiveTimeStepping && <NumericField label="自适应细分次数" value={project.solver.advection?.adaptiveTimeStepSubdivisions ?? 20} min={1} max={256} step="1" onChange={value => editAdvection('adaptiveTimeStepSubdivisions', Math.trunc(value))} />}
                <label className="visibility-option"><input type="checkbox" checked={project.solver.advection?.checkDissipation ?? true} onChange={event => editAdvection('checkDissipation', event.target.checked)} /><span><strong>检查数值耗散</strong><small>ViennaLS 原生 AdvectionParameters 开关</small></span></label>
                {project.solver.advection?.checkDissipation && <NumericField label="耗散系数 α" value={project.solver.advection?.dissipationAlpha ?? 1} min={0} max={10} step="0.05" onChange={value => editAdvection('dissipationAlpha', value)} />}
                <label className="visibility-option"><input type="checkbox" checked={project.solver.advection?.ignoreVoids ?? false} onChange={event => editAdvection('ignoreVoids', event.target.checked)} /><span><strong>忽略空洞</strong><small>传入 AdvectionParameters.ignoreVoids</small></span></label>
                <label className="visibility-option"><input type="checkbox" checked={project.solver.advection?.calculateIntermediateVelocities ?? false} onChange={event => editAdvection('calculateIntermediateVelocities', event.target.checked)} /><span><strong>计算中间速度</strong><small>原生求解器开关；不表示一定生成单独文件</small></span></label>
                <label className="visibility-option"><input type="checkbox" checked={project.solver.advection?.velocityOutput ?? false} onChange={event => editAdvection('velocityOutput', event.target.checked)} /><span><strong>启用速度输出</strong><small>ViennaLS 5.8.5 将原生速度与耗散数组写入 VTP；单位按原生数据保留</small></span></label>
              </> : <div className="not-applicable"><strong>不适用</strong><span>当前配方没有工艺过程步骤。</span></div>}
            </details>}
            {project.schemaVersion >= 7 && particleSteps > 0 && <details className="solver-advanced">
              <summary>单粒子射线跟踪参数</summary>
              <NumericField label="Disk 半径" value={project.solver.rayTracing?.diskRadius ?? 0} min={0} max={100} step="0.01" hint="ViennaPS 绑定没有声明此参数单位" onChange={value => editRayTracing('diskRadius', value)} />
              <NumericField label="最大边界命中次数" value={project.solver.rayTracing?.maxBoundaryHits ?? 1000} min={1} max={1000000} step="1" onChange={value => editRayTracing('maxBoundaryHits', Math.trunc(value))} />
              <NumericField label="最大反射次数" value={project.solver.rayTracing?.maxReflections ?? 4294967295} min={0} max={4294967295} step="1" onChange={value => editRayTracing('maxReflections', Math.trunc(value))} />
              <NumericField label="最小节点距离比例" value={project.solver.rayTracing?.minNodeDistanceFactor ?? 0.05} min={0} max={1} step="0.01" onChange={value => editRayTracing('minNodeDistanceFactor', value)} />
              <NumericField label="平滑邻居数" value={project.solver.rayTracing?.smoothingNeighbors ?? 1} min={0} max={1024} step="1" onChange={value => editRayTracing('smoothingNeighbors', Math.trunc(value))} />
              <SelectField label="通量归一化" value={project.solver.rayTracing?.normalizationType ?? 'SOURCE'} options={[{ value: 'SOURCE', label: 'SOURCE' },{ value: 'MAX', label: 'MAX' }]} onChange={value => editRayTracing('normalizationType', value as RayTracingSettings['normalizationType'])} />
              <label className="visibility-option"><input type="checkbox" checked={project.solver.rayTracing?.ignoreFluxBoundaries ?? false} onChange={event => editRayTracing('ignoreFluxBoundaries', event.target.checked)} /><span><strong>忽略通量边界</strong><small>传入 RayTracingParameters.ignoreFluxBoundaries</small></span></label>
            </details>}
            <NumericField label="CPU 线程数" value={project.solver.cpuThreads} min={capabilities?.resourcePolicy?.run?.cpuThreads?.minimum ?? 1} max={capabilities?.resourcePolicy?.run?.cpuThreads?.maximum ?? 1} step="1" disabled={!capabilities?.resourcePolicy?.run?.cpuThreads?.maximum} hint={capabilities?.resourcePolicy?.run?.cpuThreads ? `本机可用上限 ${capabilities.resourcePolicy.run.cpuThreads.maximum} 线程` : '正在读取本机 CPU 上限'} onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, cpuThreads: Math.trunc(value) } }))} />
            <NumericField label="运行超时" unit="s" value={project.solver.timeoutSeconds} min={0} max={600} step="1" hint="0 表示不限制运行时长；用户可自行观察是否卡住。" onChange={value => updateProject(previous => ({ ...previous, solver: { ...previous.solver, timeoutSeconds: Math.trunc(value) } }))} />
          </div>
        <section className="panel-section output-settings-panel" aria-label="原生输出设置">
          <div className="section-heading"><div><span className="eyebrow">输出文件</span><h2>结果文件</h2></div></div>
          {project.schemaVersion < 7
            ? <div className="not-applicable"><strong>扩展项目格式 7</strong><span>升级后可以选择额外的 ViennaPS Domain 输出。查看器所需的表面网格始终保存。</span><button className="button" onClick={() => setProject(promoteProjectToV7(project))}>升级并设置输出</button></div>
            : <div className="field-grid one-column">
              <label className="visibility-option"><input aria-label="输出原生 level-set" type="checkbox" checked={project.outputSettings?.levelSets ?? false} onChange={event => editOutputSetting('levelSets', event.target.checked)} /><span><strong>Level-set 数据</strong><small>保存 ViennaPS 每个 level set 的原生 .lvst 文件</small></span></label>
              <label className="visibility-option"><input aria-label="输出 level-set 网格" type="checkbox" checked={project.outputSettings?.levelSetMeshes ?? false} onChange={event => editOutputSetting('levelSetMeshes', event.target.checked)} /><span><strong>Level-set 网格</strong><small>按 level-set 分文件保存原生 VTP 网格</small></span></label>
              <label className="visibility-option"><input aria-label="输出原生表面 VTK" type="checkbox" checked={project.outputSettings?.surfaceMeshVtk ?? false} onChange={event => editOutputSetting('surfaceMeshVtk', event.target.checked)} /><span><strong>原生表面 VTK</strong><small>默认关闭；仅下载表面网格或交给 ParaView 等外部工具时启用，工作台预览使用快照数据</small></span></label>
              {project.dimension === 3 && <label className="visibility-option"><input aria-label="输出体网格" type="checkbox" checked={project.outputSettings?.volumeMesh ?? false} onChange={event => editOutputSetting('volumeMesh', event.target.checked)} /><span><strong>体网格 / 3D 体积预览</strong><small>使用 ViennaPS 原生 VTU 四面体；新建 3D 项目默认启用，会增加结果文件体积</small></span></label>}
              <label className="visibility-option"><input aria-label="输出 ViennaPS 元数据" type="checkbox" checked={project.outputSettings?.nativeMetadata ?? false} onChange={event => editOutputSetting('nativeMetadata', event.target.checked)} /><span><strong>Domain 元数据</strong><small>保留原生键和值；未声明的单位和点/单元关联保持未指定</small></span></label>
              <div className="inline-note">表面网格始终作为查看器结果保存；所选原生文件附带 SHA-256、尺寸和 level-set 材料映射，可逐个下载或打包导出。</div>
            </div>}
        </section>
        </section></SettingsPanel>}
      </ContextPanel>
      </aside>

      <div className="column-resizer" role="separator" aria-orientation="vertical" aria-label="调整结构栏宽度"
        aria-controls="structure-panel" aria-valuemin={panelWidthLimits.structure.min} aria-valuemax={maxPanelWidth('structure', workspaceRef.current?.clientWidth ?? 1200)}
        aria-valuenow={Math.round(panelWidths.structure)} tabIndex={0} onKeyDown={event => adjustPanelWidth(event, 'structure')}
        onPointerDown={event => beginPanelResize(event, 'structure')} onPointerMove={movePanelResize} onPointerUp={endPanelResize}
        onPointerCancel={endPanelResize} onLostPointerCapture={endPanelResize}
        onDoubleClick={() => setPanelWidths(previous => fitPanelWidths({ ...previous, structure: 280 }, workspaceRef.current?.clientWidth ?? 1000))} />

      <section id="viewer-panel" className={`viewer-panel ${mobilePanel === 'viewer' ? 'mobile-visible' : ''}`}>
        <div className="viewer-header">
          <div className="viewer-title"><div><span className="eyebrow">网格查看</span><h1>结构结果</h1></div>
            {selectedSnapshot && <div className="snapshot-chip"><span>{selectedSnapshot.stepName}</span><small>{selectedSnapshot.sliceReadback ? 'ViennaPS 真实 3D 切片' : selectedSnapshot.stepId === 'initial' ? '初始结构' : `第 ${selectedSnapshot.stepIndex} 步`}</small></div>}
          </div>
          <div className="viewer-actions">
            <label className="compare-select"><span>对比</span><select value={compareRunId} onChange={event => void loadComparison(event.target.value)}><option value="">关闭对比</option>{runs.filter(row => row.status === 'succeeded' || row.status === 'failed').map(row => <option key={row.runId} value={row.runId}>{row.runId.slice(0, 8)} · {humanStatus(row.status)}</option>)}</select></label>
            {compareInfo && <select aria-label="对比步骤" value={compareSnapshot?.stepId ?? ''} onChange={event => void loadComparison(compareInfo.runId, event.target.value)}>{compareInfo.snapshots.map(row => <option key={row.stepId} value={row.stepId}>{row.name}</option>)}</select>}
            {snapshotRows.length > 0 && <select aria-label="当前结果步骤" value={selectedStepId ?? ''} onChange={event => { setAutoFollow(false); if (displayRunId) void fetchSnapshot(displayRunId, event.target.value); }}>{snapshotRows.map(row => <option key={row.stepId} value={row.stepId}>{row.name}</option>)}</select>}
            {displayDimension === 2 && <>
            <button className="icon-button" title="缩小" onClick={() => changeView({ zoom: Math.max(0.1, view2D.zoom / 1.2) })}>−</button>
            <button className="icon-button" title="放大" onClick={() => changeView({ zoom: Math.min(20, view2D.zoom * 1.2) })}>＋</button>
            <button className="icon-button" title="显示网格" onClick={() => changeView({ showGrid: !view2D.showGrid })}>{view2D.showGrid ? '▦' : '□'}</button>
            </>}
            {displayDimension === 2 && <button className="icon-button export-svg" title="导出 SVG" onClick={exportSvg}>⇩ SVG</button>}
          </div>
        </div>
        <div className="viewer-meta">
          <span className="meta-item"><i className="meta-dot" />{selectedSnapshot ? `${selectedSnapshot.mesh.pointCount.toLocaleString()} 点 · ${(selectedSnapshot.mesh.triangleCount ?? selectedSnapshot.mesh.lineCount ?? 0).toLocaleString()} ${displayDimension === 3 ? '三角面' : '界面段'}` : '二维网格界面'}</span>
          <span className="meta-item">坐标：{displayDimension === 3 ? 'x / y / z' : 'x / h'} · µm</span>
          {selectedExecutionGpu?.uuid && <span className="meta-item" title={`CUDA Runtime 设备/同步回读：${selectedExecutionGpu.pciBusId ?? '未知'}；nvidia-smi 进程清单：${selectedExecutionGpu.process?.processListed ? '已列出 worker' : '未列出 worker'}`}>
            计算设备：{selectedExecutionGpu.name ?? selectedExecutionGpu.uuid}
            {selectedExecutionGpu.afterProcessApply?.readbackStatus === 'verified' ? ' · CUDA 已同步核验' : ''}
            {selectedExecutionGpu.process?.processListStatus === 'not-observed' ? ' · 系统未列出进程' : ''}
          </span>}
          {selectedSnapshot && <span className="meta-item">网格 Δ={selectedSnapshot.gridDelta} µm</span>}
          {selectedSnapshot?.sliceReadback && <div className="inline-note">原生操作：{selectedSnapshot.sliceReadback.nativeOperation}，y 法向位置 {selectedSnapshot.sliceReadback.position} µm；输出是 2D 几何网格，未使用查看器裁剪。</div>}
          {selectedSnapshot?.geometryReadback && <details className="solver-advanced"><summary>ViennaPS 初始几何原生回读</summary><pre>{JSON.stringify(selectedSnapshot.geometryReadback, null, 2)}</pre></details>}
          {selectedSnapshot?.noOp && <span className="tag no-op-tag">零速率 no-op</span>}
          {isStale && <span className="tag stale-tag">对应其他输入配置</span>}
          {displayRunId && <span className="run-id">RUN {displayRunId.slice(0, 12)}</span>}
          {displayInfo?.runMode && <span className="tag">{displayInfo.runMode === 'from-domain-state' ? '起始于保存的 Domain 状态' : '从初始结构运行'}</span>}
        </div>
        {selectedSnapshot && compareSnapshot && !compatibleCompare && <div className="inline-note warning">二维 / 三维直接叠加比较已禁用，请选择相同维度的快照。</div>}
        {displayDimension === 3 && selectedSnapshot ? <Viewer3D current={selectedSnapshot} compare={compatibleCompare} view={view3D} onViewChange={changeView3D} onError={setNotice} catalog={materials} /> : <SvgViewer current={selectedSnapshot} compare={compatibleCompare} view={view2D} onViewChange={changeView} svgRef={svgRef} catalog={materials} />}
        <div className={`dock-shell ${dockOpen ? 'expanded' : 'collapsed'}`}>
          <div className="dock-header">
            <button type="button" className="dock-toggle" aria-expanded={dockOpen} onClick={() => setDockOpen(value => !value)}>{dockOpen ? '▾' : '▸'} 日志</button>
            <Dock tab={dockTab} onChange={tab => { setDockTab(tab); setDockOpen(true); }} counts={{ log: runInfo ? 1 : 0, flux: displayFlux?.fields.length ?? 0, intermediate: displayInfo?.intermediateOutputs?.length ?? 0, native: directionalActual || multiParticleActual || specializedActual || geometryActual ? 1 : 0, artifacts: selectedSnapshot?.outputs?.length ?? 0 }} />
          </div>
          {dockOpen && <div className="dock-scroll">
        <div id="dock-flux" className="dock-block">
        {displayInfo?.kind === 'flux' && <section className="flux-result-panel" aria-label="ViennaPS 通量场结果">
          <div className="flux-result-heading"><div><span className="eyebrow">原生通量网格</span><h2>单次通量结果</h2></div>{displayFlux && <button className="button" onClick={exportFluxCsv} disabled={!fluxField}>导出所选字段 CSV</button>}</div>
          {!displayFlux ? <div className="inline-note">{displayInfo.status === 'succeeded' ? '正在读取 ViennaPS 通量网格…' : '通量计算运行后，ViennaPS 返回的网格字段会显示在此处。'}</div> : <>
            <div className="flux-result-meta"><span>来源：{displayFlux.source}</span><span>引擎：{displayFlux.execution.actualEngine}</span><span>种子：{displayFlux.execution.actualSeed}</span><span>ViennaPS {displayFlux.versions.viennaps} / ViennaLS {displayFlux.versions.viennals}</span></div>
            <div className="flux-result-meta"><span>网格 SHA-256：{displayFlux.meshIdentity}</span><span>{displayFlux.mesh.pointCount.toLocaleString()} 点</span><span>字段元组：{fluxField?.tuples.toLocaleString() ?? 0}</span><span>{fluxField?.association ?? '无字段'} · {fluxField?.nativeUnit ?? '原生接口未声明单位'}</span></div>
            {displayFlux.fields.length > 0 ? <>
              <label className="flux-field-select"><span>原生字段</span><select aria-label="通量字段" value={selectedFluxField} onChange={event => setSelectedFluxField(event.target.value)}>{displayFlux.fields.map(field => <option key={`${field.association}:${field.name}`} value={`${field.association}:${field.name}`}>{field.association} · {field.name} ({field.components} 分量，{field.tuples} 元组)</option>)}</select></label>
              <div className="inline-note">字段数组按 ViennaLS Mesh 原样保存在 {fluxField?.association} 容器。该通量返回网格有 {Object.values(displayFlux.mesh.cellCounts).reduce((sum, count) => sum + count, 0)} 个显式拓扑单元；表格只显示原生元组序号和值，不将数据映射到其他表面网格。{fluxField?.nativeUnit ? ` 原生单位：${fluxField.nativeUnit}。` : ' ViennaPS/ViennaLS 网格接口没有为此字段提供单位。'}</div>
              <div className="flux-table-wrap"><table className="flux-table"><thead><tr><th>tupleIndex</th><th>{fluxField?.name}</th></tr></thead><tbody>{(fluxValues ?? []).slice(0, 200).map((value, index) => <tr key={index}><td>{index}</td><td>{Array.isArray(value) ? value.map(item => Number(item).toPrecision(7)).join(', ') : Number(value).toPrecision(7)}</td></tr>)}</tbody></table>{(fluxValues?.length ?? 0) > 200 && <small>显示前 200 / {fluxValues?.length} 个元组；CSV 和运行 ZIP 包含完整原生数组。</small>}</div>
            </> : <div className="inline-note">ViennaPS 没有为此运行返回标量或向量字段数组。</div>}
          </>}
        </section>}
        </div>
        <div id="dock-intermediate" className="dock-block">
        {displayRunId && displayInfo?.kind === 'simulate' && (displayInfo.intermediateOutputs?.length ?? 0) > 0 &&
          <NativeIntermediatePanel runId={displayRunId} outputs={displayInfo.intermediateOutputs ?? []} />}
        </div>
        <div id="dock-artifacts" className="dock-block">
        <div className="result-footer">
          <div className="result-readout"><span>运行状态</span><strong className={`status-text ${runInfo?.status ?? ''}`}>{displayRunId === latestRunId ? humanStatus(runInfo?.status) : humanStatus(runs.find(row => row.runId === displayRunId)?.status)}</strong>
            {displayRunId === latestRunId && runInfo && <><span>已用时间</span><strong>{runInfo.elapsedSeconds.toFixed(1)} s</strong><span>步骤</span><strong>{runInfo.completedStepIds.length}/{runInfo.stepTotal ?? 0}</strong></>}</div>
          <div className="result-downloads"><button className="button" disabled={!displayRunId} onClick={() => void downloadRun('replay')}>导出重放配置</button><button className="button" disabled={!displayRunId || !terminalStates.has(displayInfo?.status ?? runs.find(row => row.runId === displayRunId)?.status ?? '')} onClick={() => void downloadRun('zip')}>下载运行结果 ZIP</button>{displayRunId && selectedSnapshot && <button className="button primary" disabled={loading || !terminalStates.has(displayInfo?.status ?? runs.find(row => row.runId === displayRunId)?.status ?? '') || !snapshotRows.find(row => row.stepId === selectedSnapshot.stepId)?.nativeState} title={snapshotRows.find(row => row.stepId === selectedSnapshot.stepId)?.nativeState ? '以该快照的 ViennaPS Domain 状态开始新工艺' : '该历史快照没有原生 Domain 状态'} onClick={() => void createContinuation(displayRunId, selectedSnapshot.stepId)}>以此状态开始新工艺</button>}</div>
        </div>
                  {!!selectedSnapshot?.outputs?.length && <details className="solver-advanced native-artifact-list" onToggle={event => { if (event.currentTarget.open && selectedSnapshot?.outputs?.[0]) setSelection({ kind: 'artifact', key: selectedSnapshot.outputs[0].fileName }); }}><summary>ViennaPS 原生输出 · {selectedSnapshot.outputs.length} 个文件</summary>
            {selectedSnapshot.outputs.map(artifact => <div className="native-artifact-row" key={`${artifact.fileName}:${artifact.sha256}`}>
              <span><strong>{({ surfaceMesh: '表面网格', levelSet: 'Level-set 数据', levelSetMesh: 'Level-set 网格', volumeMesh: '体网格', nativeMetadata: 'Domain 元数据' } as Record<string, string>)[artifact.kind] ?? artifact.kind}</strong>
                <small>{artifact.levelSetIndex !== undefined ? `Level-set ${artifact.levelSetIndex} · 材料 ID ${artifact.materialId} · ` : ''}{artifact.format} · {(artifact.sizeBytes / 1024).toFixed(1)} KiB</small></span>
              <a className="button" href={artifact.url} download={artifact.fileName.split('/').at(-1)} aria-label={`下载原生输出 ${artifact.fileName}`}>下载</a>
            </div>)}
          </details>}

        </div>
        <div id="dock-log" className="dock-block">
        <div className="log-panel">
          <div className="log-heading"><div><span className="eyebrow">运行输出</span><strong>运行日志</strong></div><span>{runInfo?.log.truncated ? '已达到 4 MiB 日志上限' : `游标 ${logCursor} B`}</span></div>
          <pre>{visibleLog || (displayRunId === latestRunId ? runInfo?.failureReason : displayInfo?.failureReason) || '提交运行后，ViennaPS 的标准输出和错误信息会显示在这里。'}</pre>
        </div>
        </div>
        <div id="dock-native" className="dock-block">
        {directionalActual && <div className="directional-history">
          <strong>DirectionalProcess 原生参数 · 历史记录</strong>
          <span>方向：{Array.isArray(directionalActual.requestedDirection) ? `[${directionalActual.requestedDirection.join(', ')}]` : '—'} → 单位向量 {Array.isArray(directionalActual.nativeUnitDirection) ? `[${directionalActual.nativeUnitDirection.join(', ')}]` : '—'}</span>
          <span>原生定向速度：{String(directionalActual.nativeDirectionalVelocity ?? '—')} µm/s · 原生各向同性速度：{String(directionalActual.nativeIsotropicVelocity ?? '—')} µm/s</span>
          <span>遮挡可见性：{String(directionalActual.calculateVisibility ?? '未计算')} · 掩膜材料：{Array.isArray(directionalActual.maskMaterials) ? directionalActual.maskMaterials.join(', ') || '无' : '—'}</span>
          <span>射线数 / 随机种子：不适用 · 运行引擎：{selectedExecution?.actualEngine ?? 'not-applicable'}</span>
          {selectedExecution?.noOp && <span>零速率步骤：未调用 ViennaPS Process，结果沿用前一步网格。</span>}
        </div>}
        {multiParticleActual && <div className="directional-history">
          <strong>MultiParticleProcess 原生配置与回调 · 历史记录</strong>
          <span>引擎：{selectedExecution?.actualEngine ?? '—'} · 种子：{String(selectedExecution?.actualSeed ?? '—')} · CPU 线程：{String(multiParticleActual.cpuThreads ?? '—')}</span>
          <pre>{JSON.stringify(multiParticleActual, null, 2)}</pre>
        </div>}
        {specializedActual && <div className="directional-history">
          <strong>{String((specializedActual.nativeModel as Record<string, unknown> | undefined)?.nativeClass ?? 'ViennaPS 专用模型')} 原生执行与历史参数</strong>
          <span>模型：{String((specializedActual.nativeModel as Record<string, unknown> | undefined)?.nativeModel ?? selectedExecution?.stepId)} · 引擎：{selectedExecution?.actualEngine ?? '—'} · 种子：{String(selectedExecution?.actualSeed ?? '不适用')} · CPU 线程：{String(specializedActual.cpuThreads ?? '—')}</span>
          <pre>{JSON.stringify(specializedActual, null, 2)}</pre>
        </div>}
        {geometryActual && <div className="directional-history">
          <strong>ViennaPS 几何操作 · 历史记录</strong>
          <pre>{JSON.stringify(geometryActual, null, 2)}</pre>
        </div>}
        </div>
          </div>}
        </div>
      </section>

      <div className="column-resizer" role="separator" aria-orientation="vertical" aria-label="调整参数栏宽度"
        aria-controls="settings-panel" aria-valuemin={panelWidthLimits.settings.min} aria-valuemax={maxPanelWidth('settings', workspaceRef.current?.clientWidth ?? 1200)}
        aria-valuenow={Math.round(panelWidths.settings)} tabIndex={0} onKeyDown={event => adjustPanelWidth(event, 'settings')}
        onPointerDown={event => beginPanelResize(event, 'settings')} onPointerMove={movePanelResize} onPointerUp={endPanelResize}
        onPointerCancel={endPanelResize} onLostPointerCapture={endPanelResize}
        onDoubleClick={() => setPanelWidths(previous => fitPanelWidths({ ...previous, settings: 275 }, workspaceRef.current?.clientWidth ?? 1000))} />

      <Inspector className={`${mobilePanel === 'settings' ? 'mobile-visible' : ''} ${inspectorOpen ? '' : 'inspector-collapsed'}`}>
        <section className="panel-section">
          <div className="section-heading"><div><span className="eyebrow">{selectedStepObject ? '步骤编辑' : '项目概要'}</span><h2>{selectedStepObject ? '步骤参数' : '属性检查器'}</h2></div><button type="button" className="icon-button" aria-label="关闭属性检查器" title="关闭属性检查器" onClick={() => setInspectorOpen(false)}><Icon name="close"/></button></div>
          {selectedStepObject ? <>
            <div className="inspector-tabs" role="tablist" aria-label="步骤参数分组">
              <button id="step-tab-basic" type="button" role="tab" aria-selected={inspectorTab === 'basic'} aria-controls="step-parameter-panel" tabIndex={inspectorTab === 'basic' ? 0 : -1} onClick={() => setInspectorTab('basic')}>基本</button>
              <button id="step-tab-physics" type="button" role="tab" aria-selected={inspectorTab === 'physics'} aria-controls="step-parameter-panel" tabIndex={inspectorTab === 'physics' ? 0 : -1} onClick={() => setInspectorTab('physics')}>物理</button>
              <button id="step-tab-native" type="button" role="tab" aria-selected={inspectorTab === 'native'} aria-controls="step-parameter-panel" tabIndex={inspectorTab === 'native' ? 0 : -1} onClick={() => setInspectorTab('native')}>原生参数</button>
            </div>
            <StepInspector activeTab={inspectorTab}>
            <div className="field-grid one-column step-inspector-basics">
              <div className="step-tab-panel" data-step-tab-panel="basic">
              <label className="field"><span className="field-label">步骤名称</span><input type="text" value={selectedStepObject.name} maxLength={80} onChange={event => editStep(selectedStepObject.id, step => ({ ...step, name: event.target.value }))} /></label>
              </div>
              {!isGeometryStep(selectedStepObject) ? <>
              <div className="step-tab-panel" data-step-tab-panel="basic">
              {selectedStepObject.model !== 'specialized' && <SelectField label="工艺操作" value={selectedStepObject.operation ?? 'etch'} options={[{ value: 'etch', label: '刻蚀' }, { value: 'deposit', label: '沉积' }]} onChange={value => editStep(selectedStepObject.id, step => {
                const next = { ...step, operation: value as Step['operation'] };
                if (next.operation === 'deposit') next.depositMaterial = materials.find(m => m.name === 'SiO2')?.name ?? 'SiO2'; else delete next.depositMaterial;
                delete next.materialRates; delete next.selectivityReference;
                if (next.model !== 'directional' && next.model !== 'multiParticle' && project.schemaVersion >= 4) next.rateMode = 'uniform'; else delete next.rateMode;
                return next;
              })} />}
              {selectedStepObject.model !== 'specialized' && <SelectField label="过程模型" value={selectedStepObject.model} options={[{ value: 'isotropic', label: '各向同性过程' }, { value: 'singleParticle', label: '单粒子过程' }, { value: 'multiParticle', label: '多粒子过程' }, { value: 'directional', label: '定向过程' }]} onChange={value => changeStepModel(selectedStepObject.id, value as Step['model'])} />}
              {selectedStepObject.model !== 'specialized' && selectedStepObject.operation === 'deposit' && <SelectField label="沉积材料" value={selectedStepObject.depositMaterial ?? materials[0]?.name ?? 'SiO2'} options={materials.map(m => ({ value: m.name, label: m.name }))} onChange={value => editStep(selectedStepObject.id, step => ({ ...step, depositMaterial: value }))} hint="材料不改变通用全局速率" />}
              {selectedStepObject.model === 'specialized' && <SelectField label="ViennaPS 4.6.2 专用模型" value={selectedStepObject.nativeModel ?? ''} options={specializedCatalog?.models.map(item => ({ value: item.id, label: item.label })) ?? []} onChange={value => changeSpecializedModel(selectedStepObject.id, value)} />}
              </div>
              {selectedStepObject.model === 'specialized' ? <div className="step-tab-panel" data-step-tab-panel="native">
                {(() => {
                  const spec = specializedCatalog?.models.find(item => item.id === selectedStepObject.nativeModel);
                  if (!spec) return <div className="inline-note warning">安装版 ViennaPS 专用模型目录不可用。</div>;
                  const values = selectedStepObject.nativeParameters ?? {};
                  const geometryNames = geometryMaterialNames(project.geometry);
                  return <>
                    <div className="inline-note">参数直接传给安装的 {spec.nativeClass}。{specializedCatalog?.unitsNote} 原生模型规定的工艺速率由该模型内部计算，工作台不叠加通用速率。</div>
                    {spec.id === 'oxideRegrowth' && <div className="inline-note warning">本机 ViennaPS 4.6.2 的 OxideRegrowth 已验证条件是含 SiO₂ / Si₃N₄ 的平坦多层结构。请将“顶层图案”设为“无”；此版本在带沟槽或圆孔的 Domain 上可能崩溃。{project.geometry.kind === 'stack' && project.geometry.pattern.kind !== 'none' && <button className="button" onClick={() => updateProject(previous => previous.geometry.kind === 'stack' ? { ...previous, geometry: { ...previous.geometry, pattern: { kind: 'none' } } } : previous)}>将顶层图案改为无</button>}</div>}
                    {spec.depositMaterial && <div className="not-applicable"><strong>原生沉积材料</strong><span>{spec.depositMaterial}（由模型固定）</span></div>}
                    {spec.depositMaterialSelectable && <SelectField label="沉积材料" value={selectedStepObject.depositMaterial ?? 'Al2O3'} options={materials.map(item => ({ value: item.name, label: item.name }))} onChange={value => editStep(selectedStepObject.id, step => ({ ...step, depositMaterial: value }))} />}
                    {spec.fields.map(field => {
                      const value = values[field.key];
                      const write = (next: unknown) => editSpecializedParameter(selectedStepObject.id, field.key, next);
                      if (field.kind === 'number') return <NumericField key={field.key} label={field.label} unit={field.nativeUnit ?? undefined} value={typeof value === 'number' ? value : Number(field.default ?? 0)} min={field.minimum} max={field.maximum} step={String(field.step ?? 0.1)} onChange={write} />;
                      if (field.kind === 'boolean') return <label key={field.key} className="visibility-option"><input type="checkbox" aria-label={field.label} checked={value === true} onChange={event => write(event.target.checked)} /><span><strong>{field.label}</strong><small>传入 ViennaPS {spec.nativeClass} 布尔参数</small></span></label>;
                      if (field.kind === 'enum') return <SelectField key={field.key} label={field.label} value={String(value ?? field.default ?? '')} options={(field.options ?? []).map(option => ({ value: option.value, label: option.label }))} onChange={write} />;
                      if (field.kind === 'material' || field.kind === 'materialOptional') return <SelectField key={field.key} label={field.label} value={String(value ?? '')} options={[...(field.kind === 'materialOptional' ? [{ value: '', label: '不指定' }] : []), ...materials.map(item => ({ value: item.name, label: item.name }))]} onChange={write} />;
                      if (field.kind === 'text') return <label className="field" key={field.key}><span className="field-label">{field.label}</span><input aria-label={field.label} value={String(value ?? '')} maxLength={80} onChange={event => write(event.target.value)} /></label>;
                      if (field.kind === 'vector3') return <div className="special-vector" key={field.key}><strong>{field.label}</strong>{([0, 1, 2] as const).map(axis => <NumericField key={`${field.key}-${axis}`} label={`${field.label} · ${['x', 'y', 'z'][axis]}`} value={Array.isArray(value) ? Number(value[axis] ?? 0) : Number((field.default as number[] | undefined)?.[axis] ?? 0)} step="0.1" onChange={next => { const vector = Array.isArray(value) ? [...value] as number[] : [...((field.default as number[] | undefined) ?? [0, 0, 0])]; vector[axis] = next; write(vector); }} />)}</div>;
                      if (field.kind === 'materialRates') {
                        const table = value && typeof value === 'object' ? value as Record<string, number> : {};
                        const required = spec.id === 'wetEtch' ? geometryNames : spec.id === 'selectiveEpitaxy' ? ['Si', 'SiGe'] : [];
                        const rowNames = [...new Set([...Object.keys(table), ...required])].sort();
                        return <div className="rate-table specialized-table" key={field.key}><strong>{field.label}</strong>{rowNames.map(name => <div className="rate-row" key={name}><strong>{name}</strong><NumericField label={`${name} · ${field.label}`} value={Number(table[name] ?? field.default ?? 0)} min={field.minimum} max={field.maximum} step={String(field.step ?? 0.01)} onChange={next => write({ ...table, [name]: next })} />{!required.includes(name) && <button className="icon-button" aria-label={`移除 ${name} 参数行`} onClick={() => { const next = { ...table }; delete next[name]; write(next); }}>×</button>}</div>)}{required.length === 0 && <div className="rate-add"><select aria-label={`${field.label}材料`} value={rateRowMaterial} onChange={event => setRateRowMaterial(event.target.value)}><option value="">选择材料…</option>{geometryNames.filter(name => !Object.hasOwn(table, name)).map(name => <option key={name} value={name}>{name}</option>)}</select><button className="button" disabled={!rateRowMaterial} onClick={() => { const name = rateRowMaterial; setRateRowMaterial(''); write({ ...table, [name]: Number(field.default ?? 0) }); }}>＋ 添加材料行</button></div>}</div>;
                      }
                      const table = value && typeof value === 'object' ? value as Record<string, Record<string, number>> : {};
                      return <fieldset className="special-fluorocarbon-table" key={field.key}><legend>{field.label}</legend>{Object.entries(table).sort(([a], [b]) => a.localeCompare(b)).map(([materialName, row]) => <div className="fluorocarbon-material-row" key={materialName}><strong>{materialName}</strong><div>{Object.entries(field.defaults ?? {}).map(([parameter, defaultValue]) => <NumericField key={parameter} label={`${materialName} · ${parameter}`} value={Number(row?.[parameter] ?? defaultValue)} min={field.minimum} max={field.maximum} step={String(field.step ?? 0.01)} onChange={next => write({ ...table, [materialName]: { ...row, [parameter]: next } })} />)}</div></div>)}</fieldset>;
                    })}
                    {spec.id !== 'oxidation' && <NumericField label="持续时间" unit="s" value={selectedStepObject.duration ?? 0.2} min={0.01} max={600} step="0.01" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, duration: value }))} />}
                    {spec.id === 'oxidation' && <div className="not-applicable"><strong>时间输入</strong><span>使用原生参数中的小时数与时间步；不再添加通用过程持续时间。</span></div>}
                    {spec.usesFlux && <div className="inline-note">此模型使用真实 ViennaPS 射线/通量路径。引擎、随机种子与线程会记录在本次运行历史中。</div>}
                  </>;
                })()}
              </div> : selectedStepObject.model === 'directional' ? <div className="step-tab-panel" data-step-tab-panel="physics">
                <div className="inline-note directional-map-note">方向输入会先单位化。二维坐标为 [x, h, 0]；三维为 [x, y, z]，其中 z 轴垂直。ViennaPS 原生刻蚀速度传负值、沉积速度传正值；方向和速率符号由工作台按操作映射。</div>
                <div className="directional-axis-fields">
                  {(project.dimension === 2 ? [0, 1] as const : [0, 1, 2] as const).map(axis => {
                    const direction = selectedStepObject.direction ?? defaultDirection(project.dimension);
                    const label = project.dimension === 2 ? (axis === 0 ? '方向 x 分量' : '方向 h 分量') : (['方向 x 分量', '方向 y 分量', '方向 z 分量'][axis]);
                    return <NumericField key={label} label={label} value={direction[axis]} min={-1} max={1} step="0.05" onChange={value => editStep(selectedStepObject.id, step => {
                      const next: [number, number, number] = [...(step.direction ?? defaultDirection(project.dimension))];
                      next[axis] = value;
                      if (project.dimension === 2) next[2] = 0;
                      return { ...step, direction: next };
                    })} />;
                  })}
                </div>
                <NumericField label="定向速率大小" unit="µm/s" value={selectedStepObject.directionalRateMagnitude ?? 0} min={0} max={2} step="0.01" hint="刻蚀/沉积操作自动应用原生符号" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, directionalRateMagnitude: value }))} />
                <NumericField label="各向同性速率分量" unit="µm/s" value={selectedStepObject.isotropicRateMagnitude ?? 0} min={0} max={2} step="0.01" hint="与定向分量叠加；0 表示不添加该分量" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, isotropicRateMagnitude: value }))} />
                <label className={`visibility-option ${(selectedStepObject.directionalRateMagnitude ?? 0) === 0 ? 'disabled' : ''}`}>
                  <input type="checkbox" aria-label="计算方向可见性" checked={selectedStepObject.calculateVisibility ?? true} disabled={(selectedStepObject.directionalRateMagnitude ?? 0) === 0}
                    onChange={event => editStep(selectedStepObject.id, step => ({ ...step, calculateVisibility: event.target.checked }))} />
                  <span><strong>计算遮挡可见性</strong><small>仅定向速率非零时适用；关闭后不计算遮挡</small></span>
                </label>
              </div> : selectedStepObject.model === 'multiParticle' ? <>
                <div className="step-tab-panel" data-step-tab-panel="native">
                <div className="inline-note">参数直接传给 ViennaPS 4.6.2 MultiParticleProcess。安装版绑定没有声明离子角度与能量单位，工作台不换算单位。速率关系是受控表格：按材料选择各粒子通量系数，刻蚀返回负速度、沉积返回正速度；原生通量单位未声明，系数负责换算到工作台使用的 µm/s。</div>
                <div className="multi-species-list" aria-label="多粒子种类">
                  {(selectedStepObject.species ?? []).map((particle, particleIndex) => <fieldset className="multi-species-card" key={`${particle.label}-${particleIndex}`}>
                    <legend>{particle.kind === 'ion' ? '离子' : '中性粒子'} · {particle.label}</legend>
                    <label className="field"><span className="field-label">粒子标签</span><input aria-label={`粒子标签 ${particleIndex + 1}`} value={particle.label} maxLength={40} onChange={event => editParticleSpecies(selectedStepObject.id, particleIndex, current => ({ ...current, label: event.target.value }))} /></label>
                    <button type="button" className="icon-button" aria-label={`移除粒子 ${particle.label}`} disabled={(selectedStepObject.species?.length ?? 0) <= 1} onClick={() => removeParticleSpecies(selectedStepObject.id, particleIndex)}>移除</button>
                    {particle.kind === 'ion' ? <>
                      <div className="multi-native-fields">
                        {([
                          { key: 'sourcePower', label: 'sourcePower', min: 0, max: 20, step: '0.1' },
                          { key: 'thetaRMin', label: 'thetaRMin', min: 0, max: 90, step: '1' },
                          { key: 'thetaRMax', label: 'thetaRMax', min: 0, max: 90, step: '1' },
                          { key: 'minAngle', label: 'minAngle', min: 0, max: 90, step: '1' },
                          { key: 'B_sp', label: 'B_sp', min: -1, max: 20, step: '0.1' },
                          { key: 'meanEnergy', label: 'meanEnergy', min: 0, max: 1000000, step: '1' },
                          { key: 'sigmaEnergy', label: 'sigmaEnergy', min: 0, max: 1000000, step: '1' },
                          { key: 'thresholdEnergy', label: 'thresholdEnergy', min: 0, max: 1000000, step: '1' },
                          { key: 'inflectAngle', label: 'inflectAngle', min: 0, max: 90, step: '1' },
                          { key: 'n', label: 'n', min: 1, max: 100, step: '1' },
                        ] as const).map(parameter => <NumericField key={parameter.key} label={parameter.label} value={particle[parameter.key]} min={parameter.min} max={parameter.max} step={parameter.step}
                          onChange={value => editParticleSpecies(selectedStepObject.id, particleIndex, current => current.kind === 'ion' ? { ...current, [parameter.key]: parameter.key === 'n' ? Math.round(value) : value } : current)} />)}
                      </div>
                    </> : <>
                      <NumericField label="默认黏附概率" value={particle.defaultStickingProbability} min={0} max={1} step="0.01" onChange={value => editParticleSpecies(selectedStepObject.id, particleIndex, current => current.kind === 'neutral' ? { ...current, defaultStickingProbability: value } : current)} />
                      <div className="multi-material-table">
                        {Object.entries(particle.stickingProbabilityByMaterial).map(([materialName, probability]) => <div className="rate-row" key={materialName}>
                          <strong>{materialName}</strong>
                          <NumericField label={`${materialName} 黏附概率`} value={probability} min={0} max={1} step="0.01" onChange={value => editParticleSpecies(selectedStepObject.id, particleIndex, current => current.kind === 'neutral' ? { ...current, stickingProbabilityByMaterial: { ...current.stickingProbabilityByMaterial, [materialName]: value } } : current)} />
                          <button type="button" className="icon-button" aria-label={`移除 ${materialName} 黏附覆盖`} onClick={() => editParticleSpecies(selectedStepObject.id, particleIndex, current => {
                            if (current.kind !== 'neutral') return current;
                            const stickingProbabilityByMaterial = { ...current.stickingProbabilityByMaterial }; delete stickingProbabilityByMaterial[materialName];
                            return { ...current, stickingProbabilityByMaterial };
                          })}>×</button>
                        </div>)}
                        <div className="rate-add"><select aria-label={`${particle.label} 新增材料黏附覆盖`} value={multiStickingMaterial} onChange={event => setMultiStickingMaterial(event.target.value)}><option value="">选择材料…</option>{materials.filter(material => !Object.hasOwn(particle.stickingProbabilityByMaterial, material.name)).map(material => <option key={material.name} value={material.name}>{material.name}</option>)}</select><button className="button" disabled={!multiStickingMaterial} onClick={() => {
                          const materialName = multiStickingMaterial; setMultiStickingMaterial('');
                          editParticleSpecies(selectedStepObject.id, particleIndex, current => current.kind === 'neutral' ? { ...current, stickingProbabilityByMaterial: { ...current.stickingProbabilityByMaterial, [materialName]: current.defaultStickingProbability } } : current);
                        }}>＋ 按材料设置黏附</button></div>
                      </div>
                    </>}
                  </fieldset>)}
                  <div className="rate-add"><button className="button" disabled={(selectedStepObject.species?.length ?? 0) >= 8} onClick={() => addParticleSpecies(selectedStepObject.id, 'ion')}>＋ 添加离子</button><button className="button" disabled={(selectedStepObject.species?.length ?? 0) >= 8} onClick={() => addParticleSpecies(selectedStepObject.id, 'neutral')}>＋ 添加中性粒子</button></div>
                </div>
                </div>
                <div className="step-tab-panel" data-step-tab-panel="physics">
                <div className="multi-rate-table" role="table" aria-label="多粒子速率关系">
                  <strong>速率关系系数表</strong>
                  {(selectedStepObject.species ?? []).map(particle => <NumericField key={particle.label} label={`默认系数 · ${particle.label}`} value={selectedStepObject.rateCoefficients?.defaultBySpecies[particle.label] ?? 0} min={0} max={2} step="0.001" onChange={value => editStep(selectedStepObject.id, step => ({
                    ...step, rateCoefficients: { defaultBySpecies: { ...(step.rateCoefficients?.defaultBySpecies ?? {}), [particle.label]: value }, byMaterial: step.rateCoefficients?.byMaterial ?? {} },
                  }))} />)}
                  {Object.entries(selectedStepObject.rateCoefficients?.byMaterial ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([materialName, coefficients]) => <div className="multi-material-rate-row" key={materialName}>
                    <strong>{materialName}</strong>
                    {(selectedStepObject.species ?? []).map(particle => <NumericField key={particle.label} label={`${materialName} · ${particle.label} 系数`} value={coefficients[particle.label] ?? selectedStepObject.rateCoefficients?.defaultBySpecies[particle.label] ?? 0} min={0} max={2} step="0.001" onChange={value => editStep(selectedStepObject.id, step => ({
                      ...step, rateCoefficients: { defaultBySpecies: step.rateCoefficients?.defaultBySpecies ?? {}, byMaterial: { ...(step.rateCoefficients?.byMaterial ?? {}), [materialName]: { ...(step.rateCoefficients?.byMaterial[materialName] ?? {}), [particle.label]: value } } },
                    }))} />)}
                    <button type="button" className="icon-button" aria-label={`移除 ${materialName} 速率系数覆盖`} onClick={() => editStep(selectedStepObject.id, step => {
                      const byMaterial = { ...(step.rateCoefficients?.byMaterial ?? {}) }; delete byMaterial[materialName];
                      return { ...step, rateCoefficients: { defaultBySpecies: step.rateCoefficients?.defaultBySpecies ?? {}, byMaterial } };
                    })}>×</button>
                  </div>)}
                  <div className="rate-add"><select aria-label="多粒子新增速率材料" value={multiRateMaterial} onChange={event => setMultiRateMaterial(event.target.value)}><option value="">选择材料…</option>{materials.filter(material => !Object.hasOwn(selectedStepObject.rateCoefficients?.byMaterial ?? {}, material.name)).map(material => <option key={material.name} value={material.name}>{material.name}</option>)}</select><button className="button" disabled={!multiRateMaterial} onClick={() => {
                    const materialName = multiRateMaterial; setMultiRateMaterial('');
                    editStep(selectedStepObject.id, step => ({ ...step, rateCoefficients: { defaultBySpecies: step.rateCoefficients?.defaultBySpecies ?? {}, byMaterial: { ...(step.rateCoefficients?.byMaterial ?? {}), [materialName]: { ...(step.rateCoefficients?.defaultBySpecies ?? {}) } } } }));
                  }}>＋ 添加材料系数行</button></div>
                </div>
                </div>
              </> : <>
              <div className="step-tab-panel" data-step-tab-panel="physics">
              {selectedStepObject.operation === 'etch' && <SelectField label="速率模式" value={selectedStepObject.rateMode ?? 'uniform'} options={[{ value: 'uniform', label: '统一速率' }, { value: 'material', label: '按材料设置' }]} onChange={value => setRateMode(selectedStepObject.id, value as 'uniform' | 'material')} hint="可为每种材料分别指定刻蚀速率" />}
              <NumericField label={selectedStepObject.operation === 'etch' && selectedStepObject.rateMode === 'material' ? '未列出材料默认速率' : '速率大小'} unit="µm/s" value={selectedStepObject.rateMagnitude ?? 0} min={0} max={2} step="0.01" hint={selectedStepObject.operation === 'etch' && selectedStepObject.rateMode === 'material' ? '材料表中的显式值优先；未列出的材料使用此值' : "刻蚀传入负速率，沉积传入正速率"} onChange={value => selectedStepObject.operation === 'etch' && selectedStepObject.rateMode === 'material' ? editRateProfile(selectedStepObject.id, step => ({ ...step, rateMagnitude: value, rateMode: 'material' })) : editStep(selectedStepObject.id, step => ({ ...step, rateMagnitude: value }))} />
              {selectedStepObject.operation === 'etch' && selectedStepObject.rateMode === 'material' && <>
                <div className="rate-profile-note">表格中的速率优先；未列出的材料使用默认速率。0 µm/s 表示保护。掩膜也按此表执行，同一种材料的所有区域共用速率。</div>
                <SelectField label="选择性参考材料" value={selectedStepObject.selectivityReference ?? ''} options={[{ value: '', label: '不显示参考比值' }, ...materials.map(m => ({ value: m.name, label: m.name }))]} onChange={value => setSelectivityReference(selectedStepObject.id, value)} hint="仅显示参考比值，不改变仿真" />
                <div className="rate-table" role="table" aria-label="材料相关刻蚀速率">
                  <div className="rate-table-head" role="row"><span>材料 / 速率</span><span>参考速率 ÷ 本材料</span><span /></div>
                  {Object.entries(selectedStepObject.materialRates ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([materialName, rate]) => {
                    const referenceRate = selectedStepObject.materialRates?.[selectedStepObject.selectivityReference ?? ''] ?? selectedStepObject.rateMagnitude ?? 0;
                    const ratio = rate === 0 ? referenceRate === 0 ? '— · 0/0 未定义' : '∞ · 保护' : `${(referenceRate / rate).toPrecision(4)}×`;
                    return <div className="rate-row" role="row" key={materialName}>
                      <div><strong>{materialName}</strong>{rate === 0 && <small className="protected-mark">0 · 保护</small>}<NumericField label="刻蚀速率" unit="µm/s" value={rate} min={0} max={2} step=".01" onChange={value => setMaterialRate(selectedStepObject.id, materialName, value)} /></div>
                      <span className="rate-ratio">{selectedStepObject.selectivityReference ? ratio : '—'}</span>
                      <button className="icon-button" type="button" aria-label={`移除 ${materialName} 速率覆盖`} title="移除覆盖" onClick={() => removeMaterialRate(selectedStepObject.id, materialName)}>×</button>
                    </div>;
                  })}
                  <div className="rate-add"><select aria-label="新增材料速率行" value={rateRowMaterial} onChange={event => setRateRowMaterial(event.target.value)}><option value="">选择材料…</option>{materials.filter(m => !Object.hasOwn(selectedStepObject.materialRates ?? {}, m.name)).map(m => <option key={m.name} value={m.name}>{m.name}</option>)}</select><button className="button" disabled={!rateRowMaterial} onClick={() => addMaterialRate(selectedStepObject.id, rateRowMaterial)}>＋ 添加材料行</button></div>
                </div>
              </>}
              </div></>}
              <div className="step-tab-panel" data-step-tab-panel="physics">
              {selectedStepObject.model !== 'specialized' && <NumericField label="持续时间" unit="s" value={selectedStepObject.duration ?? 0.2} min={0.01} max={600} step="0.01" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, duration: value }))} />}
              {selectedStepObject.model === 'singleParticle' && <>
                <NumericField label="黏附概率" value={selectedStepObject.stickingProbability ?? 0.5} min={0} max={1} step="0.01" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, stickingProbability: value }))} />
                <NumericField label="源角分布指数" value={selectedStepObject.sourceExponent ?? 1} min={0} max={20} step="0.1" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, sourceExponent: value }))} />
              </>}
              </div>
              </> : <div className="step-tab-panel" data-step-tab-panel="basic">
                {selectedStepObject.model === 'removeMaterial' && <SelectField label="要移除的材料" value={selectedStepObject.material ?? materials[0]?.name ?? ''} options={materials.map(material => ({ value: material.name, label: material.name }))} onChange={value => editStep(selectedStepObject.id, step => ({ ...step, material: value }))} hint="调用 ViennaPS Domain.removeMaterial" />}
                {selectedStepObject.model === 'planarize' && <NumericField label="平坦化截断高度" unit="µm" value={selectedStepObject.cutoffHeight ?? 0} min={-100} max={100} step="0.05" hint="调用 ViennaPS Planarize(domain, cutoffHeight)" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, cutoffHeight: value }))} />}
                {selectedStepObject.model === 'boolean' && <>
                  <SelectField label="布尔操作" value={selectedStepObject.booleanOperation ?? 'difference'} options={[{ value: 'union', label: '并集 · UNION' }, { value: 'intersection', label: '交集 · INTERSECT' }, { value: 'difference', label: '差集 · RELATIVE_COMPLEMENT' }]} onChange={value => editStep(selectedStepObject.id, step => ({ ...step, booleanOperation: value as Step['booleanOperation'] }))} />
                  <div className="inline-note">ViennaPS 使用真实盒体 stencil。位置是盒体底面坐标，宽度沿 x；二维高度沿 h，三维高度沿 z、深度沿 y。</div>
                  {(project.dimension === 2 ? [{ index: 0, label: '盒体 x 位置', unit: 'µm' }, { index: 1, label: '盒体 h 起始位置', unit: 'µm' }] : [{ index: 0, label: '盒体 x 位置', unit: 'µm' }, { index: 1, label: '盒体 y 位置', unit: 'µm' }, { index: 2, label: '盒体 z 起始位置', unit: 'µm' }]).map(axis => <NumericField key={axis.label} label={axis.label} unit={axis.unit} value={(selectedStepObject.position ?? [0, 0, 0])[axis.index]} min={-40} max={40} step="0.05" onChange={value => editStep(selectedStepObject.id, step => {
                    const position: [number, number, number] = [...(step.position ?? [0, 0, 0])];
                    position[axis.index] = value;
                    if (project.dimension === 2) position[2] = 0;
                    return { ...step, position };
                  })} />)}
                  <NumericField label="盒体宽度（x）" unit="µm" value={selectedStepObject.width ?? 1} min={project.domain.gridDelta * 2} max={40} step="0.05" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, width: value }))} />
                  <NumericField label={project.dimension === 2 ? '盒体高度（h）' : '盒体高度（z）'} unit="µm" value={selectedStepObject.height ?? 0.8} min={project.domain.gridDelta * 2} max={100} step="0.05" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, height: value }))} />
                  {project.dimension === 3 && <NumericField label="盒体深度（y）" unit="µm" value={selectedStepObject.depth ?? 1} min={project.domain.gridDelta * 2} max={40} step="0.05" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, depth: value }))} />}
                  <NumericField label="盒体旋转角" unit="°" value={selectedStepObject.angle ?? 0} min={-45} max={45} step="1" onChange={value => editStep(selectedStepObject.id, step => ({ ...step, angle: value }))} />
                  <label className="visibility-option"><input type="checkbox" aria-label="布尔操作应用到所有材料界面" checked={selectedStepObject.applyToAll ?? true} onChange={event => editStep(selectedStepObject.id, step => ({ ...step, applyToAll: event.target.checked }))} /><span><strong>应用到所有材料界面</strong><small>传入 ViennaPS Domain.applyBooleanOperation 的 applyToAll 参数</small></span></label>
                </>}
                <div className="not-applicable"><strong>几何操作</strong><span>没有工艺速率、物理持续时间、随机种子或通量引擎。此步骤直接修改前一步的 ViennaPS Domain。</span></div>
              </div>}
              {(isGeometryStep(selectedStepObject) || selectedStepObject.model === 'specialized') && <div className="step-tab-panel" data-step-tab-panel="physics"><div className="inline-note">{isGeometryStep(selectedStepObject) ? '几何操作没有工艺物理参数。' : '此模型的时间条件和计算参数位于“原生参数”页签。'}</div></div>}
            </div>
            {selectedStepObject.model !== 'specialized' && selectedStepObject.model !== 'multiParticle' && <div className="step-tab-panel" data-step-tab-panel="native"><div className="inline-note">此步骤没有额外的 ViennaPS 原生参数。</div></div>}
            <div className="step-tab-panel" data-step-tab-panel="basic"><div className="step-edit-actions"><button className="button" onClick={() => moveStep(selectedStepObject.id, -1)}>↑ 上移</button><button className="button" onClick={() => moveStep(selectedStepObject.id, 1)}>↓ 下移</button><button className="button" onClick={() => copyStep(selectedStepObject)}>复制</button><button className="button danger-quiet" onClick={() => { updateProject(previous => ({ ...previous, steps: previous.steps.filter(step => step.id !== selectedStepObject.id) })); setSelectedStep(null); }}>删除</button></div>
            <div className="section-divider" /></div>
          </StepInspector></> : selection.kind === 'run'
            ? <RunInspector><div className="project-summary"><strong>运行 {selection.id.slice(0, 12)}</strong><span>{humanStatus(runs.find(row => row.runId === selection.id)?.status ?? displayInfo?.status)}</span><span>{runs.find(row => row.runId === selection.id)?.kind ?? displayInfo?.kind ?? '运行'}</span></div></RunInspector>
            : selection.kind === 'material'
              ? <MaterialInspector>{selectedMaterial ? <MaterialPropertyInspector material={selectedMaterial} busy={materialBusy} onSave={saveCustomMaterialMetadata} /> : <div className="project-summary"><strong>{selection.name}</strong><span>材料目录中找不到该材料。</span></div>}</MaterialInspector>
              : selection.kind === 'artifact'
                ? <div className="project-summary"><strong>{selection.key}</strong><span>原生产物来自所选快照。</span></div>
                : <div className="project-summary"><strong>{project.name}</strong><span>{project.dimension}D · {project.steps.length} 个步骤 · {project.geometry.kind}</span><span>选择结构、配方、材料、结果或设置活动查看该域输入。</span></div>}

        </section>

        <section className="validation-card">
          <div className="validation-head"><span className={`validation-mark ${inputHash ? 'ok' : ''}`}>{inputHash ? '✓' : '!'}</span><div><strong>{inputHash ? '参数检查' : '需要修正输入'}</strong><small>{validationMessage || '等待工作台校验…'}</small></div></div>
          {warnings.map((warning, index) => <p key={index} className="warning-line">{warning}</p>)}
          <div className="validation-foot"><span>工艺步骤上限</span><strong>{capabilities?.resourcePolicy?.run?.maximumSteps == null ? '不限' : `${capabilities.resourcePolicy.run.maximumSteps} 步`}</strong></div>
          <div className="validation-foot"><span>GPU 状态</span><strong>{gpuExposed ? 'GPU_DISK · 可选择' : gpuStatus === 'unverified-outside-sandbox' ? '设备不可用' : gpuStatus}</strong></div>
        </section>
      </Inspector>
    </main>

    <StatusBar health={health} validationMessage={validationMessage} hash={inputHash} gpu={gpuExposed ? 'GPU_DISK · 可选择' : gpuStatus === 'unverified-outside-sandbox' ? '设备不可用' : gpuStatus} stepCount={runInfo?.completedStepIds.length ?? project.steps.length} stepLimit={capabilities?.resourcePolicy?.run?.maximumSteps ?? null} elapsed={runInfo ? `${runInfo.elapsedSeconds.toFixed(1)} s` : '—'} runId={displayRunId ?? ''} warnings={warnings} />

    {notice && <div className="toast" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}
    {deleteMaterialCandidate && <DeleteMaterialDialog material={deleteMaterialCandidate} busy={deletingMaterial === deleteMaterialCandidate.name} error={deleteMaterialError}
      onClose={() => { if (!deletingMaterial) { setDeleteMaterialCandidate(null); setDeleteMaterialError(''); } }}
      onConfirm={() => void deleteCustomMaterial(deleteMaterialCandidate)} />}
    {runErrorDialog && <RunErrorDialog {...runErrorDialog}
      onClose={() => setRunErrorDialog(null)}
      onViewLog={() => { setRunErrorDialog(null); setDockTab('log'); setDockOpen(true); }} />}
  </div>;
}
