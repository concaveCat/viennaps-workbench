"""Artifact accounting tolerates concurrent atomic manifest replacement."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import directory_bytes
from worker import simulate


class ArtifactBudgetTests(unittest.TestCase):
    def test_temporary_atomic_files_are_excluded_from_storage_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "snapshot.json").write_bytes(b"committed")
            (root / ".manifest.json.random.tmp").write_bytes(b"transient temporary content")
            self.assertEqual(directory_bytes(root), len(b"committed"))

    def test_disappearing_file_during_worker_budget_scan_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            racing_file = root / "snapshot.vtk"
            racing_file.write_bytes(b"complete output")
            original_stat = Path.stat
            calls = 0

            def disappear_after_is_file(path: Path, *args: object, **kwargs: object):
                nonlocal calls
                if path == racing_file:
                    calls += 1
                    if calls == 2:
                        raise FileNotFoundError(path)
                return original_stat(path, *args, **kwargs)

            with patch.object(Path, "stat", disappear_after_is_file):
                simulate.check_artifact_budget(root)
            self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
