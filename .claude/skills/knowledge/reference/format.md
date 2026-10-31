# reference/format.md

本文件是 `knowledge` skill 的子参考，提供：

- § 目录 — CONTEXT/ 目录整体结构
- § INDEX — 索引文件格式
- § 主题文件 — 横切主题文件结构
- § spec 附属文件 — 依附 spec 域的知识文件结构
- § Decision — 单条决策条目字段定义
- § ADR — 单个 ADR 文件模板
- § 防护 — 写入防护实施细节

---

## § 目录

```
CONTEXT/
├── INDEX.md                     # 索引：每主题一行
├── <topic-slug>.md              # 横切主题
└── <spec-name>/                 # 依附 spec 的主题
    └── CONTEXT.md
```

规则：

- 文件边界 = 主题边界：一个主题的全部 State / History / Decisions 在一个文件内
- 横切主题扁平文件；依附 spec 的主题按 spec 名建子目录（spec-name 与 `docs/specs/` 下目录名一致）
- 文件名英文 slug（小写连字符，≤ 5 词）；内容中文
- lazy 创建：首次写入某主题时才建文件 + 登记 INDEX

---

## § INDEX

`CONTEXT/INDEX.md` 结构：

```markdown
# CONTEXT 索引

{一句话：本目录是仓库知识库入口，agent 冷启动先读本文件}

## 主题

- `<slug 或 spec-name/>`：`<中文名>`——`<一句话现状>`（`<N>` 决策）
- ...

## 状态

- {填写本目录当前状态；没有状态内容时删除此条}
- 主源：knowledge skill 配套参考 relationship.md 的「ask → knowledge 触发链」

## 维护规则

- 新主题：agent 按对话主题动态生成，英文 slug 文件名
- 依附 spec 的主题：CONTEXT/<spec-name>/CONTEXT.md
- 单文件决策数 ≥ 50 时提示用户拆分
```

每行字段：

| 字段 | 说明 |
|---|---|
| slug / spec-name | 文件名或目录名（含尾部 `/` 表示子目录） |
| 中文名 | 主题中文名 |
| 一句话现状 | 当前该主题最关键的一句状态（如"处理后保留未处理任务顺序"） |
| N 决策 | 该主题 Decisions 段条数；有 Language 段时附术语数（如 `2 术语 / 0 决策`） |

更新时机：每次写入主题文件后同步更新对应行（新主题加行，已有主题更新现状与计数）。

---

## § 主题文件

横切主题 `CONTEXT/<topic-slug>.md` 结构：

```markdown
# {主题中文名}

{一句话：这个主题是什么、为什么存在}

## State

- {可观察事实 1}
- ...

## History

- YYYY-MM-DD {slug}：{一句话事件}（{commit/plan/spec 链接}）
- ...

## Decisions

- ts: YYYY-MM-DD
  speaker: {用户原话 / agent 推断 + 依据}
  decision: {一句话决定}
  rationale: {为什么}
  rejected: {备选 / 被推翻的方案}
  evidence: {对话具体行 / 文档路径 / spec 路径 / 决策 ID}

- → ADR-NNNN：{一句话摘要}
```

横切主题文件默认无 `## Language` 段（术语收敛走 ADR）。

---

## § spec 附属文件

依附 spec 域 `CONTEXT/<spec-name>/CONTEXT.md` 结构（四段共存）：

```markdown
# {域中文名}

{一句话描述}。与 `docs/specs/<spec-name>/spec.md` 冲突时听 spec。

## Language

**{术语}**：
{定义}
_Avoid_: {避免混用词}

（该域词表——knowledge 写入时不改写此段）

## State

- {可观察事实}

## History

- YYYY-MM-DD {slug}：{一句话事件}（{链接}）

## Decisions

- ts: YYYY-MM-DD
  ...
```

规则：

- `## Language` 段是该域词表，可能先于 knowledge 存在；knowledge 只追加后三段，不动 Language
- 若文件已存在但缺后三段，lazy 追加（段级只追加）

---

## § Decision

单条决策条目字段。**所有字段全填**，没有"可选字段"。

| 字段 | 类型 | 说明 |
|---|---|---|
| `ts` | ISO 日期 `YYYY-MM-DD` | 决策日期。取对话中最早拍板的轮次日期 |
| `speaker` | 字符串 | 谁拍板。"用户原话 + 一句话引用" 或 "agent 推断 + 一句话依据" |
| `decision` | 字符串 | 决定是什么。一句话 ≤ 30 字，避免嵌套句 |
| `rationale` | 字符串 | 为什么。引用用户原话或 agent 推断的依据 |
| `rejected` | 字符串 | 被推翻的方案或备选。如无写 `（无）` |
| `evidence` | 字符串 | 证据位置。对话具体行 / 文档路径 / spec 路径 / spec 决策 ID |

### 单条决策的 markdown 格式

以下是合成示例，不代表真实用户原话或记录。
```markdown
- ts: 2026-01-16
  speaker: 合成示例（非用户原话）：队列顺序以提交时间为准
  decision: 处理任务后保留其他任务的原顺序
  rationale: 维护者可以根据队列状态复现处理顺序
  rejected: 每次处理后重新排序
  evidence: 合成示例记录；不对应真实对话或文档
```

### ADR 指针行

涉及 spec 契约的决策写 ADR，主题文件留指针 + 摘要：

- → ADR-0001：处理后保留未处理任务顺序

---

## § ADR

ADR 路径：`docs/adr/NNNN-<slug>.md`。

### 编号规则

- 扫 `docs/adr/` 找最大 NNNN +1；首位从 `0001` 起
- 不重用已删 ADR 的编号

### slug 规则

- 小写连字符英文，主题词 ≤ 5 词
- 例：`queue-ordering` / `inventory-count`

### 单文件结构

```markdown
# {决策标题}

- ts: YYYY-MM-DD
- speaker: {用户原话 / agent 推断 + 依据}
- decision: {一句话决定}
- rationale: {为什么}
- rejected: {备选 / 被推翻的方案}

## Status

{accepted | deprecated | superseded by ADR-NNNN}

## Considered Options

{仅当被推翻的方案非显然时列出，每条一行}

## Consequences

{仅当决策影响非下游可见时列出，每条一行}

## Evidence

- {证据 1}
- {证据 2}
```

---

## § 防护

写入前必跑的三类防护。

### 路径不跳

允许写入：

- `CONTEXT/INDEX.md`
- `CONTEXT/<topic-slug>.md`
- `CONTEXT/<spec-name>/CONTEXT.md`
- `docs/adr/NNNN-*.md`

不允许写入：

- `docs/specs/**`（→ `core-spec` 范围）
- `docs/superpowers/**`（→ `writing-plans` 范围）
- 代码文件 `**/*.go` `**/*.rs` `**/*.ts` `**/*.tsx` `**/*.py` 等
- `*.env` `*.key` `*.pem` 等凭据文件
- 仓库根 `CONTEXT.md`（已废弃；知识一律走 `CONTEXT/` 目录）

### 不含凭据（主源关键字清单）

写入前 grep 关键字：

- `password`
- `secret`
- `token`
- `api[_-]?key`
- `jwt`
- `private[_-]?key`
- 32~128 位 base64 / hex 串（疑似 token / hash）

命中任意一个以上候选项 → 拒绝 + 询问。

（本清单是主源；SKILL.md § 写入防护（中间件） 为摘要，两处不一致时以本清单为准。）

### 不覆盖已有内容

- 写主题文件时只追加，不删改既有行
- `## Language` 段永不改写（词表维护走该域自己的流程）
- 被推翻的决策就地加 `superseded by ADR-NNNN` 字段，不删除原行
- ADR 文件不删改既有 ADR；如需更新，新建新 ADR 并用 `Status: superseded by ADR-NNNN`

### 防护冲突时

- 凭据命中：拒绝 + 询问用户
- 路径越界：拒绝 + 引导到正确 skill
- 覆盖冲突：拒绝 + 询问用户确认覆盖意图
