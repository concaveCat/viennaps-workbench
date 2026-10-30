"""P3 snapshot comparison, process windows and provenance reports."""

from __future__ import annotations

import hashlib
import html
import json
import math
from datetime import datetime, timezone
from typing import Any

from .. import snapshots, storage
from .metrology import snapshot_content_hash

MAX_VIEW_POINTS = 20_000
MAX_VIEW_LINES = 30_000
MAX_VIEW_TRIANGLES = 10_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def load_snapshot(run_id: str, step_id: str) -> dict[str, Any]:
    run_dir = storage.run_path(run_id)
    manifest = storage.read_json(run_dir / "manifest.json")
    if not any(item.get("stepId") == step_id for item in manifest.get("snapshots") or []):
        raise FileNotFoundError(f"snapshot {step_id!r} is not listed in run {run_id!r}")
    path = snapshots.ensure_materialized(run_dir, step_id)
    payload = storage.read_json(path)
    payload["runId"] = run_id
    payload["stepId"] = step_id
    return payload


def _material_names(snapshot: dict[str, Any]) -> dict[int, str]:
    return {int(key): str(value.get("name") or key) for key, value in (snapshot.get("materials") or {}).items()}


def _line_length(points: list[Any], line: list[int]) -> float:
    try:
        a, b = points[int(line[0])], points[int(line[1])]
        return math.dist([float(a[0]), float(a[1]), float(a[2]) if len(a) > 2 else 0.0],
                         [float(b[0]), float(b[1]), float(b[2]) if len(b) > 2 else 0.0])
    except (IndexError, TypeError, ValueError):
        return 0.0


def _triangle_area(points: list[Any], triangle: list[int]) -> float:
    try:
        a, b, c = (points[int(index)] for index in triangle)
        ax, ay, az = (float(value) for value in a[:3])
        bx, by, bz = (float(value) for value in b[:3])
        cx, cy, cz = (float(value) for value in c[:3])
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        cross = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        return 0.5 * math.sqrt(sum(value * value for value in cross))
    except (IndexError, TypeError, ValueError):
        return 0.0


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    dimension = int(snapshot.get("dimension") or 0)
    points = snapshot.get("points") or []
    lines = snapshot.get("lines") or []
    triangles = snapshot.get("triangles") or []
    material_ids = (snapshot.get("cellData") or {}).get("materialIds") or []
    names = _material_names(snapshot)
    measures: dict[str, float] = {}
    if dimension == 2:
        for index, line in enumerate(lines):
            material_id = int(material_ids[index]) if index < len(material_ids) else -1
            name = names.get(material_id, str(material_id))
            measures[name] = measures.get(name, 0.0) + _line_length(points, line)
        measure_kind = "length_um"
    elif dimension == 3:
        for index, triangle in enumerate(triangles):
            material_id = int(material_ids[index]) if index < len(material_ids) else -1
            name = names.get(material_id, str(material_id))
            measures[name] = measures.get(name, 0.0) + _triangle_area(points, triangle)
        measure_kind = "area_um2"
    else:
        measure_kind = "unknown"
    xs = [float(point[0]) for point in points if len(point) >= 2]
    ys = [float(point[1]) for point in points if len(point) >= 2]
    zs = [float(point[2]) for point in points if len(point) >= 3]
    bounds = [
        min(xs, default=0.0), max(xs, default=0.0),
        min(ys, default=0.0), max(ys, default=0.0),
        min(zs, default=0.0), max(zs, default=0.0),
    ]
    return {
        "runId": snapshot.get("runId"),
        "stepId": snapshot.get("stepId"),
        "dimension": dimension,
        "topology": snapshot.get("topology"),
        "digest": snapshot_content_hash(snapshot),
        "pointCount": len(points),
        "lineCount": len(lines),
        "triangleCount": len(triangles),
        "bounds": bounds,
        "materialMeasures": {key: round(value, 9) for key, value in sorted(measures.items())},
        "measureKind": measure_kind,
        "surfaceBounds": snapshot.get("surfaceBounds"),
        "materialTops": snapshot.get("materialTops"),
        "measuredGeometry": snapshot.get("measuredGeometry"),
        "measuredRemoval": snapshot.get("measuredRemoval"),
        "measuredAdded": snapshot.get("measuredAdded"),
    }


def _max_point_delta(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    left_points, right_points = left.get("points") or [], right.get("points") or []
    if not left_points or len(left_points) != len(right_points):
        return None
    maximum = 0.0
    for a, b in zip(left_points, right_points):
        try:
            maximum = max(maximum, math.dist([float(value) for value in a[:3]], [float(value) for value in b[:3]]))
        except (TypeError, ValueError):
            return None
    return maximum


def _bounds_delta(left: list[Any], right: list[Any]) -> float:
    return max((abs(float(a) - float(b)) for a, b in zip(left, right)), default=0.0)


def compare_snapshots(left: dict[str, Any], right: dict[str, Any], tolerance: float) -> dict[str, Any]:
    left_summary = snapshot_summary(left)
    right_summary = snapshot_summary(right)
    reasons: list[str] = []
    if left_summary["digest"] == right_summary["digest"]:
        status = "identical"
    else:
        topology_equal = all(
            left_summary[key] == right_summary[key]
            for key in ("dimension", "topology", "pointCount", "lineCount", "triangleCount")
        )
        if not topology_equal:
            reasons.append("snapshot topology or element counts differ")
        max_delta = _max_point_delta(left, right)
        bounds_delta = _bounds_delta(left_summary["bounds"], right_summary["bounds"])
        if max_delta is not None and max_delta > tolerance:
            reasons.append(f"max point delta {max_delta:.6g} exceeds tolerance {tolerance:.6g}")
        if bounds_delta > tolerance:
            reasons.append(f"bounds delta {bounds_delta:.6g} exceeds tolerance {tolerance:.6g}")
        material_delta = {
            key: round(right_summary["materialMeasures"].get(key, 0.0) - left_summary["materialMeasures"].get(key, 0.0), 9)
            for key in sorted(set(left_summary["materialMeasures"]) | set(right_summary["materialMeasures"]))
        }
        for key, delta in material_delta.items():
            if abs(delta) > tolerance:
                reasons.append(f"material {key} measure delta {delta:.6g} exceeds tolerance")
        status = "within_tolerance" if topology_equal and not reasons else "different"
    material_delta = {
        key: round(right_summary["materialMeasures"].get(key, 0.0) - left_summary["materialMeasures"].get(key, 0.0), 9)
        for key in sorted(set(left_summary["materialMeasures"]) | set(right_summary["materialMeasures"]))
    }
    return {
        "status": status,
        "leftDigest": left_summary["digest"],
        "rightDigest": right_summary["digest"],
        "maxPointDelta": _max_point_delta(left, right),
        "boundsDelta": _bounds_delta(left_summary["bounds"], right_summary["bounds"]),
        "materialDelta": material_delta,
        "reasons": reasons,
        "left": left_summary,
        "right": right_summary,
        "tolerance": tolerance,
    }


def candidate_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    dimension = int(snapshot.get("dimension") or 0)
    points = snapshot.get("points") or []
    material_ids = (snapshot.get("cellData") or {}).get("materialIds") or []
    names = _material_names(snapshot)
    stride = max(1, math.ceil(len(points) / MAX_VIEW_POINTS)) if points and dimension == 3 else 1
    sampled_points = [[round(float(value), 9) for value in point[:3]] for point in points[::stride][:MAX_VIEW_POINTS]]
    sampled_materials: list[int] = []
    if dimension == 3 and snapshot.get("triangles"):
        vertex_materials: list[int] = []
        for index, triangle in enumerate(snapshot["triangles"]):
            material = int(material_ids[index]) if index < len(material_ids) else -1
            for vertex in triangle:
                while len(vertex_materials) <= int(vertex):
                    vertex_materials.append(-1)
                vertex_materials[int(vertex)] = material
        sampled_materials = [vertex_materials[index] if index < len(vertex_materials) else -1 for index in range(0, len(points), stride)]
    elif dimension == 2:
        sampled_materials = [int(value) for value in material_ids[:len(sampled_points)]]
    lines = snapshot.get("lines") or []
    triangles = snapshot.get("triangles") or []
    return {
        "dimension": dimension,
        "topology": snapshot.get("topology"),
        "points": sampled_points,
        "lines": lines[:MAX_VIEW_LINES] if dimension == 2 else [],
        "triangles": triangles[:MAX_VIEW_TRIANGLES] if dimension == 3 else [],
        "materialIds": sampled_materials,
        "lineMaterialIds": [int(value) for value in material_ids[:MAX_VIEW_LINES]] if dimension == 2 else [],
        "triangleMaterialIds": [int(value) for value in material_ids[:MAX_VIEW_TRIANGLES]] if dimension == 3 else [],
        "materials": {str(key): value for key, value in (snapshot.get("materials") or {}).items()},
        "materialNames": {str(key): value for key, value in names.items()},
        "pointCount": len(points),
        "lineCount": len(lines),
        "triangleCount": len(triangles),
        "sampledPointCount": len(sampled_points),
        "stride": stride,
        "axisMap": snapshot.get("axisMap") or {"x": 0, "h": 1, "z": 2},
        "coordinateUnit": snapshot.get("coordinateUnit") or "um",
        "bounds": snapshot_summary(snapshot)["bounds"],
    }


def process_window(document: dict[str, Any], lower: float, upper: float) -> dict[str, Any]:
    rows = list((document.get("aggregation") or {}).get("rows") or [])
    classified = []
    for row in rows:
        mean, ci = _number(row.get("mean")), _number(row.get("ci95"))
        margin = None if mean is None else min(mean - lower, upper - mean)
        if mean is None:
            status = "not_evaluated"
        elif ci is not None and (mean + ci < lower or mean - ci > upper):
            status = "fail"
        elif ci is not None and (mean - ci < lower or mean + ci > upper):
            status = "uncertain"
        elif ci is None and (mean < lower or mean > upper):
            status = "fail"
        else:
            status = "pass"
        classified.append({
            "ordinal": row.get("ordinal"), "variantId": row.get("variantId"),
            "parameters": row.get("parameters") or {}, "mean": mean, "ci95": ci,
            "n": row.get("n"), "status": status,
            "margin": margin, "unit": row.get("unit"),
        })
    passing = [row for row in classified if row["status"] == "pass"]
    passing.sort(key=lambda row: row["margin"] if row["margin"] is not None else -math.inf, reverse=True)
    factor_windows = []
    factors = (document.get("design") or {}).get("factors") or []
    if not factors:
        discovered: set[tuple[str, str]] = set()
        for variant in document.get("variants") or []:
            for step_id, values in (variant.get("parameters") or {}).items():
                if isinstance(values, dict):
                    for parameter in values:
                        discovered.add((str(step_id), str(parameter)))
        factors = [{"stepId": step_id, "parameter": parameter} for step_id, parameter in sorted(discovered)]
    for factor in factors:
        values = []
        for row in passing:
            value = ((row.get("parameters") or {}).get(factor["stepId"]) or {}).get(factor["parameter"])
            if _number(value) is not None:
                values.append(float(value))
        factor_windows.append({
            "stepId": factor["stepId"], "parameter": factor["parameter"],
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        })
    return {
        "experimentId": document.get("experimentId"),
        "metricId": document.get("metricId"),
        "unit": document.get("unit"),
        "lower": lower, "upper": upper,
        "summary": {
            "total": len(classified),
            "pass": len(passing),
            "fail": sum(1 for row in classified if row["status"] == "fail"),
            "uncertain": sum(1 for row in classified if row["status"] == "uncertain"),
            "notEvaluated": sum(1 for row in classified if row["status"] == "not_evaluated"),
        },
        "factorWindows": factor_windows,
        "candidates": passing,
        "rows": classified,
        "generatedAt": _now(),
    }


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _svg_for_snapshot(snapshot: dict[str, Any], width: int = 520, height: int = 320) -> str:
    view = candidate_view(snapshot)
    points = view["points"]
    if not points:
        return '<p class="muted">snapshot has no points</p>'
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    if view["dimension"] == 3 and points and len(points[0]) > 2:
        ys = [point[2] for point in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    x_span = x1 - x0 or 1.0
    y_span = y1 - y0 or 1.0
    scale = min((width - 30) / x_span, (height - 30) / y_span)
    def px(x: float) -> float: return 15 + (x - x0) * scale
    def py(y: float) -> float: return height - 15 - (y - y0) * scale
    parts = [f'<svg viewBox="0 0 {width} {height}" class="report-svg" role="img" aria-label="candidate view">']
    if view["dimension"] == 2:
        for index, line in enumerate(view["lines"]):
            try:
                a, b = points[int(line[0])], points[int(line[1])]
            except (IndexError, TypeError, ValueError):
                continue
            material = view["lineMaterialIds"][index] if index < len(view["lineMaterialIds"]) else -1
            color = (view["materials"].get(str(material)) or {}).get("color") or "#7cc7ff"
            parts.append(f'<line x1="{px(a[0]):.3f}" y1="{py(a[1]):.3f}" x2="{px(b[0]):.3f}" y2="{py(b[1]):.3f}" stroke="{_escape(color)}" stroke-width="1.5"/>')
    else:
        for index, point in enumerate(points):
            material = view["materialIds"][index] if index < len(view["materialIds"]) else -1
            color = (view["materials"].get(str(material)) or {}).get("color") or "#7cc7ff"
            parts.append(f'<circle cx="{px(point[0]):.3f}" cy="{py(point[2]):.3f}" r="1.2" fill="{_escape(color)}"/>')
    parts.append("</svg>")
    return "".join(parts)


def build_report(store: Any, request: dict[str, Any]) -> dict[str, Any]:
    title = str(request.get("title") or "Workflow report")
    experiment = None
    if request.get("experimentId"):
        experiment = store.get_experiment(str(request["experimentId"]))
    run_id = request.get("runId")
    step_id = request.get("stepId")
    measurement = None
    provenance = None
    manifest = None
    route = None
    segment = None
    compile = None
    if run_id:
        run_dir = storage.run_path(str(run_id))
        manifest = storage.read_json(run_dir / "manifest.json")
        if not step_id:
            listed = manifest.get("snapshots") or []
            step_id = listed[-1].get("stepId") if listed else None
    if experiment and not run_id:
        for variant in experiment.get("variants") or []:
            replicate = (variant.get("replicates") or [{}])[0]
            if replicate.get("runId"):
                run_id = replicate["runId"]
                step_id = step_id or replicate.get("throughStepId")
                if not step_id and replicate.get("segmentId"):
                    try:
                        step_id = store.get_segment(replicate["segmentId"]).throughStepId
                    except Exception:
                        step_id = None
                measurement = replicate
                break
    if experiment and not step_id:
        for variant in experiment.get("variants") or []:
            replicate = (variant.get("replicates") or [{}])[0]
            if replicate.get("segmentId"):
                try:
                    segment = store.get_segment(replicate["segmentId"])
                    step_id = segment.throughStepId
                    route = store.get_plan(segment.attemptId and store.get_attempt(segment.attemptId).routeId or "")
                except Exception:
                    pass
                break
    if run_id and manifest is None:
        run_dir = storage.run_path(str(run_id))
        manifest = storage.read_json(run_dir / "manifest.json")
        if not step_id:
            listed = manifest.get("snapshots") or []
            step_id = listed[-1].get("stepId") if listed else None
    if experiment and experiment.get("baseCompileId"):
        try:
            compile = store.get_compile(experiment["baseCompileId"])
        except Exception:
            compile = None
    if experiment and measurement and measurement.get("measurementId"):
        try:
            measurement = store.get_measurement(measurement["measurementId"])
        except Exception:
            measurement = None
    if experiment and experiment.get("binding"):
        binding = experiment["binding"]
    elif compile:
        try:
            binding = store.get_binding(compile.compileRecord.geometryBindingId).model_dump(mode="json", exclude_none=True)
        except Exception:
            binding = None
    else:
        binding = None
    sections = []
    sections.append("<h2>流程 / Compile</h2>")
    if compile:
        record = compile.compileRecord.model_dump(mode="json", exclude_none=True)
        sections.append("<table><tr><th>compileId</th><td><code>%s</code></td></tr>" % _escape(record.get("compileId")))
        sections.append("<tr><th>flowId / revision</th><td>%s / <code>%s</code></td></tr>" % (_escape(record.get("flowId")), _escape(record.get("flowRevisionId"))))
        sections.append("<tr><th>flow hash</th><td><code>%s</code></td></tr>" % _escape(record.get("flowRevisionHash")))
        sections.append("<tr><th>variables</th><td><code>%s</code></td></tr>" % _escape(json.dumps(record.get("variableSet") or [], sort_keys=True)))
        sections.append("<tr><th>seed policy</th><td>%s</td></tr>" % _escape(record.get("seedPolicy")))
        sections.append("</table>")
        sections.append("<h3>Steps</h3><table><tr><th>#</th><th>stepId</th><th>authored</th><th>path</th></tr>")
        for index, entry in enumerate(compile.sourceMap):
            sections.append("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (
                index + 1, _escape(entry.stepId), _escape(entry.authoredStepId), _escape(entry.flowPath)))
        sections.append("</table>")
    else:
        sections.append('<p class="muted">No compile envelope selected.</p>')
    if binding:
        source = binding.get("geometrySource") or {}
        sections.append("<h2>几何 / Clip</h2><table>")
        sections.append("<tr><th>binding</th><td><code>%s</code></td></tr>" % _escape(binding.get("bindingId")))
        sections.append("<tr><th>geometry kind</th><td>%s</td></tr>" % _escape((binding.get("geometry") or {}).get("kind")))
        sections.append("<tr><th>source</th><td>%s</td></tr>" % _escape(json.dumps(source, sort_keys=True) if source else "parametric"))
        sections.append("</table>")
    sections.append("<h2>引擎 / 种子</h2>")
    if manifest:
        versions = manifest.get("versions") or {}
        solver = manifest.get("requestedSolver") or {}
        sections.append("<table>")
        sections.append("<tr><th>ViennaPS</th><td>%s</td></tr>" % _escape(versions.get("viennaps")))
        sections.append("<tr><th>ViennaLS</th><td>%s</td></tr>" % _escape(versions.get("viennals")))
        sections.append("<tr><th>requested engine</th><td>%s</td></tr>" % _escape(solver.get("enginePreference")))
        sections.append("<tr><th>actual seed</th><td>%s</td></tr>" % _escape(manifest.get("actualSeed")))
        sections.append("<tr><th>requested input hash</th><td><code>%s</code></td></tr>" % _escape(manifest.get("requestedInputHash")))
        sections.append("<tr><th>execution hash</th><td><code>%s</code></td></tr>" % _escape(manifest.get("executionHash")))
        sections.append("</table>")
        actual = manifest.get("actualExecution") or []
        if actual:
            sections.append("<h3>Step engine selection</h3><table><tr><th>step</th><th>actual engine</th><th>reason</th></tr>")
            for row in actual[:40]:
                sections.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (
                    _escape(row.get("stepId")), _escape(row.get("actualEngine")), _escape(row.get("engineSelectionReason"))))
            sections.append("</table>")
    else:
        sections.append('<p class="muted">No run manifest selected.</p>')
    sections.append("<h2>运行 / 测量</h2>")
    if run_id:
        sections.append("<p>run <code>%s</code> · step <code>%s</code></p>" % (_escape(run_id), _escape(step_id)))
    if measurement:
        sections.append("<table><tr><th>metric</th><td>%s</td></tr>" % _escape(getattr(measurement, "metricId", "")))
        sections.append("<tr><th>method</th><td>%s</td></tr>" % _escape(getattr(measurement, "method", "")))
        sections.append("<tr><th>value</th><td>%s %s</td></tr>" % (_escape(getattr(measurement, "value", None)), _escape(getattr(measurement, "unit", ""))))
        sections.append("<tr><th>binding</th><td>%s</td></tr>" % _escape(getattr(measurement, "bindingStatus", "")))
        sections.append("<tr><th>equivalence</th><td>%s</td></tr>" % _escape(getattr(measurement, "equivalenceStatus", "")))
        sections.append("</table>")
    if experiment:
        sections.append("<h2>实验 / 统计</h2>")
        sections.append("<p>%s · %s · %s conditions</p>" % (
            _escape(experiment.get("name")), _escape(experiment.get("status")),
            _escape((experiment.get("design") or {}).get("conditionCount", len(experiment.get("variants") or [])))))
        aggregation = experiment.get("aggregation") or {}
        rows = aggregation.get("rows") or []
        if rows:
            sections.append("<table><tr><th>#</th><th>n</th><th>mean</th><th>std</th><th>CI95</th><th>status</th></tr>")
            for row in rows:
                sections.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    _escape(row.get("ordinal")), _escape(row.get("n")), _escape(row.get("mean")),
                    _escape(row.get("std")), _escape(row.get("ci95")), _escape(row.get("statisticalStatus"))))
            sections.append("</table>")
            sections.append("<p class='muted'>CI method: %s</p>" % _escape(aggregation.get("ciMethod")))
        if request.get("lower") is not None and request.get("upper") is not None:
            window = process_window(experiment, float(request["lower"]), float(request["upper"]))
            sections.append("<h3>Process window</h3><p>%s</p>" % _escape(json.dumps(window["summary"], sort_keys=True)))
    sections.append("<h2>视图</h2>")
    view_snapshots = []
    if experiment:
        for variant in (experiment.get("variants") or [])[:3]:
            replicate = (variant.get("replicates") or [{}])[0]
            if replicate.get("runId") and step_id:
                try:
                    view_snapshots.append(load_snapshot(replicate["runId"], step_id))
                except Exception:
                    pass
    elif run_id and step_id:
        try:
            view_snapshots.append(load_snapshot(run_id, step_id))
        except Exception:
            pass
    if view_snapshots:
        sections.append('<div class="views">')
        for index, snapshot in enumerate(view_snapshots):
            sections.append("<figure><figcaption>candidate %s</figcaption>%s</figure>" % (index + 1, _svg_for_snapshot(snapshot)))
        sections.append("</div>")
    else:
        sections.append('<p class="muted">No snapshot view is available.</p>')
    conclusions = request.get("conclusions") or ""
    sections.append("<h2>结论</h2><p>%s</p>" % (_escape(conclusions) if conclusions else '<span class="muted">not recorded</span>'))
    body = "".join(sections)
    timestamp = _now()
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{_escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:32px;line-height:1.5;color:#17212b}}h1{{margin-bottom:0}}h2{{margin-top:26px;border-bottom:1px solid #d8dee6;padding-bottom:4px}}table{{border-collapse:collapse;width:100%;margin:8px 0}}th,td{{border:1px solid #d8dee6;padding:5px 8px;text-align:left;font-size:12px}}th{{background:#f4f7fa}}code{{font-size:11px}}.muted{{color:#6b7785}}.views{{display:flex;gap:12px;flex-wrap:wrap}}figure{{margin:0;flex:1 1 300px}}figcaption{{font-size:11px;color:#6b7785}}.report-svg{{width:100%;height:auto;border:1px solid #d8dee6;background:#fbfdff}}@media print{{body{{margin:0}}button{{display:none}}}}
</style></head><body><h1>{_escape(title)}</h1><p class="muted">generated {_escape(timestamp)} · report hash {_escape(hashlib.sha256(body.encode('utf-8')).hexdigest())}</p>
<button onclick="window.print()">打印 / 保存为 PDF</button>{body}</body></html>"""
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    return {"reportId": f"report_{digest[:16]}", "title": title, "html": document, "sha256": digest, "createdAt": timestamp}
