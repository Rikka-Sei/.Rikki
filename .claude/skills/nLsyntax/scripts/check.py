#!/usr/bin/env python3
"""nLsyntax 检查脚本：扫描文本中的口语化隐喻 / 模型腔表达。

用法：
  python3 check.py 文件.txt          # 检查文件
  echo "文本" | python3 check.py -   # 从标准输入检查
  python3 check.py --json 文件.txt   # 输出 JSON
  python3 check.py --table           # 打印约束表（Markdown）

退出码：0 = 无“禁用”级命中；1 = 有“禁用”级命中；2 = 参数错误。
“慎用”级只提示，需要结合上下文人工判断。
"""
import json
import re
import sys
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")


def load_rules():
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    window = data.get("window", 15)
    rules = []
    for r in data["rules"]:
        flags = re.IGNORECASE if "i" in r.get("flags", "") else 0
        r["_re"] = re.compile(r["pattern"], flags)
        r["_ex"] = [e.lower() for e in r.get("exclude", [])]
        rules.append(r)
    return rules, window


def scan(text, rules, window):
    hits = []
    lower = text.lower()
    for r in rules:
        found = []
        for m in r["_re"].finditer(text):
            s, e = m.start(), m.end()
            ctx = lower[max(0, s - window): e + window]
            if any(x in ctx for x in r["_ex"]):
                continue
            line = text.count("\n", 0, s) + 1
            snippet = text[max(0, s - 12): e + 12].replace("\n", " ")
            found.append({"line": line, "match": m.group(0), "context": snippet})
        limit = r.get("max_count")
        if limit is not None:
            if len(found) <= limit:
                continue
            found = found[limit:]  # 只报告超出次数的部分
        for f in found:
            f.update(term=r["term"], level=r["level"], category=r["category"], hint=r["hint"])
            hits.append(f)
    hits.sort(key=lambda h: (h["line"], h["level"] != "禁用"))
    return hits


def print_table(rules):
    print("| 级别 | 类别 | 词 / 句式 | 改写方向 | 不算违规的字面语境（示例） |")
    print("|---|---|---|---|---|")
    order = {"禁用": 0, "慎用": 1}
    for r in sorted(rules, key=lambda r: (r["category"], order[r["level"]])):
        ex = "、".join(r.get("exclude", [])[:6]) or "—"
        extra = f"（每段最多 {r['max_count']} 次）" if "max_count" in r else ""
        print(f"| {r['level']} | {r['category']} | {r['term']}{extra} | {r['hint']} | {ex} |")


def main(argv):
    rules, window = load_rules()
    if "--table" in argv:
        print_table(rules)
        return 0
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    src = args[0]
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    hits = scan(text, rules, window)
    if as_json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
    else:
        if not hits:
            print("未发现问题。")
        for h in hits:
            print(f"L{h['line']:<4}[{h['level']}][{h['category']}] {h['match']}  →  {h['hint']}")
            print(f"      …{h['context']}…")
        hard = sum(h["level"] == "禁用" for h in hits)
        print(f"\n合计：禁用 {hard} 处，慎用 {len(hits) - hard} 处。")
    return 1 if any(h["level"] == "禁用" for h in hits) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
