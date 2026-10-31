# knowledge skill

项目侧知识管理：写 `CONTEXT/` 目录（按主题切分的状态 / 历史 / 决策），写 `docs/adr/NNNN-slug.md`，维护 `CONTEXT/INDEX.md` 索引。

## 形态

```
CONTEXT/
├── INDEX.md                     # 索引：每主题一行
├── <topic-slug>.md              # 横切主题
└── <spec-name>/CONTEXT.md       # 依附 spec 的主题（按 spec 名建子目录）
```

文件边界 = 主题边界：读一个主题的全部 context = 读一个文件。

## 子文件

- [SKILL.md](SKILL.md) — skill 入口与规则
- [reference/format.md](reference/format.md) — 目录 / INDEX / 主题文件 / spec 附属文件 / Decision / ADR 模板与防护
- [reference/examples.md](reference/examples.md) — 写入示例、检索 grep 模式
- [reference/relationship.md](reference/relationship.md) — 与其他 skill 的关系图（含 ask → knowledge 触发链主源锚点）

## 什么时候用

- 写本仓"项目状态快照""历史路径""决策记录"——按主题落文件
- 一次性术语漂移收敛（走 ADR）
- 实施完成 / plan 合并 / spec 变更后留底
- `ask` 推项被选完后，按触发链判定留底
- 冷启动读 `CONTEXT/INDEX.md` 了解仓库

## 什么时候不用

- 多轮多主题设计访谈（→ `brainstorming`）
- 跨模块契约与状态机（→ `core-spec`）
- 单轮决策点推荐与选项（→ `ask`）
- 实施计划（→ `writing-plans`）

## 调用入口

- agent 自动：对话中出现项目状态 / 历史路径 / 决策点 / 术语收敛信号
- 用户显式："记下来""存到 CONTEXT""写 ADR""整理一下决策记录""CONTEXT 里有什么"
- 被动：`ask` 推项被选完后由触发链判定
