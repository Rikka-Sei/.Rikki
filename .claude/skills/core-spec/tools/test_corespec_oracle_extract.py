#!/usr/bin/env python3
"""ORACLE extraction, import, CLI routing, and snapshot-storage contract tests.

Pure extraction tests use text fixtures. Import and CLI tests execute the real
parser, loader, and orchestration code; subprocess boundaries are controlled
only where a test targets barrier decisions rather than GHC/driver behavior.
"""
from __future__ import annotations

import contextlib
import gzip
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import spec_barrier as barrier  # noqa: E402

DEFAULT_SNAPSHOT = "PROP TerminalClosure|PASS|0\nPROP Liveness|PASS|0\n"
DEFAULT_RENDERED = "stateDiagram-v2\n"
DEFAULT_GRAPH = "BOUNDED yes\nBOUND 24|4096\nCHECKEDPREFIX models=1 transitions=0 cells=0\nSTRICT yes\n"
DEFAULT_TRACE = "TRACE ok records=1\nSTRICT yes\n"
DEFAULT_REVIEW = (2, "")
SNAP_WITNESS = "WITNESS Foo|<EvStep>\nPROP TerminalClosure|PASS|0\n"
GRAPH_NEUTRAL = ("BOUNDED yes\nBOUND 24|4096\nCHECKEDPREFIX models=1 transitions=0 cells=0\n"
                 "COVERAGE models=1 truncated=yes\n"
                 "WITNESS DEFERRED 2 (judged by tracecheck)\nSTRICT no\n")
IMPORT_SPEC_A = "synthetic-alpha"
IMPORT_MACHINE_A = "machine-a"
IMPORT_SPEC_B = "synthetic-beta"
IMPORT_MACHINE_B = "machine-b"


# --------------------------------------------------------------------------- 文本夹具
def oracle_block(name: str, code: str = "module Oracle where\nvalue = 1\n") -> str:
    return (f"<!-- ORACLE:{name} BEGIN -->\n```haskell\n{code}```\n"
            f"<!-- ORACLE:{name} END -->\n")


def semantics_block(name: str, code: str = "module Semantics where\nvalue = 1\n") -> str:
    return (f"<!-- SEMANTICS:{name} BEGIN -->\n```haskell\n{code}```\n"
            f"<!-- SEMANTICS:{name} END -->\n")


def label_fence(name: str, body: str) -> str:
    return f"```ORACLE-LABEL:{name}\n{body}\n```\n"


def errmap_fence(name: str, body: str) -> str:
    return f"```ORACLE-ERRMAP:{name}\n{body}\n```\n"


def oracle_corpus(machine: str, marker: str) -> str:
    """一段形状合法的 ORACLE 语料；``marker`` 只出现在本机器语料里（用于路由断言）。"""
    return (
        f"ORACLE-BEGIN {machine}|{marker}|1\n"
        f"ORACLE {machine}|{marker}|1|1|1|Observation {{ obsEvent = EvStep, "
        f"obsObject = \"{marker}\", obsGen = 1, obsRound = 1, obsAtMs = 0, "
        f"obsWaitStartMs = 0, obsWaitEndMs = 0, obsScheduledMs = 0, "
        f"obsCooldownUntilMs = 0, obsRawErr = Nothing, obsErr = Just ErrNone, "
        f"obsRefs = [\"{marker}\"] }}|NoCurrent|NoCurrent|[]\n"
        f"ORACLE-END {machine}|{marker}|1|terminal\n"
    )


# --------------------------------------------------------------------------- 快照存储（新格式契约）
# 新代快照存储形态：``snapshots/<machine>.oracle.snap.gz``——**确定性无损 gzip**：
#   * 同一模型 → 同一字节：gzip 头 ``MTIME=0``、无 ``FNAME``/注释等非确定字段、level 9；
#   * 读侧必须完整解压，并与驱动输出做 **EOL 规范化后**的全文比较（前缀/长度近似不算）；
#   * 损坏 / 截断 / 非法 gzip / 非 UTF-8 / 超解压上限：一律硬失败退 1（不得退 3）；
#   * 旧明文 ``.oracle.snap`` **不**作验收来源（fail-closed，不得静默兜底/就地迁移）；
#     迁移只发生在 ``--oracle --update-snapshot``，且迁移后删除明文。
# The gzip fixtures construct expected bytes independently of production output.
SNAP_GZ_SUFFIX = ".oracle.snap.gz"
SNAP_PLAIN_SUFFIX = ".oracle.snap"
# Snapshot fixture size remains large enough to exercise a practical bound.
LARGE_SNAPSHOT_MIN_BYTES = 8 * 1024 * 1024
# 屏障须暴露的解压后字节上限常量（接受以下任一命名；用例据此注入小上限做负控）。
SNAP_LIMIT_NAMES = ("ORACLE_SNAP_MAX_BYTES", "ORACLE_SNAP_GZ_MAX_BYTES",
                    "ORACLE_SNAPSHOT_MAX_BYTES")

# 独立写死的期望快照全文：完整事实（PROP/MODEL/TRANS/SAMPLE）、非 ASCII、末尾换行。
FULL_SNAPSHOT = (
    "PROP TerminalClosure|PASS|0\n"
    "PROP Liveness|PASS|0\n"
    "MODEL m0 object=1 phase=Running gen=1 round=1\n"
    "MODEL m1 object=1 phase=Stopped gen=1 round=1\n"
    "TRANS m0 EvStep -> m1 effects=[Send \"前缀\", NoOp]\n"
    "TRANS m1 EvStop -> m2 effects=[]\n"
    "SAMPLE m0 obsAtMs=0 obsScheduledMs=0 obsRawErr=Nothing obsRefs=[\"α\"]\n"
)
# 只改最后一行的末字段：验证比较覆盖全文（不做前缀/长度近似）。
DRIFT_LAST_LINE = FULL_SNAPSHOT.replace('obsRefs=["α"]', 'obsRefs=["β"]')


def lf_text(text: str) -> str:
    """EOL 规范化（语义同屏障 ``norm_eol``；此处独立实现，不依赖被测函数）。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def gzip_raw_bytes(data: bytes) -> bytes:
    """确定性 gzip 编码（mtime=0、无 FNAME、level 9）：同一输入 → 同一字节。"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as fh:
        fh.write(data)
    return buf.getvalue()


def gzip_bytes(text: str) -> bytes:
    """按**原文**（不规范化 EOL）编码 gzip；用于伪造需要归一化对比的载荷。"""
    return gzip_raw_bytes(text.encode("utf-8"))


def snap_gz_bytes(text: str) -> bytes:
    """规范落盘字节：EOL 规范化后的全文 → 确定性 gzip。"""
    return gzip_bytes(lf_text(text))


def snap_gz_path(spec_dir: pathlib.Path, machine: str) -> pathlib.Path:
    return spec_dir / "snapshots" / f"{machine}{SNAP_GZ_SUFFIX}"


def snap_plain_path(spec_dir: pathlib.Path, machine: str) -> pathlib.Path:
    return spec_dir / "snapshots" / f"{machine}{SNAP_PLAIN_SUFFIX}"


def write_snap_gz(spec_dir: pathlib.Path, machine: str, text: str) -> pathlib.Path:
    path = snap_gz_path(spec_dir, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(snap_gz_bytes(text))
    return path


def write_snap_gz_raw(spec_dir: pathlib.Path, machine: str, blob: bytes) -> pathlib.Path:
    """按给定字节落盘（伪造损坏/截断/非法 gzip/非 UTF-8/CRLF 等载荷）。"""
    path = snap_gz_path(spec_dir, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return path


def write_snap_plaintext(spec_dir: pathlib.Path, machine: str, text: str) -> pathlib.Path:
    path = snap_plain_path(spec_dir, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lf_text(text), encoding="utf-8", newline="\n")
    return path


def read_snap_gz(path: pathlib.Path) -> str:
    """独立解压（stdlib，严格 UTF-8）；只对合法 gz 使用。"""
    with gzip.open(path, "rb") as fh:
        return fh.read().decode("utf-8")


def freeze_mtime_ns(path: pathlib.Path) -> int:
    """把 mtime 钉在固定过去时刻；任何重写都会把它推到现在，据此断言「未重写」。"""
    os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    return path.stat().st_mtime_ns


def snap_limit_name() -> str | None:
    """返回屏障暴露的解压上限常量名；未暴露返回 ``None``。"""
    for name in SNAP_LIMIT_NAMES:
        if isinstance(getattr(barrier, name, None), int):
            return name
    return None


class DriverStub:
    """替换 ``subprocess.run``：按命令分派受控结果并记录调用。

    只控制进程结果（returncode/stdout），``spec_barrier`` 的提取、导入改写、门编排与
    写入逻辑全部真实执行。列表结果 = 同模式按调用次序逐次取值（用于多机器）。
    """

    def __init__(
        self,
        *,
        ghc: tuple[int, str] = (0, ""),
        snapshot: tuple[int, str] | list[tuple[int, str]] = (0, DEFAULT_SNAPSHOT),
        render: tuple[int, str] | list[tuple[int, str]] = (0, DEFAULT_RENDERED),
        graph: tuple[int, str] | list[tuple[int, str]] = (0, DEFAULT_GRAPH),
        tracecheck: tuple[int, str] | list[tuple[int, str]] = (0, DEFAULT_TRACE),
        review: tuple[int, str] | list[tuple[int, str]] = DEFAULT_REVIEW,
        default: tuple[int, str] = (0, ""),
    ) -> None:
        self.results = {"ghc": ghc, "snapshot": snapshot, "render": render,
                        "graph": graph, "tracecheck": tracecheck, "review": review}
        self.default = default
        self.used: dict[str, int] = {}
        self.calls: list[tuple[str, list[str], str | None]] = []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        if cmd and pathlib.Path(cmd[0]).name == "ghc":
            mode = "ghc"
        elif "snapshot" in cmd:
            mode = "snapshot"
        elif "render" in cmd:
            mode = "render"
        elif "graph" in cmd:
            mode = "graph"
        elif "tracecheck" in cmd:
            mode = "tracecheck"
        elif "--check" in cmd:
            mode = "review"
        else:
            mode = ""
        self.calls.append((mode, cmd, kwargs.get("input")))
        spec = self.results.get(mode, self.default)
        if isinstance(spec, list):
            idx = self.used.get(mode, 0)
            self.used[mode] = idx + 1
            spec = spec[min(idx, len(spec) - 1)]
        rc, out = spec
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def tracecheck_inputs(self) -> list[str]:
        return [payload or "" for mode, _cmd, payload in self.calls if mode == "tracecheck"]


class OracleFenceExtractTest(unittest.TestCase):
    """纯文本提取：闭合性、互不吞并、结构拒绝。"""

    def test_extract_oracles_reads_closed_block(self) -> None:
        text = oracle_block("machine-a", "module Oracle where\nx = 1\n")
        got = barrier.extract_oracles(text)
        self.assertEqual([name for name, _ in got], ["machine-a"])
        self.assertIn("module Oracle", got[0][1])
        self.assertIn("x = 1", got[0][1])

    def test_extract_oracles_keeps_multiple_machines_in_order(self) -> None:
        text = oracle_block("a", "module Oracle where\na = 1\n") + oracle_block(
            "b", "module Oracle where\nb = 2\n")
        got = barrier.extract_oracles(text)
        self.assertEqual([name for name, _ in got], ["a", "b"])

    def test_extract_oracles_requires_end_marker(self) -> None:
        """BEGIN 无 END 即未闭合：不得当成合法块返回。"""
        text = "<!-- ORACLE:machine-a BEGIN -->\n```haskell\nmodule Oracle where\n```\n"
        with self.assertRaises(ValueError):
            barrier.extract_oracles(text)

    def test_extract_oracles_rejects_duplicate_machine(self) -> None:
        text = oracle_block("machine-a", "module Oracle where\nx = 1\n") + oracle_block(
            "machine-a", "module Oracle where\nx = 2\n")
        with self.assertRaises(ValueError):
            barrier.extract_oracles(text)

    def test_extract_oracles_rejects_mismatched_end_name(self) -> None:
        text = ("<!-- ORACLE:machine-a BEGIN -->\n```haskell\nmodule Oracle where\n```\n"
                "<!-- ORACLE:machine-b END -->\n")
        with self.assertRaises(ValueError):
            barrier.extract_oracles(text)

    def test_two_generations_do_not_swallow_each_other(self) -> None:
        """ORACLE 与 SEMANTICS 围栏互不吞并（两种先后顺序都验）。"""
        oracle = oracle_block("m", "module Oracle where\noracleTok = 1\n")
        semantics = semantics_block("m", "module Semantics where\nsemTok = 2\n")
        for text in (oracle + semantics, semantics + oracle):
            with self.subTest(order="oracle-first" if text.startswith("<!-- ORACLE") else "sem-first"):
                oracles = barrier.extract_oracles(text)
                self.assertEqual([name for name, _ in oracles], ["m"])
                self.assertIn("oracleTok", oracles[0][1])
                self.assertNotIn("semTok", oracles[0][1])
                sems = barrier.extract_semantics(text)
                self.assertEqual([name for name, _ in sems], ["m"])
                self.assertIn("semTok", sems[0][1])
                self.assertNotIn("oracleTok", sems[0][1])

    def test_extract_semantics_accepts_begin_only_block(self) -> None:
        """Keep SEMANTICS extraction independent of ORACLE fence-closure rules.

        SEMANTICS accepts a BEGIN-only block; ORACLE extraction separately
        requires a matched BEGIN/END pair.
        """
        text = "<!-- SEMANTICS:m BEGIN -->\n```haskell\nmodule Semantics where\nv = 1\n```\n"
        self.assertEqual(barrier.extract_semantics(text),
                         [("m", "module Semantics where\nv = 1\n")])
        self.assertEqual(barrier.extract_oracles(text), [])

    def test_extract_oracle_labels_returns_four_buckets(self) -> None:
        text = label_fence("m",
                           "state Start = 未解析\n"
                           "event EvStep = 一步\n"
                           "detail D0 = 细节\n"
                           "note Start = 备注\n")
        labels = barrier.extract_oracle_labels(text, "m")
        self.assertEqual(set(labels), {"state", "event", "detail", "note"})
        self.assertEqual(labels["state"], [("Start", "未解析")])
        self.assertEqual(labels["event"], [("EvStep", "一步")])
        self.assertEqual(labels["detail"], [("D0", "细节")])
        self.assertEqual(labels["note"], [("Start", "备注")])

    def test_extract_oracle_labels_missing_fence_is_empty(self) -> None:
        labels = barrier.extract_oracle_labels(oracle_block("m"), "m")
        self.assertEqual(set(labels), {"state", "event", "detail", "note"})
        self.assertTrue(all(v == [] for v in labels.values()))

    def test_extract_oracle_labels_rejects_unknown_key(self) -> None:
        text = label_fence("m", "phase Start = 未解析\n")
        with self.assertRaises(ValueError):
            barrier.extract_oracle_labels(text, "m")

    def test_extract_oracle_labels_rejects_malformed_line(self) -> None:
        text = label_fence("m", "state Start 未解析\n")
        with self.assertRaises(ValueError):
            barrier.extract_oracle_labels(text, "m")

    def test_extract_oracle_labels_ignores_other_fences(self) -> None:
        text = (oracle_block("m")
                + errmap_fence("m", "raw none = ErrNone\n")
                + label_fence("m", "state Start = 起点\n"))
        labels = barrier.extract_oracle_labels(text, "m")
        self.assertEqual(labels["state"], [("Start", "起点")])

    def test_extract_errmap_pairs(self) -> None:
        text = errmap_fence("m", "raw none = ErrNone\nraw code_1 = ErrSample\n")
        self.assertEqual(barrier.extract_errmap(text, "m"),
                         [("none", "ErrNone"), ("code_1", "ErrSample")])

    def test_extract_errmap_missing_fence_is_empty(self) -> None:
        self.assertEqual(barrier.extract_errmap(oracle_block("m"), "m"), [])

    def test_extract_errmap_rejects_conflicting_duplicate(self) -> None:
        """同一原始身份映射到不同 Err：冲突必须报错，不得静默取其一。"""
        text = errmap_fence("m", "raw x = ErrA\nraw x = ErrB\n")
        with self.assertRaises(ValueError):
            barrier.extract_errmap(text, "m")

    def test_extract_errmap_rejects_malformed_line(self) -> None:
        text = errmap_fence("m", "ErrA = ErrA\n")
        with self.assertRaises(ValueError):
            barrier.extract_errmap(text, "m")

    def test_extract_errmap_ignores_oracle_and_label_fences(self) -> None:
        text = (oracle_block("m")
                + label_fence("m", "state Start = 起点\n")
                + errmap_fence("m", "raw none = ErrNone\n"))
        self.assertEqual(barrier.extract_errmap(text, "m"), [("none", "ErrNone")])


class OracleImportTest(unittest.TestCase):
    """Cross-spec imports use real parsers/loaders over isolated synthetic specs."""

    def setUp(self) -> None:
        self.temporary_root = tempfile.TemporaryDirectory(prefix="oracle-import-")
        self.root = pathlib.Path(self.temporary_root.name)
        self.addCleanup(self.temporary_root.cleanup)
        self.root_patch = mock.patch.object(barrier, "REPO_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        for spec_name, oracle_name, semantics_name, marker in (
            (IMPORT_SPEC_A, IMPORT_MACHINE_A, IMPORT_MACHINE_A, "syntheticAlphaMarker"),
            (IMPORT_SPEC_B, IMPORT_MACHINE_B, IMPORT_MACHINE_B, "syntheticBetaMarker"),
        ):
            spec_dir = self.root / "docs" / "specs" / spec_name
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                oracle_block(oracle_name, f"module Oracle where\n{marker} = 1\n")
                + semantics_block(semantics_name,
                                  "module Semantics where\ntraceExempt = False\n"),
                encoding="utf-8", newline="\n")

    def test_oracle_import_rewrites_module_oracle_not_semantics(self) -> None:
        raw = (f"module Oracle where\n"
               f"corespec import {IMPORT_SPEC_A} {IMPORT_MACHINE_A} as ModelA\n"
               f"linked = 1\n")
        root, modules = barrier.resolve_corespec_imports(raw, stack=("fixture/root",),
                                                         module_name="Oracle")
        self.assertNotIn("corespec import", root)
        self.assertIn("import qualified ModelA", root)
        self.assertEqual([alias for alias, _ in modules], ["ModelA"])
        imported = modules[0][1]
        self.assertIn("module ModelA", imported)
        self.assertNotIn("module Semantics", imported)
        self.assertIn("syntheticAlphaMarker", imported)

    def test_semantics_import_remains_default(self) -> None:
        raw = (f"module Semantics where\n"
               f"corespec import {IMPORT_SPEC_A} {IMPORT_MACHINE_A} as ModelA\n"
               f"linked = 1\n")
        _root, modules = barrier.resolve_corespec_imports(raw, stack=("fixture/root",))
        self.assertEqual([alias for alias, _ in modules], ["ModelA"])
        self.assertIn("module ModelA", modules[0][1])
        self.assertIn("traceExempt", modules[0][1])

    def test_oracle_import_missing_block_fails(self) -> None:
        raw = f"corespec import {IMPORT_SPEC_A} missing as Nope\n"
        with self.assertRaises(barrier.CorespecImportError) as caught:
            barrier.resolve_corespec_imports(raw, stack=("fixture/root",), module_name="Oracle")
        self.assertIn("没有 ORACLE:missing", str(caught.exception))

    def test_oracle_import_cycle_fails(self) -> None:
        raw = f"corespec import {IMPORT_SPEC_A} {IMPORT_MACHINE_A} as ModelA\n"
        with self.assertRaises(barrier.CorespecImportError) as caught:
            barrier.resolve_corespec_imports(
                raw, stack=(f"{IMPORT_SPEC_A}/{IMPORT_MACHINE_A}",), module_name="Oracle")
        self.assertIn("成环", str(caught.exception))

    def test_oracle_import_alias_conflict_fails(self) -> None:
        raw = (f"corespec import {IMPORT_SPEC_A} {IMPORT_MACHINE_A} as Duplicate\n"
               f"corespec import {IMPORT_SPEC_B} {IMPORT_MACHINE_B} as Duplicate\n")
        with self.assertRaises(barrier.CorespecImportError) as caught:
            barrier.resolve_corespec_imports(raw, stack=("fixture/root",), module_name="Oracle")
        self.assertIn("Duplicate", str(caught.exception))

    def test_oracle_import_missing_spec_fails(self) -> None:
        raw = "corespec import no-such-spec-xyz machine as X\n"
        with self.assertRaises(barrier.CorespecImportError):
            barrier.resolve_corespec_imports(raw, stack=("fixture/root",), module_name="Oracle")

    def test_oracle_import_expands_recursively(self) -> None:
        """Expand three real synthetic specs recursively and exclude the caller block."""
        specs = self.root / "docs" / "specs"

        def put(name: str, body: str) -> None:
            spec_dir = specs / name
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(body, encoding="utf-8", newline="\n")

        put("synthetic-leaf", oracle_block("leaf", "module Oracle where\nleaf = 3\n"))
        put("synthetic-mid", oracle_block(
            "mid", "module Oracle where\n"
                  "corespec import synthetic-leaf leaf as Leaf\nmid = 2\n")
            + semantics_block("mid", "module Semantics where\nsemanticTrap = 1\n"))
        raw = ("module Oracle where\n"
               "corespec import synthetic-mid mid as Mid\nroots = 1\n")
        root, modules = barrier.resolve_corespec_imports(
            raw, stack=("synthetic-root/root",), module_name="Oracle")

        self.assertIn("import qualified Mid", root)
        self.assertNotIn("corespec import", root)
        self.assertEqual(sorted(alias for alias, _ in modules), ["Leaf", "Mid"])
        by_alias = dict(modules)
        self.assertIn("module Mid", by_alias["Mid"])
        self.assertIn("import qualified Leaf", by_alias["Mid"])
        self.assertIn("module Leaf", by_alias["Leaf"])
        self.assertNotIn("semanticTrap", "".join(code for _, code in modules))


class OracleCliTest(unittest.TestCase):
    """``--oracle`` 门编排：缺块 / 缺语料 / 失败闭合 / 路由 / 分组合写入。"""

    def setUp(self) -> None:
        self.temporary_root = tempfile.TemporaryDirectory(prefix="oracle-cli-")
        self.work = pathlib.Path(self.temporary_root.name)
        self.addCleanup(self.temporary_root.cleanup)
        root_patch = mock.patch.object(barrier, "REPO_ROOT", self.work)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        scratch_patch = mock.patch.object(
            barrier, "SCRATCH_ROOT", self.work / ".tmp" / "core-spec")
        scratch_patch.start()
        self.addCleanup(scratch_patch.stop)

    # --------------------------------------------------------------- helpers
    def make_spec(
        self,
        machines: tuple[str, ...],
        *,
        traces: bool = True,
        snapshots: bool = True,
        omit_traces: tuple[str, ...] = (),
        empty_traces: tuple[str, ...] = (),
        view_anchor: bool = True,
        snapshot_text: str = DEFAULT_SNAPSHOT,
    ) -> pathlib.Path:
        spec_dir = pathlib.Path(tempfile.mkdtemp(dir=self.work, prefix="spec-"))
        (spec_dir / "traces").mkdir()
        (spec_dir / "snapshots").mkdir()
        chunks = ["# fixture\n"]
        for machine in machines:
            chunks.append(oracle_block(machine))
            chunks.append(label_fence(machine, f"state Start = 起点\n"
                                               f"event EvStep = 一步\n"
                                               f"detail D0 = 细节\n"))
            chunks.append(errmap_fence(machine, "raw none = ErrNone\n"))
            if view_anchor:
                chunks.append(f"<!-- RENDERED-ORACLE:{machine} BEGIN -->\n```mermaid\n"
                              f"{DEFAULT_RENDERED}```\n<!-- RENDERED-ORACLE:{machine} END -->\n")
            if snapshots:
                # 新格式契约：夹具与生产一致，一律落确定性 gz（不得回归明文 .oracle.snap）。
                write_snap_gz(spec_dir, machine, snapshot_text)
            if traces and machine not in omit_traces:
                corpus = "" if machine in empty_traces else oracle_corpus(
                    machine, f"CORPUS_ONLY_{machine.upper()}")
                (spec_dir / "traces" / f"{machine}.oracle").write_text(
                    corpus, encoding="utf-8", newline="\n")
        (spec_dir / "spec.md").write_text("\n".join(chunks), encoding="utf-8", newline="\n")
        return spec_dir

    def make_semantics_only_spec(self) -> pathlib.Path:
        spec_dir = self.work / "spec-sem"
        spec_dir.mkdir()
        (spec_dir / "spec.md").write_text(
            "# fixture\n" + semantics_block("m"), encoding="utf-8", newline="\n")
        return spec_dir

    def run_cli(self, argv: list[str], stub: DriverStub) -> tuple[int, str]:
        """跑真实 ``main()``：只在 subprocess / ghc 探测边界打桩，结论只看退出码。"""
        out = io.StringIO()
        with mock.patch.object(barrier.subprocess, "run", stub):
            with mock.patch.object(barrier.shutil, "which", return_value="ghc"):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    try:
                        rc = barrier.main(argv)
                    except SystemExit as exc:      # 参数解析器与声明的 CLI 调用不一致
                        self.fail(f"main 拒绝参数 {argv}：SystemExit({exc.code})")
        return rc, out.getvalue()

    # ---------------------------------------------------------------- tests
    def test_oracle_cli_without_block_exits_nonzero(self) -> None:
        spec_dir = self.make_semantics_only_spec()
        rc, out = self.run_cli([str(spec_dir), "--oracle"], DriverStub())
        self.assertNotEqual(rc, 0, out)

    def test_oracle_cli_no_corpus_uses_graph_deferred_count(self) -> None:
        """无本机 `traces/<m>.oracle`：声明条数**只**从 graph 的 `WITNESS DEFERRED <n>` 读。

        * ``n > 0`` → 未验证（退 3）且**零** tracecheck 调用；
        * ``n == 0`` → 迹线门**跳过**（不红、同样零调用），但**该机器不计 strict pass** →
          整体仍退 3（契约 §4.4：零证据不得算绿）；
        * 读不出该行 → fail-closed 视作 ``n > 0`` → 退 3；
        * snapshot output is not used as a fallback when the graph count is absent.
        """
        cases = (
            ("witness>0", DEFAULT_SNAPSHOT,
             (3, "WITNESS DEFERRED 2 (judged by tracecheck)\nSTRICT no\n"), 3),
            ("witness==0-skip-not-strict-pass", DEFAULT_SNAPSHOT,
             (3, "WITNESS DEFERRED 0 (judged by tracecheck)\nSTRICT no\n"), 3),
            ("no-marker-fail-closed", DEFAULT_SNAPSHOT, (0, DEFAULT_GRAPH), 3),
            ("snapshot-witness-not-a-source", SNAP_WITNESS, (0, DEFAULT_GRAPH), 3),
        )
        for label, snapshot_text, graph, expect in cases:
            with self.subTest(case=label):
                spec_dir = self.make_spec(("a",), traces=False, snapshot_text=snapshot_text)
                stub = DriverStub(snapshot=(0, snapshot_text), graph=graph, review=(0, ""))
                rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
                self.assertEqual(rc, expect, out)
                self.assertEqual(stub.tracecheck_inputs(), [], out)

    def test_oracle_cli_zero_witness_with_corpus_still_tracechecked(self) -> None:
        """`n==0` **且有**语料：照常交 tracecheck 判定；全 PASS 且各门干净 → 可 strict pass。

        与"无语料 → 跳过且不计 strict pass"是两条不同的路（契约 §4.4）：有语料时语料是证据，
        仍走驱动，不因 requiredWitnesses=0 而跳过。
        """
        spec_dir = self.make_spec(("a",))
        stub = DriverStub(graph=(3, "WITNESS DEFERRED 0 (judged by tracecheck)\nSTRICT no\n"),
                          review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(stub.tracecheck_inputs()), 1, out)
        self.assertIn("CORPUS_ONLY_A", stub.tracecheck_inputs()[0], out)

    def test_oracle_cli_rejects_other_machine_corpus(self) -> None:
        """只有别的机器的语料：本机器仍算缺证据（未验证），且不喂它的语料给它。"""
        spec_dir = self.make_spec(("a", "b"), omit_traces=("a",))
        stub = DriverStub(review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 3, out)
        payloads = stub.tracecheck_inputs()
        self.assertEqual(len(payloads), 1, out)
        self.assertIn("CORPUS_ONLY_B", payloads[0], out)
        self.assertNotIn("CORPUS_ONLY_A", payloads[0], out)

    def test_oracle_cli_empty_corpus_is_handed_to_driver(self) -> None:
        """空文件（零记录）屏障**不拦**：原样交 tracecheck，由驱动按 §5 `empty` 判 FAIL。

        驱动退 2 → 屏障 FAIL（1）。
        """
        spec_dir = self.make_spec(("a",), empty_traces=("a",))
        stub = DriverStub(tracecheck=(2, "FAIL empty line=0 输入无任何记录\nSTRICT no\n"),
                          review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 1, out)
        self.assertEqual(stub.tracecheck_inputs(), [""], out)

    def test_oracle_cli_maps_driver_exit_codes(self) -> None:
        """驱动退出码契约 §4.4：1→屏障 2（用法错）、2→屏障 1、3→屏障 3、124→屏障 1。"""
        for label, kwargs, expect in (
            ("usage-1", {"snapshot": (1, "")}, 2),
            ("fail-2", {"snapshot": (2, "FAIL x\n")}, 1),
            ("unverified-3", {"tracecheck": (3, "UNVERIFIED Foo required-path-unreached\n")}, 3),
            ("timeout-124", {"snapshot": (124, "")}, 1),
        ):
            with self.subTest(case=label):
                spec_dir = self.make_spec(("a",))
                stub = DriverStub(review=(0, ""), **kwargs)
                rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
                self.assertEqual(rc, expect, out)

    def test_oracle_cli_graph_neutral_markers(self) -> None:
        """graph 的 `WITNESS DEFERRED`/自身 `STRICT no`/partial coverage **中性**；
        只有真 WARN/UNVERIFIED 才非绿（评审门打桩为干净以隔离该轴）。"""
        spec_dir = self.make_spec(("a",))
        neutral = DriverStub(graph=(3, GRAPH_NEUTRAL), review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], neutral)
        self.assertEqual(rc, 0, out)
        warned = DriverStub(graph=(3, "WARN coverage-partial\nSTRICT no\n"), review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], warned)
        self.assertEqual(rc, 3, out)

    def test_oracle_cli_empty_driver_output_is_fail(self) -> None:
        """驱动退出 0 但输出为空：失败闭合（退 1），不得当通过。"""
        spec_dir = self.make_spec(("a",))
        for label, kwargs in (("snapshot", {"snapshot": (0, "")}),
                              ("tracecheck", {"tracecheck": (0, "")})):
            with self.subTest(mode=label):
                stub = DriverStub(review=(0, ""), **kwargs)
                rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
                self.assertEqual(rc, 1, out)

    def test_oracle_cli_rejects_nonzero_driver_exit(self) -> None:
        """未约定的非零（如 5）也按 fail-closed 判 FAIL（1）。"""
        spec_dir = self.make_spec(("a",))
        stub = DriverStub(tracecheck=(5, "FAIL something\n"), review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 1, out)

    def test_oracle_cli_missing_view_anchor_is_not_strict(self) -> None:
        """缺 RENDERED-ORACLE 锚点 → 视图门未执行 → 本次不得判 strict（退 3）。

        这里把评审门打桩成"干净"，隔离证明：单靠视图门被跳过就足以否掉 strict。
        """
        spec_dir = self.make_spec(("a",), view_anchor=False)
        stub = DriverStub(review=(0, ""))
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 3, out)

    def test_oracle_cli_missing_review_is_not_strict(self) -> None:
        """门全绿但缺 ORACLE 评审留档 → 不得判 strict（退 3，不得冒充 review pass）。"""
        spec_dir = self.make_spec(("a",))
        stub = DriverStub(review=DEFAULT_REVIEW)
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 3, out)

    def test_oracle_cli_routes_only_owning_corpus(self) -> None:
        """跨机语料不得混装：每台机器的驱动实例只收到自己的 traces/<machine>.oracle。

        The review record is absent, so the result stays unverified without a
        hard failure. Every machine still runs so the corpus-routing assertions
        can be observed.
        """
        spec_dir = self.make_spec(("a", "b"))
        stub = DriverStub()
        rc, out = self.run_cli([str(spec_dir), "--oracle"], stub)
        self.assertEqual(rc, 3, out)      # 机器门全过；仅评审留档缺（本夹具无 records）
        payloads = stub.tracecheck_inputs()
        self.assertEqual(len(payloads), 2, out)
        self.assertEqual(sum("CORPUS_ONLY_A" in p for p in payloads), 1, out)
        self.assertEqual(sum("CORPUS_ONLY_B" in p for p in payloads), 1, out)
        for payload in payloads:
            self.assertFalse("CORPUS_ONLY_A" in payload and "CORPUS_ONLY_B" in payload,
                             "同一机器实例收到了两台机器的语料")

    def test_oracle_cli_update_snapshot_validates_all_before_writing(self) -> None:
        """--update-snapshot：先校验全部机器；任一台无效则整组不落盘。"""
        spec_dir = self.make_spec(("a", "b"), snapshots=False)
        before_spec = (spec_dir / "spec.md").read_text(encoding="utf-8")
        stub = DriverStub(snapshot=[(0, DEFAULT_SNAPSHOT), (0, "")],
                          render=(0, DEFAULT_RENDERED))
        rc, out = self.run_cli([str(spec_dir), "--oracle", "--update-snapshot"], stub)
        self.assertNotEqual(rc, 0, out)
        for machine in ("a", "b"):
            for snap in (snap_gz_path(spec_dir, machine), snap_plain_path(spec_dir, machine)):
                self.assertFalse(snap.exists(), f"半组落盘：{snap.name} 已写出")
        self.assertEqual((spec_dir / "spec.md").read_text(encoding="utf-8"), before_spec,
                         "校验失败仍改写了 spec.md 的 RENDERED-ORACLE 围栏")


class OracleSnapshotGzStorageTest(unittest.TestCase):
    """Deterministic gzip snapshot storage through the real barrier orchestration.

    Only subprocess and executable discovery are controlled; assertions observe
    barrier results and persisted bytes.
    """

    def setUp(self) -> None:
        self.temporary_root = tempfile.TemporaryDirectory(prefix="oracle-snapshots-")
        self.work = pathlib.Path(self.temporary_root.name)
        self.addCleanup(self.temporary_root.cleanup)
        root_patch = mock.patch.object(barrier, "REPO_ROOT", self.work)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        scratch_patch = mock.patch.object(
            barrier, "SCRATCH_ROOT", self.work / ".tmp" / "core-spec")
        scratch_patch.start()
        self.addCleanup(scratch_patch.stop)

    # --------------------------------------------------------------- helpers
    def make_spec(self, machines: tuple[str, ...], *, view_anchor: bool = True,
                  traces: bool = True) -> pathlib.Path:
        """只落 spec.md 与 traces/；存储文件由各用例自行落盘——那正是被测契约。"""
        spec_dir = pathlib.Path(tempfile.mkdtemp(dir=self.work, prefix="spec-"))
        (spec_dir / "traces").mkdir()
        (spec_dir / "snapshots").mkdir()
        chunks = ["# fixture\n"]
        for machine in machines:
            chunks.append(oracle_block(machine))
            chunks.append(label_fence(machine, "state Start = 起点\n"
                                               "event EvStep = 一步\n"
                                               "detail D0 = 细节\n"))
            chunks.append(errmap_fence(machine, "raw none = ErrNone\n"))
            if view_anchor:
                chunks.append(f"<!-- RENDERED-ORACLE:{machine} BEGIN -->\n```mermaid\n"
                              f"{DEFAULT_RENDERED}```\n<!-- RENDERED-ORACLE:{machine} END -->\n")
            if traces:
                (spec_dir / "traces" / f"{machine}.oracle").write_text(
                    oracle_corpus(machine, f"CORPUS_ONLY_{machine.upper()}"),
                    encoding="utf-8", newline="\n")
        (spec_dir / "spec.md").write_text("\n".join(chunks), encoding="utf-8", newline="\n")
        return spec_dir

    def run_cli(self, argv: list[str], stub: DriverStub) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(barrier.subprocess, "run", stub):
            with mock.patch.object(barrier.shutil, "which", return_value="ghc"):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    try:
                        rc = barrier.main(argv)
                    except SystemExit as exc:      # 参数解析器与声明的 CLI 调用不一致
                        self.fail(f"main 拒绝参数 {argv}：SystemExit({exc.code})")
        return rc, out.getvalue()

    def run_check(self, spec_dir: pathlib.Path,
                  expected: str = FULL_SNAPSHOT) -> tuple[int, str]:
        """``--oracle`` 检查门（评审留档打桩为干净，隔离快照存储这一轴）。"""
        return self.run_cli([str(spec_dir), "--oracle"],
                            DriverStub(snapshot=(0, expected), review=(0, "")))

    def run_update(self, spec_dir: pathlib.Path,
                   expected: str = FULL_SNAPSHOT) -> tuple[int, str]:
        """``--oracle --update-snapshot``。"""
        return self.run_cli([str(spec_dir), "--oracle", "--update-snapshot"],
                            DriverStub(snapshot=(0, expected), render=(0, DEFAULT_RENDERED)))

    def assert_hard_fail(self, rc: int, out: str, label: str = "") -> None:
        self.assertEqual(
            rc, 1,
            f"{label}：存储异常必须硬失败退 1（实际 {rc}；3=未验证不得冒充、0=不得伪绿）\n{out}")

    def assert_gz_control_accepted(self) -> None:
        """正控：同布局下**有效** gz 必须被接纳（退 0）。

        否则其它用例里的退 1 只证明"文件缺失"，证明不了屏障真的解压并校验了 gz 内容。
        """
        spec_dir = self.make_spec(("a",))
        write_snap_gz(spec_dir, "a", FULL_SNAPSHOT)
        rc, out = self.run_check(spec_dir)
        self.assertEqual(rc, 0, f"正控失效：有效 gz 未被接纳\n{out}")

    # ---------------------------------------------------------------- tests
    def test_snapshot_gz_roundtrip_keeps_full_text(self) -> None:
        """有效 gz 被接纳（退 0），且读路径既不改写字节也不重写文件。"""
        spec_dir = self.make_spec(("a",))
        gz = write_snap_gz(spec_dir, "a", FULL_SNAPSHOT)
        before = gz.read_bytes()
        stamp = freeze_mtime_ns(gz)
        rc, out = self.run_check(spec_dir)
        self.assertEqual(rc, 0, out)
        self.assertEqual(gz.read_bytes(), before, "读路径改写了存储字节")
        self.assertEqual(gz.stat().st_mtime_ns, stamp, "读路径重写了存储文件")
        # 往返：独立解压必须是完整全文（非 ASCII、末尾换行、事实行一条不少）。
        released = read_snap_gz(gz)
        self.assertEqual(released, FULL_SNAPSHOT, out)
        self.assertEqual(released.count("MODEL "), 2)
        self.assertEqual(released.count("TRANS "), 2)
        self.assertEqual(released.count("PROP "), 2)

    def test_snapshot_gz_canonical_write_is_deterministic(self) -> None:
        """--update-snapshot：确定性 gzip（MTIME=0、无 FNAME、level 9），同一文本同一字节。"""
        paths = []
        for _ in range(2):
            spec_dir = self.make_spec(("a",))
            rc, out = self.run_update(spec_dir)
            self.assertEqual(rc, 0, out)
            gz = snap_gz_path(spec_dir, "a")
            self.assertTrue(gz.is_file(), f"未落盘新格式快照：{gz}")
            self.assertFalse(snap_plain_path(spec_dir, "a").exists(),
                             "新格式下不得再落明文 .oracle.snap")
            paths.append(gz)
        first = paths[0].read_bytes()
        second = paths[1].read_bytes()
        self.assertEqual(first, second, "同一模型两次生成字节不同（非确定性编码）")
        self.assertEqual(first, snap_gz_bytes(FULL_SNAPSHOT),
                         "落盘字节与独立构造的规范 gzip（MTIME=0、level 9、无 FNAME）不一致")
        self.assertEqual(first[:4], b"\x1f\x8b\x08\x00",
                         "gzip 头 FLG 非 0（含 FNAME/注释等非确定字段）")
        self.assertEqual(first[4:8], b"\x00\x00\x00\x00", "gzip 头 MTIME 非 0（非确定性）")
        self.assertEqual(read_snap_gz(paths[0]), FULL_SNAPSHOT, "往返丢失内容")

    def test_update_snapshot_equivalent_gz_is_not_rewritten(self) -> None:
        """等价载荷（解压后规范化全文相同）不得重写：字节与 mtime 都要原样。"""
        spec_dir = self.make_spec(("a",))
        # 模拟另一台机器/压缩器：非规范时间戳和level，不改变解压后的完整事实。
        foreign_blob = gzip.compress(FULL_SNAPSHOT.encode("utf-8"), compresslevel=1, mtime=12345)
        gz = write_snap_gz_raw(spec_dir, "a", foreign_blob)
        before = gz.read_bytes()
        stamp = freeze_mtime_ns(gz)
        rc, out = self.run_update(spec_dir)
        self.assertEqual(rc, 0, out)
        self.assertEqual(gz.read_bytes(), before, "等价载荷被重写（字节变了）")
        self.assertEqual(gz.stat().st_mtime_ns, stamp, "等价载荷被重写（mtime 变了）")
        self.assertFalse(snap_plain_path(spec_dir, "a").exists(),
                         "--update-snapshot 落了明文 .oracle.snap（新格式不得回归明文）")

    def test_update_snapshot_crlf_payload_is_equivalent_and_not_rewritten(self) -> None:
        """CRLF / 孤立 CR 经 EOL 规范化后等价：检查门不判漂移，更新不重写。"""
        crlf = FULL_SNAPSHOT.replace("\n", "\r\n")
        mixed = crlf.replace("\r\nTRANS m1 EvStop", "\rTRANS m1 EvStop")
        self.assertEqual(mixed.count("\r\n"), FULL_SNAPSHOT.count("\n") - 1,
                         "夹具：应恰含一个孤立 CR")
        self.assertEqual(lf_text(mixed), FULL_SNAPSHOT, "夹具：规范化后应等于期望全文")

        spec_dir = self.make_spec(("a",))
        gz = write_snap_gz_raw(spec_dir, "a", gzip_bytes(mixed))
        before = gz.read_bytes()
        stamp = freeze_mtime_ns(gz)
        rc, out = self.run_update(spec_dir)
        self.assertEqual(rc, 0, out)
        self.assertEqual(gz.read_bytes(), before, "EOL 归一化等价载荷被重写（字节变了）")
        self.assertEqual(gz.stat().st_mtime_ns, stamp, "EOL 归一化等价载荷被重写（mtime 变了）")
        self.assertFalse(snap_plain_path(spec_dir, "a").exists(), "更新落了明文 .oracle.snap")
        rc2, out2 = self.run_check(spec_dir)
        self.assertEqual(rc2, 0, out2)                      # CRLF 存储不得判漂移

    def test_snapshot_gz_content_drift_fails_closed(self) -> None:
        """解压后与驱动输出不同 → 硬失败退 1；旧明文在场也不得兜底。"""
        self.assert_gz_control_accepted()
        spec_dir = self.make_spec(("a",))
        gz = write_snap_gz(spec_dir, "a", DRIFT_LAST_LINE)
        before = gz.read_bytes()
        write_snap_plaintext(spec_dir, "a", FULL_SNAPSHOT)   # 明文正确也不得回退
        rc, out = self.run_check(spec_dir)
        self.assert_hard_fail(rc, out, "快照漂移")
        self.assertEqual(gz.read_bytes(), before, "失败路径改写了存储")

    def test_snapshot_gz_trailing_content_drift_fails_closed(self) -> None:
        """比较必须覆盖全文：仅末行不同 / 少一行 / 缺末尾换行 都判漂移（退 1）。"""
        self.assert_gz_control_accepted()
        cases = (
            ("last-line-changed", DRIFT_LAST_LINE),
            ("last-line-dropped", "\n".join(FULL_SNAPSHOT.splitlines()[:-1]) + "\n"),
            ("missing-final-newline", FULL_SNAPSHOT.rstrip("\n")),
        )
        for label, payload in cases:
            with self.subTest(case=label):
                self.assertNotEqual(lf_text(payload), FULL_SNAPSHOT, f"{label}：夹具与期望相同")
                spec_dir = self.make_spec(("a",))
                write_snap_gz(spec_dir, "a", payload)
                rc, out = self.run_check(spec_dir)
                self.assert_hard_fail(rc, out, label)

    def test_snapshot_gz_corrupted_truncated_invalid_hard_fail(self) -> None:
        """损坏 / 截断 / 非法 gzip / 空文件：一律硬失败退 1（不得 3）；且不得改写存储或回退明文。"""
        self.assert_gz_control_accepted()
        valid = snap_gz_bytes(FULL_SNAPSHOT)
        mid = len(valid) // 2
        cases = (
            ("truncated", valid[:mid]),
            ("crc-corrupt", valid[:-8] + b"\xde\xad\xbe\xef\xde\xad\xbe\xef"),
            ("deflate-corrupt", valid[:mid] + bytes([valid[mid] ^ 0xFF]) + valid[mid + 1:]),
            ("not-gzip", b"PK\x03\x04 not a gzip stream\n"),
            ("empty", b""),
        )
        for label, blob in cases:
            with self.subTest(case=label):
                self.assertNotEqual(blob, valid, f"{label}：夹具与合法字节相同")
                spec_dir = self.make_spec(("a",))
                gz = write_snap_gz_raw(spec_dir, "a", blob)
                # 不落明文：必须真正走到 gzip 解码负控，不能被并存检查提前挡住。
                rc, out = self.run_check(spec_dir)
                self.assert_hard_fail(rc, out, f"损坏存储 {label}")
                self.assertEqual(gz.read_bytes(), blob, f"{label}：失败路径改写了存储")

    def test_snapshot_gz_trailing_bytes_and_second_member_hard_fail(self) -> None:
        """尾随垃圾或第二 gzip 成员不可被忽略；必须校验完整工件。"""
        self.assert_gz_control_accepted()
        valid = snap_gz_bytes(FULL_SNAPSHOT)
        for extra in (b"trailing junk", snap_gz_bytes("hidden facts\n")):
            with self.subTest(extra=extra[:16]):
                spec_dir = self.make_spec(("a",))
                write_snap_gz_raw(spec_dir, "a", valid + extra)
                rc, out = self.run_check(spec_dir)
                self.assert_hard_fail(rc, out, "尾随数据")

    def test_update_snapshot_over_limit_writes_no_artifacts(self) -> None:
        """写入侧也必须预先拒绝超限，不能生成自身读不回的工件。"""
        for limit_name in ("ORACLE_SNAP_MAX_BYTES", "ORACLE_SNAP_ARTIFACT_MAX_BYTES"):
            with self.subTest(limit=limit_name):
                spec_dir = self.make_spec(("a", "b"))
                with mock.patch.object(barrier, limit_name, 16):
                    rc, out = self.run_update(spec_dir)
                self.assert_hard_fail(rc, out, "写入超上限")
                for machine in ("a", "b"):
                    self.assertFalse(snap_gz_path(spec_dir, machine).exists(), "超限仍落盘")
                    self.assertFalse(snap_plain_path(spec_dir, machine).exists(), "超限仍落明文")

    def test_snapshot_gz_invalid_utf8_hard_fails(self) -> None:
        """合法 gzip 但载荷非 UTF-8 → 硬失败退 1（不得 3，不得 replace 后当通过，不得回退明文）。"""
        spec_dir = self.make_spec(("a",))
        blob = gzip_raw_bytes(b"\xff\xfe\x00PROP A|PASS|0\n")
        gz = write_snap_gz_raw(spec_dir, "a", blob)
        # 无旧明文并存：必须由严格 UTF-8 解码本身拒绝载荷。
        rc, out = self.run_check(spec_dir)
        self.assert_hard_fail(rc, out, "非 UTF-8 载荷")
        self.assertEqual(gz.read_bytes(), blob, "失败路径改写了存储")

    def test_snapshot_gz_decompression_limit_hard_fails(self) -> None:
        """解压后超过上限 → 硬失败退 1（不得 3、不得 0）：负控把上限调到远小于载荷。"""
        name = snap_limit_name()
        self.assertIsNotNone(
            name, f"屏障未暴露解压上限常量（候选：{', '.join(SNAP_LIMIT_NAMES)}）")
        spec_dir = self.make_spec(("a",))
        write_snap_gz(spec_dir, "a", FULL_SNAPSHOT)
        self.assertGreater(len(FULL_SNAPSHOT.encode("utf-8")), 64, "夹具需大于注入上限")
        with mock.patch.object(barrier, name, 64):
            rc, out = self.run_check(spec_dir)
        self.assert_hard_fail(rc, out, "超解压上限")
        self.assertFalse(snap_plain_path(spec_dir, "a").exists(), "失败路径落了明文")

    def test_large_snapshot_above_safety_cap_hard_fails(self) -> None:
        name = snap_limit_name()
        self.assertIsNotNone(
            name, f"屏障未暴露解压上限常量（候选：{', '.join(SNAP_LIMIT_NAMES)}）")
        spec_dir = self.make_spec(("a",))
        oversized = "x" * (LARGE_SNAPSHOT_MIN_BYTES + 1)
        write_snap_gz(spec_dir, "a", oversized)
        with mock.patch.object(barrier, name, LARGE_SNAPSHOT_MIN_BYTES):
            rc, out = self.run_check(spec_dir)
        self.assert_hard_fail(rc, out, "合理大载荷超过注入上限")

    def test_snapshot_limit_allows_reasonable_large_fixture(self) -> None:
        """The decompressed-size cap supports a practical large snapshot."""
        name = snap_limit_name()
        self.assertIsNotNone(
            name, f"屏障未暴露解压上限常量（候选：{', '.join(SNAP_LIMIT_NAMES)}）")
        limit = int(getattr(barrier, name))
        self.assertGreaterEqual(limit, LARGE_SNAPSHOT_MIN_BYTES,
                                f"{name}={limit} 小于合理大快照边界 {LARGE_SNAPSHOT_MIN_BYTES} B")

    def test_snapshot_missing_storage_hard_fails(self) -> None:
        """无任何存储工件 → 硬失败退 1（不得 3 冒充未验证）。"""
        spec_dir = self.make_spec(("a",))
        rc, out = self.run_check(spec_dir)
        self.assert_hard_fail(rc, out, "无存储工件")

    def test_snapshot_plaintext_only_hard_fails(self) -> None:
        """只存在旧明文 ``.oracle.snap``：必须硬失败（退 1），绝不静默按明文验收。"""
        spec_dir = self.make_spec(("a",))
        plain = write_snap_plaintext(spec_dir, "a", FULL_SNAPSHOT)
        rc, out = self.run_check(spec_dir)
        self.assert_hard_fail(rc, out, "旧明文存储")
        self.assertEqual(plain.read_text(encoding="utf-8"), FULL_SNAPSHOT)
        self.assertFalse(snap_gz_path(spec_dir, "a").exists(),
                         "检查门不得就地迁移（迁移只属 --update-snapshot）")

    def test_update_snapshot_migrates_plaintext_and_removes_it(self) -> None:
        """迁移：旧明文 → gz，明文必须删除（同一工件不得双份），迁移后可正常检查。"""
        spec_dir = self.make_spec(("a",))
        plain = write_snap_plaintext(spec_dir, "a", FULL_SNAPSHOT)
        rc, out = self.run_update(spec_dir)
        self.assertEqual(rc, 0, out)
        gz = snap_gz_path(spec_dir, "a")
        self.assertTrue(gz.is_file(), "旧明文未迁移出 .gz")
        self.assertFalse(plain.exists(), "迁移后旧明文未删除")
        self.assertEqual(gz.read_bytes(), snap_gz_bytes(FULL_SNAPSHOT), "迁移字节非规范编码")
        self.assertEqual(read_snap_gz(gz), FULL_SNAPSHOT, "迁移丢内容")
        rc2, out2 = self.run_check(spec_dir)
        self.assertEqual(rc2, 0, out2)

    def test_update_snapshot_multi_machine_failure_writes_no_gz(self) -> None:
        """任一台机器求值无效 → 整组不落盘：新格式 gz 也不得出现半组。"""
        good = self.make_spec(("a", "b"))
        rc, out = self.run_update(good)                     # 正控：两台有效则整组落盘
        self.assertEqual(rc, 0, out)
        for machine in ("a", "b"):
            self.assertTrue(snap_gz_path(good, machine).is_file(), f"{machine} 未落盘")
            self.assertFalse(snap_plain_path(good, machine).exists())

        bad = self.make_spec(("a", "b"))
        stub = DriverStub(snapshot=[(0, FULL_SNAPSHOT), (0, "")],
                          render=(0, DEFAULT_RENDERED))
        rc, out = self.run_cli([str(bad), "--oracle", "--update-snapshot"], stub)
        self.assertNotEqual(rc, 0, out)
        for machine in ("a", "b"):
            self.assertFalse(snap_gz_path(bad, machine).exists(),
                             f"半组落盘：{machine}{SNAP_GZ_SUFFIX} 已写出")
            self.assertFalse(snap_plain_path(bad, machine).exists(),
                             f"半组落盘：{machine}{SNAP_PLAIN_SUFFIX} 已写出")

    def test_update_snapshot_repairs_corrupt_gz(self) -> None:
        """损坏的既有 gz：``--update-snapshot`` 必须按嵌入代码重建为规范字节（可恢复）。"""
        spec_dir = self.make_spec(("a",))
        gz = write_snap_gz_raw(spec_dir, "a", b"\x1f\x8b\x08\x00 trunc")
        rc, out = self.run_update(spec_dir)
        self.assertEqual(rc, 0, out)
        self.assertEqual(gz.read_bytes(), snap_gz_bytes(FULL_SNAPSHOT), "损坏存储未被规范化重建")
        self.assertEqual(read_snap_gz(gz), FULL_SNAPSHOT, "重建丢内容")
        self.assertFalse(snap_plain_path(spec_dir, "a").exists(), "重建时落了明文")


if __name__ == "__main__":
    unittest.main()
