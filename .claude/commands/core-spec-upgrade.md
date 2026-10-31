---
description: 盘点项目全部 core spec 的版本与工具差异，逐份安全升级并验证
---

目标：检查 `docs/specs/*/spec.md` 的全部核心规格，并按当前 `.claude/skills/core-spec/` 规则逐份升级。参数 `$ARGUMENTS` 可填写要优先处理的 spec 名；不限制全量盘点，未填写则按目录名排序。执行前先读取 `core-spec` 和 `writing-skills` 技能及仓库 AGENTS.md。

1. **全量只读盘点。** 在仓库根目录运行 `python .claude/skills/core-spec/tools/spec_inventory.py`；机器处理可加 `--json`。报告总数，并逐份记录 `**版本:**`、状态、设计图、manifest 的 `audit_baseline` 与模板基线，以及三种本地工具与模板的哈希状态。正文版本是业务规格版本；旧清单 `unmarked` 表示未标记审计基线，不等于确实落后。工具哈希只是内容指纹；`different` 不是“落后”，不得因哈希不同直接覆盖文件。逐份检查共享 `spec_barrier.py`/`spec_review.py` 的使用情况；无 SEMANTICS 的 spec 不强行增加状态机。
2. **明确基线和待升级事项。** 阅读当前技能 `SKILL.md`、`reference/quality-gates.md`、`templates/spec.md`、`templates/tools/`；逐份读取实际 `spec.md`、本地 `tools/`、决策日志、开放 issue 和 `reviews/`。对比 G0–G9：先报告缺设计图、审计器缺图检查、评审留档是否随设计图变更失效、G6/G7 不可达格的现状、路径错误、工具差异及缺失工具。不同副本逐行分析专用逻辑，保留其验证规则与负例；不通过复制模板抹平差异，也不把业务版本改成工具版本。
3. **按已选模式逐份安全升级。** 先向用户呈现全量盘点及每份的升级/保留/阻断分类，然后逐份实施，无需重复询问已确定的机械步骤。发现身份、职责、后继、数量或跨层边界存在多种合理取舍时，停止该 spec 的行为性修改，给出证据和选项请用户决策；继续处理其他独立 spec。缺图时先从现行契约重建可达的入口→结果和失败路径；无法证明的后继不猜测，标为阻断。发生行为变更的规格须先补 §1.0，再更新 REQ/状态机/决策 ID/manifest；单纯工具迁移不得擅改目标行为、清零决策、重编号或修改状态为 Authoritative。未触及行为的旧 Authoritative 缺图时登记待迁移，不仅凭新门追溯改写状态；已发生行为变更却缺图的，报告 G0 未满足。
4. **工具与评审迁移。** 本地 `audit_spec.py` 增加设计图存在性检查时，必须增加缺图负例并保留原有审计规则；先观察负例失败，再改工具并运行全套本地负例测试。只有已迁入该基线规定的检查和负例、且逐份验证通过，才将本地 manifest 的 `audit_baseline` 标成模板基线；否则保持 `unmarked`/原值并报告。共享评审门对有图的 spec 将图纳入哈希；补图/改图后的每台 SEMANTICS 重新 `--emit`，对照图和所有可达迁移人工评审，`--record` 留档，禁止自动生成“无阻断项”。无 SEMANTICS 的 spec 记录不适用，不凭空补嵌入块。现有未提交改动先识别归属，保留不相关内容。
5. **每份验证、再汇总。** 以 `docs/specs/<name>/tools/` 为工作目录执行 `python test_audit_spec.py`、`python audit_spec.py`、`python barriers.py`；有 SEMANTICS 时切回仓库根目录执行 `python .claude/skills/core-spec/tools/spec_barrier.py docs/specs/<name>`，确认快照与评审门，迹线 WARN 须单列。运行时显式启用 UTF-8。失败则保留输出和未完成状态，不把 PASS 的结构审计说成设计正确。全部遍历后再运行一次只读盘点，对比前后版本/工具指纹，按 spec 报告升级成功、待决策、未迁移及具体失败；不得把未验证的 spec 宣称升级成功。除用户另有明确要求，不自动提交。

快捷用法：`/core-spec-upgrade` 全量检查并逐份升级；`/core-spec-upgrade example-domain` 仍全量检查，优先处理指定目录。这里的目录名是合成示例；参数表示优先顺序，不是跳过其余规格的过滤器。
