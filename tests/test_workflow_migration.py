"""Compatibility migration for pre-solverDigest v2 workflow databases."""

from __future__ import annotations

import json
import sqlite3
import unittest
import uuid

from backend import storage
from backend.workflow.store import SCHEMA_VERSION, WorkflowStore
from tests.workflow_fixtures import compiled_base, compiled_branch, plan_from_compiled


def downgrade_to_v2(path, *, drop_compiles: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        for table, column in (
            ("attempts", "prefix_compatibility_json"),
            ("reuse", "compatibility_json"),
        ):
            rows = connection.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
            for rowid, raw in rows:
                if raw is None:
                    continue
                payload = json.loads(raw)
                payload.pop("solverDigest", None)
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (json.dumps(payload, sort_keys=True, separators=(",", ":")), rowid),
                )
        for table in ("attempts", "reuse"):
            try:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN legacy_compatibility_json")
            except sqlite3.OperationalError:
                pass
        try:
            connection.execute("ALTER TABLE routes DROP COLUMN legacy_unverified")
        except sqlite3.OperationalError:
            pass
        if drop_compiles:
            connection.execute("DELETE FROM compiles")
        connection.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")


class WorkflowMigrationTests(unittest.TestCase):
    def _store_with_plan(self) -> tuple[WorkflowStore, str, str]:
        suffix = uuid.uuid4().hex[:6]
        base = compiled_base()
        branch = compiled_branch()
        plan = plan_from_compiled(base, branch, suffix=suffix)
        path = storage.VAR / f"test-workflow-migrate-{suffix}.sqlite3"
        store = WorkflowStore(path)
        store.initialize()
        store.register_compile(base)
        store.register_compile(branch)
        store.create_plan(plan)
        return store, path, plan.route.routeId

    def test_v2_route_is_repaired_from_saved_compiles(self):
        store, path, route_id = self._store_with_plan()
        downgrade_to_v2(path)
        migrated = WorkflowStore(path)
        migrated.initialize()
        self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
        plan = migrated.get_plan(route_id)
        self.assertFalse(plan.route.legacyUnverified)
        self.assertIsNotNone(plan.attempts[1].prefixCompatibility)
        self.assertNotEqual(plan.attempts[1].prefixCompatibility.solverDigest, "0" * 64)
        self.assertEqual(
            plan.attempts[1].prefixCompatibility.solverDigest,
            plan.reuse[0].compatibility.solverDigest,
        )
        self.assertIsNone(plan.attempts[1].legacyCompatibility)

    def test_v2_route_without_compiles_loads_as_unverified(self):
        store, path, route_id = self._store_with_plan()
        downgrade_to_v2(path, drop_compiles=True)
        migrated = WorkflowStore(path)
        migrated.initialize()
        plan = migrated.get_plan(route_id)
        self.assertTrue(plan.route.legacyUnverified)
        self.assertIsNotNone(plan.attempts[1].prefixCompatibility)
        self.assertEqual(plan.attempts[1].prefixCompatibility.solverDigest, "0" * 64)
        self.assertIsNotNone(plan.attempts[1].legacyCompatibility)
        self.assertIsNotNone(plan.reuse[0].legacyCompatibility)


if __name__ == "__main__":
    unittest.main()
