import asyncio
import tempfile
import unittest
from pathlib import Path

from orchestra.workspace.local_workspace import LocalWorkspace


class LocalWorkspaceTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = LocalWorkspace(Path(tmp), "session-1")
            asyncio.run(workspace.write("notes.md", "hello"))
            self.assertEqual(asyncio.run(workspace.read("notes.md")), "hello")
            self.assertEqual(asyncio.run(workspace.list_files()), ["notes.md"])

    def test_path_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = LocalWorkspace(Path(tmp), "session-1")
            with self.assertRaises(ValueError):
                asyncio.run(workspace.write("../escape.md", "x"))


if __name__ == "__main__":
    unittest.main()