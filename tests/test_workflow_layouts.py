"""P2 layout extraction, clip assets and layout-to-domain compiles."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend import storage  # noqa: E402
from backend.main import app  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_workflow_adoption import project  # noqa: E402


class LayoutClipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        source = Path(__file__).resolve().parents[1] / "docs" / "stage5-evidence" / "two-structure-two-layer.gds"
        uploaded = await asgi_request(
            "/api/gds-assets?file_name=two-structure-two-layer.gds",
            method="POST", payload=source.read_bytes(),
            headers=[(b"content-type", b"application/octet-stream")],
        )
        self.assertEqual(uploaded[0], 201, uploaded[2])
        self.asset_id = json_body(uploaded)["assetId"]
        registered = await asgi_request(
            "/api/v1/workflow/layouts", method="POST",
            payload={"assetId": self.asset_id, "fileName": "two-structure-two-layer.gds"},
        )
        self.assertEqual(registered[0], 201, registered[2])
        self.layout = json_body(registered)

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def _clip(self, name: str, structure: str, layer: int, bbox: dict, annotations=None) -> dict:
        response = await asgi_request(
            "/api/v1/workflow/clips", method="POST",
            payload={
                "layoutId": self.layout["layoutId"], "name": name, "structure": structure,
                "layers": [{"layer": layer, "material": "Si"}], "bbox": bbox,
                "annotations": annotations or [],
            },
        )
        self.assertEqual(response[0], 201, response[2])
        return json_body(response)

    async def test_hierarchical_reference_flattens_into_layout_bounds(self):
        source = Path(__file__).resolve().parents[1] / "docs" / "stage5-evidence" / "hierarchical-reference.gds"
        uploaded = await asgi_request(
            "/api/gds-assets?file_name=hierarchical-reference.gds",
            method="POST", payload=source.read_bytes(),
            headers=[(b"content-type", b"application/octet-stream")],
        )
        self.assertEqual(uploaded[0], 201, uploaded[2])
        asset_id = json_body(uploaded)["assetId"]
        registered = await asgi_request(
            "/api/v1/workflow/layouts", method="POST",
            payload={"assetId": asset_id, "fileName": "hierarchical-reference.gds"},
        )
        self.assertEqual(registered[0], 201, registered[2])
        layout = json_body(registered)
        top = next(item for item in layout["structures"] if item["name"] == "TOP")
        self.assertEqual(top["bounds"], {"x0": 3.0, "y0": 0.0, "x1": 5.0, "y1": 1.0})
        preview = await asgi_request(
            f"/api/v1/workflow/layouts/{layout['layoutId']}/preview", method="POST",
            payload={
                "structure": "TOP", "layers": [{"layer": 3, "material": "Si"}],
                "bbox": {"x0": 2.0, "y0": -1.0, "x1": 6.0, "y1": 2.0},
            },
        )
        self.assertEqual(preview[0], 200, preview[2])
        body = json_body(preview)
        self.assertEqual(body["polygonCount"], 1)
        self.assertEqual(body["contentBounds"], {"x0": 1.0, "y0": 1.0, "x1": 3.0, "y1": 2.0})

    async def test_extract_three_clips_and_compile_same_flow_three_times(self):
        preview = await asgi_request(
            f"/api/v1/workflow/layouts/{self.layout['layoutId']}/preview", method="POST",
            payload={
                "structure": "CELL_A",
                "layers": [{"layer": 1, "material": "Si"}],
                "bbox": {"x0": -0.1, "y0": -0.1, "x1": 2.1, "y1": 1.1},
            },
        )
        self.assertEqual(preview[0], 200, preview[2])
        preview_body = json_body(preview)
        self.assertEqual(preview_body["polygonCount"], 1)
        self.assertEqual(preview_body["polygons"][0]["layer"], 1)

        clip_a = await self._clip(
            "clip A", "CELL_A", 1,
            {"x0": -0.1, "y0": -0.1, "x1": 2.1, "y1": 1.1},
        )
        clip_b = await self._clip(
            "clip B", "CELL_B", 2,
            {"x0": 2.9, "y0": -0.1, "x1": 5.1, "y1": 2.1},
        )
        clip_c = await self._clip(
            "clip C", "CELL_A", 1,
            {"x0": 0.5, "y0": 0.2, "x1": 1.5, "y1": 0.8},
            annotations=[{"text": "center", "x": 0.5, "y": 0.3}],
        )
        for clip in (clip_a, clip_b, clip_c):
            self.assertRegex(clip["gdsAssetId"], r"^[a-f0-9]{64}$")
            self.assertRegex(clip["clipHash"], r"^[a-f0-9]{64}$")
            self.assertGreaterEqual(clip["polygonCount"], 1)
            self.assertTrue(storage.resolve_gds_asset(clip["gdsAssetId"]).is_file())

        annotated = await asgi_request(
            f"/api/v1/workflow/clips/{clip_c['clipId']}/annotations", method="POST",
            payload={"text": "edge", "x": 1.0, "y": 0.1},
        )
        self.assertEqual(annotated[0], 201, annotated[2])
        clip_c = json_body(annotated)
        self.assertEqual(len(clip_c["annotations"]), 2)

        compiled = []
        flow_id = "flow_clip_p2"
        for clip in (clip_a, clip_b, clip_c):
            response = await asgi_request(
                f"/api/v1/workflow/clips/{clip['clipId']}/compile", method="POST",
                payload={"project": project(), "flowId": flow_id},
            )
            self.assertEqual(response[0], 201, response[2])
            body = json_body(response)
            self.assertEqual(body["dimension"], 2)
            self.assertEqual(body["binding"]["geometrySource"]["clipId"], clip["clipId"])
            self.assertEqual(body["binding"]["geometrySource"]["clipHash"], clip["clipHash"])
            self.assertRegex(body["binding"]["bindingId"], r"^bind_[a-f0-9]{16}$")
            compiled.append(body)

        hashes = {body["compile"]["compileRecord"]["geometryBindingHash"] for body in compiled}
        self.assertEqual(len(hashes), 3)
        self.assertEqual(len({body["binding"]["bindingId"] for body in compiled}), 3)
        step_ids = {body["compile"]["project"]["steps"][0]["id"] for body in compiled}
        self.assertEqual(len(step_ids), 1, "same flow must keep stable compiled step ids across clips")
        authored = {body["compile"]["sourceMap"][0]["authoredStepId"] for body in compiled}
        self.assertEqual(authored, {"step_etch_01"})
        self.assertTrue(all(body["compile"]["project"]["geometry"]["kind"] == "gds" for body in compiled))

        listing = await asgi_request(f"/api/v1/workflow/clips?layout_id={self.layout['layoutId']}")
        self.assertEqual(listing[0], 200, listing[2])
        self.assertEqual(len(json_body(listing)["clips"]), 3)


if __name__ == "__main__":
    unittest.main()
