#!/usr/bin/env python3
"""core-spec 审计器负例自测模板。

用法：python test_audit_spec.py

用临时 fixture 验证 audit_spec.py 对每一类违规都会失败（G4 的“审计器自身
可信”部分）。新增检查规则时，必须同步在本文件新增一个负例。
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
AUDIT = HERE / "audit_spec.py"

VALID_SPEC = """# 示例系统 — 权威规格（Spec）

**状态:** Draft
**版本:** 0.1
**受众:** 开发
**范围:** 测试
**治理:** 变更先确认设计图，再归因决策日志
**变更历史:** 见 history/

## 1. 背景与目标
### 1.0 设计图景
入口 -> 操作者检查 spec -> 成功: 记录结论; 失败: 返回错误。
spec 是唯一权威。

## 2. 术语表
| 术语 | 定义 |
|---|---|
| spec | 权威规格 |

## 3. 系统模型与状态机
状态机文本。

## 4. 功能需求与接口契约
REQ-001 示例需求。

## 5. 错误模型
错误表。

## 6. 非功能需求（NFR）
性能数据。

## 7. 异常与边界条件
异常表。

## 8. 验收标准
AC-001 验收。

## 9. 决策日志
| D01 | 示例决策 | 1 | spec 出现 |
| 已确认决策 | 主要影响章节 | 验证规则 |

## 10. 验证
python tools/audit_spec.py
"""


def _base_manifest() -> dict:
    return {
        "spec_file": "spec.md",
        "spec_status": "draft",
        "repo_root": ".",
        "required_sections": [
            "1. 背景与目标", "2. 术语表", "3. 系统模型与状态机",
            "4. 功能需求与接口契约", "5. 错误模型", "6. 非功能需求（NFR）",
            "7. 异常与边界条件", "8. 验收标准", "9. 决策日志", "10. 验证",
        ],
        "metadata_terms": ["状态:", "版本:", "受众:", "范围:", "治理:", "变更历史:"],
        "vague_terms": ["可能", "大概", "尽量", "友好", "快速"],
        "forbidden_patterns": ["（已被\\s*D\\d+\\s*取代）", "补丁标记"],
        "requirement_prefix": "REQ-",
        "min_requirements": 1,
        "decision_prefix": "D",
        "latest_decision": 1,
        "decisions": [{
            "decision_id": "D01",
            "required_terms": ["spec"],
            "obsolete_terms": [],
            "scoped_terms": [],
            "whitelist": [],
            "required_occurrences": [
                {"pattern": "spec", "count": {"min": 1}, "sections": ["1"],
                 "reason": "spec 术语必须在背景章节出现"}],
            "forbidden_occurrences": [],
        }],
        "code_contracts": [],
        "obsolete_code_contracts": [],
        "test_anchors": [],
        "barriers": [],
    }


def _write_fixture(tmp: pathlib.Path, spec_text: str, manifest: dict,
                   files: dict[str, str] | None = None) -> pathlib.Path:
    (tmp / "spec.md").write_text(spec_text, encoding="utf-8")
    manifest_path = tmp / "audit_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    for relative, content in (files or {}).items():
        path = tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return manifest_path


def _run(tmp: pathlib.Path, manifest_path: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AUDIT), str(manifest_path)],
                          cwd=tmp, capture_output=True, text=True, check=False)


class AuditTemplateSelfTest(unittest.TestCase):
    @contextlib.contextmanager
    def _fixture(self, *, spec_text: str = VALID_SPEC, manifest=None,
                 files=None):
        tmpdir = tempfile.TemporaryDirectory()
        try:
            tmp = pathlib.Path(tmpdir.name)
            manifest = manifest if manifest is not None else _base_manifest()
            manifest_path = _write_fixture(tmp, spec_text, manifest, files)
            yield tmpdir, tmp, manifest_path
        finally:
            tmpdir.cleanup()

    def test_missing_design_view_fails(self):
        spec_text = VALID_SPEC.replace("### 1.0 设计图景\n入口 -> 操作者检查 spec -> 成功: 记录结论; 失败: 返回错误。\n", "")
        with self._fixture(spec_text=spec_text) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("1.0 设计图景", completed.stdout)

    def test_empty_design_view_fails(self):
        spec_text = VALID_SPEC.replace("入口 -> 操作者检查 spec -> 成功: 记录结论; 失败: 返回错误。", "\n### 1.1 为什么写")
        with self._fixture(spec_text=spec_text) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("设计图景", completed.stdout)

    def test_valid_spec_passes(self):
        with self._fixture() as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_missing_section_fails(self):
        spec = VALID_SPEC.replace("## 5. 错误模型", "")
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_vague_term_fails(self):
        spec = VALID_SPEC.replace("状态机文本。", "状态机可能快速切换。")
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_patch_marker_fails(self):
        spec = VALID_SPEC.replace("| D01 | 示例决策 | 1 | spec 出现 |",
                                  "| D01 | 示例决策（已被 D99 取代） | 1 | spec 出现 |")
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_duplicate_requirement_fails(self):
        spec = VALID_SPEC.replace("REQ-001 示例需求。", "REQ-001 A。REQ-001 B。")
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_obsolete_term_in_spec_fails(self):
        manifest = _base_manifest()
        manifest["decisions"][0]["obsolete_terms"] = ["LegacyName"]
        spec = VALID_SPEC.replace("状态机文本。", "LegacyName 状态机文本。")
        with self._fixture(spec_text=spec, manifest=manifest) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_hand_drawn_state_diagram_fails(self):
        spec = VALID_SPEC.replace(
            "状态机文本。",
            "状态机文本。\n\n```mermaid\nstateDiagram-v2\n    [*] --> Idle\n```\n",
        )
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("手绘 stateDiagram", completed.stdout + completed.stderr)

    def test_generated_oracle_state_diagram_passes(self):
        spec = VALID_SPEC.replace(
            "状态机文本。",
            "状态机文本。\n<!-- ORACLE:auth BEGIN -->\n```haskell\nmodule Oracle where\n```\n"
            "<!-- ORACLE:auth END -->\n```ORACLE-LABEL:auth\nstate Idle = 空闲\n```\n"
            "<!-- RENDERED-ORACLE:auth BEGIN -->\n```mermaid\nstateDiagram-v2\n"
            "    [*] --> Idle\n```\n<!-- RENDERED-ORACLE:auth END -->",
        )
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_oracle_state_diagram_requires_matching_semantics_and_label(self):
        diagram = ("<!-- RENDERED-ORACLE:auth BEGIN -->\n```mermaid\nstateDiagram-v2\n"
                   "    [*] --> Idle\n```\n<!-- RENDERED-ORACLE:auth END -->")
        for other in ("", "<!-- ORACLE:auth BEGIN -->\n",
                      "```ORACLE-LABEL:auth\nstate Idle = 空闲\n```\n",
                      "<!-- ORACLE:auth BEGIN -->\n```ORACLE-LABEL:auth\nstate Idle = 空闲\n```\n"):
            with self.subTest(other=other):
                spec = VALID_SPEC.replace("状态机文本。", "状态机文本。\n" + other + diagram)
                with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
                    completed = _run(tmp, manifest_path)
                self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_legacy_semantics_marker_accepts_whitespace(self):
        diagram = ("<!--  SEMANTICS:auth BEGIN -->\n```haskell\nmodule Semantics where\n```\n"
                   "<!-- SEMANTICS:auth END -->\n```LABEL:auth\nstate Idle = 空闲\n```\n"
                   "<!-- RENDERED:auth BEGIN -->\n```mermaid\nstateDiagram-v2\n"
                   "    [*] --> Idle\n```\n<!-- RENDERED:auth END -->")
        spec = VALID_SPEC.replace("状态机文本。", "状态机文本。\n" + diagram)
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_duplicate_rendered_oracle_is_rejected(self):
        diagram = ("<!-- RENDERED-ORACLE:auth BEGIN -->\n```mermaid\nstateDiagram-v2\n"
                   "    [*] --> Idle\n```\n<!-- RENDERED-ORACLE:auth END -->")
        prefix = ("<!-- ORACLE:auth BEGIN -->\n```haskell\nmodule Oracle where\n```\n"
                  "<!-- ORACLE:auth END -->\n```ORACLE-LABEL:auth\nstate Idle = 空闲\n```\n")
        spec = VALID_SPEC.replace("状态机文本。", "状态机文本。\n" + prefix + diagram + "\n" + diagram)
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("生成围栏重复", completed.stdout + completed.stderr)

    def test_rendered_oracle_rejects_multiple_diagrams_in_one_block(self):
        diagram = ("<!-- ORACLE:auth BEGIN -->\n```haskell\nmodule Oracle where\n```\n"
                   "<!-- ORACLE:auth END -->\n```ORACLE-LABEL:auth\nstate Idle = 空闲\n```\n"
                   "<!-- RENDERED-ORACLE:auth BEGIN -->\n```mermaid\nstateDiagram-v2\n```\n"
                   "```mermaid\nstateDiagram-v2\n```\n<!-- RENDERED-ORACLE:auth END -->")
        spec = VALID_SPEC.replace("状态机文本。", "状态机文本。\n" + diagram)
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("必须只包含一个 Mermaid 围栏", completed.stdout + completed.stderr)

    def test_rendered_oracle_rejects_empty_then_nonempty_diagram(self):
        diagram = ("<!-- ORACLE:auth BEGIN -->\n```haskell\nmodule Oracle where\n```\n"
                   "<!-- ORACLE:auth END -->\n```ORACLE-LABEL:auth\nstate Idle = 空闲\n```\n"
                   "<!-- RENDERED-ORACLE:auth BEGIN -->\n```mermaid\n```\n"
                   "```mermaid\nstateDiagram-v2\n```\n<!-- RENDERED-ORACLE:auth END -->")
        spec = VALID_SPEC.replace("状态机文本。", "状态机文本。\n" + diagram)
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("必须只包含一个 Mermaid 围栏", completed.stdout + completed.stderr)

    def test_stale_decision_range_fails(self):
        spec = VALID_SPEC.replace("| D01 | 示例决策 |", "| D01–D24 覆盖声明 |")
        with self._fixture(spec_text=spec) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_authoritative_missing_anchor_fails(self):
        manifest = _base_manifest()
        manifest["spec_status"] = "authoritative"
        manifest["test_anchors"] = [{
            "name": "TestContractAnchor",
            "source_globs": ["src/**/*_test.go"],
            "required": True,
        }]
        with self._fixture(manifest=manifest) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    # ---- Go/Rust 测试锚点扫描 ----
    # 锚点扫描支持 Go 的 `func 名称(` 与 Rust 的 `#[test] fn 名称(`；
    # 注释/字符串里的名字不是真实测试函数。

    def _authoritative_anchor_manifest(self, *anchors: dict) -> dict:
        """权威 spec + 给定锚点：锚点缺失即 FAIL，用来锁住扫描器行为。"""
        manifest = _base_manifest()
        manifest["spec_status"] = "authoritative"
        manifest["test_anchors"] = list(anchors)
        return manifest

    def test_rust_test_fn_anchor_is_recognized(self):
        """真实 Rust 单元测试（`#[test]` + `fn 名称(`）必须被锚点扫描命中。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_present",
            "source_globs": ["src/**/*.rs"],
            "required": True,
        })
        files = {"src/module/tests.rs": (
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    #[test]\n"
            "    fn rust_anchor_present() {\n"
            "        assert!(true);\n"
            "    }\n"
            "}\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_authoritative_missing_rust_fn_anchor_fails(self):
        """权威 spec 下缺失的 Rust 测试锚点必须 FAIL。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_absent",
            "source_globs": ["src/**/*.rs"],
            "required": True,
        })
        files = {"src/module.rs": "pub fn helper() {}\n"}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_rust_anchor_only_in_comment_is_not_a_real_fn(self):
        """只出现在 `//` 或 `///` 注释里的完整测试文本不是真实测试函数，必须判缺失。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_in_comment",
            "source_globs": ["src/**/*.rs"],
            "required": True,
        })
        files = {"src/module.rs": (
            "// #[test] fn rust_anchor_in_comment() {}\n"
            "/// #[test] fn rust_anchor_in_comment() {}\n"
            "pub fn helper() {}\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("rust_anchor_in_comment", completed.stdout + completed.stderr)

    def test_rust_anchor_only_in_string_literal_is_not_a_real_fn(self):
        """只出现在字符串字面量里的名字不是真实测试函数，必须判缺失。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_in_string",
            "source_globs": ["src/**/*.rs"],
            "required": True,
        })
        files = {"src/module.rs": (
            "pub fn helper() -> &'static str {\n"
            "    \"#[test] fn rust_anchor_in_string() {}\"\n"
            "}\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("rust_anchor_in_string", completed.stdout + completed.stderr)

    def test_rust_anchor_only_in_raw_string_is_not_a_real_fn(self):
        """只出现在 raw 字符串（`r#"…"#`）里的测试文本不是真实测试函数，必须判缺失。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_in_raw_string",
            "source_globs": ["src/**/*.rs"],
            "required": True,
        })
        files = {"src/module.rs": (
            "pub fn helper() -> &'static str {\n"
            "    r#\"#[test] fn rust_anchor_in_raw_string() {}\"#\n"
            "}\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("rust_anchor_in_raw_string", completed.stdout + completed.stderr)

    def test_rust_anchor_only_in_nested_block_comment_is_not_a_real_fn(self):
        """只出现在嵌套块注释里的测试文本不是真实测试函数，必须判缺失。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_in_block_comment",
            "source_globs": ["src/**/*.rs"],
            "required": True,
        })
        files = {"src/module.rs": (
            "/* outer /* inner */\n"
            "#[test] fn rust_anchor_in_block_comment() {} */\n"
            "pub fn helper() {}\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("rust_anchor_in_block_comment", completed.stdout + completed.stderr)

    def test_unexpanded_rust_macro_is_not_test_evidence(self):
        """未被调用的宏定义不是已经编译的测试，不能满足权威锚点。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "rust_anchor_macro_only",
            "source_globs": ["src/**/*.rs"], "required": True,
        })
        files = {"src/module.rs": (
            "macro_rules! unused { () => { #[test] fn rust_anchor_macro_only() {} }; }\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("rust_anchor_macro_only", completed.stdout + completed.stderr)

    def test_go_test_fn_anchor_is_still_recognized(self):
        """支持 Rust 锚点不得破坏既有 Go 锚点识别。"""
        manifest = self._authoritative_anchor_manifest({
            "name": "TestGoAnchorStillRecognized",
            "source_globs": ["src/**/*_test.go"],
            "required": True,
        })
        files = {"src/pkg/thing_test.go": (
            "package pkg\n"
            "\n"
            "import \"testing\"\n"
            "\n"
            "func TestGoAnchorStillRecognized(t *testing.T) {}\n"
        )}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_code_contract_drift_fails(self):
        manifest = _base_manifest()
        manifest["spec_status"] = "authoritative"
        manifest["code_contracts"] = [{
            "id": "tracker-signature",
            "pattern": r"func \(t \*Tracker\) MarkIdentityBound",
            "count": 1,
            "source_globs": ["src/**/*.go"],
            "reason": "权威契约必须与代码一致",
        }]
        manifest["obsolete_code_contracts"] = [{
            "pattern": r"MarkIdentityResolved",
            "source_globs": ["src/**/*.go"],
        }]
        files = {"src/tracker.go": "func (t *Tracker) MarkIdentityResolved() {}\n"}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_draft_code_contract_drift_is_warning(self):
        manifest = _base_manifest()
        manifest["code_contracts"] = [{
            "pattern": r"func \(t \*Tracker\) MarkIdentityBound",
            "count": 1,
            "source_globs": ["src/**/*.go"],
        }]
        manifest["obsolete_code_contracts"] = [{
            "pattern": r"MarkIdentityResolved",
            "source_globs": ["src/**/*.go"],
        }]
        files = {"src/tracker.go": "func (t *Tracker) MarkIdentityResolved() {}\n"}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_code_contract_match_passes(self):
        manifest = _base_manifest()
        manifest["code_contracts"] = [{
            "id": "tracker-signature",
            "pattern": r"func \(t \*Tracker\) MarkIdentityBound",
            "count": 1,
            "source_globs": ["src/**/*.go"],
            "reason": "权威契约必须与代码一致",
        }]
        files = {"src/tracker.go": "func (t *Tracker) MarkIdentityBound() {}\n"}
        with self._fixture(manifest=manifest, files=files) as (_, tmp, manifest_path):
            completed = _run(tmp, manifest_path)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
