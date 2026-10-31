#!/usr/bin/env python3
"""只读盘点所有 core spec 的正文版本与工具模板差异。"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

TOOL_NAMES = ("audit_spec.py", "test_audit_spec.py", "barriers.py")


def fingerprint(path: pathlib.Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def audit_baseline(path: pathlib.Path) -> str:
    if not path.is_file():
        return "unmarked"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid-manifest"
    value = data.get("audit_baseline")
    return value if isinstance(value, str) and value.strip() else "unmarked"


def collect(root: pathlib.Path) -> list[dict]:
    specs_root = root / "docs" / "specs"
    templates = root / ".claude" / "skills" / "core-spec" / "templates" / "tools"
    if not specs_root.is_dir():
        raise FileNotFoundError(f"core-spec 目录不存在: {specs_root}")
    rows = []
    for spec in sorted(specs_root.iterdir()):
        if not spec.is_dir() or not (spec / "spec.md").is_file():
            continue
        text = (spec / "spec.md").read_text(encoding="utf-8")
        version = re.search(r"^\*\*版本:\*\*\s*(\d+(?:\.\d+)*(?:[-+][A-Za-z0-9.]+)?)", text, re.MULTILINE)
        status = re.search(r"^\*\*状态:\*\*\s*(Draft|Authoritative)\b", text, re.MULTILINE | re.IGNORECASE)
        view = re.search(r"^### 1\.0 设计图景[^\S\n]*\n(.*?)(?=^#{1,3} |\Z)",
                         text, re.MULTILINE | re.DOTALL)
        row = {
            "name": spec.name,
            "spec_version": version.group(1) if version else "unmarked",
            "status": status.group(1) if status else "unmarked",
            "design_view": bool(view and view.group(1).strip()),
            "manifest": (spec / "tools" / "audit_manifest.json").is_file(),
            "audit_baseline": audit_baseline(spec / "tools" / "audit_manifest.json"),
            "template_audit_baseline": audit_baseline(templates / "audit_manifest.json"),
        }
        for name in TOOL_NAMES:
            local_hash = fingerprint(spec / "tools" / name)
            template_hash = fingerprint(templates / name)
            row[name] = ("missing" if local_hash is None else
                         "template-missing" if template_hash is None else
                         "same" if local_hash == template_hash else "different")
            row[f"{name}_hash"] = local_hash
            row[f"{name}_template_hash"] = template_hash
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="只读盘点全部 core spec 的业务版本和工具基线")
    parser.add_argument("--root", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[4],
                        help="项目根目录；默认当前 skill 所在项目")
    parser.add_argument("--json", action="store_true", help="输出供后续处理的 JSON")
    args = parser.parse_args()
    try:
        rows = collect(args.root.resolve())
    except (OSError, UnicodeError) as exc:
        parser.exit(1, f"[FAIL] {exc}\n")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"[INVENTORY] {len(rows)} 个 core spec；same 仅表示字节相同，different 不表示本地工具过期")
        for row in rows:
            tools = ", ".join(f"{name}={row[name]}" for name in TOOL_NAMES)
            print(f"{row['name']}: 版本={row['spec_version']} 状态={row['status']} "
                  f"设计图={'有' if row['design_view'] else '缺'} manifest={'有' if row['manifest'] else '缺'} "
                  f"审计基线={row['audit_baseline']} 模板基线={row['template_audit_baseline']} {tools}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
