# ask skill

自动访谈 skill。agent 在执行非平凡任务前主动调起，把所有需要拍板的点**一次摆开**（决策图：推荐 *** + 不希望 双栏），同时给每项推荐默认。用户确认后进入**免打扰**模式：执行中 agent 选安全可回滚选项跳过，仅在"问题太大"或"不可逆"时回来问。核心原则：**做的准确比做的多更好**。

> ⚠️ **HARD-RULE**：决策点 N ≥ 1 时，决策图必须经 `ask` 工具的 `questions[]` 参数 emit；**不得**在对话流中以 markdown 列表替代。下面 § 形态概览 是**概念骨架**便于人类审阅 ask agent 实际 emit 形态——不是 emit 形态本身。详见 SKILL.md § HARD-RULE 与 reference/format.md § 决策图 emit 通道。

## 子文件

- [SKILL.md](SKILL.md) — skill 入口与规则（含 § HARD-RULE）
- [reference/format.md](reference/format.md) — 决策图 emit 通道 / 决策图概念骨架 / 探路 / 必拍板 / 免打扰 / 超限的格式与细则
- [reference/triggers.md](reference/triggers.md) — 任务级触发词与决策点识别（含 § 自检第 5 条「决策图必须走 ask tool emit」）

## 形态概览（概念骨架，不是 emit 形态）

```markdown
## 决策图：{任务名}（概念示意）

扫描到 N 个决策点。

### 必拍板
#### Q1 - {标题}
{正文}

推荐（***）：
1. {推荐项 1} —— {依据}
2. {推荐项 2} —— {依据}

不希望：
3. {忌讳 1} —— {为什么忌讳}
4. {忌讳 2} —— {为什么忌讳}

🛑 必须拍板 / 可默认 · *** 项最强推荐

### 探路
- 我猜用户不想要的是：{一句话}（依据：{仓库惯例 / spec}）

---

可用回话（1-2 字）：
- 够了 / go → 进入免打扰
- 我没看懂 → 拆词解释
- 重说 → 重新生成决策图
- 加进去 X → 补决策点
- 拆 → 拆子任务
- 暂停 → 立即停手
```

> **实际 emit 形态**（必须走 `ask` 工具）：
> ```typescript
> ask({
>   questions: [
>     {
>       id: "q1_id",
>       header: "≤12 字短标题",
>       question: "完整问题描述",
>       options: [
>         {label: "推荐项", description: "依据 + 简要说明"},
>         {label: "另一可选项", description: "..."}
>       ],
>       multi: false
>     }
>   ]
> })
> ```

## 什么时候用

- agent 开始非平凡任务（多步骤 / 多决策）
- 任务路径涉及 2+ 个独立拍板点
- 用户希望"开始前先对齐认知"
- 用户显式要决策图

## 什么时候不用

- 任务非常简单（≤ 1 个独立决策点）—— 直接做
- 事实查询 —— 直接 grep
- 多轮多主题设计访谈 —— 走 brainstorming
- 跨模块契约 —— 走 core-spec
- 实施计划 —— 走 writing-plans

## 调用入口

- agent 自动：任务到达 + 决策点 ≥ 2
- 用户显式："先列决策图" / "把要问的问完"
- 触发链：brainstorming 完成后可转 ask 收尾（待定）

## 与 knowledge 的关系

ask 决策图本身不写仓；推项被选完后，按 [knowledge 的触发链](../knowledge/reference/relationship.md) 判定留底。