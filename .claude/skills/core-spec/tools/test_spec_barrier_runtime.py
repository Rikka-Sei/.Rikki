#!/usr/bin/env python3
"""spec_barrier 运行时屏障回归测试：真实 python 屏障对进程非零退出/超时/空输出的处理。

被测边界是既有 API——``spec_barrier.check_semantics`` 的全门判定，以及它内部
``eval_sem`` 的调用方式。子进程结果在 ``subprocess.run`` 边界受控替换（因此不需要 ghc），
但 ``check_semantics``、``run``、标签/围栏解析等真实代码全部照常执行；另有一条测试
真实拉起 python 子进程，证明 ``run`` 本体不是被纯 mock 掉的。

测试命名的都是生产行为（改动实现会破坏的行为）：
  * 编译成功但 snapshot 进程非零退出 / 空输出；
  * render 进程非零退出；
  * tracecheck 进程非零退出、以及 legacy 语料空/垃圾；
  * driver 挂起（超时）；
  * 空/垃圾 legacy 语料不得被当成"全部门绿"；
  * 合法 legacy 语料默认 strict 返回专用未验证状态码 3（显式 allow_legacy/`--allow-legacy` 才兼容 0）；
  * 多机器聚合：任一机器硬失败（1）优先于"仅 legacy 未验证"（3），且 3 不得短路扫描；
  * 超时要保留超时前的 stderr/stdout 原文；
  * 屏障自身的临时编译目录也必须落在 repo/.tmp；
  * `--update-snapshot` 必须 snapshot 与 render 都 valid 才落盘（不留半写）。

Each fixture uses an isolated temporary repository root with UTF-8/LF files;
the barrier's scratch-directory placement is checked relative to that root.
Fixtures stay outside ``docs/specs`` and use the current SEMANTICS / LABEL /
RENDERED / TRACE text forms.

运行：python .claude/skills/core-spec/tools/test_spec_barrier_runtime.py
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import spec_barrier as barrier  # noqa: E402

# 生产代码在 check_semantics 末尾打印的"全门通过"字样；空/垃圾语料不得打印它。
GREEN_CLAIM = "全部门绿"
# 可接受的"未验证/未生效"指示（不锁定具体措辞，只要求确有标示或非零退出）。
UNVERIFIED_HINT = re.compile(r"(?i)(unverif|未验证|无法验证|跳过|skip|warn|fail|legacy|旧版|降级)")
LEGACY_HINT = re.compile(r"(?i)(legacy|unverif|未验证|无法验证|降级|旧版|旧语料|跳过|skip|仅报告)")
LEGACY_UNVERIFIED_RC = 3

# 与真实 driver 输出一致的合法迹线（traceCheck 对合法迹线返回空列表 → stdout 为空）。
VALID_TRACE = "TRACE s1|EvStep|Start|Done|EffectA\n"
DEFAULT_SNAPSHOT = "STATE Start\nSTATE Done\nPROP TerminalClosure|PASS|0\nPROP Liveness|PASS|0\n"
DEFAULT_RENDERED = 'stateDiagram-v2\n    state "未解析" as Start\n'


class DriverStub:
    """替换 ``subprocess.run``：按命令分派受控结果，记录调用。

    只控制进程结果（returncode/stdout），check_semantics 与 run 的真实逻辑照常执行。
    ``raise_on`` 里的模式模拟"进程挂起 → TimeoutExpired"。
    """

    def __init__(
        self,
        *,
        ghc: tuple[int, str] = (0, ""),
        snapshot: tuple[int, str] = (0, DEFAULT_SNAPSHOT),
        render: tuple[int, str] = (0, DEFAULT_RENDERED),
        tracecheck: tuple[int, str] = (0, ""),
        review: tuple[int, str] = (0, ""),
        raise_on: tuple[str, ...] = (),
    ) -> None:
        self.results = {"ghc": ghc, "snapshot": snapshot, "render": render,
                        "tracecheck": tracecheck, "--check": review}
        self.raise_on = set(raise_on)
        self.used: dict[str, int] = {}
        self.calls: list[tuple[str, list[str], str | None]] = []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        if cmd and pathlib.Path(cmd[0]).name == "ghc":
            mode = "ghc"
        elif cmd:
            mode = cmd[-1]
        else:
            mode = ""
        self.calls.append((mode, cmd, kwargs.get("input")))
        if mode in self.raise_on:
            raise subprocess.TimeoutExpired(cmd, 1)
        spec = self.results.get(mode, (0, ""))
        if isinstance(spec, list):          # 多机器：按调用次序逐台给结果
            idx = self.used.get(mode, 0)
            self.used[mode] = idx + 1
            spec = spec[min(idx, len(spec) - 1)]
        rc, out = spec
        return subprocess.CompletedProcess(cmd, rc, out, "")


class SpecBarrierRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_root = tempfile.TemporaryDirectory(prefix="spec-barrier-runtime-")
        self.work = pathlib.Path(self.temporary_root.name)
        self.addCleanup(self.temporary_root.cleanup)
        root_patch = mock.patch.object(barrier, "REPO_ROOT", self.work)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        scratch_patch = mock.patch.object(
            barrier, "SCRATCH_ROOT", self.work / ".tmp" / "core-spec")
        scratch_patch.start()
        self.addCleanup(scratch_patch.stop)

    # ------------------------------------------------------------------ helpers
    def make_spec(
        self,
        *,
        name: str = "model",
        snapshot_text: str = DEFAULT_SNAPSHOT,
        rendered: str = DEFAULT_RENDERED,
        traces: tuple[tuple[str, str], ...] = (),
        extra_machines: tuple[str, ...] = (),
    ) -> dict:
        """在隔离临时仓库中造一份最小 spec：每台机器一组 SEMANTICS + LABEL + RENDERED + 快照。

        每次调用都新建独立子目录——subTest 里重复调用不会撞上已有目录。
        ``extra_machines`` 追加多台机器（一个 spec 多个 SEMANTICS 块）。
        """
        spec_dir = pathlib.Path(tempfile.mkdtemp(dir=self.work, prefix=f"spec-{name}-"))
        (spec_dir / "snapshots").mkdir()
        code = "module Semantics where\nvalue = 1\n"
        blocks = []
        for machine in (name, *extra_machines):
            blocks.append(
                f"<!-- SEMANTICS:{machine} BEGIN -->\n```haskell\n{code}```\n\n"
                f"```LABEL:{machine}\nstate S0 = 起点\n```\n\n"
                f"<!-- RENDERED:{machine} BEGIN -->\n```mermaid\n{rendered}\n```\n"
                f"<!-- RENDERED:{machine} END -->\n"
            )
            (spec_dir / "snapshots" / f"{machine}.snap").write_text(
                snapshot_text, encoding="utf-8", newline="\n")
        (spec_dir / "spec.md").write_text("# fixture\n\n" + "\n".join(blocks),
                                         encoding="utf-8", newline="\n")
        if traces:
            tdir = spec_dir / "traces"
            tdir.mkdir(exist_ok=True)
            for filename, content in traces:
                (tdir / filename).write_text(content, encoding="utf-8", newline="\n")
        return {"dir": spec_dir, "name": name, "code": code,
                "snapshot": snapshot_text, "rendered": rendered}

    def run_barrier(self, fx: dict, stub: DriverStub, update: bool = False,
                    allow_legacy: bool | None = None) -> tuple[int, str]:
        """跑真实 check_semantics；只在 subprocess.run 边界注入 stub。

        不 patch tempfile.tempdir：该 patch 会掩盖屏障是否把 scratch 放在目标仓库根目录。
        """
        out = io.StringIO()
        with mock.patch.object(barrier.subprocess, "run", stub):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                if allow_legacy is None:
                    rc = barrier.check_semantics(fx["dir"], fx["name"], fx["code"], update)
                else:
                    rc = barrier.check_semantics(fx["dir"], fx["name"], fx["code"], update,
                                                 allow_legacy=allow_legacy)
        return rc, out.getvalue()

    def run_cli(self, argv: list[str], stub: DriverStub) -> tuple[int, str]:
        """跑真实 main()（ghc 探测打桩）；结论只看退出码，不靠解析日志。"""
        out = io.StringIO()
        with mock.patch.object(barrier.subprocess, "run", stub):
            with mock.patch.object(barrier.shutil, "which", return_value="ghc"):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    try:
                        rc = barrier.main(argv)
                    except SystemExit as exc:      # argparse 拒绝未知参数
                        self.fail(f"main 拒绝参数 {argv}：SystemExit({exc.code})")
        return rc, out.getvalue()

    # ------------------------------------------------------------- run() 本体
    def test_real_run_reports_exit_code_and_stdout(self) -> None:
        """barrier.run 是真实实现：真实子进程的 returncode 与 stdout 都要取回。"""
        with contextlib.redirect_stdout(io.StringIO()):
            proc = barrier.run(
                [sys.executable, "-c", "import sys; sys.stdout.write('ok'); sys.exit(7)"],
                cwd=self.work,
            )
        self.assertEqual(proc.returncode, 7)
        self.assertIn("ok", proc.stdout or "")

    def test_timeout_preserves_partial_stderr(self) -> None:
        """超时必须保留超时前的 stderr 原文（附在返回对象并打印），不得只剩固定超时文字。"""
        def hang(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1, output="partial out\n", stderr="partial err\n")

        out = io.StringIO()
        with mock.patch.object(barrier.subprocess, "run", hang):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                proc = barrier.run([sys.executable, "-c", "pass"], cwd=self.work)
        self.assertNotEqual(proc.returncode, 0, out.getvalue())
        self.assertIn("partial err", proc.stderr or "", "超时丢弃了 stderr 根因")
        self.assertIn("partial err", out.getvalue(), "超时的 stderr 原文未打印")
        self.assertIn("partial out", proc.stdout or "")

    # ------------------------ 合法 legacy 语料：默认 strict 未验证，不得声称全门绿
    def test_healthy_legacy_corpus_returns_unverified_status(self) -> None:
        """编译/snapshot/render 全 0 且迹线合法：默认 strict 返回专用未验证状态码。

        旧 v1 语料只标 unverified/legacy：既不得打印"全门绿"、也不得返回 0
        （调用方 barriers.py 只看退出码，rc 0 会被记成 PASS），且必须与门失败 1 区分。
        """
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        stub = DriverStub(snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]))
        rc, out = self.run_barrier(fx, stub)
        self.assertEqual(rc, LEGACY_UNVERIFIED_RC,
                         f"默认 strict 下仅 legacy 证据必须返回未验证状态：rc={rc}\n{out}")
        self.assertNotEqual(rc, 1, "未验证不是门失败，必须与 1 区分")
        self.assertNotIn(GREEN_CLAIM, out, f"合法 legacy 语料不得打印全门绿：\n{out}")

    def test_check_semantics_allow_legacy_compat_returns_zero(self) -> None:
        """显式 allow_legacy=True 才兼容返回 0，且仍标 LEGACY/unverified（不得全门绿）。"""
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        stub = DriverStub(snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]))
        try:
            rc, out = self.run_barrier(fx, stub, allow_legacy=True)
        except TypeError as exc:
            self.fail(f"check_semantics 未接受 allow_legacy 兼容参数：{exc}")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, LEGACY_HINT, f"兼容模式未标注 legacy/unverified：\n{out}")
        self.assertNotIn(GREEN_CLAIM, out, f"兼容模式仍不得打印全门绿：\n{out}")

    # ------------------------------------------- CLI 契约：调用方只读退出码
    def test_cli_default_strict_legacy_returns_unverified_status(self) -> None:
        """默认（strict）CLI：只有旧 v1 迹线证据 → 专用未验证非零状态码（3）。"""
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        stub = DriverStub(snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]))
        rc, out = self.run_cli([str(fx["dir"])], stub)
        self.assertEqual(rc, LEGACY_UNVERIFIED_RC,
                         f"默认 strict CLI 应返回未验证状态：rc={rc}\n{out}")
        self.assertNotEqual(rc, 1, "未验证不能被记成门失败 1")

    def test_cli_allow_legacy_flag_returns_zero_and_labels_legacy(self) -> None:
        """显式 --allow-legacy 才兼容 rc 0，且输出仍标 LEGACY/unverified。"""
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        stub = DriverStub(snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]))
        rc, out = self.run_cli([str(fx["dir"]), "--allow-legacy"], stub)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, LEGACY_HINT, f"--allow-legacy 未标注 legacy/unverified：\n{out}")
        self.assertNotIn(GREEN_CLAIM, out, f"--allow-legacy 仍不得打印全门绿：\n{out}")

    def test_cli_hard_failure_outranks_legacy_unverified(self) -> None:
        """多机器（多 SEMANTICS 块）：前面机器的未验证（3）不得掩盖后面机器的真失败（1）。

        聚合规则：逐台扫完，硬失败优先；未验证只在无硬失败时作为整体结论。
        """
        for flags in ([], ["--allow-legacy"]):
            with self.subTest(flags=flags):
                fx = self.make_spec(extra_machines=("model2",),
                                    traces=(("ok.trace", VALID_TRACE),))
                # 第 1 台 snapshot 正常（legacy → 3，或 --allow-legacy 兼容 → 0），第 2 台 snapshot 崩 → 1
                stub = DriverStub(snapshot=[(0, fx["snapshot"]), (1, "")],
                                  render=(0, fx["rendered"]))
                rc, out = self.run_cli([str(fx["dir"]), *flags], stub)
                self.assertEqual(rc, 1,
                                 f"硬失败必须压过未验证（flags={flags}）：rc={rc}\n{out}")

    def test_barrier_scratch_stays_under_repo_tmp_and_is_cleaned(self) -> None:
        """屏障编译目录必须落在 repo/.tmp 之下且用完清理（中间产物只在 .tmp）。

        只断言 repo/.tmp 前缀，不锁定 scratch 子目录名：生产换子目录名不算破坏。
        """
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        stub = DriverStub(snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]))
        self.run_barrier(fx, stub)
        ghc = [cmd for mode, cmd, _ in stub.calls if mode == "ghc"]
        self.assertEqual(len(ghc), 1, stub.calls)
        includes = [arg[2:] for arg in ghc[0] if arg.startswith("-i")]
        scratch = [p for p in includes if p.startswith(str(self.work / ".tmp"))]
        self.assertEqual(len(scratch), 1, f"编译目录不在 repo/.tmp 下：{ghc[0]}")
        self.assertFalse(pathlib.Path(scratch[0]).exists(), "屏障未清理临时编译目录")

    # ----------------------------------------- --update-snapshot 的写前校验/落盘
    def test_update_snapshot_invalid_render_leaves_files_untouched(self) -> None:
        """--update-snapshot 必须两产物均 valid 才落盘：render 崩了不得半写快照。"""
        fx = self.make_spec(snapshot_text="STATE OLD\n")
        snap = fx["dir"] / "snapshots" / f"{fx['name']}.snap"
        md = fx["dir"] / "spec.md"
        before_snap = snap.read_text(encoding="utf-8")
        before_md = md.read_text(encoding="utf-8")
        stub = DriverStub(snapshot=(0, "STATE NEW\nPROP Liveness|PASS|0\n"), render=(1, ""))
        rc, out = self.run_barrier(fx, stub, update=True)
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(snap.read_text(encoding="utf-8"), before_snap, "render 崩了仍写了快照")
        self.assertEqual(md.read_text(encoding="utf-8"), before_md, "render 崩了仍改了 spec.md")

    def test_update_snapshot_writes_both_when_valid(self) -> None:
        """--update-snapshot 两产物 valid 时同时落盘，且更新锁用完清理。"""
        fx = self.make_spec(snapshot_text="STATE OLD\n", rendered="")
        stub = DriverStub(
            snapshot=(0, "STATE NEW\nPROP Liveness|PASS|0\n"),
            render=(0, "stateDiagram-v2\n  A --> B"),
        )
        rc, out = self.run_barrier(fx, stub, update=True)
        self.assertEqual(rc, 0, out)
        snap = fx["dir"] / "snapshots" / f"{fx['name']}.snap"
        self.assertEqual(snap.read_text(encoding="utf-8"),
                         "STATE NEW\nPROP Liveness|PASS|0\n")
        self.assertIn("stateDiagram-v2\n  A --> B",
                      (fx["dir"] / "spec.md").read_text(encoding="utf-8"))
        self.assertEqual(list((barrier.REPO_ROOT / ".tmp").glob("**/barrier-*.lock")), [],
                         "更新锁未清理")

    # ------------------------------------------------- 编译成功但 snapshot 异常
    def test_snapshot_driver_nonzero_exit_must_fail(self) -> None:
        """snapshot 进程非零退出必须 fail-closed——即使它吐出的文本与快照一字不差。

        （回归：eval_sem 曾丢弃 returncode，崩溃输出碰巧合法就被当成成功。）
        """
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(3, fx["snapshot"]), render=(0, fx["rendered"])))
        self.assertNotEqual(
            rc, 0,
            f"snapshot 进程非零退出却判通过（eval_sem 忽略 returncode）：rc={rc}\n{out}",
        )

    def test_snapshot_driver_empty_output_must_fail(self) -> None:
        """snapshot 空输出（rc 0 或非零）都不能被当成通过。"""
        for rc_in in (0, 3):
            with self.subTest(snapshot_rc=rc_in):
                fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
                rc, out = self.run_barrier(fx, DriverStub(
                    snapshot=(rc_in, ""), render=(0, fx["rendered"])))
                self.assertNotEqual(rc, 0, out)
                # 空输出既可在求值门直接拦下，也可落到性质门（无 PROP 行）——都必须非零。
                self.assertRegex(out, r"求值门|性质门")

    # ------------------------------------------------------------- render 异常
    def test_render_driver_nonzero_exit_must_fail(self) -> None:
        """render 进程非零退出必须 fail-closed；空 stdout 恰好与空围栏相等时更危险。"""
        fx = self.make_spec(rendered="", traces=(("ok.trace", VALID_TRACE),))
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, fx["snapshot"]), render=(1, "")))
        self.assertNotEqual(
            rc, 0,
            f"render 进程非零退出却判通过（空输出与空围栏比较相等）：rc={rc}\n{out}",
        )

    # ---------------------------------------------------------- tracecheck 异常
    def test_tracecheck_driver_nonzero_exit_must_fail(self) -> None:
        """tracecheck 进程非零退出（崩溃、无 FAIL 行）必须 fail-closed。"""
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]), tracecheck=(2, "")))
        self.assertNotEqual(
            rc, 0,
            f"tracecheck 进程非零退出却判通过（只按 stdout 里有无 FAIL 判定）：rc={rc}\n{out}",
        )

    def test_tracecheck_fail_line_must_fail(self) -> None:
        """tracecheck 打印 FAIL 反例行时必须失败（既有正确行为，锁住）。"""
        fx = self.make_spec(traces=(("bad.trace", VALID_TRACE),))
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]),
            tracecheck=(0, "FAIL #1 id=s1 event=EvStep expected=Done got=Other\n")))
        self.assertNotEqual(rc, 0, out)

    # ------------------------------------------------------------------ 超时
    def test_hung_driver_must_fail_closed_not_escape(self) -> None:
        """任一 driver 进程挂起都必须以非零结果收场，不得让 TimeoutExpired 逃出屏障。"""
        for mode in ("ghc", "snapshot", "render", "tracecheck"):
            with self.subTest(hung_mode=mode):
                fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
                stub = DriverStub(
                    snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]),
                    raise_on=(mode,))
                try:
                    rc, out = self.run_barrier(fx, stub)
                except subprocess.TimeoutExpired as exc:
                    self.fail(f"{mode} 挂起：屏障未按超时 fail-closed，异常外泄：{exc}")
                self.assertNotEqual(rc, 0, f"{mode} 挂起未判失败：rc={rc}\n{out}")

    # ---------------------------------------------------- legacy 语料空 / 垃圾
    def test_garbage_legacy_corpus_must_not_claim_all_gates_green(self) -> None:
        """垃圾/空 legacy 语料得不到任何 FAIL 行，不得据此外推为全门绿。"""
        for label, content in (("garbage", "not-a-trace\nrandom noise\n"), ("empty-file", "")):
            with self.subTest(corpus=label):
                fx = self.make_spec(traces=(("legacy.trace", content),))
                rc, out = self.run_barrier(fx, DriverStub(
                    snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"]),
                    tracecheck=(0, "")))
                self.assertNotIn(
                    GREEN_CLAIM, out,
                    f"{label} 语料被当成全门绿（driver 对无法解析的语料静默返回空）：\n{out}",
                )
                self.assertTrue(
                    rc != 0 or UNVERIFIED_HINT.search(out),
                    f"{label} 语料既未非零退出、也未标注 unverified：rc={rc}\n{out}",
                )

    def test_empty_legacy_corpus_must_not_claim_all_gates_green(self) -> None:
        """无任何迹线语料 = 无证据：不得打印全门绿，必须非零退出或明确标注 unverified。"""
        fx = self.make_spec(traces=())
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, fx["snapshot"]), render=(0, fx["rendered"])))
        self.assertNotIn(GREEN_CLAIM, out, f"无迹线语料却打印全门绿：\n{out}")
        self.assertTrue(
            rc != 0 or UNVERIFIED_HINT.search(out),
            f"无迹线语料既未非零退出、也未标注 unverified：rc={rc}\n{out}",
        )

    # ------------------------------------------------------ 既有门仍须生效（对照）
    def test_compile_gate_failure_must_fail(self) -> None:
        """编译门非零 → 立即失败，不再进入求值门。"""
        fx = self.make_spec()
        rc, out = self.run_barrier(fx, DriverStub(ghc=(1, "")))
        self.assertNotEqual(rc, 0)
        self.assertIn("编译门", out)

    def test_snapshot_drift_must_fail(self) -> None:
        """重新求值输出与已提交快照不一致 → 快照门失败。"""
        fx = self.make_spec(
            snapshot_text="STATE Start\nPROP Liveness|PASS|0\n",
            traces=(("ok.trace", VALID_TRACE),))
        drifted = "STATE Start\nSTATE Stale\nPROP Liveness|PASS|0\n"
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, drifted), render=(0, fx["rendered"])))
        self.assertNotEqual(rc, 0, out)

    def test_property_fail_line_must_fail(self) -> None:
        """快照输出含 PROP …|FAIL| 反例行 → 性质门失败。"""
        fail_snap = "STATE Start\nPROP Liveness|FAIL|1\n"
        fx = self.make_spec(snapshot_text=fail_snap, traces=(("ok.trace", VALID_TRACE),))
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, fail_snap), render=(0, fx["rendered"])))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("性质门", out)

    def test_render_view_drift_must_fail(self) -> None:
        """render 输出与 spec.md 的 RENDERED 围栏不一致 → 视图门失败。"""
        fx = self.make_spec(traces=(("ok.trace", VALID_TRACE),))
        rc, out = self.run_barrier(fx, DriverStub(
            snapshot=(0, fx["snapshot"]), render=(0, "stateDiagram-v2\n    STATE CHANGED\n")))
        self.assertNotEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
