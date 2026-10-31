# Rikki~

Rikki 的工作流与 Skills 集合，用于辅助编码任务中的需求确认、规格设计、增量交付、知识记录和技术说明。

仓库按 `.claude/skills/` 和 `.claude/commands/` 组织内容。技能保存可复用规则；目标项目的状态、决策和运行记录留在目标项目中。

## Skills

| 技能 | 用途 |
| --- | --- |
| [ask](.claude/skills/ask/SKILL.md) | 集中确认需要用户决定的选项，减少执行中的反复询问。 |
| [audited-incremental-delivery](.claude/skills/audited-incremental-delivery/SKILL.md) | 将复杂任务拆成可验证的增量，配合独立审计、worktree 隔离和上下文恢复。 |
| [binary-reverse-analysis](.claude/skills/binary-reverse-analysis/SKILL.md) | 二进制逆向、调用约定分析、模式搜索、补丁和跨架构核对。 |
| [clear-explain](.claude/skills/clear-explain/SKILL.md) | 用户需要解释时，用具体动作说明设计、调用关系和事件流转。 |
| [core-spec](.claude/skills/core-spec/SKILL.md) | 编写和审计核心规格，通过嵌入式 Haskell 模型及配套工具核对语义。 |
| [knowledge](.claude/skills/knowledge/SKILL.md) | 维护目标项目的 `CONTEXT/` 与 `docs/adr/`，记录状态、历史和决策依据。 |
| [nLsyntax](.claude/skills/nLsyntax/SKILL.md) | 让说明更直白，保留标准技术术语和原有技术含义。 |

具体触发条件、执行规则和所需工具以各技能的 `SKILL.md` 为准。

## 命令

以下名称用于支持 `.claude/commands/` 的工具，不是终端命令。

| 命令 | 用途 |
| --- | --- |
| [/commit-message](.claude/commands/commit-message.md) | 分析暂存区，生成中文 Conventional Commits 消息；检测可用密钥并询问是否 GPG 签名。 |
| [/commit-tag](.claude/commands/commit-tag.md) | 汇总发布变更，创建 CalVer 与短提交哈希组成的附注标签。 |
| [/core-spec-upgrade](.claude/commands/core-spec-upgrade.md) | 盘点目标项目的核心规格及工具差异，逐份升级并验证。 |

提交命令默认只处理暂存区内容，不自动暂存其他文件。实现、提交和推送分别遵循用户授权。

## 使用方式

1. 安装 Superpowers。本仓库的多项技能引用了其中的规划、调试、审计与验证流程。
2. 将所需技能目录放入目标项目的 `.claude/skills/`，将所需命令文件放入 `.claude/commands/`。复制前检查同名文件，避免覆盖已有配置。
3. 保留技能的完整目录结构。尤其是 `core-spec`，不能只复制 `SKILL.md` 而遗漏 `reference/`、`templates/` 和 `tools/`。
4. 由运行工具按各技能的触发条件加载，或明确要求使用某项技能。其他目录布局和命令语法以所用工具的支持情况为准。

Superpowers 需要单独安装；Nix 开发环境不会安装或注册这些技能。

## 许可证

见 [LICENSE](LICENSE)。