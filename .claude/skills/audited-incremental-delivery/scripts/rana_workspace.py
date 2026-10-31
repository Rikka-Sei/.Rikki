#!/usr/bin/env python3
"""创建或定位 .RānaKaname/workspaces/<slug>/progress.md。

这个脚本只负责本地状态目录，不读取也不依赖 .superpowers。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "increment-ledger.md"


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def slugify(value: str) -> str:
    value = value.replace("\\", "/").rstrip("/")
    value = value.rsplit("/", 1)[-1]
    if value.endswith(".md"):
        value = value[:-3]
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", value)
    value = value.strip("-._")
    return value or "task"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="定位当前 worktree 的 .RānaKaname 交付记录目录"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--plan", help="计划文件；用文件名作为 slug 和 owner")
    source.add_argument("--slug", help="直接用 branch 或任务名作为 slug")
    args = parser.parse_args()

    try:
        root = Path(git_output("rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"无法定位仓库根: {exc}", file=sys.stderr)
        return 2

    if args.plan:
        plan = Path(args.plan)
        if not plan.is_absolute():
            plan = (root / plan).resolve()
        owner = os.path.relpath(plan, root).replace("\\", "/")
        slug = slugify(plan.name)
    elif args.slug:
        owner = args.slug
        slug = slugify(args.slug)
    else:
        owner = git_output("rev-parse", "--abbrev-ref", "HEAD")
        slug = slugify(owner)

    workspace = root / ".RānaKaname" / "workspaces" / slug
    workspace.mkdir(parents=True, exist_ok=True)

    owner_file = workspace / "owner"
    if owner_file.exists():
        existing = owner_file.read_text(encoding="utf-8").strip()
        if existing != owner:
            print(
                f"workspace '{slug}' 已属于 '{existing}'，不能用于 '{owner}'",
                file=sys.stderr,
            )
            return 2
    else:
        owner_file.write_text(owner + "\n", encoding="utf-8")

    progress = workspace / "progress.md"
    if not progress.exists():
        if TEMPLATE.exists():
            progress.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            progress.write_text("# 增量交付记录表\n", encoding="utf-8")

    print(workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
