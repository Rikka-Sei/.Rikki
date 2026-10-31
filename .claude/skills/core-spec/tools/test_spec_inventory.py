#!/usr/bin/env python3
"""Inventory behavior tests over neutral temporary directory fixtures."""
import json
import pathlib
import tempfile
import unittest

import spec_inventory


class InventoryTest(unittest.TestCase):
    def test_lists_spec_versions_and_local_tool_differences_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            specs = root / "docs" / "specs"
            templates = root / ".claude" / "skills" / "core-spec" / "templates" / "tools"
            templates.mkdir(parents=True)
            (templates / "audit_spec.py").write_text("template", encoding="utf-8")
            (specs / "alpha" / "tools").mkdir(parents=True)
            (specs / "alpha" / "spec.md").write_text(
                "**版本:** 1.2（决策基线）\n**状态:** Draft（待接线）\n### 1.0 设计图景\n入口\n", encoding="utf-8")
            (specs / "alpha" / "tools" / "audit_spec.py").write_text(
                "custom", encoding="utf-8")
            (specs / "beta").mkdir(parents=True)
            (specs / "beta" / "spec.md").write_text(
                "**状态:** Authoritative\n", encoding="utf-8")
            (specs / "not-a-spec.md").write_text("hello", encoding="utf-8")
            before = sorted((str(p.relative_to(root)), p.read_bytes())
                            for p in root.rglob("*") if p.is_file())
            result = spec_inventory.collect(root)
            after = sorted((str(p.relative_to(root)), p.read_bytes())
                           for p in root.rglob("*") if p.is_file())
            self.assertEqual(before, after)
            self.assertEqual([r["name"] for r in result], ["alpha", "beta"])
            self.assertEqual(result[0]["spec_version"], "1.2")
            self.assertEqual(result[0]["status"], "Draft")
            self.assertEqual(result[0]["audit_spec.py"], "different")
            self.assertTrue(result[0]["design_view"])
            self.assertEqual(result[1]["spec_version"], "unmarked")
            self.assertEqual(result[1]["audit_spec.py"], "missing")
            self.assertFalse(result[1]["design_view"])

    def test_reports_independent_audit_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            template = root / ".claude" / "skills" / "core-spec" / "templates" / "tools"
            local = root / "docs" / "specs" / "alpha" / "tools"
            template.mkdir(parents=True)
            local.mkdir(parents=True)
            (local.parent / "spec.md").write_text("**版本:** 0.4", encoding="utf-8")
            (template / "audit_manifest.json").write_text(json.dumps({"audit_baseline": "template-1"}), encoding="utf-8")
            (local / "audit_manifest.json").write_text(json.dumps({"audit_baseline": "baseline-0"}), encoding="utf-8")
            row, = spec_inventory.collect(root)
            self.assertEqual(row["spec_version"], "0.4")
            self.assertEqual(row["audit_baseline"], "baseline-0")
            self.assertEqual(row["template_audit_baseline"], "template-1")

    def test_empty_design_view_is_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            spec = root / "docs" / "specs" / "beta" / "spec.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("## 1. 背景与目标\n### 1.0 设计图景\n\n### 1.1 为什么写\n", encoding="utf-8")
            row, = spec_inventory.collect(root)
            self.assertFalse(row["design_view"])

    def test_identical_template_and_missing_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            templates = root / ".claude" / "skills" / "core-spec" / "templates" / "tools"
            templates.mkdir(parents=True)
            (templates / "audit_spec.py").write_text("same", encoding="utf-8")
            path = root / "docs" / "specs" / "gamma"
            (path / "tools").mkdir(parents=True)
            (path / "spec.md").write_text("# alpha", encoding="utf-8")
            (path / "tools" / "audit_spec.py").write_text("same", encoding="utf-8")
            row, = spec_inventory.collect(root)
            self.assertEqual(row["audit_spec.py"], "same")
            self.assertEqual(row["test_audit_spec.py"], "missing")


if __name__ == "__main__":
    unittest.main()
