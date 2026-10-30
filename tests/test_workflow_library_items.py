"""P4-4 reusable library objects: material, process, flow and experiment presets."""

from __future__ import annotations

import sqlite3
import unittest
import uuid

from backend import storage
from backend.main import app
from backend.workflow.library_items import (
    EXPERIMENT_RUNTIME_FIELDS,
    MAX_LIBRARY_ITEM_PAYLOAD_BYTES,
    MAX_LIBRARY_ITEMS_PER_CATEGORY,
    LibraryItemV1,
    create_library_item,
    materialize_process_preset,
    update_library_item,
    validate_library_payload,
)
from backend.workflow.store import (
    SCHEMA_VERSION,
    WorkflowConflict,
    WorkflowNotFound,
    WorkflowStore,
    WorkflowValidation,
)
from tests.test_api_integration import asgi_request, json_body

CATALOG = ["Mask", "Si", "SiO2", "Si3N4", "PolySi", "Air"]

ISOTROPIC_ETCH = {
    "operation": "etch",
    "model": "isotropic",
    "rateMagnitude": 0.1,
    "duration": 1.0,
    "rateMode": "uniform",
}


def material_payload(**overrides) -> dict:
    payload = {
        "catalogName": "Si",
        "displayName": "Silicon substrate",
        "color": "#c79a08",
        "role": "substrate",
        "notes": "verified native material",
    }
    payload.update(overrides)
    return payload


def flow_payload(**overrides) -> dict:
    payload = {
        "name": "stack etch",
        "definitionId": "def_library_stack_etch",
        "steps": ["oxide etch", "nitride etch"],
        "parameters": {"duration": "seconds, resolved at compile time"},
        "notes": "reference only; the definition stays authoritative",
    }
    payload.update(overrides)
    return payload


def experiment_payload(**overrides) -> dict:
    payload = {
        "recipeId": "recipe_library_depth",
        "recipeVersion": 2,
        "design": {"kind": "doe", "factors": [{"name": "duration", "levels": [0.5, 1.0]}]},
        "variants": [{"label": "A", "parameters": {"duration": 0.5}}],
        "notes": "re-plannable template",
    }
    payload.update(overrides)
    return payload


class LibraryPayloadValidationTests(unittest.TestCase):
    def test_material_preset_references_the_installed_catalog(self) -> None:
        payload = validate_library_payload("material", material_payload(), catalog_names=CATALOG)
        self.assertEqual(payload["catalogName"], "Si")

    def test_material_preset_rejects_unknown_catalog_name(self) -> None:
        with self.assertRaises(WorkflowValidation) as caught:
            validate_library_payload(
                "material", material_payload(catalogName="Unobtainium"), catalog_names=CATALOG
            )
        self.assertIn("not in the installed material catalog", str(caught.exception))

    def test_material_preset_uses_the_real_catalog_by_default(self) -> None:
        # The shipped catalog contains Si; the default path must not need an
        # injected list, because the API validates against GET /api/materials.
        payload = validate_library_payload("material", {"catalogName": "Si"})
        self.assertEqual(payload, {"catalogName": "Si"})

    def test_material_preset_rejects_engine_fields_and_bad_metadata(self) -> None:
        with self.assertRaises(WorkflowValidation):
            validate_library_payload(
                "material", material_payload(densityGcm3=2.33), catalog_names=CATALOG
            )
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("material", material_payload(color="blue"), catalog_names=CATALOG)
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("material", {"displayName": "no reference"}, catalog_names=CATALOG)

    def test_process_preset_accepts_a_valid_step_without_id(self) -> None:
        payload = validate_library_payload("process", ISOTROPIC_ETCH, item_name="isotropic etch")
        self.assertNotIn("id", payload)
        self.assertEqual(payload["model"], "isotropic")

    def test_process_preset_accepts_any_legal_authored_id(self) -> None:
        payload = validate_library_payload(
            "process", {**ISOTROPIC_ETCH, "id": "step_author_01"}, item_name="etch"
        )
        self.assertEqual(payload["id"], "step_author_01")

    def test_process_preset_rejects_invalid_step_fields(self) -> None:
        cases = (
            {**ISOTROPIC_ETCH, "duration": 0.0},  # below the engine floor
            {**ISOTROPIC_ETCH, "bogusField": 1},  # unknown field
            {**ISOTROPIC_ETCH, "model": "notAModel"},  # not a StepV7 model
            {"operation": "etch", "model": "isotropic"},  # missing required fields
            {"model": "isotropic", "rateMagnitude": 0.1, "duration": 1.0},  # no operation
            {"operation": "etch"},  # no model
            {**ISOTROPIC_ETCH, "id": "bad id"},  # invalid authored id
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(WorkflowValidation):
                    validate_library_payload("process", case, item_name="etch")

    def test_process_preset_inserts_under_a_fresh_authored_id(self) -> None:
        preset = validate_library_payload("process", ISOTROPIC_ETCH, item_name="isotropic etch")
        first = materialize_process_preset(preset, item_name="isotropic etch")
        second = materialize_process_preset(preset, item_name="isotropic etch")
        self.assertRegex(first["id"], r"^[A-Za-z0-9_-]{8,64}$")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["operation"], "etch")
        self.assertEqual(first["model"], "isotropic")
        # The preset itself is never mutated by insertion.
        self.assertNotIn("id", preset)
        explicit = materialize_process_preset(preset, step_id="step_authored_99")
        self.assertEqual(explicit["id"], "step_authored_99")

    def test_flow_preset_requires_a_name_and_a_reference(self) -> None:
        payload = validate_library_payload("flow", flow_payload())
        self.assertEqual(payload["definitionId"], "def_library_stack_etch")
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("flow", flow_payload(name=""))
        with self.assertRaises(WorkflowValidation):
            validate_library_payload(
                "flow", {"name": "dangling", "notes": "no reference or summary"}
            )
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("flow", flow_payload(definitionId="bad id"))
        # A template reference or a steps summary alone is enough.
        validate_library_payload("flow", {"name": "tpl only", "templateId": "tpl_library_01"})
        validate_library_payload("flow", {"name": "steps only", "steps": ["etch"]})
        # Internal flow JSON must not be copied into the form.
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("flow", flow_payload(definition={"nodes": []}))

    def test_experiment_preset_requires_recipe_and_design(self) -> None:
        payload = validate_library_payload("experiment", experiment_payload())
        self.assertEqual(payload["recipeVersion"], 2)
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("experiment", experiment_payload(recipeVersion=0))
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("experiment", experiment_payload(recipeId=""))
        with self.assertRaises(WorkflowValidation):
            validate_library_payload(
                "experiment", {"recipeId": "recipe_library_depth", "recipeVersion": 1}
            )

    def test_experiment_preset_rejects_run_state(self) -> None:
        for key in ("runId", "measurementId", "status"):
            with self.subTest(key=key):
                with self.assertRaises(WorkflowValidation) as caught:
                    validate_library_payload(
                        "experiment", experiment_payload(**{key: "x"})
                    )
                self.assertIn(key, str(caught.exception))
        # Nested run state inside a variant summary is rejected as well.
        with self.assertRaises(WorkflowValidation):
            validate_library_payload(
                "experiment",
                experiment_payload(variants=[{"label": "A", "status": "completed"}]),
            )
        self.assertTrue({"runId", "measurementId", "status"} <= EXPERIMENT_RUNTIME_FIELDS)

    def test_payload_size_and_item_limits_are_enforced(self) -> None:
        huge = {**flow_payload(), "notes": "x" * (MAX_LIBRARY_ITEM_PAYLOAD_BYTES + 1)}
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("flow", huge)
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("process", "not-an-object")
        item = create_library_item(
            category="material", name="Si", payload=material_payload(), catalog_names=CATALOG
        )
        with self.assertRaises(ValueError):
            LibraryItemV1(
                itemId=item.itemId,
                category=item.category,
                name=item.name,
                payload={"catalogName": "Si", "notes": "x" * (MAX_LIBRARY_ITEM_PAYLOAD_BYTES + 1)},
                createdAt=item.createdAt,
                updatedAt=item.updatedAt,
            )
        self.assertEqual(MAX_LIBRARY_ITEMS_PER_CATEGORY, 500)


class LibraryItemStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.root = storage.VAR / f"test-library-items-{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = WorkflowStore(self.root / "workflow.sqlite3")
        self.store.initialize()

    def _item(self, category: str = "material", name: str = "Silicon", **overrides):
        payload = {
            "material": material_payload,
            "process": lambda: dict(ISOTROPIC_ETCH),
            "flow": flow_payload,
            "experiment": experiment_payload,
        }[category]()
        payload.update(overrides)
        return create_library_item(
            category=category,
            name=name,
            description="local preset",
            payload=payload,
            catalog_names=CATALOG,
        )

    def test_crud_round_trip_and_category_filter(self) -> None:
        material = self._item("material")
        process = self._item("process", name="isotropic etch")
        self.store.save_library_item(material.model_dump(mode="json"))
        self.store.save_library_item(process.model_dump(mode="json"))

        stored = self.store.get_library_item(material.itemId)
        self.assertEqual(stored["payload"], material.payload)
        self.assertEqual(stored["description"], "local preset")
        self.assertEqual(stored["createdAt"], material.createdAt)

        everything = self.store.list_library_items()
        self.assertEqual({row["itemId"] for row in everything}, {material.itemId, process.itemId})
        materials = self.store.list_library_items("material")
        self.assertEqual([row["itemId"] for row in materials], [material.itemId])
        self.assertEqual(self.store.list_library_items("experiment"), [])
        self.assertEqual(self.store.count_library_items("material"), 1)

    def test_repeated_item_id_is_idempotent_only_for_identical_content(self) -> None:
        item = self._item()
        first = self.store.save_library_item(item.model_dump(mode="json"))
        again = self.store.save_library_item(item.model_dump(mode="json"))
        self.assertEqual(first, again)
        self.assertEqual(self.store.count_library_items("material"), 1)

        changed = item.model_copy(update={"description": "different"})
        with self.assertRaises(WorkflowConflict):
            self.store.save_library_item(changed.model_dump(mode="json"))
        # The conflict never overwrites the stored row.
        self.assertEqual(self.store.get_library_item(item.itemId)["description"], "local preset")

    def test_update_preserves_identity_and_delete_is_404_afterwards(self) -> None:
        item = self._item()
        self.store.save_library_item(item.model_dump(mode="json"))
        updated = update_library_item(
            item, name="Silicon (renamed)", description="edited", payload=material_payload(role="mask"),
            catalog_names=CATALOG,
        )
        stored = self.store.update_library_item(updated.model_dump(mode="json"))
        self.assertEqual(stored["itemId"], item.itemId)
        self.assertEqual(stored["category"], "material")
        self.assertEqual(stored["name"], "Silicon (renamed)")
        self.assertEqual(stored["payload"]["role"], "mask")
        self.assertEqual(stored["createdAt"], item.createdAt)

        # Re-sending identical content is a no-op, not a new edit timestamp.
        repeated = self.store.update_library_item(updated.model_dump(mode="json"))
        self.assertEqual(repeated["updatedAt"], stored["updatedAt"])

        self.store.delete_library_item(item.itemId)
        with self.assertRaises(WorkflowNotFound):
            self.store.get_library_item(item.itemId)
        with self.assertRaises(WorkflowNotFound):
            self.store.delete_library_item(item.itemId)
        with self.assertRaises(WorkflowNotFound):
            self.store.update_library_item(updated.model_dump(mode="json"))

    def test_category_is_immutable_on_update(self) -> None:
        item = self._item()
        self.store.save_library_item(item.model_dump(mode="json"))
        moved = item.model_copy(update={"category": "flow"})
        with self.assertRaises(WorkflowConflict):
            self.store.update_library_item(moved.model_dump(mode="json"))

    def test_per_category_limit_is_enforced_inside_the_write(self) -> None:
        first = self._item(name="one")
        second = self._item(name="two")
        self.store.save_library_item(first.model_dump(mode="json"), max_per_category=2)
        self.store.save_library_item(second.model_dump(mode="json"), max_per_category=2)
        third = self._item(name="three")
        with self.assertRaises(WorkflowValidation):
            self.store.save_library_item(third.model_dump(mode="json"), max_per_category=2)
        # Other categories are unaffected by one category being full.
        self.store.save_library_item(
            self._item("flow", name="flow one").model_dump(mode="json"), max_per_category=2
        )

    def test_stored_item_survives_a_catalog_change(self) -> None:
        # Category validation is a write-path rule. A material retired from the
        # catalog must not make an existing library entry vanish on read.
        item = self._item()
        self.store.save_library_item(item.model_dump(mode="json"))
        self.assertEqual(self.store.get_library_item(item.itemId)["payload"]["catalogName"], "Si")
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("material", {"catalogName": "Si"}, catalog_names=["SiO2"])
        with self.assertRaises(WorkflowValidation):
            validate_library_payload("not-a-category", {"catalogName": "Si"}, catalog_names=CATALOG)

    def test_schema_9_migration_adds_the_library_items_table(self) -> None:
        path = self.root / "migrate.sqlite3"
        legacy = WorkflowStore(path)
        legacy.initialize()
        # Simulate a database written before schema 9: no table, version 8.
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE library_items")
            connection.execute("UPDATE schema_meta SET value = '8' WHERE key = 'schema_version'")
        self.assertNotEqual(SCHEMA_VERSION, "8")

        upgraded = WorkflowStore(path)
        upgraded.initialize()
        self.assertEqual(upgraded.schema_version(), SCHEMA_VERSION)
        with sqlite3.connect(path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertIn("library_items", tables)

        item = self._item()
        upgraded.save_library_item(item.model_dump(mode="json"))
        self.assertEqual(upgraded.get_library_item(item.itemId)["name"], item.name)

    def test_older_schema_versions_still_upgrade(self) -> None:
        for version in ("6", "7", "8"):
            with self.subTest(version=version):
                path = self.root / f"migrate-{version}.sqlite3"
                WorkflowStore(path).initialize()
                with sqlite3.connect(path) as connection:
                    connection.execute("DROP TABLE library_items")
                    connection.execute(
                        "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (version,)
                    )
                upgraded = WorkflowStore(path)
                upgraded.initialize()
                self.assertEqual(upgraded.schema_version(), SCHEMA_VERSION)
                self.assertEqual(upgraded.list_library_items(), [])


class LibraryItemApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.suffix = uuid.uuid4().hex[:8]

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    async def post(self, path: str, payload: dict):
        return await asgi_request(path, method="POST", payload=payload)

    async def get(self, path: str):
        return await asgi_request(path, method="GET")

    async def put(self, path: str, payload: dict):
        return await asgi_request(path, method="PUT", payload=payload)

    async def delete(self, path: str):
        return await asgi_request(path, method="DELETE")

    async def test_item_crud_and_category_filter(self) -> None:
        created = await self.post(
            "/api/v1/workflow/library/items",
            {
                "category": "material",
                "name": f"Silicon {self.suffix}",
                "description": "native reference",
                "payload": material_payload(),
            },
        )
        self.assertEqual(created[0], 201, created[2])
        item = json_body(created)
        self.assertRegex(item["itemId"], r"^item_[a-f0-9]{24}$")
        self.assertEqual(item["schemaVersion"], 1)
        self.assertEqual(item["payload"]["catalogName"], "Si")
        item_id = item["itemId"]

        fetched = await self.get(f"/api/v1/workflow/library/items/{item_id}")
        self.assertEqual(fetched[0], 200, fetched[2])
        self.assertEqual(json_body(fetched), item)

        listed = await self.get("/api/v1/workflow/library/items?category=material")
        self.assertEqual(listed[0], 200, listed[2])
        self.assertIn(item_id, {row["itemId"] for row in json_body(listed)["items"]})
        processes = await self.get("/api/v1/workflow/library/items?category=process")
        self.assertEqual(processes[0], 200, processes[2])
        self.assertNotIn(item_id, {row["itemId"] for row in json_body(processes)["items"]})

        updated = await self.put(
            f"/api/v1/workflow/library/items/{item_id}",
            {
                "name": f"Silicon mask {self.suffix}",
                "description": "edited",
                "payload": material_payload(role="mask", color="#333333"),
            },
        )
        self.assertEqual(updated[0], 200, updated[2])
        self.assertEqual(json_body(updated)["name"], f"Silicon mask {self.suffix}")
        self.assertEqual(json_body(updated)["payload"]["role"], "mask")
        self.assertEqual(json_body(updated)["createdAt"], item["createdAt"])

        deleted = await self.delete(f"/api/v1/workflow/library/items/{item_id}")
        self.assertEqual(deleted[0], 204, deleted[2])
        missing = await self.get(f"/api/v1/workflow/library/items/{item_id}")
        self.assertEqual(missing[0], 404, missing[2])
        self.assertEqual((await self.delete(f"/api/v1/workflow/library/items/{item_id}"))[0], 404)

    async def test_invalid_payloads_and_categories_return_422(self) -> None:
        unknown_material = await self.post(
            "/api/v1/workflow/library/items",
            {"category": "material", "name": "bogus", "payload": {"catalogName": "Unobtainium"}},
        )
        self.assertEqual(unknown_material[0], 422, unknown_material[2])
        bad_step = await self.post(
            "/api/v1/workflow/library/items",
            {"category": "process", "name": "bad", "payload": {**ISOTROPIC_ETCH, "duration": 0.0}},
        )
        self.assertEqual(bad_step[0], 422, bad_step[2])
        run_state = await self.post(
            "/api/v1/workflow/library/items",
            {
                "category": "experiment",
                "name": "run",
                "payload": experiment_payload(status="completed"),
            },
        )
        self.assertEqual(run_state[0], 422, run_state[2])
        bad_category = await self.get("/api/v1/workflow/library/items?category=unknown")
        self.assertEqual(bad_category[0], 422, bad_category[2])
        missing = await self.get("/api/v1/workflow/library/items/item_" + "0" * 24)
        self.assertEqual(missing[0], 404, missing[2])
        missing_put = await self.put(
            "/api/v1/workflow/library/items/item_" + "0" * 24,
            {"name": "nothing", "payload": {"catalogName": "Si"}},
        )
        self.assertEqual(missing_put[0], 404, missing_put[2])

    async def test_process_preset_round_trips_and_inserts(self) -> None:
        created = await self.post(
            "/api/v1/workflow/library/items",
            {
                "category": "process",
                "name": f"isotropic etch {self.suffix}",
                "payload": {**ISOTROPIC_ETCH, "id": "step_author_01"},
            },
        )
        self.assertEqual(created[0], 201, created[2])
        payload = json_body(created)["payload"]
        self.assertEqual(payload["id"], "step_author_01")
        inserted = materialize_process_preset(payload, item_name="isotropic etch")
        self.assertNotEqual(inserted["id"], payload["id"])

    def test_openapi_exposes_typed_library_item_endpoints(self) -> None:
        schema = app.openapi()
        paths = schema["paths"]
        for path, methods in (
            ("/api/v1/workflow/library/items", {"get", "post"}),
            ("/api/v1/workflow/library/items/{item_id}", {"get", "put", "delete"}),
        ):
            self.assertIn(path, paths)
            self.assertTrue(methods <= set(paths[path]))
        components = schema["components"]["schemas"]
        for name in (
            "LibraryItemV1",
            "LibraryItemCreateRequest",
            "LibraryItemUpdateRequest",
            "LibraryItemListResponse",
        ):
            self.assertIn(name, components)
        self.assertIn(
            "LibraryItemV1",
            components["LibraryItemListResponse"]["properties"]["items"]["items"]["$ref"],
        )
        create_schema = components["LibraryItemCreateRequest"]
        self.assertFalse(create_schema.get("additionalProperties", False))
        self.assertEqual(
            set(create_schema["required"]), {"category", "name", "payload"}
        )
        # Deleting returns no document.
        self.assertNotIn("200", paths["/api/v1/workflow/library/items/{item_id}"]["delete"]["responses"])
        self.assertIn("204", paths["/api/v1/workflow/library/items/{item_id}"]["delete"]["responses"])


if __name__ == "__main__":
    unittest.main()
