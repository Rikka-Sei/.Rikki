#!/usr/bin/env python3
"""corespec import 驱动语法测试。不复制到 spec 目录。"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import spec_barrier as barrier  # noqa: E402


class CorespecImportTest(unittest.TestCase):
    def write_spec(self, root: pathlib.Path, name: str, body: str) -> None:
        spec_dir = root / "docs" / "specs" / name
        spec_dir.mkdir(parents=True)
        (spec_dir / "spec.md").write_text(body, encoding="utf-8", newline="\n")

    def test_rewrite_synthetic_spec_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="corespec-import-") as tmp:
            root = pathlib.Path(tmp)
            self.write_spec(root, "synthetic-alpha",
                            "<!-- SEMANTICS:sample BEGIN -->\n```haskell\n"
                            "module Semantics where\ninitialState = 1\n"
                            "```\n<!-- SEMANTICS:sample END -->\n")
            raw = (
                "module Semantics where\n"
                "corespec import synthetic-alpha sample as ModelA\n"
                "linked = ModelA.initialState\n"
            )
            with mock.patch.object(barrier, "REPO_ROOT", root):
                rewritten, modules = barrier.resolve_corespec_imports(
                    raw, stack=("fixture/root",))
        self.assertIn("import qualified ModelA", rewritten)
        self.assertNotIn("corespec import", rewritten)
        self.assertEqual(modules[0][0], "ModelA")
        self.assertIn("module ModelA", modules[0][1])
        self.assertNotIn("corespec import", modules[0][1])

    def test_cycle_includes_current_block(self) -> None:
        raw = "corespec import synthetic-alpha machine-a as ModelA\n"
        with self.assertRaises(barrier.CorespecImportError) as caught:
            barrier.resolve_corespec_imports(raw, stack=("synthetic-alpha/machine-a",))
        self.assertIn("成环", str(caught.exception))


    def test_missing_semantics_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="corespec-import-") as tmp:
            root = pathlib.Path(tmp)
            self.write_spec(root, "synthetic-alpha", "# No matching semantics block\n")
            raw = "corespec import synthetic-alpha missing as ModelA\n"
            with mock.patch.object(barrier, "REPO_ROOT", root):
                with self.assertRaises(barrier.CorespecImportError) as caught:
                    barrier.resolve_corespec_imports(raw, stack=("fixture/root",))
        self.assertIn("没有 SEMANTICS:missing", str(caught.exception))

    def test_ghc_accepts_rewritten_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="corespec-import-") as tmp:
            root = pathlib.Path(tmp)
            self.write_spec(root, "synthetic-alpha",
                            "<!-- SEMANTICS:sample BEGIN -->\n```haskell\n"
                            "module Semantics where\ninitialState = 1\n"
                            "```\n<!-- SEMANTICS:sample END -->\n")
            raw = (
                "module Semantics where\n"
                "corespec import synthetic-alpha sample as ModelA\n"
                "linked = ModelA.initialState\n"
            )
            with mock.patch.object(barrier, "REPO_ROOT", root):
                rewritten, modules = barrier.resolve_corespec_imports(
                    raw, stack=("fixture/root",))
            tmp_path = root / "ghc"
            tmp_path.mkdir()
            (tmp_path / "Semantics.hs").write_text(rewritten, encoding="utf-8", newline="\n")
            for alias, code in modules:
                (tmp_path / f"{alias}.hs").write_text(code, encoding="utf-8", newline="\n")
            completed = subprocess.run(
                ["ghc", "-Wall", "-fno-code", "-i" + str(tmp_path), str(tmp_path / "Semantics.hs")],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
