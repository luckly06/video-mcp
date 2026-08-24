# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import mcp_server as server


class ResolveOutputLocationTests(unittest.TestCase):
    def test_current_artifact_opens_its_task_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            task_dir = root / "14__9bf46b63"
            task_dir.mkdir()
            artifact = task_dir / "14_去重.mp4"
            artifact.write_bytes(b"test")

            with patch.object(server, "_output_dir_for", return_value=root):
                output_dir, target, opened_dir = server._resolve_output_location(
                    "14__9bf46b63/14_去重.mp4",
                    subdir="去重",
                    open_parent=True,
                )

            self.assertEqual(output_dir, root)
            self.assertEqual(target, artifact)
            self.assertEqual(opened_dir, task_dir)

    def test_missing_current_artifact_is_not_silently_replaced_by_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            with patch.object(server, "_output_dir_for", return_value=root):
                with self.assertRaises(FileNotFoundError):
                    server._resolve_output_location(
                        "missing/task.mp4",
                        subdir="去重",
                        open_parent=True,
                    )

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            outside = root.parent / "outside.mp4"
            outside.write_bytes(b"test")
            try:
                with patch.object(server, "_output_dir_for", return_value=root):
                    with self.assertRaises(ValueError):
                        server._resolve_output_location(
                            "../outside.mp4",
                            subdir="去重",
                            open_parent=True,
                        )
            finally:
                if outside.exists():
                    outside.unlink()


if __name__ == "__main__":
    unittest.main()