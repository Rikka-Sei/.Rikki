# reference/examples.md

本文件是 `knowledge` skill 的子参考，提供：

- § 目录实例 — CONTEXT/ 目录与 INDEX.md 真实形态
- § 主题文件实例 — 横切主题完整填法
- § spec 附属文件实例 — 依附 spec 域的知识文件
- § 写 ADR — 一个完整 ADR 落地实例
- § 检索 — 常见查询的 grep 模式

---

## § 目录实例

以下示例均为合成内容，仅展示格式，不代表真实项目记录或用户原话。

```
CONTEXT/
├── INDEX.md
├── task-queue.md
└── inventory/
    └── CONTEXT.md
```

`CONTEXT/INDEX.md`：

```markdown
# CONTEXT 索引

本目录是仓库知识库入口，agent 冷启动先读本文件再按需读主题文件。

## 主题

- `task-queue`：任务队列——处理中的任务保留原顺序（1 决策）
- `inventory/`：资源清单词表（2 术语 / 0 决策）

## 状态

- {填写本目录当前状态；没有状态内容时删除此条}
- 主源：knowledge skill 配套参考 relationship.md 的「ask → knowledge 触发链」

## 维护规则

- 新主题：agent 按对话主题动态生成，英文 slug 文件名
- 依附 spec 的主题：CONTEXT/<spec-name>/CONTEXT.md
- 单文件决策数 ≥ 50 时提示用户拆分
```

---

## § 主题文件实例

`CONTEXT/task-queue.md`（合成示例；日期和事件不对应真实记录）：

```markdown
# 任务队列

展示任务的顺序与状态，供维护者核对队列处理行为。

## State

- {可观察事实}

## History

- 2026-01-15 queue-ordering：合成历史事件，模拟运行后保持其余任务顺序（示例记录）
- 2026-01-12 queue-view：合成历史事件，增加队列状态摘要（示例记录）

## Decisions

- ts: 2026-01-16
  speaker: 合成示例（非用户原话）：队列顺序以提交时间为准
  decision: 处理后保留其余任务的原有顺序
  rationale: 便于维护者根据队列状态复现处理顺序
  rejected: 每次处理后重新排序
  evidence: 合成示例记录；不对应真实对话或文档

- → ADR-0001：队列处理后保留未处理任务顺序
```

---

## § spec 附属文件实例

`CONTEXT/inventory/CONTEXT.md`（合成词表与记录示例）：

```markdown
# 资源清单

资源清单记录已登记资源及其可观察状态。与 `docs/specs/inventory/spec.md` 冲突时听 spec。

## Language

**登记项**：
资源清单中的一条记录，包含资源标识和当前状态。
_Avoid_: 资源对象（未明确指哪类对象时）

**可用项**：
通过检查并可供任务分配的登记项。
_Avoid_: 健康资源（未定义检查范围时）

## State

- {可观察事实}

## History

- 2026-01-14 inventory-count：合成历史事件，更新清单统计口径（示例记录）

## Decisions

- ts: 2026-01-15
  speaker: 合成示例（非用户原话）：统计只包含通过检查的登记项
  decision: 可用数量按最近一次成功检查结果统计
  rationale: 统计结果与分配时的可用状态一致
  rejected: 按登记项总数统计
  evidence: 合成示例记录；不对应真实对话或文档
```

---

## § 写 ADR

`docs/adr/0001-queue-ordering.md`（合成示例）：

```markdown
# 处理后保留未处理任务顺序

- ts: 2026-01-16
- speaker: 合成示例（非用户原话）：队列顺序以提交时间为准
- decision: 处理任务后保留其他任务的原顺序
- rationale: 维护者可以根据队列状态复现处理顺序
- rejected: 每次处理后重新排序

## Status

accepted

## Considered Options

- 每次处理后重新排序：可能改变未处理任务的先后次序，不选
- 保留原顺序：保持提交顺序，选择此项

## Consequences

- 后续处理按原有顺序继续

## Evidence

- 合成示例决策；不对应真实对话或文档
```

对应主题文件 Decisions 段留指针（见 § 主题文件实例末行）。

---


## § 检索

### 按字段查

```bash
# 查所有决策日期
grep -rn "^- ts:" CONTEXT/

# 查所有被用户拍板的决策
grep -rn -B1 -A4 "speaker: 用户原话" CONTEXT/

# 查所有 ADR 指针
grep -rn "→ ADR-" CONTEXT/
```

### 按主题查

```bash
# 冷启动：先读索引
cat CONTEXT/INDEX.md

# 主题检索：直接读主题文件
cat CONTEXT/task-queue.md
cat CONTEXT/inventory/CONTEXT.md

# 跨主题关键词（把 <关键词> 替换为实际词；多词用 \| 分隔）
grep -rn "任务\|队列" CONTEXT/
grep -rn "资源\|可用" CONTEXT/
```

### 复合查询

```bash
# 查某日期后所有合成示例决策（示例日期）
awk '/^- ts: 2026-01-15/{flag=1;next} /^- ts:/{flag=0} flag && /speaker: 合成示例/' CONTEXT/task-queue.md
```

### 用户查询应答模板

用户说"CONTEXT 里说了什么" / "X 决策过几次" → agent 调起 knowledge 后：

1. `read CONTEXT/INDEX.md` 定位相关主题
2. 读主题文件 grep 相关行
3. 返回带证据的总结（**不修改**）

返回格式：

```
[knowledge] 查询结果：
- {主题} 决策 N 次，最近 YYYY-MM-DD
- 关键决策：
  - ts: ...，decision: ...，evidence: ...
  - ...
- 完整记录见 {CONTEXT/<topic>.md ## Decisions / docs/adr/NNNN}
```
