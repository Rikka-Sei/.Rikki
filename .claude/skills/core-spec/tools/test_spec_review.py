#!/usr/bin/env python3
"""评审材料与留档新鲜度回归测试。"""
import contextlib
import io
import pathlib
import tempfile
import unittest

import spec_review


SPEC = """## 1. 背景与目标
### 1.0 设计图景
入口 -> 操作者检查对象 -> 返回结果
## 3. 系统模型与状态机
<!-- SEMANTICS:alpha BEGIN -->
```haskell
module Semantics where
```
<!-- SEMANTICS:alpha END -->
```LABEL:alpha
state Idle = 空闲
```
"""


class ReviewDesignTest(unittest.TestCase):
    def test_design_change_invalidates_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "spec.md").write_text(SPEC, encoding="utf-8")
            reviews = root / "reviews"
            findings = root / "findings.txt"
            findings.write_text("逐事件已核对，无阻断项。", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(spec_review.cmd_record(root, reviews, findings, "human", "alpha"), 0)
            updated = SPEC.replace("返回结果", "返回错误")
            (root / "spec.md").write_text(updated, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(spec_review.cmd_check(root, reviews), 0)

    def test_emit_contains_design_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "spec.md").write_text(SPEC, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(spec_review.cmd_emit(root, root / "reviews"), 0)
            prompt = (root / "reviews" / "alpha.prompt.md").read_text(encoding="utf-8")
            self.assertIn("操作者检查对象", prompt)


if __name__ == "__main__":
    unittest.main()
