---
description: 智能生成提交发布 tag（CalVer + 短哈希）
agent: build
---

分析上一次发布 tag 以来的提交，生成结构化变更日志，创建 `v<YYYY.MM.DD>-<短哈希>` 形式的 annotated tag，并交互式确认。

## 设计边界

- 发布 tag 只标识本次交付对应的提交，不读取或修改任何固定产品的版本文件。
- 本命令采用 `v<YYYY.MM.DD>-<短哈希>` 格式；现有其他格式的 tag 保持原样。

## 流程概览

```
1. 读取 HEAD、短哈希和日期
   ↓
2. 找到上一次发布 tag
   ↓
3. 统计提交并生成变更摘要
   ↓
4. 组装候选 tag 与 tag 消息
   ↓
5. ask 确认（本地创建 / 创建并推送 / 取消）
   ↓
6. 创建 tag
   ↓
7. 验证结果
```

**强制要求**：创建 tag 前必须使用 `ask` 工具确认候选 tag、提交、范围与变更摘要。直接尝试调用工具；仅在工具明确返回错误时才使用文本交互。

## 1. 读取版本信息

```bash
SHORT_SHA=$(git rev-parse --short=8 HEAD)
FULL_SHA=$(git rev-parse HEAD)
BRANCH=$(git branch --show-current)
DATE=$(date +%Y.%m.%d)
TAG="v${DATE}-${SHORT_SHA}"
```

说明：

- 短哈希固定 8 位，候选名为 `v<YYYY.MM.DD>-<8位短哈希>`。

## 2. 找到上一次发布 tag

```bash
LAST_TAG=$(git describe --tags --abbrev=0 --match 'v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]-*' 2>/dev/null || true)
if [ -z "$LAST_TAG" ]; then
  LAST_TAG=$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || true)
fi
RANGE=$([ -n "$LAST_TAG" ] && echo "${LAST_TAG}..HEAD" || echo "HEAD")
```

- 优先匹配新的 `v<日期>-<短哈希>` tag。
- 没有新式 tag 时退回最后一个 SemVer tag。
- 两者都没有时使用 `HEAD`；必须在确认信息里明确写出“范围为全部历史，可能很长”。

## 3. 统计提交并生成变更摘要

```bash
echo "=== 发布信息 ==="
echo "候选 tag: $TAG"
echo "提交: $FULL_SHA"
echo "分支: $BRANCH"
echo "日期: $DATE"
echo "范围: $RANGE"
echo ""
echo "=== 提交统计 ==="
echo "新功能 (feat): $(git log --oneline $RANGE | grep -cE '^[0-9a-f]+ feat')"
echo "问题修复 (fix): $(git log --oneline $RANGE | grep -cE '^[0-9a-f]+ fix')"
echo "性能优化 (perf): $(git log --oneline $RANGE | grep -cE '^[0-9a-f]+ perf')"
echo "代码重构 (refactor): $(git log --oneline $RANGE | grep -cE '^[0-9a-f]+ refactor')"
echo "文档/测试/杂项: $(git log --oneline $RANGE | grep -cE '^[0-9a-f]+ (docs|test|style|chore)')"
echo "合并: $(git log --oneline $RANGE | grep -cE '^[0-9a-f]+ merge')"
echo ""
echo "=== 变更摘要 ==="
echo "▎新功能"
git log --oneline $RANGE | grep -E '^[0-9a-f]+ feat' | head -20 | sed 's/^[0-9a-f]* /- /'
echo ""
echo "▎问题修复"
git log --oneline $RANGE | grep -E '^[0-9a-f]+ fix' | head -20 | sed 's/^[0-9a-f]* /- /'
echo ""
echo "▎性能优化"
git log --oneline $RANGE | grep -E '^[0-9a-f]+ perf' | head -20 | sed 's/^[0-9a-f]* /- /'
echo ""
echo "▎代码重构"
git log --oneline $RANGE | grep -E '^[0-9a-f]+ refactor' | head -20 | sed 's/^[0-9a-f]* /- /'
echo ""
echo "▎文档与测试"
git log --oneline $RANGE | grep -E '^[0-9a-f]+ (docs|test|style|chore)' | head -20 | sed 's/^[0-9a-f]* /- /'
echo ""
echo "▎合并"
git log --merges --oneline $RANGE | head -20 | sed 's/^[0-9a-f]* /- /'
```

摘要原则：

- 按 conventional commit 前缀分组。
- 每组最多列 20 条；超出时在组尾注明“其余 N 条见 git log”。
- 中文描述直接沿用提交标题；必要时改写成发布说明语气，但不得虚构。
- 不使用 `#` 前缀作为分组标题；使用 `▎`。

## 4. 组装候选 tag 与消息

候选 tag：`$TAG`。创建前检查是否已存在：

```bash
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "tag $TAG 已存在，停止"
  exit 1
fi
```

tag 消息模板（标题禁止以 `#` 开头）：

```text
Release <TAG>

提交: <FULL_SHA>
日期: <DATE>
范围: <RANGE>

▎新功能
- ...

▎问题修复
- ...

▎性能优化
- ...

▎代码重构
- ...

▎文档与测试
- ...

▎合并
- ...
```

## 5. 交互确认

使用 `ask` 工具一次性确认：

- 候选 tag 名
- 完整提交 SHA、日期、变更范围
- 变更摘要
- 推送方式

选项：

1. 仅本地创建 tag（推荐，后续可再推）
2. 创建 tag 并推送 origin
3. 取消

用户取消则中止，不创建 tag；不修改任何产品或组件的版本号。

## 6. 创建 tag

使用消息文件，避免多行 `-m` 的转义问题：

```bash
mkdir -p .tmp
cat > .tmp/release-tag-message.txt <<'EOF'
Release <TAG>

提交: <FULL_SHA>
日期: <DATE>
范围: <RANGE>

▎新功能
- ...

▎问题修复
- ...

▎性能优化
- ...

▎代码重构
- ...

▎文档与测试
- ...

▎合并
- ...
EOF
git tag -a "$TAG" -F .tmp/release-tag-message.txt
```

如果用户选择推送：

```bash
git push origin "$TAG"
```

## 7. 验证结果

```bash
git tag -l "$TAG"
git show "$TAG" --quiet
git rev-parse "$TAG^{commit}"
```

如果已推送：

```bash
git ls-remote --tags origin | grep "$TAG"
```

## 约束规则

1. 使用中文描述。
2. tag 名必须是 `v<YYYY.MM.DD>-<8位短哈希>`。
3. 本命令不修改产品或组件版本文件。
4. 不自动推送；只有用户在确认中选择推送才执行 `git push origin <tag>`。
5. tag 消息禁止以 `#` 开头的标题，使用 `▎`；禁止任何 AI 助手标识。
6. 交互取消即中止，不创建 tag。
7. 必须在确认信息里写明提交 SHA、日期、范围和变更摘要。
8. 如果 `$RANGE` 为 `HEAD`（无历史发布 tag），必须明确提示范围包含全部历史。

## 使用场景

当你想要：

- 为当前提交创建可追溯的发布 tag；
- 按日期与提交生成发布变更日志；
- 不改动产品或组件版本文件。
