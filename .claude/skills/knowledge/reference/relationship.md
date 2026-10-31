# reference/relationship.md

`knowledge` skill 与仓库内其他 skill 的关系与场景划分。

## 关系图

```
                        用户提问 / agent 推断
                                ↓
            ┌───────────────────┼───────────────────┐
            ↓                   ↓                  ↓
      状态 / 历史 / 决策      多主题设计       单点决策
            ↓                   ↓                  ↓
        knowledge         brainstorming          ask
        (写 CONTEXT/         (多轮访谈            (单轮推荐
         / ADR)             + 写 spec)           + 选项)
                                ↓                  ↓
                            core-spec           knowledge
                            (写权威 spec)        (留底时调)
```

## 场景划分

| 场景 | skill | 关键判据 |
|---|---|---|
| 用户说"记下来""存到 CONTEXT" | knowledge | 显式触发 |
| agent 观察到决策点（"就这样定了"） | knowledge | 自动触发 |
| agent 观察到状态快照（"现在 Go 1.24"） | knowledge | 自动触发 |
| agent 观察到 plan 合并 / spec 变更 | knowledge | 自动触发 |
| agent 观察到术语漂移（同一词指不同概念） | knowledge → ADR | 自动触发 |
| 用户问"现在状态""决策过几次" | knowledge（**只读**） | 只读不写 |
| 用户说"设计 X 模块" | brainstorming | 多主题 + 多轮 |
| 用户说"选 A 还是 B" | ask | 单点 + 2-4 选项 |
| ask 推项被选完后 | knowledge（被动） | "难以反悔 / 不直观 / 真实权衡"任一 |
| 用户说"写权威 spec" | brainstorming → core-spec | 跨模块契约 |
| 用户说"写实施 plan" | writing-plans | 实施步骤 |

## 与 core-spec 的关系（最易混）

| 维度 | knowledge | core-spec |
|---|---|---|
| 写什么 | 项目状态 / 历史 / 决策（项目侧） | 跨模块契约（权威规格） |
| 文件 | `CONTEXT/` 目录 / `docs/adr/` | `docs/specs/<name>/spec.md` |
| 谁拍板 | 用户对话 + agent 推断 | spec 决策日志（§9 决策 ID） |
| 是否动态 | 每次对话都可能追加 | 切到 `authoritative` 后契约锁定 |
| 与代码关系 | 留底 + 反查 | 行为定义 + 测试锚点 |

### 跨引用方向

- **knowledge → core-spec**：knowledge ADR 的 `evidence` 字段可填 core-spec 决策 ID
- **core-spec → knowledge**：core-spec §9 决策日志**不引用** knowledge 条目（避免反向耦合）

### 何时调谁

| 想做的事 | 调起 skill |
|---|---|
| 把决策记下来 | knowledge |
| 把跨模块契约写下来 | core-spec |
| 决策涉及新跨模块契约 | brainstorming → core-spec → knowledge（留底） |
| 决策不涉及契约 | knowledge |

## 与 ask 的关系（最易混）

| 维度 | knowledge | ask |
|---|---|---|
| 动作 | 写入 / 读取 | 推荐 + 选项 |
| 输出 | `CONTEXT/` 目录 / `docs/adr/` | 问题 markdown |
| 触发 | agent 推断 / 用户显式 / 被动 | agent 推断 / 用户显式 |
| 写仓 | 是 | 否 |

### ask → knowledge 触发链（主源锚点）

> 本节是 ask → knowledge 留底触发链的**单一来源**。其他文件（knowledge SKILL § 被动调起、ask SKILL § 调起后流程）只引用本锚点，不重写规则。

ask 推项被用户选完后，agent 按以下三条判定是否调 knowledge 留底：

1. **难以反悔**：被推翻会导致跨模块回滚或大范围返工
2. **不直观**：未来 agent / 用户看代码会问"为什么"
3. **真实权衡**：对话中确实出现过 2+ 个合理选项

任一条为真 → 调 knowledge 写 ADR 或主题文件 ## Decisions。三条全否 → 不留底，直接执行。

## 与 brainstorming 的关系

| 维度 | knowledge | brainstorming |
|---|---|---|
| 模式 | 单点 + 自动 | 多轮 + 手动 |
| 输出 | 留底文件 | 设计文档 |
| 触发 | 决策点 + 状态 + 历史 | 用户启动设计任务 |

brainstorming 完成后**不自动**调 knowledge。如设计结果需留底，由 agent 推断调起。

## 与 writing-plans 的关系

| 维度 | knowledge | writing-plans |
|---|---|---|
| 文件 | `CONTEXT/` 目录 / `docs/adr/` | `docs/superpowers/plans/YYYY-MM-DD-*.md` |
| 内容 | 决策 + 状态 + 历史 | 实施步骤 + 测试 + 验收 |
| 触发 | 留底场景 | 设计完成后实施前 |

writing-plans 完成后**不自动**调 knowledge。plan 合并后由 agent 推断是否记入 主题文件 `## History`。

## 反模式

| 错误 | 后果 | 正确 |
|---|---|---|
| 把跨模块契约写进 CONTEXT/ | 失去契约权威性 | 走 core-spec |
| 把实施步骤写进 ADR | ADR 变 plan | 走 writing-plans |
| 把 ask 推项当 knowledge 触发器（无留底条件） | 每次 ask 后都记一条决策，刷屏 | 按"难以反悔 / 不直观 / 真实权衡"三条判定 |
| 把 brainstorming 多轮访谈结果直接写 CONTEXT/ | 失去上下文 | brainstorming 完成后走核心 spec / plan，再由 agent 推断留底 |