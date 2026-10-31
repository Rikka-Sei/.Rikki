#!/usr/bin/env python3
"""core-spec 评审门（skill 共享工具）——人工或 AI 辅助判断。

定位：嵌入语义与标签的**正确性**无法机械判定（门只能保证齐全与自洽）；
语义质量由人工或 AI 辅助评审判定；AI 可协助编写正文，但**不进入
确定性执行路径**——评审产出留档，门只做确定性校验（评审是否新鲜、是否
有未闭环的阻断项）。

与 AI 的配合方式（可选、外部进行）：
  1. --emit    生成评审材料（设计图 + 嵌入语义 + 标签 + 检查清单）→ reviews/<name>.prompt.md
  2. 由 AI 或人工依该材料评审，产出 findings 文本
  3. --record <findings>   写入评审留档 reviews/<name>.review.md（含语义哈希）
  4. --check   门：留档存在、哈希与当前语义/标签一致、无未闭环 BLOCKING 项

设计图、语义或标签一经变更，哈希变化 → 旧评审失效 → 门变红，强制重新评审。

用法：
  python spec_review.py <spec-dir> --emit
  python spec_review.py <spec-dir> --record findings.md [--model <名>]
  python spec_review.py <spec-dir> --check
  python spec_review.py <spec-dir> --oracle --emit|--check|--record …   # 新代 ORACLE 机器

新代（``--oracle``）：哈希对象是 ``ORACLE:<name>`` + ```ORACLE-LABEL:<name>```` +
```ORACLE-ERRMAP:<name>````（+ 设计图景），与旧代 ``SEMANTICS``/``LABEL`` 互不失效；
留档名带 ``.oracle`` 段（``reviews/<name>.oracle.prompt.md`` / ``<name>.oracle.review.md``），
与旧代文件互不覆盖。本工具**只**做机械校验：不自动写留档、不自动判「无阻断」、
不伪新鲜哈希——findings 必须由外部评审产出并经 ``--record`` 写入。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import pathlib
import re
import sys

SKILL_TOOLS = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SKILL_TOOLS.parents[3]

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BLOCKING_PREFIX = "BLOCKING:"


def fail(msg: str) -> int:
    print(f"[FAIL] {msg}", file=sys.stderr)
    return 1


def norm_eol(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_blocks(spec_text: str, kind: str) -> list[tuple[str, str]]:
    """SEMANTICS/ORACLE 用注释标记 + haskell 围栏；其余用 ```KIND:<name> 围栏。

    旧代 ``SEMANTICS`` / ``LABEL`` 与旧 ``ORACLE:``/``ORACLE-LABEL:``/``ORACLE-ERRMAP:``
    互不吞并（前缀不同，各自独立匹配）。
    """
    if kind == "SEMANTICS":
        pattern = re.compile(
            r"<!--\s*SEMANTICS:(\S+)\s+BEGIN\s*-->\s*\n```haskell\n(.*?)```", re.DOTALL)
    elif kind == "ORACLE":
        pattern = re.compile(
            r"<!--\s*ORACLE:(\S+)\s+BEGIN\s*-->\s*\n```haskell\n(.*?)```", re.DOTALL)
    elif kind == "ORACLE-LABELS":
        pattern = re.compile(r"```ORACLE-LABEL:(\S+)\n(.*?)\n```", re.DOTALL)
    elif kind == "ORACLE-ERRMAP":
        pattern = re.compile(r"```ORACLE-ERRMAP:(\S+)\n(.*?)\n```", re.DOTALL)
    else:
        pattern = re.compile(r"```LABEL:(\S+)\n(.*?)\n```", re.DOTALL)
    return [(m.group(1), m.group(2)) for m in pattern.finditer(spec_text)]


def design_view(spec_text: str) -> str:
    """只提取 1.0 设计图景；存量未补图时保持原评审哈希。"""
    match = re.search(r"^### 1\.0 设计图景\s*$\n(.*?)(?=^#{1,3} |\Z)",
                      norm_eol(spec_text), re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def machine_hash(spec_text: str, name: str, generation: str = "Semantics") -> str:
    """单台预言机的评审哈希。一个 spec 多台机器时，改一台不得使另一台的留档过期。

    ``generation="Oracle"`` 只哈希新代围栏（``ORACLE`` + ``ORACLE-LABEL`` + ``ORACLE-ERRMAP``）：
    改旧代 ``SEMANTICS``/``LABEL`` 不得使 ORACLE 留档过期，反之亦然。缺省（旧代）逐字不变。
    """
    kinds = ("ORACLE", "ORACLE-LABELS", "ORACLE-ERRMAP") if generation == "Oracle" \
        else ("SEMANTICS", "LABELS")
    parts = []
    for kind in kinds:
        for block_name, body in extract_blocks(spec_text, kind):
            if block_name == name:
                parts.append(f"{kind}:{block_name}:{norm_eol(body).strip()}")
    view = design_view(spec_text)
    if view:
        parts.insert(0, f"DESIGN:{view}")
    return hashlib.sha256("\n---\n".join(parts).encode("utf-8")).hexdigest()[:16]


def load_review(path: pathlib.Path) -> dict | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    head, _, body = text.partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    meta["body"] = body
    return meta


def cmd_emit(spec_dir: pathlib.Path, reviews_dir: pathlib.Path, oracle: bool = False) -> int:
    spec_md = spec_dir / "spec.md"
    spec_text = norm_eol(spec_md.read_text(encoding="utf-8"))
    machine_kind = "ORACLE" if oracle else "SEMANTICS"
    label_kind = "ORACLE-LABELS" if oracle else "LABELS"
    suffix = ".oracle" if oracle else ""
    sem = extract_blocks(spec_text, machine_kind)
    lab = dict(extract_blocks(spec_text, label_kind))
    errmap = dict(extract_blocks(spec_text, "ORACLE-ERRMAP")) if oracle else {}
    if not sem:
        return fail(f"{spec_md} 无 {machine_kind} 块")
    reviews_dir.mkdir(parents=True, exist_ok=True)
    for name, code in sem:
        h = machine_hash(spec_text, name, "Oracle" if oracle else "Semantics")
        out = reviews_dir / f"{name}{suffix}.prompt.md"
        view = design_view(spec_text)
        title = (f"# 评审材料：{name}（Oracle 语义哈希 {h}）" if oracle
                 else f"# 评审材料：{name}（语义哈希 {h}）")
        body = [title, ""]
        if view:
            body += ["## 待评审的设计图景", "", "```text", view, "```", ""]
        else:
            body += ["## 设计图景缺失（存量规格待迁移；不得据此宣称 G0 通过）", ""]
        body += [
            "## 待评审的嵌入语义", "", "```haskell", code.strip(), "```", "",
            "## 待评审的标签", "",
        ]
        if name in lab:
            body += ["```", lab[name].strip(), "```", ""]
        else:
            body += ["（无标签块）", ""]
        if oracle:
            body += ["## 待评审的原始错误身份映射（ORACLE-ERRMAP）", ""]
            if name in errmap:
                body += ["```", errmap[name].strip(), "```", ""]
            else:
                body += ["（无 ERRMAP 块；驱动未收录的原始身份即 unmapped-error）", ""]
        checklist = [
            "## 评审清单", "",
            "1. 这台机器是预言机：它判定观察是否合法，还是在规定实现必须调用的函数？后者记阻断。",
            "2. 每个 state/event 的中文标签是否与该名字在语义中的角色一致（不误导）？",
            "3. 每条 note 陈述的性质是否被嵌入语义支持（给出反例即不成立）？",
            "4. 是否有语义中存在的关键行为未被标注、或被标注了语义不存在的行为？",
            "5. 图上每条可达路径的后继是否与语义一致？逐事件核对标签和每条可达迁移（含失败观察）；冲突写 BLOCKING。",
            "6. 标签是否与 spec 正文行为条款冲突？无设计图时明确记为迁移债务，不得声称 G0 已通过。",
        ]
        if oracle:
            checklist += [
                "7. ORACLE-ERRMAP 是否覆盖观察点可能写出的全部原始身份（未收录即"
                " unmapped-error）；缺项写 BLOCKING。",
                "8. 观察值是否只经 Observation 入参、模型没有自造时间/次数/错误身份？"
                "（模型自证即循环，写 BLOCKING。）",
            ]
        body += checklist + [
            "",
            "## 输出格式", "",
            "- 逐条结论（引用标签名与语义依据）；",
            f"- 存在必须修复的问题时，每条以 `{BLOCKING_PREFIX} <问题>` 单起一行；",
            "- 无阻断问题时不必写 BLOCKING 行。", "",
        ]
        out.write_text("\n".join(body), encoding="utf-8", newline="\n")
        print(f"[EMIT] 评审材料已写出：{out}")
        print(f"[EMIT] {name} 语义哈希：{h}")
        print("[EMIT] 注意：本工具只生成材料；**不**自动写留档、**不**自动判「无阻断」。")
    print(f"[EMIT] 评审后：python {pathlib.Path(__file__).name} {spec_dir}"
          + (" --oracle" if oracle else "") + " --name <机器名> --record <findings.md>")
    return 0


def cmd_record(spec_dir: pathlib.Path, reviews_dir: pathlib.Path, findings: pathlib.Path,
               model: str, name: str, oracle: bool = False) -> int:
    spec_md = spec_dir / "spec.md"
    spec_text = norm_eol(spec_md.read_text(encoding="utf-8"))
    machine_kind = "ORACLE" if oracle else "SEMANTICS"
    suffix = ".oracle" if oracle else ""
    sem = extract_blocks(spec_text, machine_kind)
    if not sem:
        return fail(f"{spec_md} 无 {machine_kind} 块")
    names = [n for n, _ in sem]
    if not name:
        if len(names) != 1:
            return fail(f"多台预言机必须指定 --name，已有：{', '.join(names)}")
        name = names[0]
    if name not in names:
        return fail(f"没有 {machine_kind}:{name}（已有：{', '.join(names)}）")
    if not findings.is_file():
        return fail(f"findings 文件不存在：{findings}")
    # 留档只由外部 findings 文本构成：本工具**不**自动生成"无阻断"结论，也不伪新鲜哈希。
    h = machine_hash(spec_text, name, "Oracle" if oracle else "Semantics")
    body = norm_eol(findings.read_text(encoding="utf-8")).strip()
    reviews_dir.mkdir(parents=True, exist_ok=True)
    out = reviews_dir / f"{name}{suffix}.review.md"
    head_lines = [f"name: {name}"]
    if oracle:
        head_lines.append("generation: Oracle")
    head_lines += [
        f"hash: {h}",
        f"model: {model or '(未标注)'}",
        f"date: {datetime.date.today().isoformat()}",
    ]
    head = "\n".join(head_lines)
    out.write_text(head + "\n---\n" + body + "\n", encoding="utf-8", newline="\n")
    print(f"[RECORD] 评审留档已写出：{out}（哈希 {h}）")
    return 0


def cmd_check(spec_dir: pathlib.Path, reviews_dir: pathlib.Path, oracle: bool = False) -> int:
    spec_md = spec_dir / "spec.md"
    spec_text = norm_eol(spec_md.read_text(encoding="utf-8"))
    machine_kind = "ORACLE" if oracle else "SEMANTICS"
    suffix = ".oracle" if oracle else ""
    sem = extract_blocks(spec_text, machine_kind)
    if not sem:
        return fail(f"{spec_md} 无 {machine_kind} 块")
    for name, _ in sem:
        h = machine_hash(spec_text, name, "Oracle" if oracle else "Semantics")
        review = load_review(reviews_dir / f"{name}{suffix}.review.md")
        if review is None:
            return fail(f"评审门失败：缺少评审留档 reviews/{name}{suffix}.review.md；"
                        f"先 --emit 生成材料、评审后 --name {name} --record 写入")
        if review.get("hash") != h:
            return fail(f"评审门失败：{name} 设计图/语义/标签已变更（当前哈希 {h}，留档 {review.get('hash')}）；"
                        f"需重新评审并 --record")
        blocking = [ln.strip() for ln in review.get("body", "").splitlines()
                    if ln.strip().startswith(BLOCKING_PREFIX)]
        if blocking:
            for ln in blocking:
                print(f"  阻断项: {ln}", file=sys.stderr)
            return fail(f"评审门失败：{name} 存在 {len(blocking)} 条未闭环阻断项")
        print(f"[PASS] 评审门 {name}（留档新鲜，哈希 {h}，无阻断项）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="core-spec 评审门（人工或 AI 辅助）")
    parser.add_argument("spec_dir")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit", action="store_true", help="生成评审材料")
    g.add_argument("--record", metavar="FINDINGS", help="写入评审留档")
    g.add_argument("--check", action="store_true", help="门：校验留档新鲜且无阻断项")
    parser.add_argument("--model", default="", help="评审所用模型名（写入留档）")
    parser.add_argument("--name", default="", help="要写入留档的预言机名；多台时必填")
    parser.add_argument("--oracle", action="store_true",
                        help="按 ORACLE 新代机器判哈希（ORACLE + ORACLE-LABEL + ORACLE-ERRMAP），"
                             "留档名带 .oracle 段；缺省仍是旧代 SEMANTICS/LABEL，两者互不覆盖")
    args = parser.parse_args(argv)

    spec_dir = (REPO_ROOT / args.spec_dir).resolve()
    reviews_dir = spec_dir / "reviews"
    if not (spec_dir / "spec.md").is_file():
        return fail(f"spec.md 不存在：{spec_dir / 'spec.md'}")

    if args.emit:
        return cmd_emit(spec_dir, reviews_dir, args.oracle)
    if args.check:
        return cmd_check(spec_dir, reviews_dir, args.oracle)
    return cmd_record(spec_dir, reviews_dir, pathlib.Path(args.record), args.model, args.name,
                      args.oracle)


if __name__ == "__main__":
    sys.exit(main())
