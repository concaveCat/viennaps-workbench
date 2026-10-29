"""SQLite schema version management for the workflow store."""

from __future__ import annotations

import sqlite3
import unittest
import uuid

from backend import storage
from backend.workflow.store import SCHEMA_VERSION, WorkflowStore


class WorkflowStoreSchemaTests(unittest.TestCase):
    def test_fresh_store_has_explicit_schema_version(self):
        store = WorkflowStore(storage.VAR / f"test-workflow-fresh-{uuid.uuid4().hex}.sqlite3")
        store.initialize()
        self.assertEqual(store.schema_version(), SCHEMA_VERSION)

    def test_version_one_store_is_migrated_to_current(self):
        path = storage.VAR / f"test-workflow-v1-{uuid.uuid4().hex}.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1')")
        store = WorkflowStore(path)
        store.initialize()
        self.assertEqual(store.schema_version(), SCHEMA_VERSION)

    def test_unknown_schema_version_is_rejected(self):
        path = storage.VAR / f"test-workflow-bad-{uuid.uuid4().hex}.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '99')")
        store = WorkflowStore(path)
        with self.assertRaises(RuntimeError):
            store.initialize()


if __name__ == "__main__":
    unittest.main()
