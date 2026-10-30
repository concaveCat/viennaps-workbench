/**
 * vtk.js scene manager for the process-structure viewer.
 *
 * Design constraints (see README):
 *  - exactly one actor / one mapper / one draw call for the whole structure,
 *    coloured by a per-cell scalar + lookup table,
 *  - the render window is created once per mount and never rebuilt when the
 *    step changes (only the poly data is swapped),
 *  - rendering is coalesced through `requestAnimationFrame`,
 *  - the camera survives step switches,
 *  - every vtk object created here is deleted in `destroy()`.
 *
 * vtk.js is loaded with dynamic `import()` so it never lands in the first
 * paint bundle.
 */
import { hexToRgb } from './colors';
import type { vtkActor } from '@kitware/vtk.js/Rendering/Core/Actor';
import type { vtkAxesActor } from '@kitware/vtk.js/Rendering/Core/AxesActor';
import type { vtkDataArray } from '@kitware/vtk.js/Common/Core/DataArray';
import type { vtkLookupTable } from '@kitware/vtk.js/Common/Core/LookupTable';
import type { vtkPlane } from '@kitware/vtk.js/Common/DataModel/Plane';
import type { vtkPolyData } from '@kitware/vtk.js/Common/DataModel/PolyData';
import type { vtkGenericRenderWindow } from '@kitware/vtk.js/Rendering/Misc/GenericRenderWindow';
import type { vtkMapper } from '@kitware/vtk.js/Rendering/Core/Mapper';
import type { vtkOrientationMarkerWidget } from '@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget';
import type { ClipState, DisplayMode, ParsedMesh } from './types';

export type StandardView = 'iso' | 'xy' | 'xz' | 'yz';

export type VtkScene = {
  attach(mesh: ParsedMesh, hidden: ReadonlySet<number>): void;
  setHidden(hidden: ReadonlySet<number>): void;
  setDisplayMode(mode: DisplayMode): void;
  setProjection(mode: 'parallel' | 'perspective'): void;
  setClip(clip: ClipState): void;
  resetView(): void;
  setStandardView(view: StandardView): void;
  rotate(azimuthDeg: number, elevationDeg: number): void;
  requestRender(): void;
  renderNow(): void;
  getFps(): number;
  getTriangleCount(): number;
  getPointCount(): number;
  readPixels(): { width: number; height: number; pixels: Uint8Array } | null;
  destroy(): void;
};

type Geometry = {
  positions: Float32Array;
  polys: Uint32Array;
  pointColor: Float32Array;
  triangleCount: number;
};

/**
 * Drop the triangles of hidden materials. The vertex buffer and the per-vertex
 * colour scalars are shared by all materials, so hiding only rebuilds the index
 * buffer: still one actor and one draw call.
 */
function filterGeometry(geometry: Geometry, hidden: ReadonlySet<number>): Uint32Array {
  if (hidden.size === 0) return geometry.polys;
  const { polys, pointColor, triangleCount } = geometry;
  let kept = 0;
  for (let t = 0; t < triangleCount; t++) if (!hidden.has(pointColor[polys[t * 4 + 1]])) kept += 1;
  const outPolys = new Uint32Array(kept * 4);
  let w = 0;
  for (let t = 0; t < triangleCount; t++) {
    if (hidden.has(pointColor[polys[t * 4 + 1]])) continue;
    outPolys[w * 4] = 3;
    outPolys[w * 4 + 1] = polys[t * 4 + 1];
    outPolys[w * 4 + 2] = polys[t * 4 + 2];
    outPolys[w * 4 + 3] = polys[t * 4 + 3];
    w += 1;
  }
  return outPolys;
}

const AXIS_INDEX = { x: 0, y: 1, z: 2 } as const;

export async function createVtkScene(container: HTMLElement): Promise<VtkScene> {
  // The profile registers the OpenGL poly data mapper; it must load first.
  await import('@kitware/vtk.js/Rendering/OpenGL/Profiles/Geometry');
  const [genericModule, polyDataModule, mapperModule, actorModule, lutModule, dataArrayModule, planeModule, axesModule, widgetModule] =
    await Promise.all([
      import('@kitware/vtk.js/Rendering/Misc/GenericRenderWindow'),
      import('@kitware/vtk.js/Common/DataModel/PolyData'),
      import('@kitware/vtk.js/Rendering/Core/Mapper'),
      import('@kitware/vtk.js/Rendering/Core/Actor'),
      import('@kitware/vtk.js/Common/Core/LookupTable'),
      import('@kitware/vtk.js/Common/Core/DataArray'),
      import('@kitware/vtk.js/Common/DataModel/Plane'),
      import('@kitware/vtk.js/Rendering/Core/AxesActor'),
      import('@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget'),
    ]);

  const win: vtkGenericRenderWindow = genericModule.default.newInstance({
    background: [0.965, 0.968, 0.973],
    listenWindowResize: false,
  });
  win.setContainer(container);
  win.resize();
  const renderer = win.getRenderer();
  const renderWindow = win.getRenderWindow();
  const interactor = win.getInteractor();

  const axes: vtkAxesActor = axesModule.default.newInstance();
  // The default palette (bright yellow Y axis) disappears on the light stage
  // background; deepen every shaft so the orientation marker stays readable.
  axes.setXAxisColor([178, 34, 34]);
  axes.setYAxisColor([161, 98, 7]);
  axes.setZAxisColor([21, 115, 71]);
  axes.update();
  const widget: vtkOrientationMarkerWidget = widgetModule.default.newInstance({
    actor: axes,
    interactor,
    parentRenderer: renderer,
  });
  widget.setEnabled(true);
  widget.setViewportSize(0.16);
  widget.setMinPixelSize(96);
  widget.setMaxPixelSize(160);

  const data: vtkPolyData = polyDataModule.default.newInstance();
  let lookupTable: vtkLookupTable = lutModule.default.newInstance();
  lookupTable.setTable([[0, 0, 0, 255], [255, 255, 255, 255]]);
  lookupTable.setRange(0, 1);
  const scalars: vtkDataArray = dataArrayModule.default.newInstance({
    name: 'material',
    numberOfComponents: 1,
    values: new Float32Array(0),
  });
  data.getPointData().setScalars(scalars);
  const mapper: vtkMapper = mapperModule.default.newInstance();
  mapper.setInputData(data);
  mapper.setLookupTable(lookupTable);
  mapper.setUseLookupTableScalarRange(true);
  mapper.setScalarModeToUsePointData();
  mapper.setColorModeToMapScalars();
  mapper.setInterpolateScalarsBeforeMapping(false);
  mapper.setScalarVisibility(true);
  const actor: vtkActor = actorModule.default.newInstance();
  actor.setMapper(mapper);
  actor.getProperty().setInterpolationToFlat();
  actor.getProperty().setAmbient(0.25);
  actor.getProperty().setDiffuse(0.85);
  actor.getProperty().setSpecular(0.08);
  renderer.addActor(actor);

  const plane: vtkPlane = planeModule.default.newInstance();
  const camera = renderer.getActiveCamera();
  camera.setParallelProjection(true);

  let currentMesh: ParsedMesh | null = null;
  let hiddenMaterials: ReadonlySet<number> = new Set<number>();
  let frameTimes: number[] = [];
  let rafId: number | null = null;
  let destroyed = false;
  let cameraInitialized = false;

  const recordFrame = (): void => {
    const now = performance.now();
    frameTimes.push(now);
    if (frameTimes.length > 90) frameTimes.splice(0, frameTimes.length - 90);
  };

  const renderNow = (): void => {
    if (destroyed) return;
    renderWindow.render();
    recordFrame();
  };

  const requestRender = (): void => {
    if (destroyed || rafId !== null) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      renderNow();
    });
  };

  const frameSubscription = interactor.onEndAnimation(() => recordFrame());

  const resizeObserver = new ResizeObserver(() => {
    if (destroyed) return;
    win.resize();
    renderer.resetCameraClippingRange();
  });
  resizeObserver.observe(container);

  const boundsOf = (): { center: [number, number, number]; radius: number } => {
    const bounds = currentMesh?.bounds;
    if (!bounds) return { center: [0, 0, 0], radius: 1 };
    const center: [number, number, number] = [
      (bounds[0] + bounds[3]) / 2,
      (bounds[1] + bounds[4]) / 2,
      (bounds[2] + bounds[5]) / 2,
    ];
    const radius = Math.max(
      Math.hypot(bounds[3] - bounds[0], bounds[4] - bounds[1], bounds[5] - bounds[2]) / 2,
      1e-6,
    );
    return { center, radius };
  };

  const lookFrom = (direction: [number, number, number], viewUp: [number, number, number]): void => {
    const { center, radius } = boundsOf();
    const length = Math.hypot(direction[0], direction[1], direction[2]) || 1;
    camera.setFocalPoint(center[0], center[1], center[2]);
    camera.setPosition(
      center[0] + (direction[0] / length) * radius * 3,
      center[1] + (direction[1] / length) * radius * 3,
      center[2] + (direction[2] / length) * radius * 3,
    );
    camera.setViewUp(viewUp[0], viewUp[1], viewUp[2]);
    renderer.resetCamera();
    renderer.resetCameraClippingRange();
    cameraInitialized = true;
    requestRender();
  };

  const resetView = (): void => lookFrom([1, -1, 0.8], [0, 0, 1]);

  const setStandardView = (view: StandardView): void => {
    if (view === 'xy') lookFrom([0, 0, 1], [0, 1, 0]);
    else if (view === 'xz') lookFrom([0, -1, 0], [0, 0, 1]);
    else if (view === 'yz') lookFrom([1, 0, 0], [0, 0, 1]);
    else lookFrom([1, -1, 0.8], [0, 0, 1]);
  };

  const applyMaterials = (mesh: ParsedMesh): void => {
    const count = mesh.materials.length;
    // vtk.js 37 dropped setTableValue/setNumberOfTableValues: build a fresh
    // lookup table with one RGBA entry per material and swap it in.
    const table: number[][] = [];
    for (let i = 0; i < Math.max(2, count); i++) {
      const material = mesh.materials[Math.min(i, count - 1)];
      const [r, g, b] = hexToRgb(material?.color ?? '#9aa3ad');
      table.push([Math.round(r * 255), Math.round(g * 255), Math.round(b * 255), 255]);
    }
    const next = lutModule.default.newInstance();
    next.setTable(table);
    next.setRange(0, Math.max(1, count - 1));
    next.modified();
    mapper.setLookupTable(next);
    lookupTable.delete();
    lookupTable = next;
  };

  const upload = (mesh: ParsedMesh): void => {
    data.getPoints().setData(mesh.positions, 3);
    data.getPolys().setData(filterGeometry(mesh, hiddenMaterials));
    scalars.setData(mesh.pointColor, 1);
    data.getPointData().setScalars(scalars);
    applyMaterials(mesh);
    data.modified();
    mapper.modified();
  };

  const attach = (mesh: ParsedMesh, hidden: ReadonlySet<number>): void => {
    currentMesh = mesh;
    hiddenMaterials = hidden;
    upload(mesh);
    renderer.resetCameraClippingRange();
    if (!cameraInitialized) resetView();
    else requestRender();
  };

  const setHidden = (hidden: ReadonlySet<number>): void => {
    if (!currentMesh) return;
    hiddenMaterials = hidden;
    upload(currentMesh);
    requestRender();
  };

  const setDisplayMode = (mode: DisplayMode): void => {
    const property = actor.getProperty();
    if (mode === 'wireframe') property.setRepresentationToWireframe();
    else property.setRepresentationToSurface();
    requestRender();
  };

  const setProjection = (mode: 'parallel' | 'perspective'): void => {
    camera.setParallelProjection(mode === 'parallel');
    requestRender();
  };

  const setClip = (clip: ClipState): void => {
    mapper.removeAllClippingPlanes();
    if (!clip.enabled) {
      requestRender();
      return;
    }
    const index = AXIS_INDEX[clip.axis];
    const normal: [number, number, number] = [0, 0, 0];
    normal[index] = clip.keep === 'positive' ? 1 : -1;
    const origin: [number, number, number] = [0, 0, 0];
    origin[index] = clip.position;
    plane.setNormal(normal[0], normal[1], normal[2]);
    plane.setOrigin(origin[0], origin[1], origin[2]);
    mapper.addClippingPlane(plane);
    requestRender();
  };

  const rotate = (azimuthDeg: number, elevationDeg: number): void => {
    camera.azimuth(azimuthDeg);
    camera.elevation(elevationDeg);
    camera.orthogonalizeViewUp();
    renderNow();
  };

  const getFps = (): number => {
    if (frameTimes.length < 2) return 0;
    const now = performance.now();
    const last = frameTimes[frameTimes.length - 1];
    if (now - last > 1000) {
      frameTimes = [];
      return 0;
    }
    const span = last - frameTimes[0];
    return span > 0 ? ((frameTimes.length - 1) * 1000) / span : 0;
  };

  const readPixels = (): { width: number; height: number; pixels: Uint8Array } | null => {
    if (destroyed) return null;
    const canvas = container.querySelector('canvas');
    if (!canvas) return null;
    const gl = (canvas.getContext('webgl2') ?? canvas.getContext('webgl')) as WebGL2RenderingContext | null;
    if (!gl) return null;
    renderNow();
    const width = canvas.width;
    const height = canvas.height;
    const pixels = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    return { width, height, pixels };
  };

  const destroy = (): void => {
    if (destroyed) return;
    destroyed = true;
    if (rafId !== null) cancelAnimationFrame(rafId);
    rafId = null;
    frameSubscription.unsubscribe();
    resizeObserver.disconnect();
    widget.setEnabled(false);
    widget.delete();
    axes.delete();
    mapper.removeAllClippingPlanes();
    plane.delete();
    renderer.removeActor(actor);
    actor.delete();
    mapper.delete();
    scalars.delete();
    data.delete();
    lookupTable.delete();
    const style = interactor.getInteractorStyle();
    win.delete();
    interactor.delete();
    if (style && typeof style.delete === 'function') style.delete();
    renderer.delete();
    renderWindow.delete();
    for (const canvas of Array.from(container.querySelectorAll('canvas'))) canvas.remove();
  };

  return {
    attach,
    setHidden,
    setDisplayMode,
    setProjection,
    setClip,
    resetView,
    setStandardView,
    rotate,
    requestRender,
    renderNow,
    getFps,
    getTriangleCount: () => currentMesh?.triangleCount ?? 0,
    getPointCount: () => currentMesh?.pointCount ?? 0,
    readPixels,
    destroy,
  };
}
