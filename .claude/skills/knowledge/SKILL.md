---
name: knowledge
description: Project-side knowledge maintenance for the current repository. Use to write or read CONTEXT/ (topic-scoped state / history / decisions) and docs/adr/. Auto-invokable when conversation surfaces a project decision, a state snapshot moment, a term drift, or a history handoff. User can also invoke explicitly with "记下来 / 写进 CONTEXT / 整理一下决策".
---

# knowledge

项目侧知识管理：写 `CONTEXT/` 目录（按主题切分的状态 / 历史 / 决策），写 `docs/adr/NNNN-slug.md`，维护 `CONTEXT/INDEX.md` 索引。本 skill **不写权威 spec**（那是 `core-spec` 的事）；**不做设计访谈**（单轮决策点推荐走 `ask`，多轮多主题访谈仍走 `brainstorming`）；**不改代码**。

## 目录形态

```
CONTEXT/
├── INDEX.md                     # 索引：每主题一行（slug + 中文名 + 一句话现状 + 决策数）
├── <topic-slug>.md              # 横切主题（不依附单一 spec）
└── <spec-name>/                 # 依附 spec 的主题（按 spec 名建子目录）
    └── CONTEXT.md               # 该 spec 域的知识文件
```

设计原则：**文件边界 = 主题边界**。agent 读一个主题的全部 context = 读一个文件，不需要跨段拼图。

| 主题类型 | 路径 | 判断依据 |
|---|---|---|
| 依附某 spec 域 | `CONTEXT/<spec-name>/CONTEXT.md` | 对话主题触及 `docs/specs/<name>/` 的域 |
| 横切主题（跨域或不属于任何 spec） | `CONTEXT/<topic-slug>.md` | 无单一 spec 归属 |

主题文件内部结构（四段，按需出现）：

- `## Language`：该域词表（依附 spec 的文件常见；knowledge 写入时**不动此段**）
- `## State`：该主题当前可观察事实
- `## History`：该主题按时间倒序的事件
- `## Decisions`：该主题的决策记录

文件级 schema 与示例见 [reference/format.md](reference/format.md)。

## 读取流程

| 场景 | 动作 |
|---|---|
| 冷启动（新会话了解仓库） | `read CONTEXT/INDEX.md`（索引极小）→ 按需读相关主题文件 |
| 主题检索（处理某主题拿全部 context） | `read CONTEXT/<topic-slug>.md` 或 `read CONTEXT/<spec-name>/CONTEXT.md` |
| 跨主题查询（"X 决策过几次"） | `grep -rn "<关键词>" CONTEXT/` |
| ADR 查询 | `grep -rn "ADR-NNNN" CONTEXT/ docs/adr/` |

## 何时调起

### agent 自动

- **项目状态**：对话中出现可观察事实（版本号、依赖、commit hash、资源统计）——记入主题文件 `## State`；避免记录私密基础设施信息、管理入口或内部节点信息，除非用户明确要求且确有必要
- **历史路径**：plan 合并 / spec 变更 / 重大 commit 落地 / 工作流变更——记入主题文件 `## History`
- **决策点**：用户表态（"就这样定""是""别忘了""记住""存一下"）/ agent 推断（"继续往下推就是隐式决策"）——按字段记入主题文件 `## Decisions` 或 `docs/adr/NNNN-slug.md`
- **术语收敛**：用户要求统一某术语含义 / agent 推断出两段对话中同一词指不同概念——写 ADR

### 用户显式

- "记下来""存到 CONTEXT""写 ADR""整理一下决策记录""CONTEXT 里有什么"
- "这个以后都要这么做，记一下"
- "决策日志在哪儿"

### 被动调起

`ask` 推项被用户选完后，按 [reference/relationship.md](reference/relationship.md) § ask → knowledge 触发链（主源锚点）判定是否留底。

## 何时不调起

| 情况 | 改走 |
|---|---|
| 多轮多主题设计访谈 | `brainstorming` |
| 跨模块 / 跨进程契约 | `core-spec` |
| 单轮决策点推荐 / 选项 | `ask` |
| 实施计划 | `writing-plans` |
| 用户只问"现在状态" / "决策过几次" | 调起 knowledge 读 CONTEXT/，**不写入** |
| agent 推断只是口头表述未拍板 | 不调起，等下次表态 |

## 写哪些

### State（主题文件 `## State`）
仅记**当前可观察事实**，不做解读；只记录实现任务确需保留的信息。

可记：技术栈版本、关键依赖、与当前决策直接相关的资源统计。

默认不记录凭据或私密基础设施信息（包括管理入口、内部节点等）。只有用户明确要求且确有必要时，才记录最少必要信息，并先确认不会暴露敏感信息。

不记："X 是因为 Y 才这么设计"（那是 ADR 范围）、"未来要做什么"。
### History（主题文件 `## History`）

按时间倒序记**指向性事实**。每行格式：

```
- YYYY-MM-DD {slug}：{一句话事件}（{commit/plan/spec 链接}）
```

不记：未来计划、实施细节。

### Decisions（主题文件 `## Decisions`）

每条决策记入两层之一：

- **不涉及 spec 契约**：直接写入主题文件 `## Decisions`
- **涉及 spec 契约**：写入 `docs/adr/NNNN-<slug>.md`，并在主题文件 `## Decisions` 留指针 + 一句话摘要（格式 `→ ADR-NNNN：{摘要}`，不跳文件也能读懂）

每条决策条目字段：

- **ts**: ISO 日期 `YYYY-MM-DD`
- **speaker**: 谁拍板（用户原话 / agent 推断 + 依据）
- **decision**: 决定是什么（一句话，≤ 30 字）
- **rationale**: 为什么（用户原话 / agent 推断 + 依据）
- **rejected**: 备选或被推翻的方案（如无写 `（无）`）
- **evidence**: 证据位置 — 对话具体行 / 文档路径 / spec 路径 / 决策 ID

字段定义与填写示例见 [reference/format.md](reference/format.md) § Decision。

## 主题切分规则

- agent 按对话主题动态生成主题文件；INDEX.md 同步登记
- 文件名英文 slug（小写连字符，≤ 5 词）；INDEX.md 里主题名用中文
- 依附 spec 的主题：`CONTEXT/<spec-name>/CONTEXT.md`（spec-name 与 `docs/specs/` 下目录名一致）
- 横切主题：`CONTEXT/<topic-slug>.md`
- 单主题文件决策数 ≥ 50 时，agent 提示用户拆出子主题
- 无最小阈值：一条决策也可成文件

## INDEX.md 维护

每次写入主题文件后同步更新 `CONTEXT/INDEX.md` 对应行。每行格式：

```
- `<slug 或 spec-name/>`：`<中文名>`——`<一句话现状>`（`<N>` 决策）
```

``` 
- `inventory/`：资源清单——已登记资源的可用状态（2 术语 / 0 决策）
- `task-queue`：任务队列——处理后保留未处理任务顺序（1 决策）
```

INDEX 格式细则见 [reference/format.md](reference/format.md) § INDEX。

## 决策点词表

agent 自动触发写入的场景，命中以下任一即视为决策点：

- 用户原话包含"就这样""定了""是""用这个""别忘了""记住""存一下"
- 用户表态 / 推翻 / 选择一项（特别是经过 ask 推荐后）
- agent 完成一次 spec / plan 改写但被合入
- 连续 3 轮以上同一术语含义漂移
- 用户明确要求"以后照此办理"

## 写入防护（中间件）

仅做三类防护，不做重型锁：

1. **路径不跳**：写仓时只写 `CONTEXT/INDEX.md` / `CONTEXT/<topic-slug>.md` / `CONTEXT/<spec-name>/CONTEXT.md` / `docs/adr/NNNN-*.md`。不写 `docs/specs/`，不写代码文件，不写 `docs/superpowers/plans/`。
2. **不含凭据**：写入前 grep 凭据关键字（`password` / `secret` / `token` / `api[_-]?key` / `jwt` / `private[_-]?key` / 32~128 位 base64-hex 串），命中即拒绝写入并询问用户。主源关键字清单见 [reference/format.md](reference/format.md) § 防护。
3. **不覆盖已有内容**：写主题文件时只追加，不删改既有行与既有段（含 `## Language` 段——knowledge 永不改写词表）。被推翻的决策就地加 status 字段 `superseded by ADR-NNNN`，不删除。

## 写后形态

agent 写完后，对话里**只输出**：

```
[knowledge] 已记：{决策简述，≤ 30 字} → {CONTEXT/<topic>.md ## Decisions / docs/adr/NNNN}
```

例：`[knowledge] 已记：处理后保留未处理任务顺序 → CONTEXT/task-queue.md ## Decisions`
不贴全文。不交互（除非中间件命中）。

### 被动触发留底的告知（来自 ask 触发链）

当 `ask` 推项被选完后，按 [reference/relationship.md](reference/relationship.md) § ask → knowledge 触发链 判定命中，agent 留底后**必须**告知用户：

```
[knowledge] 留底：{简述}（触发判据：{难以反悔 / 不直观 / 真实权衡}）→ {路径}
```

含判据命中理由 + 文件路径 + 30 字内摘要。**给用户复核 / 否决入口**。

## 检索接口

### agent 检索

- 冷启动：`read CONTEXT/INDEX.md`
- 主题：`read CONTEXT/<topic>.md`
- 跨主题关键词：`grep -rn "<关键词>" CONTEXT/`（`<关键词>` 替换为实际词，多词用 `\|` 分隔）
- 字段定位：`grep -rn "^- ts:\|decision:\|evidence:" CONTEXT/`

### 用户显式查询

用户说"CONTEXT 里说了什么" / "X 决策过几次" → agent 调起 knowledge 后 grep 返回带证据的总结（**不修改**）。

完整 grep 模式与示例见 [reference/examples.md](reference/examples.md) § 检索。

## 与其他 skill 的关系

- **`brainstorming`**：多轮多主题访谈 + 设计章节 + 写 spec 文档。**不被 knowledge 替代**。划界主源见 [reference/relationship.md](reference/relationship.md) § 与 brainstorming 的关系。
- **`ask`**：单轮决策点推荐 + 选项。**不写仓**。ask → knowledge 留底触发链主源见 [reference/relationship.md](reference/relationship.md) § ask → knowledge 触发链。
- **`core-spec`**：跨模块/跨进程契约 + 状态机 + 决策日志（§9 决策 ID）。本 skill 是 core-spec 的"项目侧副手"：core-spec 决策 ID 可被 knowledge 的 ADR 跨引用（写进 `evidence` 字段），但不直接写 spec 正文。
- **`writing-plans`**：实施计划，写 `docs/superpowers/plans/`。本 skill 不参与。

完整关系图与场景划分见 [reference/relationship.md](reference/relationship.md)。

## 静态约束

- 主题文件三段（State / History / Decisions）不放术语（术语收敛走 ADR）；`## Language` 段（如已有）不并入、不删除、不改写
- 主题文件不写实现细节（不写代码片段、不写 import、不写路径清单）
- 主题文件不写未来计划（不写具体阶段或波次编号）
- ADR 不写实施步骤，仅记录"为什么这么选 + 反向参考 + 证据"
- 所有文件用中文撰写（与仓库 AGENTS.md 语言要求一致）；文件名 / slug 用英文
