#!/usr/bin/env python3
"""core-spec 内嵌可执行语义屏障（共享工具，跟随 skill，禁止复制到 spec 或项目目录）。

spec.md 是唯一规格源：Haskell 代码块直接嵌在 spec 的对应章节里（SEMANTICS 标记），
本工具机械提取、编译、执行。渲染表由嵌入代码经本工具确定性生成——不经过 AI。

嵌入代码零字符串字面量（G7 硬约束）；全部格式化/解析/报告由本工具生成的临时驱动承担。

用法：
  python spec_barrier.py <spec-dir>                    # 验证（含渲染 diff）
  python spec_barrier.py <spec-dir> --update-snapshot  # 重新生成快照与渲染视图
  python spec_barrier.py <spec-dir> --allow-legacy     # 仅 legacy 证据时兼容返回 0
  python spec_barrier.py <spec-dir> --oracle           # ORACLE 新代门（ORACLE:<machine> 围栏）
  python spec_barrier.py <spec-dir> --oracle --update-snapshot

退出码：旧代 1 = 门失败；3 = 仅旧 v1 迹线证据或无迹线证据（unverified，不计入 strict 合规）。
0 仅用于快照更新成功，或显式 --allow-legacy 接受已通过的旧 v1 语料；无语料不能兼容为 0。
旧代多机器逐块校验后聚合：任一机器硬失败即返回该失败码，绝不被未验证状态掩盖。
新代（--oracle）按契约 §4.4：0 = 全部 strict pass；1 = 有 FAIL；2 = 用法错（驱动退 1）；
3 = 无 FAIL 但有 UNVERIFIED/WARN；FAIL 优先于未验证。

验证流程：
  1. 编译门：ghc -Wall -Werror=incomplete-patterns（格子闭合由编译器穷尽匹配承担）
  2. 性质门：求值嵌入代码的全局性质，全部违规列表为空
  3. 快照门：求值输出与提交快照 diff（行尾归一化）
  4. 视图门：从嵌入代码渲染状态转换表，与 spec.md 内 RENDERED 区块 diff
  5. 迹线门：traces/*.trace 语料喂给驱动迹线重放，FAIL 反例即失败

失败闭合：编译之后的每次求值都校验子进程退出码与输出（超时/无法启动/非零退出/空输出
一律判门失败，转非零退出码，不外泄异常）；语料缺失、为空或含非 TRACE 行同样直接拒绝，
不得据"没有 FAIL"外推为通过。旧 v1 迹线语料只报 legacy 能力（unverified），不作为符合声明。

前置：ghc 在 PATH。全部中间产物（编译目录、构建产物、锁文件）只落 repo/.tmp/core-spec，
不污染 spec 目录、也不落系统临时目录。写入使用临时文件 + os.replace 原子替换；
读路径对并发替换冲突重试。
ORACLE 快照存储（契约 §9）：snapshots/<machine>.oracle.snap.gz —— 确定性无损 gzip
（MTIME=0、无 FNAME、level 9）；读侧有界流式解压后按 norm_eol 语义做完整全文比较，
损坏/截断/CRC/非法 gzip/尾随数据/非 UTF-8/超上限一律硬失败退 1（绝不截断、绝不回退明文）。
"""
from __future__ import annotations

import argparse
import codecs
import contextlib
import gzip
import hashlib
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib

SKILL_TOOLS = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SKILL_TOOLS.parents[3]

# 全部中间产物（编译目录、构建产物、锁文件）只落 repo/.tmp/core-spec，
# 使临时文件集中在仓库工作区，避免污染仓库其余路径或系统临时目录。
SCRATCH_ROOT = REPO_ROOT / ".tmp" / "core-spec"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 固定驱动（skill 共享，含 IO/字符串，属工具侧）：模型侧保持纯声明。
DRIVER_PATH = SKILL_TOOLS / "driver" / "SpecDriver.hs"
# ORACLE 新代固定驱动（契约 §9）：模块名 Oracle，标签模块 Driver/Oracle/Labels.hs 由本工具生成。
ORACLE_DRIVER_PATH = SKILL_TOOLS / "driver" / "SpecOracle.hs"


def fail(msg: str) -> int:
    print(f"[FAIL] {msg}", file=sys.stderr)
    return 1


class GateError(Exception):
    """受控门失败：driver 非零退出/超时/空输出等，统一转 fail()，不外泄栈。"""


class OracleGateError(Exception):
    """ORACLE 新代门失败（编译/求值/语料/围栏）：统一转 fail()，不落半写。"""


# 默认 strict 下"仅旧 v1 迹线证据"的专用状态码：必须非 0（消费方 barriers.py 只看退出码，
# rc 0 会被记成 [PASS]），且必须与门失败 1 区分（未验证不是失败）。
# 只有显式兼容（CLI --allow-legacy / check_semantics(allow_legacy=True)）才降级为 0。
LEGACY_UNVERIFIED_RC = 3

# ORACLE 新代：缺视图锚点（RENDERED-ORACLE 未落地）或缺 ORACLE 评审留档时，本次结论
# 只能是"未验证"——必须非 0（否则会被消费方记成 [PASS]），且与门失败 1 区分。
ORACLE_UNVERIFIED_RC = 3


# ---------------------------------------------------------------------------
# 并发保护（多进程同时运行本工具时安全）
#   1. 写路径（--update-snapshot）持跨进程互斥锁串行化；
#   3. 读路径对并发替换引发的短暂共享冲突重试。
# 锁文件放 repo/.tmp/core-spec（按 spec 路径派生名字），不污染仓库其余路径。
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def update_lock(spec_dir: pathlib.Path, timeout: float = 120.0):
    key = hashlib.sha1(str(spec_dir).encode("utf-8")).hexdigest()[:16]
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = SCRATCH_ROOT / f"barrier-{key}.lock"
    deadline = time.monotonic() + timeout
    fd = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > timeout:          # 崩溃残留的陈旧锁：清理后重试
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"等待更新锁超时：{lock_path}")
            time.sleep(0.1)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except OSError:
            pass


def atomic_write_text(path: pathlib.Path, content: str) -> None:
    """原子写：同目录临时文件 + os.replace；并发读者只见旧完整版或新完整版。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_text_retry(path: pathlib.Path, attempts: int = 20) -> str:
    """读文本；并发替换可能短暂触发共享冲突，重试即可。"""
    last: OSError | None = None
    for _ in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:      # PermissionError / 文件瞬时不可读
            last = exc
            time.sleep(0.05)
    raise last if last else OSError(f"无法读取：{path}")


def _stream_text(data: str | bytes | None) -> str:
    """把 TimeoutExpired 携带的 stdout/stderr 统一成 UTF-8 文本（缺失时为空串）。"""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data


def run(cmd: list[str], cwd: pathlib.Path, stdin_text: str | None = None,
        timeout: float = 600.0) -> subprocess.CompletedProcess:
    """跑一条命令。超时/无法启动都转成非零退出码，绝不外泄异常。"""
    print(f"[CMD] {' '.join(cmd)}" + (" < stdin" if stdin_text is not None else ""), flush=True)
    try:
        completed = subprocess.run(
            cmd, cwd=cwd, check=False, timeout=timeout,
            input=stdin_text,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        partial_out = _stream_text(exc.stdout)
        partial_err = _stream_text(exc.stderr)   # 超时前写下的诊断原文要留得住
        print(f"[FAIL] 命令超时（{timeout:g}s）：{' '.join(cmd)}", file=sys.stderr, flush=True)
        for chunk in (partial_out, partial_err):
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
        note = f"timeout after {timeout:g}s"
        return subprocess.CompletedProcess(
            cmd, 124, partial_out, f"{note}\n{partial_err}".rstrip() if partial_err else note)
    except OSError as exc:
        print(f"[FAIL] 命令无法启动：{' '.join(cmd)}：{exc}", file=sys.stderr, flush=True)
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    if completed.stderr:                   # 成功也可能有 warning（如 ghc -Wall）：原文可见
        sys.stdout.write(completed.stderr)
        sys.stdout.flush()
    if completed.stdout and completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    return completed


def norm_eol(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def prop_lines(text: str) -> list[str]:
    """快照输出里的性质行（``PROP …``）。"""
    return [ln for ln in text.splitlines() if ln.startswith("PROP ")]


def trace_corpus_problem(trace_text: str) -> str | None:
    """迹线语料形状检查：旧驱动对无法识别的行静默返回空输出，必须在此挡住。

    返回问题描述；None 表示语料形状可核（逐行交给驱动重放）。
    """
    rows = [ln for ln in norm_eol(trace_text).splitlines() if ln.strip()]
    if not rows:
        return "语料为空"
    stray = [ln for ln in rows if not ln.startswith("TRACE ")]
    if stray:
        return f"存在非 TRACE 行（{len(stray)} 行，如 {stray[0].strip()[:60]!r}）"
    return None


def extract_labels(spec_text: str, name: str) -> dict:
    """提取 ```LABEL:<name> 围栏：state/event/note 三类键值（工具侧领域标签）。"""
    m = re.search(r"```LABEL:" + re.escape(name) + r"\n(.*?)\n```", spec_text, re.DOTALL)
    out = {"state": [], "event": [], "note": [], "detail": []}
    if not m:
        return out
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        parts = key.split()
        if len(parts) != 2 or parts[0] not in out:
            continue
        out[parts[0]].append((parts[1].strip(), val.strip()))
    return out


def write_labels_module(tmp_path: pathlib.Path, labels: dict) -> None:
    """生成 Labels.hs（工具侧数据模块；驱动 import）。无标签时生成空列表。"""
    def pairs(key):
        out = []
        for k, v in labels.get(key, []):
            esc = v.replace("\\", "\\\\").replace('"', '\\"')
            out.append('("%s","%s")' % (k, esc))
        return ", ".join(out)
    code = (
        "module Labels (stateLabels, eventLabels, notes, detailLabels) where\n\n"
        "stateLabels :: [(String, String)]\n"
        "stateLabels = [" + pairs("state") + "]\n\n"
        "eventLabels :: [(String, String)]\n"
        "eventLabels = [" + pairs("event") + "]\n\n"
        "notes :: [(String, String)]\n"
        "notes = [" + pairs("note") + "]\n\n"
        "detailLabels :: [(String, String)]\n"
        "detailLabels = [" + pairs("detail") + "]\n"
    )
    (tmp_path / "Labels.hs").write_text(code, encoding="utf-8", newline="\n")


def extract_semantics(spec_text: str) -> list[tuple[str, str]]:
    """从 spec.md 提取嵌入 Haskell 代码块：返回 (名称, 代码) 列表。"""
    pattern = re.compile(
        r"<!--\s*SEMANTICS:(\S+)\s+BEGIN\s*-->\s*\n"
        r"```haskell\s*\n(.*?)```",
        re.DOTALL,
    )
    return [(m.group(1), m.group(2)) for m in pattern.finditer(spec_text)]


# ---------------------------------------------------------------------------
# ORACLE 新代围栏提取（契约 §2.3 / §6 / §9）：与旧代 SEMANTICS / LABEL 分离。
#   * extract_oracles       -> `<!-- ORACLE:<m> BEGIN -->` … ```haskell…``` … ORACLE:<m> END
#   * extract_oracle_labels -> ```ORACLE-LABEL:<m>   state/event/detail/note
#   * extract_errmap        -> ```ORACLE-ERRMAP:<m>   raw <原始身份> = <Err 构造子>
# 三类围栏互不吞并；结构非法一律 ValueError（不静默丢弃）。
# ---------------------------------------------------------------------------

_ORACLE_MARKER_RE = re.compile(r"<!--\s*ORACLE:(\S+)\s+(BEGIN|END)\s*-->")
_ORACLE_CODE_RE = re.compile(r"\s*\n```haskell\s*\n(.*?)```", re.DOTALL)
_ORACLE_LABEL_KEYS = ("state", "event", "detail", "note")


def _oracle_fence(name: str, kind: str) -> re.Pattern:
    return re.compile(r"```" + re.escape(kind) + r":" + re.escape(name) + r"\n(.*?)\n```",
                      re.DOTALL)


def extract_oracles(spec_text: str) -> list[tuple[str, str]]:
    """提取 ``<!-- ORACLE:<machine> BEGIN -->`` … ```haskell … ``` … ``ORACLE:<machine> END``。

    只认 ORACLE 围栏（``extract_semantics`` 只认 SEMANTICS，两者互不吞并）；BEGIN/END
    必须成对且同名，同名 machine 出现两次即报错。结构非法一律 ``ValueError``。
    """
    markers = list(_ORACLE_MARKER_RE.finditer(spec_text))
    if len(markers) % 2 != 0:
        last = markers[-1]
        raise ValueError(
            f"ORACLE:{last.group(1)} {last.group(2)} 未配对：围栏必须 BEGIN/END 闭合")
    blocks: list[tuple[str, str]] = []
    seen: set[str] = set()
    for idx in range(0, len(markers), 2):
        begin, end = markers[idx], markers[idx + 1]
        name = begin.group(1)
        if begin.group(2) != "BEGIN":
            raise ValueError(f"ORACLE:{name} 的 END 先于 BEGIN：围栏未闭合")
        if end.group(2) != "END":
            raise ValueError(
                f"ORACLE:{name} 的 BEGIN 未闭合（下一个标记是 BEGIN:{end.group(1)}）")
        if end.group(1) != name:
            raise ValueError(f"ORACLE BEGIN({name}) 与 END({end.group(1)}) 名称不符")
        if name in seen:
            raise ValueError(f"重复的 ORACLE 机器：{name}")
        seen.add(name)
        code_m = _ORACLE_CODE_RE.match(spec_text, begin.end())
        if code_m is None or code_m.end() > end.start():
            raise ValueError(f"ORACLE:{name} 的 BEGIN 之后未紧随 ```haskell 代码块")
        blocks.append((name, code_m.group(1)))
    return blocks


def extract_oracle_labels(spec_text: str, name: str) -> dict:
    """提取 ```ORACLE-LABEL:<name> 围栏：state/event/detail/note 四类 ``(token, 说明)``。

    结构非法（缺 ``=``、键名不在四类、或键不是「类别 + 名字」两段）即 ``ValueError``；
    围栏缺席返回四类空桶（完整性由 render 门判定，不在提取层宽判）。
    """
    out: dict[str, list[tuple[str, str]]] = {k: [] for k in _ORACLE_LABEL_KEYS}
    m = _oracle_fence(name, "ORACLE-LABEL").search(spec_text)
    if not m:
        return out
    for lineno, raw_line in enumerate(m.group(1).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"ORACLE-LABEL:{name} 第 {lineno} 行缺少 '='：{line!r}")
        key, _, val = line.partition("=")
        parts = key.split()
        if len(parts) != 2 or parts[0] not in out:
            raise ValueError(
                f"ORACLE-LABEL:{name} 第 {lineno} 行键须为 state/event/detail/note + 名字：{line!r}")
        out[parts[0]].append((parts[1].strip(), val.strip()))
    return out


def extract_errmap(spec_text: str, name: str) -> list[tuple[str, str]]:
    """提取 ```ORACLE-ERRMAP:<name>：逐行 ``raw <原始身份> = <Err 构造子>``。

    同一 raw 冲突（映射到不同 Err）即 ``ValueError``；同 raw 同值去重。结构非法即报错。
    """
    out: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    m = _oracle_fence(name, "ORACLE-ERRMAP").search(spec_text)
    if not m:
        return out
    for lineno, raw_line in enumerate(m.group(1).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("raw ") or " = " not in line:
            raise ValueError(
                f"ORACLE-ERRMAP:{name} 第 {lineno} 行须形如 'raw <身份> = <Err>'：{line!r}")
        raw, _, err = line[len("raw "):].partition(" = ")
        raw, err = raw.strip(), err.strip()
        if not raw or not err:
            raise ValueError(f"ORACLE-ERRMAP:{name} 第 {lineno} 行 raw/Err 为空：{line!r}")
        if raw in seen:
            if seen[raw] != err:
                raise ValueError(
                    f"ORACLE-ERRMAP:{name} 冲突：{raw} 已映射 {seen[raw]}，又映射 {err}")
            continue
        seen[raw] = err
        out.append((raw, err))
    return out


def _hs_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def write_oracle_labels_module(tmp_path: pathlib.Path, labels: dict,
                               errmap: list[tuple[str, str]]) -> None:
    """生成 ``Driver/Oracle/Labels.hs``（工具侧数据模块；固定驱动 import）。

    与旧代 ``Labels.hs`` 互不覆盖：模块名 ``Driver.Oracle.Labels``，五个 ``[(String,String)]``
    表（stateLabels/eventLabels/notes/detailLabels/rawErrors）。
    """
    def block(field: str, entries: list[tuple[str, str]]) -> str:
        cells = ", ".join('("%s","%s")' % (_hs_escape(k), _hs_escape(v)) for k, v in entries)
        return "%s :: [(String, String)]\n%s = [%s]\n" % (field, field, cells)

    code = (
        "module Driver.Oracle.Labels\n"
        "  ( stateLabels, eventLabels, notes, detailLabels, rawErrors ) where\n\n"
        + block("stateLabels", labels.get("state", [])) + "\n"
        + block("eventLabels", labels.get("event", [])) + "\n"
        + block("notes", labels.get("note", [])) + "\n"
        + block("detailLabels", labels.get("detail", [])) + "\n"
        + block("rawErrors", errmap)
    )
    target = tmp_path / "Driver" / "Oracle"
    target.mkdir(parents=True, exist_ok=True)
    (target / "Labels.hs").write_text(code, encoding="utf-8", newline="\n")


class CorespecImportError(Exception):
    """corespec import 无法解析。这是驱动语法，不是 Haskell 错误。"""


# 行首的 corespec import 不是合法 Haskell。驱动在交给 ghc 之前改写。
# 形式：corespec import <docs/specs 下的目录名> <SEMANTICS 名> as <模块别名>
_CORESPEC_IMPORT_RE = re.compile(
    r"^[ \t]*corespec import[ \t]+(\S+)[ \t]+(\S+)[ \t]+as[ \t]+([A-Z][A-Za-z0-9_]*)[ \t]*$",
    re.MULTILINE,
)


def spec_md_path(spec_name: str) -> pathlib.Path:
    """spec 目录名对应 docs/specs/<name>/spec.md。目录名可含 @。"""
    if not spec_name or spec_name in (".", "..") or "/" in spec_name or "\\" in spec_name:
        raise CorespecImportError(f"非法 spec 名：{spec_name!r}")
    return REPO_ROOT / "docs" / "specs" / spec_name / "spec.md"


def load_semantics_block(spec_name: str, sem_name: str) -> str:
    path = spec_md_path(spec_name)
    if not path.is_file():
        raise CorespecImportError(f"找不到 spec：{path}")
    blocks = dict(extract_semantics(read_text_retry(path)))
    if sem_name not in blocks:
        raise CorespecImportError(
            f"{spec_name} 没有 SEMANTICS:{sem_name}（已有：{', '.join(blocks) or '无'}）"
        )
    return blocks[sem_name]


def load_oracle_block(spec_name: str, machine: str) -> str:
    """新代：取对方 spec 的 ``ORACLE:<machine>`` 块（``module Oracle``）。"""
    path = spec_md_path(spec_name)
    if not path.is_file():
        raise CorespecImportError(f"找不到 spec：{path}")
    try:
        blocks = dict(extract_oracles(read_text_retry(path)))
    except ValueError as exc:
        raise CorespecImportError(f"{spec_name} 的 ORACLE 围栏非法：{exc}") from exc
    if machine not in blocks:
        raise CorespecImportError(
            f"{spec_name} 没有 ORACLE:{machine}（已有：{', '.join(blocks) or '无'}）"
        )
    return blocks[machine]


def rewrite_as_module(code: str, module_name: str, expected: str = "Semantics") -> str:
    """把嵌入块的 ``module <expected>`` 改成导入别名。缺声明则失败。"""
    pattern = re.compile(r"^module\s+" + re.escape(expected) + r"\b", re.MULTILINE)
    if not pattern.search(code):
        raise CorespecImportError(f"被导入块没有 module {expected}，不能改名为 {module_name}")
    return pattern.sub(f"module {module_name}", code, count=1)


def resolve_corespec_imports(code: str, stack: tuple[str, ...] = (),
                             *, module_name: str = "Semantics"
                             ) -> tuple[str, list[tuple[str, str]]]:
    """把 corespec import 行改写成 import qualified，并返回要一并编译的 (别名, 源码)。

    stack 元素是 ``spec/machine``，用于拒绝环。被导入块里的 corespec import 递归展开。
    ``module_name="Semantics"``（缺省、旧代）取 SEMANTICS 块并按 ``module Semantics`` 改名；
    ``module_name="Oracle"``（新代）取对方 ``ORACLE`` 块并按 ``module Oracle`` 改名。
    """
    modules: list[tuple[str, str]] = []
    seen_alias: dict[str, str] = {}
    loader = load_oracle_block if module_name == "Oracle" else load_semantics_block

    def walk(raw: str, walk_stack: tuple[str, ...]) -> str:
        def replace(match: re.Match) -> str:
            spec_name, sem_name, alias = match.group(1), match.group(2), match.group(3)
            key = f"{spec_name}/{sem_name}"
            if key in walk_stack:
                cycle = " -> ".join((*walk_stack, key))
                raise CorespecImportError(f"corespec import 成环：{cycle}")
            if alias in seen_alias and seen_alias[alias] != key:
                raise CorespecImportError(
                    f"别名 {alias} 已用于 {seen_alias[alias]}，不能再指向 {key}"
                )
            seen_alias[alias] = key
            imported = loader(spec_name, sem_name)
            rewritten = rewrite_as_module(imported, alias, module_name)
            rewritten = walk(rewritten, walk_stack + (key,))
            modules.append((alias, rewritten))
            return f"import qualified {alias}"

        return _CORESPEC_IMPORT_RE.sub(replace, raw)

    root = walk(code, stack)
    return root, modules




def check_semantics(spec_dir: pathlib.Path, name: str, code: str, update_snapshot: bool,
                    allow_legacy: bool = False) -> int:
    """校验一台机器（一个 SEMANTICS 块）。

    返回 1=门失败；3=仅旧 v1 迹线证据或无迹线证据（未验证）。
    快照更新成功返回 0；allow_legacy 仅使有合法旧语料的检查返回 0，仍标注 [LEGACY]。
    """
    traces_dir = spec_dir / "traces"
    snapshot_dir = spec_dir / "snapshots"
    snap_path = snapshot_dir / f"{name}.snap"

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sem-", dir=SCRATCH_ROOT) as tmp:
        tmp_path = pathlib.Path(tmp)
        build_dir = tmp_path / "build"
        exe = tmp_path / "spec-driver.exe"

        try:
            root_code, imported = resolve_corespec_imports(
                code, stack=(f"{spec_dir.name}/{name}",)
            )
        except CorespecImportError as exc:
            return fail(f"corespec import：{exc}")
        sem_file = tmp_path / "Semantics.hs"
        sem_file.write_text(norm_eol(root_code), encoding="utf-8", newline="\n")
        for alias, imported_code in imported:
            (tmp_path / f"{alias}.hs").write_text(norm_eol(imported_code), encoding="utf-8", newline="\n")
        if imported:
            print(
                f"[IMPORT] {name} 展开 {', '.join(alias for alias, _ in imported)}",
                flush=True,
            )
        write_labels_module(tmp_path, extract_labels(read_text_retry(spec_dir / "spec.md"), name))

        # 门 1：编译门（固定驱动 + 提取出的 Semantics 模块）
        print(f"[BARRIER] 编译门 {name}", flush=True)
        r = run([
            "ghc", "-Wall", "-Werror=incomplete-patterns",
            "-i" + str(tmp_path),
            "-i" + str(DRIVER_PATH.parent),
            "-outputdir", str(build_dir),
            "-o", str(exe), str(DRIVER_PATH),
        ], cwd=REPO_ROOT)
        if r.returncode != 0:
            return fail(f"编译门失败：{name}（含穷尽匹配检查）")

        def eval_sem(mode: str, stdin_text: str | None = None, allow_empty: bool = False) -> str:
            """求值嵌入代码；进程非零退出/超时/（默认）空输出都是受控门失败。"""
            completed = run([str(exe), mode], cwd=REPO_ROOT, stdin_text=stdin_text)
            out = completed.stdout or ""
            if completed.returncode != 0:
                raise GateError(f"{mode} 进程退出码 {completed.returncode}（stdout {len(out)} 字节）")
            if not out.strip() and not allow_empty:
                raise GateError(f"{mode} 无输出")
            return out

        try:
            snapshot_text = eval_sem("snapshot")
        except GateError as exc:
            return fail(f"求值门失败：{name} snapshot 不可用（{exc}）")

        if update_snapshot:
            # 两个产物都先判 valid，再在锁内原子落盘：render 崩了不留半写快照。
            if not prop_lines(snapshot_text):
                return fail(f"快照更新失败：{name} snapshot 输出无 PROP 行（未落盘）")
            try:
                rendered = eval_sem("render")
            except GateError as exc:
                return fail(f"快照更新失败：{name} render 不可用（{exc}）（未落盘）")
            with update_lock(spec_dir):
                atomic_write_text(snap_path, norm_eol(snapshot_text))
                print(f"[UPDATE] 快照已更新：{snap_path}", flush=True)
                vr = update_rendered_fence(spec_dir / "spec.md", name, rendered)
                if vr != 0:
                    return vr
            return 0

        # 门 2：性质门
        print(f"[BARRIER] 性质门 {name}", flush=True)
        props = prop_lines(snapshot_text)
        if not props:
            return fail(f"性质门失败：{name} 快照输出中无 PROP 行")
        prop_failures = [ln for ln in props if "|FAIL|" in ln]
        if prop_failures:
            for ln in prop_failures:
                print(f"  反例: {ln}", flush=True)
            return fail(f"性质门失败：{name} 存在 FAIL 性质")
        print(f"[PASS] 性质门（{len(props)} 条性质全 PASS）", flush=True)

        # 门 3：快照门
        print(f"[BARRIER] 快照门 {name}", flush=True)
        if not snap_path.is_file():
            return fail(f"已提交快照缺失：{snap_path}（先运行 --update-snapshot）")
        if norm_eol(read_text_retry(snap_path)) != norm_eol(snapshot_text):
            return fail(
                f"快照漂移：{snap_path.name} 与嵌入代码重新求值输出不一致；"
                f"以嵌入代码为准修正 spec，或运行 --update-snapshot"
            )
        print("[PASS] 快照门（快照一致）", flush=True)

        # 门 4：视图门
        print(f"[BARRIER] 视图门 {name}", flush=True)
        try:
            rendered = eval_sem("render")
        except GateError as exc:
            return fail(f"视图门失败：{name} render 不可用（{exc}）")
        vr = check_rendered_fence(spec_dir / "spec.md", name, rendered)
        if vr != 0:
            return vr

        # 门 5：迹线门（旧 v1 语料：可核，但只证明 legacy 能力）
        machine_traces_dir = traces_dir / name
        traces = sorted(machine_traces_dir.glob("*.trace")) if machine_traces_dir.is_dir() else (sorted(traces_dir.glob("*.trace")) if traces_dir.is_dir() else [])
        if not traces:
            print(f"[WARN] 迹线门跳过：{machine_traces_dir} 无语料", flush=True)
        for trace in traces:
            print(f"[BARRIER] 迹线门 {name} trace {trace.name}", flush=True)
            trace_text = read_text_retry(trace)
            problem = trace_corpus_problem(trace_text)
            if problem:
                return fail(f"迹线门失败：{trace.name} 语料不可核（{problem}）")
            try:
                # 合法迹线零违规时驱动输出为空，这是唯一允许空输出的模式。
                out = eval_sem("tracecheck", norm_eol(trace_text), allow_empty=True)
            except GateError as exc:
                return fail(f"迹线门失败：{trace.name}（{exc}）")
            lines = [ln for ln in out.splitlines() if ln.startswith(("FAIL ", "WARN "))]
            for ln in lines:
                print(f"  {ln}", flush=True)
            if any(ln.startswith("FAIL ") for ln in lines):
                return fail(f"迹线门失败：{trace.name} 与嵌入语义不一致")
            print(f"[PASS] 迹线门 {trace.name}", flush=True)

    # SEMANTICS 只验证旧 v1 语料；无语料和 legacy 都不能声明 strict 通过。
    if not traces:
        print(
            f"[UNVERIFIED] 嵌入语义 {name}：没有可核验的迹线语料"
            f"（退出码 {LEGACY_UNVERIFIED_RC}；--allow-legacy 不适用于无语料）",
            flush=True,
        )
        return LEGACY_UNVERIFIED_RC
    if allow_legacy:
        print(
            f"[LEGACY] 嵌入语义 {name}：仅旧 v1 迹线语料核验通过"
            f"（unverified；allow_legacy 兼容返回 0，不计入 strict 合规）",
            flush=True,
        )
        return 0
    print(
        f"[LEGACY] 嵌入语义 {name}：仅旧 v1 迹线语料核验通过"
        f"（unverified，不计入 strict 合规；退出码 {LEGACY_UNVERIFIED_RC}，--allow-legacy 可兼容返回 0）",
        flush=True,
    )
    return LEGACY_UNVERIFIED_RC


def _fence_pattern(name: str) -> re.Pattern:
    return re.compile(
        r"<!--\s*RENDERED:" + re.escape(name) + r"\s+BEGIN\s*-->\s*\n```mermaid\n(.*?)\n?```\s*\n?<!--\s*RENDERED:"
        + re.escape(name) + r"\s+END\s*-->",
        re.DOTALL)


def check_rendered_fence(spec_md: pathlib.Path, name: str, rendered: str) -> int:
    """视图门：spec.md 内 ```RENDERED::<name> 围栏内容与驱动 render 输出 diff。"""
    if not spec_md.is_file():
        return fail(f"spec.md 不存在：{spec_md}")
    m = _fence_pattern(name).search(read_text_retry(spec_md))
    if m is None:
        print(f"[WARN] 视图门跳过：{spec_md.name} 无 RENDERED:{name} 锚点区块", flush=True)
        return 0
    if m.group(1).strip() != rendered.strip():
        return fail(
            f"视图漂移：spec.md 的 RENDERED:{name} 区块与驱动 render 输出不一致；"
            f"运行 --update-snapshot 重新渲染"
        )
    print("[PASS] 视图门（RENDERED 围栏一致）", flush=True)
    return 0


def update_rendered_fence(spec_md: pathlib.Path, name: str, rendered: str) -> int:
    """把驱动 render 输出写回 spec.md 的 ```RENDERED::<name> 围栏。"""
    if not spec_md.is_file():
        return fail(f"spec.md 不存在：{spec_md}")
    text = read_text_retry(spec_md)
    pat = _fence_pattern(name)
    if pat.search(text) is None:
        print(f"[SKIP] spec.md 无 RENDERED:{name} 锚点区块，跳过视图更新", flush=True)
        return 0
    repl = ("<!-- RENDERED:" + name + " BEGIN -->\n```mermaid\n" + rendered
            + "\n```\n<!-- RENDERED:" + name + " END -->")
    new_text = pat.sub(lambda _m: repl, text, count=1)
    atomic_write_text(spec_md, new_text)
    print(f"[UPDATE] 视图已更新：RENDERED::{name}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# ORACLE 新代门（契约 §7 §9）：与旧代 RENDERED 围栏分离，用 RENDERED-ORACLE:<name>。
# ---------------------------------------------------------------------------

def _oracle_fence_pattern(name: str) -> re.Pattern:
    return re.compile(
        r"<!--\s*RENDERED-ORACLE:" + re.escape(name) + r"\s+BEGIN\s*-->\s*\n```mermaid\n(.*?)"
        r"\n?```\s*\n?<!--\s*RENDERED-ORACLE:" + re.escape(name) + r"\s+END\s*-->",
        re.DOTALL)


def check_oracle_rendered_fence(spec_md: pathlib.Path, name: str, rendered: str) -> int:
    """ORACLE 视图门：RENDERED-ORACLE:<name> 围栏与驱动 render diff。

    返回 0=一致；1=漂移（门失败）；``ORACLE_UNVERIFIED_RC``=无锚点跳过——**跳过不等于通过**，
    本次不得判 strict（视图门没有被执行，就不能声称它绿）。
    """
    if not spec_md.is_file():
        return fail(f"spec.md 不存在：{spec_md}")
    m = _oracle_fence_pattern(name).search(read_text_retry(spec_md))
    if m is None:
        print(f"[WARN] ORACLE 视图门跳过：{spec_md.name} 无 RENDERED-ORACLE:{name} 锚点区块"
              f"（本次非 strict）", flush=True)
        return ORACLE_UNVERIFIED_RC
    if m.group(1).strip() != rendered.strip():
        return fail(
            f"ORACLE 视图漂移：spec.md 的 RENDERED-ORACLE:{name} 区块与驱动 render 输出不一致；"
            f"运行 --oracle --update-snapshot 重新渲染"
        )
    print("[PASS] ORACLE 视图门（RENDERED-ORACLE 围栏一致）", flush=True)
    return 0


def update_oracle_rendered_fence(spec_md: pathlib.Path, name: str, rendered: str) -> int:
    """把驱动 render 输出写回 spec.md 的 ```RENDERED-ORACLE:<name> 围栏。"""
    if not spec_md.is_file():
        return fail(f"spec.md 不存在：{spec_md}")
    text = read_text_retry(spec_md)
    pat = _oracle_fence_pattern(name)
    if pat.search(text) is None:
        print(f"[SKIP] spec.md 无 RENDERED-ORACLE:{name} 锚点区块，跳过视图更新", flush=True)
        return 0
    repl = ("<!-- RENDERED-ORACLE:" + name + " BEGIN -->\n```mermaid\n" + rendered
            + "\n```\n<!-- RENDERED-ORACLE:" + name + " END -->")
    atomic_write_text(spec_md, pat.sub(lambda _m: repl, text, count=1))
    print(f"[UPDATE] ORACLE 视图已更新：RENDERED-ORACLE::{name}", flush=True)
    return 0


# ORACLE 门：驱动退出码约定（契约 §4.4）与屏障映射。
#   驱动 0=全 PASS、1=用法错、2=有 FAIL、3=有 UNVERIFIED/WARN
#   屏障 0=全部 strict pass、1=有 FAIL、2=用法错、3=无 FAIL 但有 UNVERIFIED/WARN
DRIVER_OK, DRIVER_USAGE, DRIVER_FAIL, DRIVER_UNVERIFIED = 0, 1, 2, 3
ORACLE_USAGE_RC = 2

# 驱动输出行分类：只有"真" FAIL 与"真" WARN/UNVERIFIED 参与判定。
# ``WITNESS DEFERRED``、graph 自身 ``STRICT no``、partial coverage/BOUND/COVERAGE 之类
# 报告行都是**中性**标记，不得据此判非绿（契约 §4.4）。
_ORACLE_FAIL_PREFIX = ("FAIL ", "OBLIGFAIL ", "DEADEND ", "INV ")
_WITNESS_DEFERRED_RE = re.compile(r"WITNESS\s+DEFERRED\s+(\d+)")


def _oracle_line_class(text: str) -> tuple[bool, bool]:
    """返回 (有 FAIL 行, 有真 WARN/UNVERIFIED 行)；中性标记不计入。"""
    fail = unverified = False
    for raw in norm_eol(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(_ORACLE_FAIL_PREFIX) or "|FAIL|" in line:
            fail = True
        elif line.startswith(("WARN", "UNVERIFIED")):
            unverified = True
    return fail, unverified


def _oracle_map_rc(rc: int, out: str, *, neutral_when_unverified: bool = False,
                   classify_lines: bool = True) -> int:
    """驱动 (退出码, stdout) → 屏障判定码（0/1/2/3）。

    FAIL 优先：有 FAIL 行或驱动 2 → 1；驱动 1（用法错）→ 2；真 WARN/UNVERIFIED 行 → 3。
    驱动 3 时要看行分类：``neutral_when_unverified``（``graph``）下若只有中性标记
    （`WITNESS DEFERRED` / 自身 `STRICT no` / partial coverage）则**中性**，不判非绿；
    其它模式或出现真 WARN/UNVERIFIED 行时→ 3。超时（124）/无法启动（127）→ 1。

    ``classify_lines=False``（快照/视图）：行语义由各自的门承担（如 ``PROP X|FAIL`` 由性质门
    打印反例），此处只看退出码，避免抢走更精确的诊断。
    """
    fail, unverified = _oracle_line_class(out) if classify_lines else (False, False)
    if fail or rc == DRIVER_FAIL:
        return 1
    if rc == DRIVER_USAGE:
        return ORACLE_USAGE_RC
    if unverified:
        return ORACLE_UNVERIFIED_RC
    if rc == DRIVER_UNVERIFIED:
        return 0 if neutral_when_unverified else ORACLE_UNVERIFIED_RC
    if rc == DRIVER_OK:
        return 0
    return 1


def _oracle_run_rc(r, *, mode: str, require_output: bool = True,
                   neutral_when_unverified: bool = False,
                   classify_lines: bool = True) -> int:
    """一次驱动求值的屏障判定码。退出 0 但空输出仍按失败闭合（1）。

    非零退出**不**当异常：保留原始 stdout/退出码后分类（契约 §4.4 的退出码映射）。
    """
    out = r.stdout or ""
    if r.returncode == DRIVER_OK and require_output and not out.strip():
        print(f"[FAIL] ORACLE {mode} 退出 0 但无输出（失败闭合）", file=sys.stderr, flush=True)
        return 1
    mapped = _oracle_map_rc(r.returncode, out, neutral_when_unverified=neutral_when_unverified,
                            classify_lines=classify_lines)
    if mapped == ORACLE_USAGE_RC:
        print(f"[USAGE] ORACLE {mode} 用法错：驱动退出码 {r.returncode}（屏障退 {ORACLE_USAGE_RC}）",
              file=sys.stderr, flush=True)
    return mapped


def oracle_witness_count(graph_text: str) -> int | None:
    """从 ``graph`` 输出解析 ``requiredWitnesses`` 声明条数（**唯一**来源，不查源文本）。

    契约 §4.4：``graph`` **必须无条件**输出 ``WITNESS DEFERRED <n>``（``n`` 可为 0）。
    屏障**只**认这一行；``snapshot`` 的 ``WITNESS …`` 行（若将来有）仅作附加报告，
    既不首选也不回退——避免两处来源互相兜底而掩盖声明错误。

    读不出该行返回 ``None``：调用方按 fail-closed 视作 ``n > 0``（不得当作 0 而误判通过）。
    """
    m = _WITNESS_DEFERRED_RE.search(norm_eol(graph_text))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# ORACLE 快照存储（契约 §9）：``snapshots/<machine>.oracle.snap.gz``——确定性无损 gzip。
#   * 编码：MTIME=0、无 FNAME/注释等非确定字段、level 9 —— 同一全文 → 同一字节；
#   * 读侧：有界流式解压（防 gzip 炸弹）+ 严格 UTF-8 + ``norm_eol`` 语义的**完整全文**比较
#     （不存 hash、不抽样、不做前缀/长度近似）；
#   * 损坏 / 截断 / CRC 不符 / 非法 gzip / 尾随非 gzip 内容 / 非 UTF-8 / 超上限：
#     一律硬失败退 1（不得退 3 冒充未验证、不得退 0 伪绿、**不得截断当通过**）；
#   * 旧明文 ``.oracle.snap`` 不作验收来源（fail-closed），检查门也不就地迁移；
#     迁移只发生在 ``--oracle --update-snapshot``：原子替换成功且预校验通过后才删明文。
# ---------------------------------------------------------------------------

ORACLE_SNAP_GZ_SUFFIX = ".oracle.snap.gz"
ORACLE_SNAP_PLAIN_SUFFIX = ".oracle.snap"
# 解压后字节上限：对单个快照工件设置宽裕且有限的上限，超限硬失败。
ORACLE_SNAP_MAX_BYTES = 512 * 1024 * 1024
# 落盘 gz 字节上限：对压缩工件设置独立上限，作为解压上限之外的第二道闸。
ORACLE_SNAP_ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
_GZ_CHUNK_BYTES = 1 << 20


class OracleSnapshotError(Exception):
    """快照存储不可信（缺失/损坏/超限/非 UTF-8）：转 fail() 硬失败，绝不静默降级。"""


def oracle_snap_gz_path(spec_dir: pathlib.Path, name: str) -> pathlib.Path:
    """新代存储路径：``snapshots/<machine>.oracle.snap.gz``。"""
    return spec_dir / "snapshots" / f"{name}{ORACLE_SNAP_GZ_SUFFIX}"


def oracle_snap_plain_path(spec_dir: pathlib.Path, name: str) -> pathlib.Path:
    """旧明文路径（只用于识别与迁移，绝不作验收来源）。"""
    return spec_dir / "snapshots" / f"{name}{ORACLE_SNAP_PLAIN_SUFFIX}"


def encode_oracle_snap_gz(text: str) -> bytes:
    """规范编码：EOL 规范化 → 确定性 gzip（MTIME=0、无 FNAME、level 9）：同文本同字节。"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as fh:
        fh.write(norm_eol(text).encode("utf-8"))
    return buf.getvalue()


def atomic_write_bytes(path: pathlib.Path, blob: bytes) -> None:
    """原子写字节：同目录临时文件 + os.replace；失败清理临时文件，读者不见半写内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_bytes(blob)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def read_bytes_retry(path: pathlib.Path, attempts: int = 20) -> bytes:
    """读字节；并发替换可能短暂触发共享冲突，重试即可（口径同 read_text_retry）。"""
    last: OSError | None = None
    for _ in range(attempts):
        try:
            return path.read_bytes()
        except OSError as exc:
            last = exc
            time.sleep(0.05)
    raise last if last else OSError(f"无法读取：{path}")


def _gunzip_bounded(blob: bytes, limit: int, label: str) -> bytes:
    """有界流式解压**单个** gzip 成员：输出最多累积 ``limit + 1`` 字节，超限立即失败。

    截断判据只用 ``eof``（流结束标记是否已处理完）：全量输入喂完后 ``eof`` 仍为假即截断，
    因此**不需要**无上限的 ``flush()``——那会引入一条绕过输出上限的路径。
    不做多成员拼接（尾随数据一律拒绝）；CRC/格式错误由 zlib 在 ``decompress`` 时抛出。
    """
    dec = zlib.decompressobj(zlib.MAX_WBITS | 16)
    out = bytearray()
    pos = 0
    total = len(blob)
    while pos < total and not dec.eof:
        chunk = blob[pos:pos + _GZ_CHUNK_BYTES]
        pos += len(chunk)
        try:
            # max_length 只允许再产出「到上限还差一个字节」：任何截断都立刻可见。
            out += dec.decompress(chunk, max(0, limit - len(out)) + 1)
        except zlib.error as exc:
            raise OracleSnapshotError(f"gzip 解压失败（格式/CRC）：{label}（{exc}）")
        if len(out) > limit:
            raise OracleSnapshotError(
                f"快照解压超过上限 {limit} B（疑似超限载荷或 gzip 炸弹）：{label}"
                f"——硬失败，不得截断当通过")
    if dec.unconsumed_tail:               # 上限截断后的残留输入：同样按超限失败
        raise OracleSnapshotError(
            f"快照解压超过上限 {limit} B（有未消费输入残留）：{label}")
    if not dec.eof:
        raise OracleSnapshotError(f"gzip 流被截断（未见流结束标记）：{label}")
    if dec.unused_data or pos < total:
        raise OracleSnapshotError(f"gzip 流后存在尾随非 gzip 数据（拒绝）：{label}")
    return bytes(out)


def validate_plain_snap(path: pathlib.Path) -> None:
    """旧明文迁移前预校验：分块读全文件并严格 UTF-8 解码（不把整份明文读进内存）。

    不可完整读取（并发替换/权限/非法 UTF-8）即抛 ``OracleSnapshotError``：宁可不迁移，
    读取带并发替换冲突重试（口径同读路径）。
    """
    fh = None
    last: OSError | None = None
    for _ in range(20):
        try:
            fh = path.open("rb")
            break
        except FileNotFoundError as exc:      # 并发下已被移除：不再无谓重试
            raise OracleSnapshotError(f"旧明文已不存在（并发迁移？）：{path.name}（{exc}）")
        except OSError as exc:                # PermissionError / 文件瞬时不可读
            last = exc
            time.sleep(0.05)
    if fh is None:
        raise OracleSnapshotError(f"旧明文不可读：{path.name}（{last}）")
    dec = codecs.getincrementaldecoder("utf-8")()
    try:
        with fh:
            while True:
                chunk = fh.read(_GZ_CHUNK_BYTES)
                if not chunk:
                    break
                dec.decode(chunk)
        dec.decode(b"", True)       # 收尾：残留不完全的多字节序列也算非法
    except (OSError, UnicodeDecodeError) as exc:
        raise OracleSnapshotError(f"旧明文不可完整读取：{path.name}（{exc}）")


def decode_oracle_snap_gz(path: pathlib.Path) -> str:
    """严格读取 gz 存储并返回完整全文；任何不可信形态抛 ``OracleSnapshotError``。"""
    limit = ORACLE_SNAP_MAX_BYTES                    # 运行时读取（测试可注入小上限做负控）
    artifact_limit = ORACLE_SNAP_ARTIFACT_MAX_BYTES
    label = path.name
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OracleSnapshotError(f"快照存储不可读：{label}（{exc}）")
    if size <= 0:
        raise OracleSnapshotError(f"快照存储为空文件：{label}")
    if size > artifact_limit:
        raise OracleSnapshotError(
            f"快照存储超过落盘上限 {artifact_limit} B：{label}（{size} B）")
    try:
        blob = read_bytes_retry(path)
    except OSError as exc:
        raise OracleSnapshotError(f"快照存储不可读：{label}（{exc}）")
    if len(blob) > artifact_limit:
        raise OracleSnapshotError(
            f"快照存储超过落盘上限 {artifact_limit} B：{label}（读取到 {len(blob)} B）")
    raw = _gunzip_bounded(blob, limit, label)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleSnapshotError(f"快照载荷非 UTF-8：{label}（{exc}）")


def check_oracle_snapshot_store(spec_dir: pathlib.Path, name: str,
                                snapshot_text: str) -> int | None:
    """快照门读侧：只认 ``.gz``；一致返回 ``None``，否则返回硬失败码（1）。

    旧明文既不作验收来源、也不在检查门就地迁移（迁移只属 ``--oracle --update-snapshot``）：
    与 gz 并存或单独存在都硬失败——否则旧明文会冒充新代证据。
    """
    gz_path = oracle_snap_gz_path(spec_dir, name)
    plain_path = oracle_snap_plain_path(spec_dir, name)
    if plain_path.is_file() and gz_path.is_file():
        return fail(f"ORACLE 快照存储非法：旧明文 {plain_path.name} 与 {gz_path.name} 并存"
                    f"（同一工件双份）——旧明文不作验收来源；"
                    f"确认后运行 --oracle --update-snapshot 迁移")
    if plain_path.is_file():
        return fail(f"ORACLE 快照存储非法：只存在旧明文 {plain_path.name}"
                    f"（新格式为 {gz_path.name}）——旧明文不作验收来源，"
                    f"运行 --oracle --update-snapshot 迁移后再验")
    if not gz_path.is_file():
        return fail(f"ORACLE 已提交快照缺失：{gz_path}（先运行 --oracle --update-snapshot）")
    try:
        stored = decode_oracle_snap_gz(gz_path)
    except OracleSnapshotError as exc:
        return fail(f"ORACLE 快照存储不可信：{exc}（不改写存储；不得回退明文兜底）")
    if norm_eol(stored) != norm_eol(snapshot_text):
        return fail(f"ORACLE 快照漂移：{gz_path.name} 解压后与嵌入代码重新求值输出不一致；"
                    f"以嵌入代码为准修正 spec，或运行 --oracle --update-snapshot")
    return None


def _oracle_compile(spec_dir: pathlib.Path, name: str, code: str, tmp_path: pathlib.Path):
    """编译一台 ORACLE 机器，返回 ``run_mode(mode_args, stdin=None) -> CompletedProcess``。

    在 tmp_path 内落地 ``Oracle.hs``、递归展开的别名模块与生成的
    ``Driver/Oracle/Labels.hs``；编译失败抛 ``OracleGateError``。求值**不抛**——
    原始退出码/stdout 原样返回，由调用方按契约 §4.4 映射。
    """
    try:
        root_code, imported = resolve_corespec_imports(
            code, stack=(f"{spec_dir.name}/{name}",), module_name="Oracle")
    except CorespecImportError as exc:
        raise OracleGateError(f"corespec import：{exc}") from exc
    spec_md = spec_dir / "spec.md"
    try:
        labels = extract_oracle_labels(read_text_retry(spec_md), name)
        errmap = extract_errmap(read_text_retry(spec_md), name)
    except ValueError as exc:
        raise OracleGateError(f"标签/映射围栏非法：{exc}") from exc

    (tmp_path / "Oracle.hs").write_text(norm_eol(root_code), encoding="utf-8", newline="\n")
    for alias, imported_code in imported:
        (tmp_path / f"{alias}.hs").write_text(norm_eol(imported_code), encoding="utf-8",
                                              newline="\n")
    write_oracle_labels_module(tmp_path, labels, errmap)
    if imported:
        print(f"[IMPORT] {name} 展开 {', '.join(alias for alias, _ in imported)}", flush=True)

    build_dir = tmp_path / "build"
    exe = tmp_path / "spec-oracle.exe"
    print(f"[BARRIER] ORACLE 编译门 {name}", flush=True)
    r = run([
        "ghc", "-Wall", "-Werror=incomplete-patterns",
        "-i" + str(tmp_path),
        "-i" + str(ORACLE_DRIVER_PATH.parent),
        "-outputdir", str(build_dir),
        "-o", str(exe), str(ORACLE_DRIVER_PATH),
    ], cwd=REPO_ROOT)
    if r.returncode != 0:
        raise OracleGateError(f"ORACLE 编译门失败：{name}")

    def run_mode(mode_args: list[str], stdin_text: str | None = None) -> subprocess.CompletedProcess:
        """跑一次 SpecOracle；超时/无法启动由 run() 转成非零码，此处不抛异常。"""
        return run([str(exe), *mode_args], cwd=REPO_ROOT, stdin_text=stdin_text)

    return run_mode


def check_oracle(spec_dir: pathlib.Path, name: str, code: str) -> int:
    """ORACLE 检查门（一台机器）：编译 + 快照 + 性质 + 视图 + 图 + 迹线。

    返回屏障判定码：0=该机器 strict pass；1=有 FAIL；2=用法错；3=有 UNVERIFIED/WARN。
    FAIL 优先于未验证；迹线只喂 ``traces/<name>.oracle``（本机器语料），不得混装其它机器。
    快照门只认 ``snapshots/<name>.oracle.snap.gz``（确定性 gzip，完整全文比较）；存储损坏
    或旧明文在场一律硬失败（退 1），绝不静默回退明文、也不退 3 冒充未验证。
    """
    trace_path = spec_dir / "traces" / f"{name}.oracle"
    machine_unverified = False
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oracle-", dir=SCRATCH_ROOT) as tmp:
        try:
            run_mode = _oracle_compile(spec_dir, name, code, pathlib.Path(tmp))
        except OracleGateError as exc:
            return fail(str(exc))

        # 门 1：快照（含不变式 PROP 行）
        print(f"[BARRIER] ORACLE 快照门 {name}", flush=True)
        snap_run = run_mode(["snapshot"])
        snap_rc = _oracle_run_rc(snap_run, mode="snapshot", classify_lines=False)
        if snap_rc == 1:
            return fail(f"ORACLE 快照门失败：{name}（驱动退出码 {snap_run.returncode}）")
        if snap_rc == ORACLE_USAGE_RC:
            return ORACLE_USAGE_RC
        if snap_rc == ORACLE_UNVERIFIED_RC:
            machine_unverified = True
        snapshot_text = snap_run.stdout or ""
        if not machine_unverified:
            store_rc = check_oracle_snapshot_store(spec_dir, name, snapshot_text)
            if store_rc is not None:
                return store_rc
            print("[PASS] ORACLE 快照门（快照一致）", flush=True)

            # 门 2：性质门
            print(f"[BARRIER] ORACLE 性质门 {name}", flush=True)
            props = prop_lines(snapshot_text)
            if not props:
                return fail(f"ORACLE 性质门失败：{name} 快照输出中无 PROP 行")
            prop_failures = [ln for ln in props if "|FAIL|" in ln]
            if prop_failures:
                for ln in prop_failures:
                    print(f"  反例: {ln}", flush=True)
                return fail(f"ORACLE 性质门失败：{name} 存在 FAIL 性质")
            print(f"[PASS] ORACLE 性质门（{len(props)} 条性质全 PASS）", flush=True)

        # 门 3：视图门（标签完整性由 render 承担；仅视图，非判定证据）
        print(f"[BARRIER] ORACLE 视图门 {name}", flush=True)
        render_run = run_mode(["render"])
        render_rc = _oracle_run_rc(render_run, mode="render", classify_lines=False)
        if render_rc == 1:
            return fail(f"ORACLE 视图门失败：{name}（驱动退出码 {render_run.returncode}）")
        if render_rc == ORACLE_USAGE_RC:
            return ORACLE_USAGE_RC
        if render_rc == ORACLE_UNVERIFIED_RC:
            machine_unverified = True
        else:
            vr = check_oracle_rendered_fence(spec_dir / "spec.md", name, render_run.stdout or "")
            if vr not in (0, ORACLE_UNVERIFIED_RC):
                return vr
            if vr == ORACLE_UNVERIFIED_RC:
                machine_unverified = True

        # 门 4：图门（samples×step 前缀 BFS）
        #   ``WITNESS DEFERRED`` / 自身 ``STRICT no`` / partial coverage 是中性标记：
        #   只有真 FAIL 或真 WARN/UNVERIFIED 才影响判定（契约 §4.4）。
        print(f"[BARRIER] ORACLE 图门 {name}", flush=True)
        graph_run = run_mode(["graph"])
        graph_rc = _oracle_run_rc(graph_run, mode="graph", neutral_when_unverified=True)
        graph_out = graph_run.stdout or ""
        if graph_rc == 1:
            for ln in graph_out.splitlines():
                if ln.strip().startswith(_ORACLE_FAIL_PREFIX) or "|FAIL|" in ln:
                    print(f"  反例: {ln}", flush=True)
            return fail(f"ORACLE 图门失败：{name}（驱动退出码 {graph_run.returncode}）")
        if graph_rc == ORACLE_USAGE_RC:
            return ORACLE_USAGE_RC
        if graph_rc == ORACLE_UNVERIFIED_RC:
            for ln in graph_out.splitlines():
                print(f"  {ln}", flush=True)
            machine_unverified = True
        else:
            print("[PASS] ORACLE 图门（无 FAIL；DEFERRED/覆盖报告中性）", flush=True)

        # 门 5：迹线门（只喂本机器语料）
        print(f"[BARRIER] ORACLE 迹线门 {name}", flush=True)
        if not trace_path.is_file():
            # 声明条数唯一来源是 graph 的 `WITNESS DEFERRED <n>`（无条件输出，n 可为 0）。
            declared = oracle_witness_count(graph_out)
            if declared == 0:
                # 契约：n==0 且无语料时**跳过节**（不红），但该机器**不计 strict pass**——
                # 零证据不得算绿，故整体仍非绿（退 3）。仍然不调用驱动。
                print(f"[UNVERIFIED] ORACLE 迹线门 {name}：graph 声明 requiredWitnesses=0 且无"
                      f" traces/{name}.oracle 语料——跳过节（不调用驱动），"
                      f"但零证据不计 strict pass", flush=True)
                machine_unverified = True
            else:
                shown = "读不出（fail-closed 视作 >0）" if declared is None else str(declared)
                print(f"[UNVERIFIED] ORACLE 迹线门 {name}：无 traces/{name}.oracle 语料"
                      f"（requiredWitnesses={shown}）——不调用驱动，本次非 strict", flush=True)
                machine_unverified = True
        else:
            # 空文件（零记录）**不拦**：原样交驱动按 §5 ``empty`` 判 FAIL。
            trace_text = read_text_retry(trace_path)
            trace_run = run_mode(["tracecheck", name], norm_eol(trace_text))
            trace_rc = _oracle_run_rc(trace_run, mode="tracecheck")
            out = trace_run.stdout or ""
            for ln in out.splitlines():
                print(f"  {ln}", flush=True)
            if trace_rc == 1:
                return fail(f"ORACLE 迹线门失败：{name} 与嵌入语义不一致"
                            f"（驱动退出码 {trace_run.returncode}）")
            if trace_rc == ORACLE_USAGE_RC:
                return ORACLE_USAGE_RC
            if trace_rc == ORACLE_UNVERIFIED_RC:
                machine_unverified = True
            else:
                print(f"[PASS] ORACLE 迹线门 {trace_path.name}"
                      f"（语料与模型一致；不证明语料来源为真实运行——G8 provenance 由 wrapper 另判）",
                      flush=True)
    if machine_unverified:
        print(f"[UNVERIFIED] ORACLE 机器门 {name}：本次非 strict（退出码 {ORACLE_UNVERIFIED_RC}）",
              flush=True)
        return ORACLE_UNVERIFIED_RC
    print(f"[PASS] ORACLE 机器门 {name}（编译/快照/性质/视图/图/迹线）", flush=True)
    return 0


def update_oracle_snapshots(spec_dir: pathlib.Path, oracles: list[tuple[str, str]]) -> int:
    """``--oracle --update-snapshot``：先校验所有机器 valid，全部通过才在锁内整组落盘。

    只生成快照与 RENDERED-ORACLE 视图；不跑迹线，**不构成 strict 证据**（允许 spec-first）。
    存储写侧（契约 §9）：目标 ``.oracle.snap.gz`` 规范字节（MTIME=0、无 FNAME、level 9）；
    既有 gz 解压后与本次求值全文（``norm_eol`` 语义）等价则跳过重写（字节与 mtime 稳定），
    不可信/不一致则按嵌入代码重建；旧明文只在 gz 原子替换**成功且预校验通过**后删除。
    """
    prepared: list[tuple[str, str, str]] = []          # (name, snapshot_text, rendered)
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    for name, code in oracles:
        with tempfile.TemporaryDirectory(prefix="oracle-", dir=SCRATCH_ROOT) as tmp:
            try:
                run_mode = _oracle_compile(spec_dir, name, code, pathlib.Path(tmp))
            except OracleGateError as exc:
                return fail(f"ORACLE 快照更新失败：{exc}（未落盘）")
            snap_run = run_mode(["snapshot"])
            render_run = run_mode(["render"])
            if snap_run.returncode != 0 or not (snap_run.stdout or "").strip():
                return fail(f"ORACLE 快照更新失败：{name} snapshot 不可用"
                            f"（驱动退出码 {snap_run.returncode}）（未落盘）")
            if render_run.returncode != 0 or not (render_run.stdout or "").strip():
                return fail(f"ORACLE 快照更新失败：{name} render 不可用"
                            f"（驱动退出码 {render_run.returncode}）（未落盘）")
            snapshot_text = snap_run.stdout or ""
            rendered = render_run.stdout or ""
        if not prop_lines(snapshot_text):
            return fail(f"ORACLE 快照更新失败：{name} snapshot 输出无 PROP 行（未落盘）")
        prepared.append((name, snapshot_text, rendered))

    snap_dir = spec_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    # 全部机器**求值完成**后才进锁判存储；判定期只读——任一不可信即整组不落盘。
    with update_lock(spec_dir):
        plan: list[tuple[str, bytes, pathlib.Path, pathlib.Path, bool]] = []
        for name, snapshot_text, _rendered in prepared:
            gz_path = oracle_snap_gz_path(spec_dir, name)
            plain_path = oracle_snap_plain_path(spec_dir, name)
            plain_exists = plain_path.is_file()
            if plain_exists and gz_path.is_file():
                return fail(f"ORACLE 快照更新失败：{plain_path.name} 与 {gz_path.name} 并存"
                            f"（同一工件双份，无法判断权威来源）——确认后删除旧明文再更新"
                            f"（未落盘）")
            # 写前整组预校验（先于任何写盘）：新全文与规范 gz 都必须落在**读侧同样的**限额内，
            # 否则会写出自己读不回的工件；超限硬失败，绝不截断（不缩水证据）。
            blob = encode_oracle_snap_gz(snapshot_text)
            text_bytes = len(norm_eol(snapshot_text).encode("utf-8"))
            if text_bytes > ORACLE_SNAP_MAX_BYTES:
                return fail(f"ORACLE 快照更新失败：{name} 全文 {text_bytes} B 超过解压上限"
                            f" {ORACLE_SNAP_MAX_BYTES} B（不得截断）")
            if len(blob) > ORACLE_SNAP_ARTIFACT_MAX_BYTES:
                return fail(f"ORACLE 快照更新失败：{name} 规范 gz {len(blob)} B 超过落盘上限"
                            f" {ORACLE_SNAP_ARTIFACT_MAX_BYTES} B（不得截断）")
            needs_write = True
            if gz_path.is_file():
                try:
                    stored = decode_oracle_snap_gz(gz_path)
                except OracleSnapshotError as exc:
                    # 不可信/过旧：按嵌入代码重建（跨 zlib 版本字节差异也走这条）。
                    print(f"[WARN] ORACLE 快照存储不可信（按嵌入代码重建）：{exc}", flush=True)
                else:
                    if norm_eol(stored) == norm_eol(snapshot_text):
                        needs_write = False
            if needs_write and plain_exists:
                # 迁移前预校验：旧明文须完整可读（严格 UTF-8），否则整组不落盘、不删明文。
                try:
                    validate_plain_snap(plain_path)
                except OracleSnapshotError as exc:
                    return fail(f"ORACLE 快照更新失败：{exc}（未落盘）")
            plan.append((name, blob, gz_path, plain_path, needs_write))
        for name, blob, gz_path, plain_path, needs_write in plan:
            if not needs_write:
                print(f"[UPDATE] ORACLE 快照已等价（未改写，字节与 mtime 保持）：{gz_path}",
                      flush=True)
                continue
            try:
                # 先原子替换 gz，成功后才删旧明文：宁可留双份报错，也不丢唯一副本。
                atomic_write_bytes(gz_path, blob)
            except OSError as exc:
                return fail(f"ORACLE 快照更新失败：写 {gz_path.name} 出错（{exc}）")
            print(f"[UPDATE] ORACLE 快照已更新：{gz_path}", flush=True)
            if plain_path.is_file():
                try:
                    plain_path.unlink()
                except OSError as exc:
                    return fail(f"ORACLE 快照更新失败：{gz_path.name} 已原子落盘，但旧明文"
                                f" {plain_path.name} 删除失败（{exc}）——请手工删除该明文")
                print(f"[UPDATE] 旧明文已迁移并删除：{plain_path}", flush=True)
        for name, _snapshot_text, rendered in prepared:
            vr = update_oracle_rendered_fence(spec_dir / "spec.md", name, rendered)
            if vr != 0:
                return vr
    print("[UPDATE] ORACLE 只生成（未验证；不构成 strict 证据）", flush=True)
    return 0


def oracle_review_clean(spec_dir: pathlib.Path) -> bool:
    """ORACLE 评审门：只走 ``spec_review.py --check --oracle``（ORACLE 哈希）。

    绝不把旧代 ``reviews/<name>.review.md``（SEMANTICS/LABEL 哈希）当成新模型留档——
    哈希对象不同，用错旧评审等于把旧模型的结论当中新模型的通过证据。
    留档缺失或过期（如 spec 正在 new rev）时本次一律非 strict（不伪绿、不跳过）。
    """
    print("[BARRIER] ORACLE 评审门", flush=True)
    r = run([sys.executable, str(SKILL_TOOLS / "spec_review.py"), str(spec_dir),
             "--check", "--oracle"], cwd=REPO_ROOT)
    if r.returncode != 0:
        print("[WARN] ORACLE 评审门未通过（ORACLE 哈希接口未接入 / 留档缺失或过期）"
              "→ 本次非 strict", flush=True)
        return False
    print("[PASS] ORACLE 评审门（ORACLE 留档新鲜，无阻断项）", flush=True)
    return True


def run_oracle(spec_dir: pathlib.Path, update_snapshot: bool) -> int:
    """``--oracle`` 门编排：逐机器编译/快照/性质/视图/图/迹线 + 评审门。

    只有全部机器全门绿**且** ORACLE 评审留档干净才返回 0；缺 RENDERED-ORACLE 锚点或
    缺评审留档时返回 ``ORACLE_UNVERIFIED_RC``（未验证 ≠ 通过）。
    """
    spec_md = spec_dir / "spec.md"
    if not spec_md.is_file():
        return fail(f"spec.md 不存在：{spec_md}")
    try:
        oracles = extract_oracles(read_text_retry(spec_md))
    except ValueError as exc:
        return fail(f"ORACLE 围栏提取失败：{exc}")
    if not oracles:
        return fail("spec.md 中无 ORACLE 标记的嵌入代码块")
    for name, code in oracles:
        if not re.search(r"^module\s+Oracle\b", code, re.MULTILINE):
            return fail(f"嵌入块 {name} 必须声明 module Oracle（固定驱动静态 import）")

    # 误路由：traces 下有 .oracle 语料，其文件名不是已声明的机器。
    declared = {name for name, _ in oracles}
    traces_dir = spec_dir / "traces"
    if traces_dir.is_dir():
        for path in sorted(traces_dir.glob("*.oracle")):
            if path.stem not in declared:
                return fail(
                    f"未知机器语料（误路由）：{path.name}（已声明机器："
                    f"{', '.join(sorted(declared)) or '无'}）")

    if update_snapshot:
        return update_oracle_snapshots(spec_dir, oracles)

    if shutil.which("ghc") is None:
        return fail("ghc 不在 PATH")
    unverified = False
    for name, code in oracles:
        rc = check_oracle(spec_dir, name, code)
        if rc == 0:
            continue
        if rc == ORACLE_USAGE_RC:
            return rc                      # 用法错：无法继续判定
        if rc != ORACLE_UNVERIFIED_RC:
            return rc                      # FAIL 优先：立即暴露
        unverified = True                  # 视图/迹线/图未验证：记下并继续扫其余机器
    if not oracle_review_clean(spec_dir):
        unverified = True
    if unverified:
        print(f"[UNVERIFIED] ORACLE 本次非 strict 证据（视图锚点/迹线/图/评审之一未验证）；"
              f"退出码 {ORACLE_UNVERIFIED_RC}，不得记成 [PASS]", flush=True)
        return ORACLE_UNVERIFIED_RC
    print(f"[PASS] ORACLE 全部门绿（编译/快照/性质/视图/图/迹线/评审；{len(oracles)} 台机器）",
          flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="core-spec 内嵌可执行语义屏障")
    parser.add_argument("spec_dir")
    parser.add_argument("--update-snapshot", action="store_true")
    parser.add_argument("--allow-legacy", action="store_true",
                        help="仅有旧 v1 迹线证据时兼容返回 0（仍打印 [LEGACY]，不计入 strict 合规）")
    parser.add_argument("--oracle", action="store_true",
                        help="走 ORACLE 新代门（ORACLE:<machine> 围栏 + SpecOracle 驱动）；"
                             "缺省仍是旧代 SEMANTICS 路径，两者互不影响")
    args = parser.parse_args(argv)

    spec_dir = (REPO_ROOT / args.spec_dir).resolve()
    if args.oracle:
        return run_oracle(spec_dir, args.update_snapshot)

    spec_md = spec_dir / "spec.md"
    if not spec_md.is_file():
        return fail(f"spec.md 不存在：{spec_md}")

    semantics = extract_semantics(read_text_retry(spec_md))
    if not semantics:
        return fail("spec.md 中无 SEMANTICS 标记的嵌入代码块")
    for sname, scode in semantics:
        if not re.search(r"^module\s+Semantics\b", scode, re.MULTILINE):
            return fail(f"嵌入块 {sname} 必须声明 module Semantics（固定驱动静态 import）")
    if shutil.which("ghc") is None:
        return fail("ghc 不在 PATH")

    # 逐机器聚合：未验证（3）不得短路掉后面机器的真失败（1），反之亦然由硬失败优先。
    unverified = False
    for name, code in semantics:
        rc = check_semantics(spec_dir, name, code, args.update_snapshot, args.allow_legacy)
        if rc == 0:
            continue
        if rc != LEGACY_UNVERIFIED_RC:
            return rc                      # 硬失败：立即暴露
        unverified = True                  # 未验证：继续扫描，给后续机器的失败优先权

    # 评审门（每 spec 一次）：人工或 AI 辅助评审；门只确定性校验——
    # 留档与设计图/语义/标签哈希一致，且无未闭环阻断项。
    if not args.update_snapshot:
        print("[BARRIER] 评审门", flush=True)
        r = run([sys.executable, str(SKILL_TOOLS / "spec_review.py"), str(spec_dir), "--check"],
                cwd=REPO_ROOT)
        if r.returncode != 0:
            return fail("评审门失败：见上方输出（需 --emit 生成材料、评审后 --record 写入）")
        print("[PASS] 评审门（留档新鲜，无阻断项）", flush=True)
    if unverified:
        print(
            f"[UNVERIFIED] 本次有 SEMANTICS 机器未验证，不计入 strict 合规"
            f"（退出码 {LEGACY_UNVERIFIED_RC}）",
            flush=True,
        )
        return LEGACY_UNVERIFIED_RC
    return 0


if __name__ == "__main__":
    sys.exit(main())
