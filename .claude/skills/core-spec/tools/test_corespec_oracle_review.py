#!/usr/bin/env python3
"""ORACLE review hash and CLI behavior tests."""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import spec_review as review  # noqa: E402

F = "```"


def fixture_spec(oracle_code="module Oracle where\nx = 1\n",
                 errmap="raw none = ErrNone\n") -> str:
    return (
        "# fixture\n\n"
        "<!-- SEMANTICS:m BEGIN -->\n" + F + "haskell\nmodule Semantics where\nv = 1\n" + F + "\n\n"
        + F + "LABEL:m\nstate S = 起点\n" + F + "\n\n"
        + "<!-- ORACLE:m BEGIN -->\n" + F + "haskell\n" + oracle_code + F + "\n"
        + "<!-- ORACLE:m END -->\n\n"
        + F + "ORACLE-LABEL:m\nstate S = 起点\n" + F + "\n\n"
        + F + "ORACLE-ERRMAP:m\n" + errmap + F + "\n"
    )


class OracleReviewHashTest(unittest.TestCase):
    def test_oracle_generation_hashes_oracle_fences(self) -> None:
        base = fixture_spec()
        h = review.machine_hash(base, "m", generation="Oracle")
        self.assertRegex(h, r"^[0-9a-f]{16}$")
        # 改 ORACLE 代码 → 哈希变
        self.assertNotEqual(
            h, review.machine_hash(fixture_spec(oracle_code="module Oracle where\nx = 2\n"),
                                   "m", generation="Oracle"))
        # 改 ORACLE-ERRMAP → 哈希变（原始错误身份是契约证据）
        self.assertNotEqual(
            h, review.machine_hash(fixture_spec(errmap="raw none = ErrNone\nraw z = ErrZ\n"),
                                   "m", generation="Oracle"))

    def test_oracle_hash_ignores_legacy_fences(self) -> None:
        base = fixture_spec()
        h = review.machine_hash(base, "m", generation="Oracle")
        # 只改 SEMANTICS / LABEL → ORACLE 哈希不得变（否则旧代一改就误判 ORACLE 留档过期）
        legacy_changed = base.replace("module Semantics where\nv = 1",
                                      "module Semantics where\nv = 999")
        legacy_changed = legacy_changed.replace(F + "LABEL:m\nstate S = 起点\n",
                                                F + "LABEL:m\nstate S = 新名\n")
        self.assertNotEqual(legacy_changed, base)
        self.assertEqual(h, review.machine_hash(legacy_changed, "m", generation="Oracle"))
        # 旧代缺省哈希仍走 SEMANTICS/LABEL，与 ORACLE 哈希不同
        self.assertNotEqual(h, review.machine_hash(base, "m"))


class OracleReviewCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_root = tempfile.TemporaryDirectory(prefix="oracle-review-")
        self.root = pathlib.Path(self.temporary_root.name)
        self.addCleanup(self.temporary_root.cleanup)
        root_patch = mock.patch.object(review, "REPO_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.spec_dir = self.root / "fixture-spec"
        self.spec_dir.mkdir()
        (self.spec_dir / "spec.md").write_text(fixture_spec(), encoding="utf-8", newline="\n")

    def run_review(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                rc = review.main(argv)
            except SystemExit as exc:      # 参数解析器与声明的 CLI 调用不一致
                self.fail(f"spec_review 拒绝参数 {argv}：SystemExit({exc.code})")
        return rc, out.getvalue()

    def test_oracle_check_without_record_fails(self) -> None:
        rc, out = self.run_review([str(self.spec_dir), "--check", "--oracle"])
        self.assertNotEqual(rc, 0, out)

    def test_oracle_emit_writes_oracle_named_material(self) -> None:
        rc, out = self.run_review([str(self.spec_dir), "--emit", "--oracle"])
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.spec_dir / "reviews" / "m.oracle.prompt.md").is_file(), out)
        # 与旧代留档互不覆盖
        self.assertFalse((self.spec_dir / "reviews" / "m.prompt.md").exists(), out)


if __name__ == "__main__":
    unittest.main()
