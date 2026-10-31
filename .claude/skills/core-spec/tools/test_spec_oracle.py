#!/usr/bin/env python3
"""Compile and exercise the fixed SpecOracle driver against synthetic fixtures.

Each case feeds generated fixture modules to the real GHC-built driver and
asserts its observable exit status and output. Fixtures stay outside
``docs/specs`` and never depend on repository-owned specifications.
"""
from __future__ import annotations
import atexit
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
DRIVER_DIR = TOOLS_DIR / "driver"
DRIVER_MAIN = DRIVER_DIR / "SpecOracle.hs"
_TEST_ROOT = tempfile.TemporaryDirectory(prefix="core-spec-oracle-tests-")
atexit.register(_TEST_ROOT.cleanup)
REPO_ROOT = pathlib.Path(_TEST_ROOT.name)
SCRATCH_ROOT = REPO_ROOT / ".tmp" / "oracle-tests"

MACHINE = "tool"
OBJECT = "1"
REFS = ["1", "2", "3"]

# 契约 §4.4 的 strict 标记：实现可写 `STRICT yes` / `strict=no` / `strict: yes`
STRICT_YES = re.compile(r"(?i)strict\s*[=:]?\s*yes")
STRICT_NO = re.compile(r"(?i)strict\s*[=:]?\s*no")

# 夹具 samples 覆盖的事件集（模式 0）；合法迹线只能使用其中的事件，否则会被判 Unknown。
SAMPLED_EVENTS = frozenset(
    ["EvNoop", "EvHold", "EvHoldFlag", "EvNewRound", "EvTimed", "EvIdented",
     "EvErrored", "EvFinish", "EvOff"]
)
LEGIT_EVENTS = frozenset(
    ["EvNoop", "EvHold", "EvTimed", "EvIdented", "EvErrored", "EvFinish"]
)

OBS_FIELDS = (
    "obsEvent",
    "obsObject",
    "obsGen",
    "obsRound",
    "obsAtMs",
    "obsWaitStartMs",
    "obsWaitEndMs",
    "obsScheduledMs",
    "obsCooldownUntilMs",
    "obsRawErr",
    "obsErr",
    "obsRefs",
)

# ---------------------------------------------------------------------------
# 夹具：受控 Oracle 模块（工具测试用，非业务规格）
#   __FIXTURE_MODE__ 0 = 正常；1 = 死路；2 = 不变式违例；3 = 进展义务违例
# 相位/事件/详情的构造子全集被 Labels.hs 全覆盖，render 的完整性门才可被验证。
# ---------------------------------------------------------------------------
FIXTURE_ORACLE = r'''-- 工具测试夹具（非业务规格，不进 docs/specs、不进 traces）。
-- 由 .claude/skills/core-spec/tools/test_spec_oracle.py 机械写入 .tmp，供固定驱动 SpecOracle 链接。
-- 接口按 reference/oracle-contract.md §2：观察值入参的纯模型。
module Oracle
  ( Model (..), Seed (..), Ev (..), Observation (..), ObsId
  , Payload (..), Effect (..), Err (..), PropName (..), Phase (..), Detail (..)
  , Rejection (..), Completion (..), ProgressDecl (..), FairnessTag (..), Bound (..)
  , samples, initialModel, step, enabled, completion, invariants
  , requiredWitnesses, bounded, bound, progress, marks, phase
  ) where

data Phase = PStart | PHold | PDone | PStuck
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data Err = ErrNone | ErrFixtureFail
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data Ev
  = EvNoop
  | EvHold
  | EvHoldFlag
  | EvFinish
  | EvTimed
  | EvIdented
  | EvErrored
  | EvOff
  | EvNewRound
  | EvStuck
  | EvStuckLate
  | EvUnsampled
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data Effect = EffectA | EffectB
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data Detail = DIdle | DHeld | DDone | DStuck
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

-- 域扩展位：本夹具只用 NoPayload（工具测试不搬任何域模型数据）。
data Payload = NoPayload
  deriving (Eq, Ord, Show, Read)

data Rejection = NotEnabled | UnknownEvent | ObservedMismatch
  deriving (Eq, Ord, Show, Read)

data Completion = Terminal | Checkpoint | Unfinished
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data FairnessTag = TaskStarted | TaskFinished | TaskCancelled
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data Bound = Bound { maxDepth :: Int, maxModels :: Int }
  deriving (Eq, Ord, Show, Read)

type ObsId = String

-- 12 公共字段 + 域扩展位 obsPayload：顺序即最终契约，迹线文本由驱动 read 进来。
data Observation = Observation
  { obsEvent :: Ev
  , obsObject :: ObsId
  , obsGen :: Int
  , obsRound :: Int
  , obsAtMs :: Int
  , obsWaitStartMs :: Int
  , obsWaitEndMs :: Int
  , obsScheduledMs :: Int
  , obsCooldownUntilMs :: Int
  , obsRawErr :: Maybe ObsId
  , obsErr :: Maybe Err
  , obsRefs :: [ObsId]
  , obsPayload :: Payload
  } deriving (Eq, Ord, Show, Read)

data Seed = Seed { seedObject :: ObsId, seedGen :: Int }
  deriving (Eq, Ord, Show, Read)

data PropName = PWaitPositive | PRefWhenHeld
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data ProgressDecl
  = Obligation PropName (Model -> Observation -> Bool) (Model -> Int)
  | AssumedFair FairnessTag

data Model = Model
  { mObject :: ObsId
  , mPhase :: Phase
  , mGen :: Int
  , mRound :: Int
  , mFlag :: Bool
  , mRef :: [ObsId]
  , mWaitMs :: Int
  , mLastAtMs :: Int
  , mErr :: Err
  } deriving (Eq, Ord, Show, Read)

fixtureMode :: Int
fixtureMode = __FIXTURE_MODE__

initialModel :: Seed -> Model
initialModel s =
  Model (seedObject s) PStart (seedGen s) 1 False [] 1000 0 ErrNone

phase :: Model -> Phase
phase = mPhase

completion :: Model -> Completion
completion m = case mPhase m of
  PStart -> Checkpoint
  PHold -> Checkpoint
  PDone -> Terminal
  PStuck -> Unfinished

bounded :: Bool
bounded = False

-- 业务见证（契约 §2/§3.1）：每条 (性质, 事件路径) 必须由**真实迹线**走通一次；
-- graph 看不到语料，故不判见证（非空时只报 WITNESS DEFERRED）；判定归 tracecheck。
-- 工具夹具默认空（无见证义务）；mode 8 = 不可达路径，mode 9 = 可达路径。
requiredWitnesses :: [(PropName, [Ev])]
requiredWitnesses
  | fixtureMode == 8 = [ (PRefWhenHeld, [EvHold, EvFinish, EvHold]) ]
  | fixtureMode == 9 = [ (PRefWhenHeld, [EvHold]) ]
  | fixtureMode == 10 = [ (PRefWhenHeld, []) ]
  | otherwise = []

-- fixtureMode 5 = maxModels 0（< 1）；fixtureMode 6 = maxDepth -1（< 0）：两者都是退化 bound。
bound :: Bound
bound
  | fixtureMode == 5 = Bound 12 0
  | fixtureMode == 6 = Bound (-1) 512
  | otherwise = Bound 12 512

progress :: [ProgressDecl]
progress =
  [ Obligation PWaitPositive (\_ o -> obsEvent o == EvTimed) mWaitMs
  , AssumedFair TaskStarted
  ]

marks :: Model -> [Detail]
marks m = case mPhase m of
  PStart -> [DIdle]
  PHold -> [DHeld]
  PDone -> [DDone]
  PStuck -> [DStuck]

invariants :: Model -> [(PropName, Bool)]
invariants m =
  [ (PWaitPositive, waitOk)
  , (PRefWhenHeld, refOk)
  ]
  where
    waitOk
      | fixtureMode == 2 = False
      | fixtureMode == 4 = mWaitMs m > 0
      | otherwise = mWaitMs m >= 0
    -- Property coverage fixtures exercise the false branches for each declaration.
    refOk
      | fixtureMode == 11 = False
      | otherwise = mPhase m /= PHold || not (null (mRef m))

-- 身份 token 由非字符串字面量派生；真实身份只从迹线 read。
obj :: ObsId
obj = show (1 :: Int)

refs :: [ObsId]
refs = map show ([1 .. 3] :: [Int])

noop :: Observation
noop = Observation EvNoop obj 1 1 0 0 0 0 0 Nothing Nothing refs NoPayload

hold :: Observation
hold = Observation EvHold obj 1 1 100 0 0 0 0 Nothing Nothing refs NoPayload

-- EvHold 的「仅置位 flag」同胞：同一相位、同一时刻、同一 refs，只有 mFlag 不同。
holdFlag :: Observation
holdFlag = Observation EvHoldFlag obj 1 1 100 0 0 0 0 Nothing Nothing refs NoPayload

timed :: Observation
timed = Observation EvTimed obj 1 1 1000 0 1000 1000 0 Nothing Nothing refs NoPayload

idented :: Observation
idented = Observation EvIdented obj 1 1 1100 0 0 0 0 Nothing Nothing refs NoPayload

errored :: Observation
errored = Observation EvErrored obj 1 1 1200 0 0 0 0 Nothing (Just ErrFixtureFail) refs NoPayload

finish :: Observation
finish = Observation EvFinish obj 1 1 1300 0 0 0 0 Nothing Nothing refs NoPayload

off :: Observation
off = Observation EvOff obj 1 1 0 0 0 0 0 Nothing Nothing refs NoPayload

-- 真实的轮次前进样本：obsRound = mRound + 1，step 推进构型里的 mRound。
newRound :: Observation
newRound = Observation EvNewRound obj 1 2 200 0 0 0 0 Nothing Nothing refs NoPayload

stuck :: Observation
stuck = Observation EvStuck obj 1 1 100 0 0 0 0 Nothing Nothing refs NoPayload

stuckLate :: Observation
stuckLate = Observation EvStuckLate obj 1 1 100 0 0 0 0 Nothing Nothing refs NoPayload

-- 抽象输入样本：obsRawErr = Nothing，obsErr 直接带构造子；迹线才写原始身份。
-- EvUnsampled 故意不在样本里：它只能在迹线里被拒为 unknown-event。
-- fixtureMode 7 = 空 samples（退化输入，图门必须 FAIL）。
samples :: [Observation]
samples
  | fixtureMode == 7 = []
  | fixtureMode == 1 =
      [ noop, hold, holdFlag, newRound, timed, idented, errored, finish, off, stuck, stuckLate ]
  | otherwise =
      [ noop, hold, holdFlag, newRound, timed, idented, errored, finish, off ]

stamped :: Model -> Observation -> Model
stamped m o = m { mLastAtMs = obsAtMs o }

nextWait :: Int -> Int
nextWait w = if fixtureMode == 3 then w else w - 1000

timedCheck :: Observation -> Int -> Bool
timedCheck o w =
  obsScheduledMs o == w
    && obsWaitEndMs o == obsAtMs o
    && obsWaitEndMs o - obsWaitStartMs o >= obsScheduledMs o

step :: Model -> Observation -> Either Rejection (Model, [Effect])
step m o
  | obsObject o /= mObject m = Left ObservedMismatch
  | obsGen o < mGen m = Left ObservedMismatch
  | obsRound o < mRound m = Left ObservedMismatch
  | obsCooldownUntilMs o /= 0 = Left ObservedMismatch
  | obsAtMs o > 0 && mLastAtMs m > 0 && obsAtMs o < mLastAtMs m = Left ObservedMismatch
  | otherwise = stepEnabled m o

stepEnabled :: Model -> Observation -> Either Rejection (Model, [Effect])
stepEnabled m o = case obsEvent o of
  EvNoop
    | mPhase m == PStart && obsErr o == Nothing && not (null (obsRefs o)) -> Right (m, [])
    | mPhase m == PStart -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvHold
    | mPhase m == PStart && obsErr o == Nothing && not (null (obsRefs o)) ->
        Right ((stamped m o) { mPhase = PHold, mRef = take 1 (obsRefs o) }, [EffectA])
    | mPhase m == PStart -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvHoldFlag
    | mPhase m == PStart && obsErr o == Nothing && not (null (obsRefs o)) ->
        Right ((stamped m o) { mPhase = PHold, mRef = take 1 (obsRefs o), mFlag = True }, [EffectA])
    | mPhase m == PStart -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvFinish
    | mPhase m == PHold && obsErr o == Nothing ->
        Right ((stamped m o) { mPhase = PDone }, [EffectA, EffectB])
    | mPhase m == PHold -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvTimed
    | mPhase m == PHold && mWaitMs m >= 1000 && timedCheck o (mWaitMs m) ->
        Right ((stamped m o) { mWaitMs = nextWait (mWaitMs m) }, [])
    | mPhase m == PHold -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvIdented
    | mPhase m == PHold && obsErr o == Nothing && not (null (mRef m))
        && take 1 (obsRefs o) == mRef m -> Right (stamped m o, [])
    | mPhase m == PHold -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvErrored
    | mPhase m == PHold && obsErr o == Just ErrFixtureFail ->
        Right ((stamped m o) { mErr = ErrFixtureFail, mFlag = True }, [EffectB])
    | mPhase m == PHold -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvNewRound
    | mPhase m == PHold && obsErr o == Nothing && obsRound o == mRound m + 1 ->
        Right ((stamped m o) { mRound = obsRound o }, [])
    | mPhase m == PHold -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvStuck
    | mPhase m == PStart && obsErr o == Nothing ->
        Right ((stamped m o) { mPhase = PStuck }, [])
    | mPhase m == PStart -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvStuckLate
    | mPhase m == PHold && obsErr o == Nothing ->
        Right ((stamped m o) { mPhase = PStuck }, [])
    | mPhase m == PHold -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvUnsampled
    | mPhase m == PStart && obsErr o == Nothing ->
        Right ((stamped m o) { mFlag = True }, [EffectA])
    | mPhase m == PStart -> Left ObservedMismatch
    | otherwise -> Left NotEnabled
  EvOff -> Left NotEnabled

enabled :: Model -> Observation -> Bool
enabled m o = case step m o of
  Right _ -> True
  Left _ -> False
'''

# 标签围栏（工具侧文本，机械生成 Driver/Oracle/Labels.hs）。
# 只登记「render 会出现」的名字：可达 Phase + 样本 Ev + marks 的 Detail；
# 多登记一个不属于该集合的名字即触发 §2.3「引用不存在的名字」报错（含 detail 悬空）。
# PDone 的标签故意含引号，用于验证 render 的引号转义。
LABEL_FENCE = """
state PStart = 起点
state PHold = 保持
state PDone = 完成"态"
event EvNoop = 空操作
event EvHold = 保持
event EvHoldFlag = 保持置位
event EvFinish = 结束
event EvTimed = 计时
event EvIdented = 身份
event EvErrored = 错误
event EvOff = 占位
event EvNewRound = 新轮次
detail DIdle = 空闲
detail DHeld = 保持中
detail DDone = 已结束
note PHold = 夹具中间态
"""

# ERRMAP 围栏：raw <原始身份原文> = <Err 构造子>
ERRMAP_FENCE = """
raw none = ErrNone
raw FixtureFail = ErrFixtureFail
"""

# variant -> (fixtureMode, 删除的标签, 额外追加的标签行)
VARIANTS = {
    "normal": (0, None, None),
    "deadend": (1, None, None),
    "invfail": (2, None, None),
    "obligfail": (3, None, None),
    "invfail-late": (4, None, None),
    "labelgap": (0, "PStart", None),
    "labeldangle": (0, None, "detail DStuck = 卡住"),
    "boundzero": (5, None, None),
    "boundneg": (6, None, None),
    "nosamples": (7, None, None),
    "witnessgap": (8, None, None),
    "witnessok": (9, None, None),
    "witnessempty": (10, None, None),
    "invfail-ref": (11, None, None),
}

# For each declared property, a registered variant must violate it; normal mode
# supplies the passing examples used by the corresponding acceptance checks.
PROP_VIOLATION_VARIANTS = {
    "PWaitPositive": ["invfail", "invfail-late"],
    "PRefWhenHeld": ["invfail-ref"],
}

# 「不变式样例覆盖」的**显式豁免口**：确实无法在夹具中构造违反样例的性质，必须在此逐条登记并写理由
# （不得静默省略；“造不出反例”要是一个有记录的决定）。当前为空：两个声明都有违反 variant。
PROP_NO_VIOLATION: dict[str, str] = {}


def declared_prop_names() -> list[str]:
    """夹具 `data PropName` 的构造子列表（空列表 = 解析不到）。"""
    decl = re.search(r"data PropName = (.*)", FIXTURE_ORACLE)
    if decl is None:
        return []
    return [t.strip() for t in decl.group(1).split("|") if t.strip()]


def prop_coverage_problems(declared, violations, exemptions, variants) -> list[str]:
    """Return property-coverage problems; empty means all properties are covered.

    Kept pure so coverage rules can be checked independently from GHC runs.
    """
    problems: list[str] = []
    overlap = sorted(set(violations) & set(exemptions))
    if overlap:
        problems.append("同一性质同时有违反样例与豁免：%s" % overlap)
    for name in declared:
        if name in exemptions:
            if not exemptions[name].strip():
                problems.append("豁免必须写下理由：%s" % name)
        elif not violations.get(name):
            problems.append("声明了却既无违反样例、又未登记豁免：%s" % name)
        for variant in violations.get(name, []):
            if variant not in variants:
                problems.append("样例 variant 未注册：%s" % variant)
    if sorted(set(violations) | set(exemptions)) != sorted(declared):
        problems.append("违反表 ∪ 豁免表 与声明集合不一致（不得静默省略/留孤儿）")
    return problems

_BUILT: dict[str, pathlib.Path] = {}


def hs_escape(text: str) -> str:
    """Haskell 字符串字面量转义（与屏障 write_labels_module 同规则）。"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def labels_module(drop_label: str | None = None, extra_label: str | None = None) -> str:
    """从围栏文本机械生成 Driver/Oracle/Labels.hs（含 rawErrors::[(String,String)]）。

    值按 Haskell 字符串字面量转义（反斜杠、引号）；extra_label 追加一行标签（用于悬空用例）。
    """
    buckets: dict[str, list[str]] = {"state": [], "event": [], "detail": [], "note": []}
    fence = LABEL_FENCE
    if extra_label:
        fence = fence + extra_label + "\n"
    for line in fence.strip().splitlines():
        key, _, val = line.partition("=")
        parts = key.split()
        if len(parts) != 2 or parts[0] not in buckets:
            continue
        kind, name = parts
        if drop_label is not None and name == drop_label:
            continue
        buckets[kind].append('("%s","%s")' % (name, hs_escape(val.strip())))

    raw: list[str] = []
    for line in ERRMAP_FENCE.strip().splitlines():
        m = re.match(r"^raw\s+(\S+)\s*=\s*(\S+)$", line.strip())
        if m:
            raw.append('("%s","%s")' % (m.group(1), m.group(2)))

    def block(name: str, entries: list[str]) -> str:
        return "%s :: [(String, String)]\n%s = [%s]\n" % (name, name, ", ".join(entries))

    return (
        "module Driver.Oracle.Labels\n"
        "  ( stateLabels, eventLabels, notes, detailLabels, rawErrors ) where\n\n"
        + block("stateLabels", buckets["state"])
        + "\n"
        + block("eventLabels", buckets["event"])
        + "\n"
        + block("notes", buckets["note"])
        + "\n"
        + block("detailLabels", buckets["detail"])
        + "\n"
        + block("rawErrors", raw)
    )


def _build_driver(variant: str) -> pathlib.Path:
    """Compile a generated fixture with the fixed SpecOracle driver."""
    if variant in _BUILT:
        return _BUILT[variant]
    mode, drop, extra = VARIANTS[variant]
    d = SCRATCH_ROOT / variant
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    (d / "Driver" / "Oracle").mkdir(parents=True, exist_ok=True)
    (d / "Oracle.hs").write_text(
        FIXTURE_ORACLE.replace("__FIXTURE_MODE__", str(mode)), encoding="utf-8", newline="\n"
    )
    (d / "Driver" / "Oracle" / "Labels.hs").write_text(
        labels_module(drop, extra), encoding="utf-8", newline="\n"
    )
    if not DRIVER_MAIN.is_file():
        raise RuntimeError(
            "固定驱动文件缺失：%s；已生成的测试夹具位于 %s" % (DRIVER_MAIN, d)
        )
    if shutil.which("ghc") is None:
        raise RuntimeError("找不到 ghc：判定路径依赖 ghc 编译固定驱动（契约 §1.1）。")

    cmd = [
        "ghc", "-Wall", "-Werror=incomplete-patterns",
        "-i" + str(d), "-i" + str(DRIVER_DIR),
        "-outputdir", str(d / "build"),
        "-o", str(d / "spec-oracle"), str(DRIVER_MAIN),
    ]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "ghc 编译门失败（variant=%s）：\n%s\n%s" % (variant, proc.stdout, proc.stderr)
        )
    exe = d / "spec-oracle.exe"
    if not exe.exists():
        exe = d / "spec-oracle"
    _BUILT[variant] = exe
    return exe


# ---------------------------------------------------------------------------
# 语料构造：全部机械拼装（观察值/模型的 Show 文本按契约字段顺序生成）
# ---------------------------------------------------------------------------
def obs(event, object_=OBJECT, gen=1, round_=1, at=0, wait_start=0, wait_end=0,
        scheduled=0, cooldown=0, raw=None, err=None, refs=None):
    if refs is None:
        refs = REFS
    raw_s = "Nothing" if raw is None else 'Just "%s"' % raw
    err_s = "Nothing" if err is None else "Just %s" % err
    refs_s = "[" + ",".join('"%s"' % r for r in refs) + "]"
    return (
        'Observation {obsEvent = %s, obsObject = "%s", obsGen = %d, obsRound = %d, '
        "obsAtMs = %d, obsWaitStartMs = %d, obsWaitEndMs = %d, obsScheduledMs = %d, "
        "obsCooldownUntilMs = %d, obsRawErr = %s, obsErr = %s, obsRefs = %s, "
        "obsPayload = NoPayload}"
    ) % (event, object_, gen, round_, at, wait_start, wait_end, scheduled, cooldown,
         raw_s, err_s, refs_s)


def model(object_=OBJECT, phase="PStart", gen=1, round_=1, flag=False, refs=(),
          wait=1000, last=0, err="ErrNone"):
    refs_s = "[" + ",".join('"%s"' % r for r in refs) + "]"
    return (
        'Model {mObject = "%s", mPhase = %s, mGen = %d, mRound = %d, mFlag = %s, '
        "mRef = %s, mWaitMs = %d, mLastAtMs = %d, mErr = %s}"
    ) % (object_, phase, gen, round_, "True" if flag else "False", refs_s, wait, last, err)


def rec(round_, seq, observation, from_, to, effects="[]", machine=MACHINE,
        object_=OBJECT, gen=1):
    return {
        "machine": machine, "object": object_, "gen": gen, "round": round_,
        "seq": seq, "obs": observation, "from": from_, "to": to, "effects": effects,
    }


def render_trace(records, outcome="terminal", machine=MACHINE, object_=OBJECT, gen=1,
                 begin=True, end=True):
    lines = []
    if begin:
        lines.append("ORACLE-BEGIN %s|%s|%d" % (machine, object_, gen))
    for r in records:
        lines.append("ORACLE %s|%s|%s|%s|%s|%s|%s|%s|%s" % (
            r["machine"], r["object"], r["gen"], r["round"], r["seq"],
            r["obs"], r["from"], r["to"], r["effects"]))
    if end:
        lines.append("ORACLE-END %s|%s|%d|%s" % (machine, object_, gen, outcome))
    return "\n".join(lines) + ("\n" if lines else "")


def legit_records(gen=1):
    """合法执行：no-op（空效果）→ 保持 → 计时 → 身份 → 原始错误身份 → 终态。"""
    a0 = model(gen=gen)
    a1 = model(phase="PHold", refs=("1",), last=100, gen=gen)
    a2 = model(phase="PHold", refs=("1",), wait=0, last=1000, gen=gen)
    a3 = model(phase="PHold", refs=("1",), wait=0, last=1100, gen=gen)
    a4 = model(phase="PHold", refs=("1",), wait=0, last=1200, flag=True,
               err="ErrFixtureFail", gen=gen)
    a5 = model(phase="PDone", refs=("1",), wait=0, last=1300, flag=True,
               err="ErrFixtureFail", gen=gen)
    return [
        rec(1, 1, obs("EvNoop", at=0, gen=gen), a0, a0, "[]", gen=gen),
        rec(1, 2, obs("EvHold", at=100, gen=gen), a0, a1, "[EffectA]", gen=gen),
        rec(1, 3, obs("EvTimed", at=1000, wait_start=0, wait_end=1000, scheduled=1000,
                      gen=gen), a1, a2, "[]", gen=gen),
        rec(1, 4, obs("EvIdented", at=1100, gen=gen), a2, a3, "[]", gen=gen),
        rec(1, 5, obs("EvErrored", at=1200, raw="FixtureFail", gen=gen), a3, a4,
            "[EffectB]", gen=gen),
        rec(1, 6, obs("EvFinish", at=1300, gen=gen), a4, a5, "[EffectA,EffectB]", gen=gen),
    ]


def legit_trace(outcome="terminal"):
    return render_trace(legit_records(), outcome=outcome)


def legit_checkpoint_records(gen=1):
    """合法 checkpoint 执行：no-op → 保持，止于 PHold（Checkpoint），非终态。"""
    return legit_records(gen=gen)[:2]


def legit_checkpoint_trace(gen=1):
    return render_trace(legit_checkpoint_records(gen=gen), outcome="checkpoint", gen=gen)


def round_advance_records():
    """合法轮次前进：EvNewRound 真实推进构型 mRound（1→2），seq 随 round 作用域重置为 1。"""
    a0 = model()
    a1 = model(phase="PHold", refs=("1",), last=100)
    a2 = model(phase="PHold", refs=("1",), round_=2, last=200)
    a3 = model(phase="PHold", refs=("1",), round_=2, last=300)
    return [
        rec(1, 1, obs("EvNoop", at=0), a0, a0, "[]"),
        rec(1, 2, obs("EvHold", at=100), a0, a1, "[EffectA]"),
        rec(2, 1, obs("EvNewRound", round_=2, at=200), a1, a2, "[]"),
        rec(2, 2, obs("EvIdented", round_=2, at=300), a2, a3, "[]"),
    ]


class OracleDriverTestBase(unittest.TestCase):
    """真实驱动跑用例的公共骨架（无 test_* 方法，不被收集）。"""

    def run_driver(self, *args, variant="normal", stdin_text=""):
        exe = _build_driver(variant)
        return subprocess.run(
            [str(exe), *args], input=stdin_text, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(REPO_ROOT), timeout=180,
        )

    @staticmethod
    def out(proc):
        return (proc.stdout or "") + (proc.stderr or "")

    @staticmethod
    def fail_line(out, case):
        pat = re.compile(r"FAIL[^\n]*" + re.escape(case))
        for line in out.splitlines():
            if pat.search(line):
                return line
        return None

    def expect_reject(self, corpus, case=None, args=None, variant="normal"):
        proc = self.run_driver(*(args or ["tracecheck", MACHINE]), variant=variant, stdin_text=corpus)
        out = self.out(proc)
        self.assertEqual(
            proc.returncode, 2, msg="§4.4：有 FAIL 必须退 2。输出：\n" + out)
        self.assertIn("FAIL", out, msg="期望输出含 FAIL。输出：\n" + out)
        if case is None:
            return out
        line = self.fail_line(out, case)
        self.assertIsNotNone(line, msg="期望 FAIL ... %s。输出：\n%s" % (case, out))
        return line

    def expect_reject_any(self, corpus, cases, args=None, variant="normal"):
        out = self.expect_reject(corpus, case=None, args=args, variant=variant)
        for case in cases:
            if self.fail_line(out, case) is not None:
                return out
        self.fail("期望 FAIL 之一 %s。输出：\n%s" % (list(cases), out))

    def expect_accept(self, corpus, args=None, variant="normal"):
        proc = self.run_driver(*(args or ["tracecheck", MACHINE]), variant=variant, stdin_text=corpus)
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="期望退出 0（合法执行）。输出：\n" + out)
        noisy = [l for l in out.splitlines()
                 if "FAIL" in l and not re.fullmatch(r"\s*FAIL\s*[:=]?\s*\d+\s*", l)]
        self.assertFalse(noisy, msg="合法执行不得出现 FAIL 诊断。输出：\n" + out)
        self.assertRegex(out, STRICT_YES, msg="合法执行须显式 strict pass 标记。输出：\n" + out)
        return out


# ---------------------------------------------------------------------------
# 接受侧：合法 no-op / checkpoint / 终态，必须 exit 0 且 STRICT yes
# ---------------------------------------------------------------------------
class TracecheckAcceptTests(OracleDriverTestBase):
    def test_legit_terminal_execution(self):
        self.expect_accept(legit_trace("terminal"))

    def test_legit_checkpoint_execution(self):
        self.expect_accept(legit_checkpoint_trace())

    def test_legit_noop_only_execution(self):
        a0 = model()
        corpus = render_trace([rec(1, 1, obs("EvNoop", at=0), a0, a0, "[]")], outcome="checkpoint")
        self.expect_accept(corpus)

    def test_round_advance_resets_seq(self):
        """同一段内 round 前进合法（EvNewRound 真实推进 mRound）；seq 作用域随 round 重置为 1。"""
        self.expect_accept(render_trace(round_advance_records(), outcome="checkpoint"))


# ---------------------------------------------------------------------------
# §5 拒绝矩阵：解析 / 信封 / 段结构
# ---------------------------------------------------------------------------
class TracecheckParseEnvelopeTests(OracleDriverTestBase):
    def test_empty_input(self):
        out = self.expect_reject("", "empty")
        self.assertNotRegex(out, STRICT_YES)

    def test_garbage_input(self):
        self.expect_reject("this is not a trace line\n", "garbage")

    def test_empty_segment_rejected(self):
        """段内零记录 = 无执行证据，必须拒（BEGIN/END 包着的空段不算完成）。"""
        self.expect_reject(render_trace([], outcome="checkpoint"), "empty")

    def test_nonpositive_envelope_numbers_rejected(self):
        """gen/round/seq 从 1 起；0 或负数不得被数值解析放行（§4.1/§4.2）。"""
        for gen in (0, -1):
            with self.subTest(gen=gen):
                self.expect_reject_any(
                    render_trace(legit_checkpoint_records(gen=gen), outcome="checkpoint", gen=gen),
                    ["bad-observation", "envelope-mismatch", "foreign-generation", "garbage"],
                )
        recs = legit_records()
        recs[0]["round"] = 0
        recs[0]["obs"] = obs("EvNoop", round_=0, at=0)
        self.expect_reject_any(
            render_trace(recs), ["bad-observation", "envelope-mismatch", "foreign-generation"])
        recs = legit_records()
        recs[0]["seq"] = 0
        self.expect_reject_any(render_trace(recs), ["duplicate", "bad-observation"])
        recs = legit_records()
        recs[1]["gen"] = -1
        self.expect_reject_any(render_trace(recs), ["bad-observation", "envelope-mismatch"])

    def test_seq_must_restart_after_round_change(self):
        """round 变化后 seq 必须重置为 1（作用域是 (machine, object, gen, round)）。"""
        recs = round_advance_records()
        recs[2]["seq"] = 2
        self.expect_reject(render_trace(recs, outcome="checkpoint"), "duplicate")

    def test_observation_object_must_match_envelope(self):
        """观察值 obsObject 必须等于信封 object，不能只靠模型比对兜底。"""
        recs = legit_records()
        recs[2]["obs"] = obs("EvTimed", object_="9", at=1000, wait_start=0, wait_end=1000, scheduled=1000)
        self.expect_reject(render_trace(recs), "envelope-mismatch")

    def test_effects_list_must_read_strictly(self):
        """effects 必须可 read 成 [Effect]；`[EffectA,,EffectB]` / `[EffectA,]` 不得被宽松接受。"""
        for bad in ("[EffectA,,EffectB]", "[EffectA,]"):
            with self.subTest(effects=bad):
                recs = legit_records()
                recs[1]["effects"] = bad
                self.expect_reject_any(
                    render_trace(recs), ["unknown-effect", "bad-observation"])

    def test_bad_observation(self):
        # 不可 Read（必填字段缺失）
        recs = legit_records()
        recs[2]["obs"] = "Observation {obsEvent = EvNope}"
        self.expect_reject(render_trace(recs), "bad-observation")
        # obsErr 被伪造（迹线必须写 Nothing，防伪造映射）
        recs = legit_records()
        recs[4]["obs"] = obs("EvErrored", at=1200, raw="FixtureFail", err="ErrFixtureFail")
        self.expect_reject(render_trace(recs), "bad-observation")
        # 值内含 |
        recs = legit_records()
        recs[1]["obs"] = obs("EvHold", at=100, object_="1|x")
        self.expect_reject(render_trace(recs), "bad-observation")

    def test_missing_begin_or_end(self):
        self.expect_reject(render_trace(legit_records(), begin=False), "missing")
        self.expect_reject(render_trace(legit_records(), end=False), "missing")
        self.expect_reject(render_trace([], begin=False, end=True), "missing")

    def test_sequence_violations(self):
        recs = legit_records()
        recs[3]["seq"] = 3  # 重复
        self.expect_reject(render_trace(recs), "duplicate")
        recs = legit_records()
        recs[3]["seq"] = 7  # 跳号
        self.expect_reject(render_trace(recs), "duplicate")
        # 同 (object, gen) 段重开（首段止于 Checkpoint，本身合法）
        corpus = legit_checkpoint_trace() + render_trace(
            legit_checkpoint_records(), outcome="checkpoint")
        self.expect_reject_any(corpus, ["duplicate", "foreign-generation"])

    def test_generation_violations(self):
        # gen 回退：END 后同 object 且 gen 递减（两段各自合法，回退是唯一偏差）
        corpus = legit_checkpoint_trace(gen=2) + legit_checkpoint_trace(gen=1)
        self.expect_reject_any(corpus, ["foreign-generation"])
        # 段内 round 回退（先有一次合法 round 前进，回退才是唯一偏差）
        recs = round_advance_records()
        recs[3]["round"] = 1
        recs[3]["obs"] = obs("EvIdented", round_=1, at=300)
        self.expect_reject(render_trace(recs, outcome="checkpoint"), "foreign-generation")

    def test_envelope_mismatch(self):
        recs = legit_records()
        recs[2]["gen"] = 2  # 信封 gen != obsGen
        self.expect_reject(render_trace(recs), "envelope-mismatch")
        recs = legit_records()
        recs[2]["round"] = 2  # 信封 round != obsRound
        self.expect_reject(render_trace(recs), "envelope-mismatch")

    def test_machine_mismatch_and_routing(self):
        # 语料属于别的机器，却被路由到 tool
        self.expect_reject(render_trace(legit_records(), machine="other"), "machine-mismatch")
        # 语料属于 tool，却被路由到别的机器
        self.expect_reject(
            legit_trace(), "machine-mismatch", args=["tracecheck", "other"])

    def test_effects_and_errmap(self):
        recs = legit_records()
        recs[1]["effects"] = "[EffectA,Bogus]"  # 非 Effect 构造子
        self.expect_reject_any(render_trace(recs), ["unknown-effect", "bad-observation"])
        recs = legit_records()
        recs[1]["effects"] = "[EffectA"  # 语法坏 → 不可 Read
        self.expect_reject(render_trace(recs), "bad-observation")
        recs = legit_records()
        recs[4]["obs"] = obs("EvErrored", at=1200, raw="NoSuchRaw")  # 不在 ERRMAP
        self.expect_reject(render_trace(recs), "unmapped-error")


# ---------------------------------------------------------------------------
# §5 拒绝矩阵：step 判定（not-enabled / obs-mismatch / after-terminal）
# ---------------------------------------------------------------------------
class TracecheckStepTests(OracleDriverTestBase):
    def test_not_enabled(self):
        a0 = model()
        corpus = render_trace([
            rec(1, 1, obs("EvNoop", at=0), a0, a0, "[]"),
            rec(1, 2, obs("EvOff", at=0), a0, a0, "[]"),  # 不可达占位
        ], outcome="checkpoint")
        self.expect_reject(corpus, "not-enabled")

    def test_obs_mismatch(self):
        # 实测等待区间小于计划
        recs = legit_records()
        recs[2]["obs"] = obs("EvTimed", at=1000, wait_start=100, wait_end=1000, scheduled=1000)
        self.expect_reject(render_trace(recs), "obs-mismatch")
        # 身份必需事件留空 obsRefs
        recs = legit_records()
        recs[3]["obs"] = obs("EvIdented", at=1100, refs=[])
        self.expect_reject(render_trace(recs), "obs-mismatch")

    def test_unfinished_segment_rejected(self):
        """段止于在途态（Unfinished）必拒，与 END outcome 用词无关（§4.2）。"""
        a0 = model()
        stuck = model(phase="PStuck", refs=(), wait=1000, last=100)
        recs = [rec(1, 1, obs("EvStuck", at=100), a0, stuck, "[]")]
        for outcome in ("incomplete", "unfinished", "checkpoint", "terminal"):
            with self.subTest(outcome=outcome):
                self.expect_reject_any(
                    render_trace(recs, outcome=outcome),
                    ["unfinished-segment", "outcome-mismatch"],
                    variant="deadend")

    def test_after_terminal(self):
        a5 = model(phase="PDone", refs=("1",), wait=0, last=1300, flag=True, err="ErrFixtureFail")
        recs = legit_records()
        recs.append(rec(1, 7, obs("EvNoop", at=1400), a5, a5, "[]"))
        self.expect_reject_any(render_trace(recs), ["after-terminal", "not-enabled"])

    def test_unknown_event_not_in_samples(self):
        recs = [rec(1, 1, obs("EvUnsampled", at=10), model(), model(flag=True, last=10), "[EffectA]")]
        self.expect_reject_any(
            render_trace(recs, outcome="checkpoint"), ["unknown-event", "unknown"])


# ---------------------------------------------------------------------------
# §5 拒绝矩阵：from / to / effects / END outcome 偏差
# ---------------------------------------------------------------------------
class TracecheckTransitionTests(OracleDriverTestBase):
    def test_from_mismatch_with_flag_diff(self):
        recs = legit_records()
        # 上一条 to = A2（flag=False）；这里 from 只把 flag 改成 True
        recs[3]["from"] = model(phase="PHold", refs=("1",), wait=0, last=1000, flag=True)
        line = self.expect_reject(render_trace(recs), "from-mismatch")
        self.assertIn("mFlag", line, msg="from-mismatch 须给出期望/实际 diff。行：%s" % line)
        self.assertIn("True", line, msg="diff 须含实际 flag=True。行：%s" % line)
        self.assertIn("False", line, msg="diff 须含期望 flag=False。行：%s" % line)

    def test_step_mismatch_target_and_effects(self):
        recs = legit_records()
        recs[1]["to"] = model()  # 期望 PHold，实际写回 PStart
        line = self.expect_reject(render_trace(recs), "step-mismatch")
        self.assertIn("PHold", line, msg="step-mismatch 须含期望 target。行：%s" % line)
        recs = legit_records()
        recs[1]["effects"] = "[EffectB]"  # 期望 [EffectA]
        line = self.expect_reject(render_trace(recs), "step-mismatch")
        self.assertIn("EffectA", line, msg="step-mismatch 须含期望 effects。行：%s" % line)

    def test_end_outcome_deviation(self):
        # 终态段却报 checkpoint
        self.expect_reject(render_trace(legit_records(), outcome="checkpoint"))
        # checkpoint 态却报 incomplete
        corpus = render_trace([rec(1, 1, obs("EvNoop", at=0), model(), model(), "[]")],
                              outcome="incomplete")
        self.expect_reject(corpus)
        # END 后同 object 且 gen 不递增（段重开）
        self.expect_reject_any(
            legit_trace()
            + render_trace(legit_checkpoint_records(), outcome="checkpoint"),
            ["duplicate", "foreign-generation"])


# ---------------------------------------------------------------------------
# §5 `invariant` / §9 迹线门：每条记录的 `to` 都必须满足 invariants（含末条）
# ---------------------------------------------------------------------------
class TraceInvariantGateTests(OracleDriverTestBase):
    def test_tracecheck_rejects_to_violating_invariants(self):
        """任一记录的 `to` 使 `invariants` 为 False（含末条）→ `FAIL invariant <prop> #<n>`（退 2）。
        §4.4 定死 `#<n>` = **语料行号（1-based，整文件）**（可附 `seq=<seq>`）。
        夹具 mode 4 的 `PWaitPositive = mWaitMs > 0`，故 `EvTimed` 之后的构型（wait 归零）违反它。"""
        recs = legit_records()[:3]  # noop → hold → timed(wait 1000 → 0)
        corpus = render_trace(recs, outcome="checkpoint")
        # 该语料：第 1 行 BEGIN、第 2..4 行三条记录、第 5 行 END ⇒ 违例记录的 `to` 在第 4 行
        self.assertEqual(len(corpus.splitlines()), 5)
        proc = self.run_driver("tracecheck", MACHINE, variant="invfail-late", stdin_text=corpus)
        out = self.out(proc)
        self.assertEqual(proc.returncode, 2, msg="不变量被违反属 FAIL，须退 2。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+invariant\s+PWaitPositive\s+#4\b",
                         msg="须报 FAIL invariant <prop> #<语料行号>（此例第 4 行）。输出：\n" + out)

    def test_tracecheck_rejects_ref_prop_violation(self):
        """`invariant` 行也要覆盖另一个声明的性质：mode 11 让 `PRefWhenHeld` 为 False，
        末条/首条记录（此处首条）的 `to` 违反它 → `FAIL invariant PRefWhenHeld #<行号>` + 退 2。"""
        recs = [rec(1, 1, obs("EvNoop", at=0), model(), model(), "[]")]
        corpus = render_trace(recs, outcome="checkpoint")
        # BEGIN(1) / 记录(2) / END(3)：首条记录的 `to` 即违例
        self.assertEqual(len(corpus.splitlines()), 3)
        proc = self.run_driver("tracecheck", MACHINE, variant="invfail-ref", stdin_text=corpus)
        out = self.out(proc)
        self.assertEqual(proc.returncode, 2, msg="不变量被违反属 FAIL，须退 2。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+invariant\s+PRefWhenHeld\s+#2\b",
                         msg="须报 FAIL invariant PRefWhenHeld #<语料行号>（此例第 2 行）。输出：\n" + out)

    def test_tracecheck_invariants_do_not_false_positive(self):
        """同一 mode 下，`to` 全部满足不变式的合法语料不得误报（含末条）。"""
        ok = render_trace(legit_records()[:2], outcome="checkpoint")  # 两条 to 的 wait 均 > 0
        proc = self.run_driver("tracecheck", MACHINE, variant="invfail-late", stdin_text=ok)
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="合法构型不得因该判定变红。输出：\n" + out)
        self.assertNotRegex(out, r"FAIL\s+invariant", msg="不得误报不变量失败。输出：\n" + out)
        self.assertRegex(out, STRICT_YES, msg="无违例即 strict pass。输出：\n" + out)


# ---------------------------------------------------------------------------
# §3.1 业务见证：由迹线门判定（判 graph 只 defer）；§4.4 三态与退出码
# ---------------------------------------------------------------------------
class WitnessGateTests(OracleDriverTestBase):
    def test_tracecheck_witness_reached_is_strict_pass(self):
        """见证路径被真实迹线走通一次 → 退 0 + STRICT yes，且无 UNVERIFIED。"""
        proc = self.run_driver("tracecheck", MACHINE, variant="witnessok",
                              stdin_text=legit_trace("terminal"))
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="见证触达应退 0。输出：\n" + out)
        self.assertRegex(out, STRICT_YES, msg="见证触达是 strict pass。输出：\n" + out)
        self.assertNotIn("UNVERIFIED", out, msg="触达后不得再报 UNVERIFIED。输出：\n" + out)
        self.assertNotIn("FAIL", out, msg="合法执行不得有 FAIL。输出：\n" + out)

    def test_tracecheck_witness_unreached_is_third_state(self):
        """见证路径未被迹线走通 → UNVERIFIED（第三态：非 FAIL 非 PASS），退 3、strict=no。"""
        proc = self.run_driver("tracecheck", MACHINE, variant="witnessgap",
                              stdin_text=legit_trace("terminal"))
        out = self.out(proc)
        self.assertEqual(proc.returncode, 3, msg="§4.4：UNVERIFIED 须退 3。输出：\n" + out)
        self.assertRegex(out, r"UNVERIFIED\s+PRefWhenHeld\s+required-path-unreached",
                         msg="须报 UNVERIFIED <prop> required-path-unreached。输出：\n" + out)
        self.assertNotIn("FAIL", out, msg="UNVERIFIED 不是 FAIL。输出：\n" + out)
        self.assertRegex(out, STRICT_NO, msg="第三态不得是 strict pass。输出：\n" + out)


    def test_tracecheck_rejects_empty_witness_path(self):
        """§5 `bad-witness`：空路径 `[]` 不是恒真（同“起点即完成”的伪证明），必须拒（退 2）。"""
        proc = self.run_driver("tracecheck", MACHINE, variant="witnessempty",
                              stdin_text=legit_trace("terminal"))
        out = self.out(proc)
        self.assertEqual(proc.returncode, 2, msg="bad-witness 是声明错误，须退 2。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+bad-witness", msg="须报 FAIL bad-witness。输出：\n" + out)


class CliExitCodeTests(OracleDriverTestBase):
    def test_usage_error_exit_1(self):
        """§4.4：用法错误（缺机器名/非法模式）退 1。"""
        for args in (["bogus-mode"], ["tracecheck"]):
            with self.subTest(args=args):
                proc = self.run_driver(*args)
                out = self.out(proc)
                self.assertEqual(proc.returncode, 1, msg="用法错误应退 1。输出：\n" + out)


# ---------------------------------------------------------------------------
# §3 图门：前缀 BFS、最短反例、覆盖；bounded=False 只报前缀不是证明
# ---------------------------------------------------------------------------
class GraphTests(OracleDriverTestBase):
    def test_clean_prefix_report(self):
        proc = self.run_driver("graph")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="干净前缀 BFS 应退出 0。输出：\n" + out)
        self.assertRegex(out, r"BOUNDED\s+no", msg="须报告 bounded 取值。输出：\n" + out)
        self.assertRegex(out, r"CHECKEDPREFIX", msg="须标明只检查声明前缀（非证明）。输出：\n" + out)
        self.assertRegex(out, r"COVERAGE", msg="须报告覆盖数值。输出：\n" + out)
        self.assertRegex(out, STRICT_YES, msg="退 0 即 strict pass，须显式。输出：\n" + out)
        self.assertRegex(out, r"(?m)^WITNESS DEFERRED 0$",
                         msg="n=0 也必须无条件输出该行（§4.4 L181）。输出：\n" + out)
        self.assertNotIn("UNVERIFIED", out,
                         msg="UNVERIFIED 由迹线门判，不由 graph 判。输出：\n" + out)

    def test_graph_defers_witnesses_when_required(self):
        """requiredWitnesses 非空时 graph 不判见证：只报 WITNESS DEFERRED 并 strict=no（退 3）。
        初始 Checkpoint 的零步不得当作完成证据，故不能直接 PASS。"""
        for variant in ("witnessgap", "witnessok"):
            with self.subTest(variant=variant):
                proc = self.run_driver("graph", variant=variant)
                out = self.out(proc)
                self.assertEqual(proc.returncode, 3,
                                 msg="WITNESS DEFERRED 是第三态（非绿），须退 3。输出：\n" + out)
                self.assertRegex(out, r"(?m)^WITNESS DEFERRED 1$",
                                 msg="须报固定格式 WITNESS DEFERRED <n>。输出：\n" + out)
                self.assertRegex(out, STRICT_NO, msg="defer 不得是 strict pass。输出：\n" + out)
                self.assertNotIn("UNVERIFIED", out,
                                 msg="UNVERIFIED 归迹线门，graph 不得自判。输出：\n" + out)

    def test_graph_deferral_count_is_unconditional(self):
        """`WITNESS DEFERRED <n>` 无条件输出且 n 可为 0；
        n=0 不得置非绿（退 0 + strict pass），n>0 才退 3 + strict=no。
        且 n 必须与 snapshot 的 `WITNESS <prop>|…` 行数一致（同一事实源，供交叉核对）。"""
        proc = self.run_driver("graph")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="n=0 时 graph 仍须退 0。输出：\n" + out)
        self.assertRegex(out, r"(?m)^WITNESS DEFERRED 0$",
                         msg="n=0 也必须无条件输出该行（不得省略）。输出：\n" + out)
        self.assertRegex(out, STRICT_YES, msg="n=0 仍是 strict pass。输出：\n" + out)
        snap = self.out(self.run_driver("snapshot"))
        self.assertEqual([l for l in snap.splitlines() if l.startswith("WITNESS ")], [],
                         msg="n=0 时 snapshot 不得有 WITNESS 行。输出：\n" + snap)

        proc = self.run_driver("graph", variant="witnessgap")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 3, msg="n>0 时 graph 退 3。输出：\n" + out)
        self.assertRegex(out, r"(?m)^WITNESS DEFERRED 1$",
                         msg="n=1 须如实计数。输出：\n" + out)
        self.assertRegex(out, STRICT_NO, msg="n>0 置 strict=no。输出：\n" + out)
        snap = self.out(self.run_driver("snapshot", variant="witnessgap"))
        self.assertEqual(len([l for l in snap.splitlines() if l.startswith("WITNESS ")]), 1,
                         msg="snapshot 的 WITNESS 行数须与 n 一致。输出：\n" + snap)

    def test_graph_rejects_degenerate_bound(self):
        """退化 bound（maxModels < 1 / maxDepth < 0）必须 FAIL，不得静默 strict pass（常规回归）。"""
        for variant in ("boundzero", "boundneg"):
            with self.subTest(variant=variant):
                proc = self.run_driver("graph", variant=variant)
                out = self.out(proc)
                self.assertEqual(proc.returncode, 2, msg="退化 bound 是 FAIL，须退 2。输出：\n" + out)
                self.assertRegex(out, r"FAIL\s+bound-invalid", msg="须报 bound-invalid。输出：\n" + out)

    def test_graph_rejects_empty_samples(self):
        """samples 为空时必须 FAIL（无抽象输入可枚举），不得静默 strict pass（常规回归）。"""
        proc = self.run_driver("graph", variant="nosamples")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 2, msg="空 samples 是 FAIL，须退 2。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+samples-missing", msg="须报 samples-missing。输出：\n" + out)

    def test_deadend_counterexample_is_shortest(self):
        proc = self.run_driver("graph", variant="deadend")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="死路必须非零退出。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+deadend", msg="须报死路反例。输出：\n" + out)
        self.assertRegex(out, r"depth\s*=\s*1", msg="须给最短反例（步数）。输出：\n" + out)
        self.assertIn("PStuck", out, msg="反例须给出出错构型。输出：\n" + out)

    def test_invariant_counterexample(self):
        proc = self.run_driver("graph", variant="invfail")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="不变式失败必须非零退出。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+invariant", msg="须报不变式反例。输出：\n" + out)
        self.assertIn("PWaitPositive", out, msg="须指出违例 PropName。输出：\n" + out)

    def test_obligation_counterexample(self):
        proc = self.run_driver("graph", variant="obligfail")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="义务失败必须非零退出。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+obligation", msg="须报进展义务反例。输出：\n" + out)
        self.assertIn("PWaitPositive", out, msg="须指出违例 PropName。输出：\n" + out)
        self.assertRegex(out, r"depth\s*=\s*2", msg="须给最短反例（步数）。输出：\n" + out)

    def test_deadend_counterexample_has_event_path(self):
        """最短反例必须给出真实事件路径（每步的实际事件），不只是步数。"""
        proc = self.run_driver("graph", variant="deadend")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="死路必须非零退出。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+deadend", msg="须报死路反例。输出：\n" + out)
        self.assertRegex(out, r"path=\[EvStuck\]", msg="死路路径须是 [EvStuck]。输出：\n" + out)
        self.assertRegex(out, r"depth\s*=\s*1", msg="路径长度须与 depth 一致。输出：\n" + out)

    def test_obligation_counterexample_has_event_path(self):
        """义务反例路径 = 到源构型的路径 + 违反义务的那次迁移事件。"""
        proc = self.run_driver("graph", variant="obligfail")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="义务失败必须非零退出。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+obligation", msg="须报进展义务反例。输出：\n" + out)
        self.assertRegex(
            out, r"path=\[EvHold,EvTimed\]",
            msg="义务反例路径须是 [EvHold,EvTimed]。输出：\n" + out)
        self.assertRegex(out, r"depth\s*=\s*2", msg="路径长度须与 depth 一致。输出：\n" + out)

    def test_invariant_counterexample_has_event_path(self):
        """非初始构型的不变式反例同样须带事件路径（mode 4 只在第二次迁移后失败）。"""
        proc = self.run_driver("graph", variant="invfail-late")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="不变式失败必须非零退出。输出：\n" + out)
        self.assertRegex(out, r"FAIL\s+invariant", msg="须报不变式反例。输出：\n" + out)
        self.assertIn("PWaitPositive", out, msg="须指出违例 PropName。输出：\n" + out)
        self.assertRegex(
            out, r"path=\[EvHold,EvTimed\]",
            msg="不变式反例路径须是 [EvHold,EvTimed]。输出：\n" + out)
        self.assertRegex(out, r"depth\s*=\s*2", msg="路径长度须与 depth 一致。输出：\n" + out)
        self.assertIn("mWaitMs = 0", out, msg="反例构型须是违例的那一个。输出：\n" + out)


# ---------------------------------------------------------------------------
# §2.3 / §9：snapshot 与 render（视图门，非判定证据）
# ---------------------------------------------------------------------------
class SnapshotRenderTests(OracleDriverTestBase):
    def test_snapshot_reports_invariants(self):
        proc = self.run_driver("snapshot")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="snapshot 应退出 0。输出：\n" + out)
        self.assertTrue(out.strip(), msg="snapshot 不得为空。")
        self.assertIn("PWaitPositive", out, msg="snapshot 须含不变式结果。输出：\n" + out)
        self.assertIn("PRefWhenHeld", out, msg="snapshot 须含全部不变式。输出：\n" + out)

    def test_render_uses_labels(self):
        proc = self.run_driver("render")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="标签齐全时 render 应退出 0。输出：\n" + out)
        self.assertTrue(out.strip(), msg="render 不得为空。")
        self.assertTrue(
            "PStart" in out or "起点" in out,
            msg="render 须出现相位名或其标签。输出：\n" + out)

    def test_render_rejects_missing_label(self):
        proc = self.run_driver("render", variant="labelgap")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="标签缺失时 render 必须非零退出。输出：\n" + out)
        self.assertTrue(out.strip(), msg="标签缺失须给出诊断。输出：\n" + out)

    def test_snapshot_full_state_differs_on_flag(self):
        """快照必须给出完整构型：同 phase、仅 mFlag 不同的两个可达构型必须是不同行。"""
        proc = self.run_driver("snapshot")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="snapshot 应退出 0。输出：\n" + out)
        plain = model(phase="PHold", refs=("1",), last=100)
        flagged = model(phase="PHold", refs=("1",), last=100, flag=True)
        self.assertIn(plain, out, msg="快照须含完整构型（逐字段 show Model）。输出：\n" + out)
        self.assertIn(flagged, out, msg="仅 mFlag 不同的同 phase 构型须分别出现。输出：\n" + out)
        phold_lines = [l for l in out.splitlines() if "mPhase = PHold" in l]
        self.assertTrue(any("mFlag = False" in l for l in phold_lines),
                        msg="缺 mFlag=False 的完整构型行。输出：\n" + out)
        self.assertTrue(any("mFlag = True" in l for l in phold_lines),
                        msg="缺 mFlag=True 的完整构型行。输出：\n" + out)
        self.assertNotEqual(plain, flagged, msg="夹具自身：两构型必须仅 mFlag 不同")

    def test_snapshot_transitions_carry_full_facts(self):
        """快照须列出每条 checked legal transition，并带完整 obs / full from / full to / effects。"""
        proc = self.run_driver("snapshot")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="snapshot 应退出 0。输出：\n" + out)
        lines = [l for l in out.splitlines() if l.startswith("TRANS ")]
        self.assertTrue(lines, msg="快照须列出每条 checked legal transition。输出：\n" + out)

        obs_plain = obs("EvHold", at=100)
        obs_flag = obs("EvHoldFlag", at=100)
        from0 = model()
        to_plain = model(phase="PHold", refs=("1",), last=100)
        to_flag = model(phase="PHold", refs=("1",), last=100, flag=True)

        hit_plain = [l for l in lines
                     if obs_plain in l and from0 in l and to_plain in l and "[EffectA]" in l]
        hit_flag = [l for l in lines
                    if obs_flag in l and from0 in l and to_flag in l and "[EffectA]" in l]
        self.assertTrue(hit_plain, msg="TRANS 行须含完整 obs/from/to/effects。输出：\n" + out)
        self.assertTrue(
            hit_flag,
            msg="仅 mFlag 不同的目标构型必须产生不同 TRANS 行（全 fact diff）。输出：\n" + out)
        for l in lines:
            self.assertIn("Observation {", l, msg="TRANS 行的 obs 必须完整。行：%s" % l)
            self.assertIn("Model {", l, msg="TRANS 行的 from/to 必须完整。行：%s" % l)

    def test_render_uses_event_name_not_full_observation(self):
        """render 的事件名必须取自 obsEvent 的标签，不得把整段 Observation 当事件名打印。"""
        proc = self.run_driver("render")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="render 应退出 0。输出：\n" + out)
        self.assertIn("空操作", out, msg="render 须出现事件标签。输出：\n" + out)
        self.assertIn("保持置位", out, msg="render 须覆盖全部样本事件标签。输出：\n" + out)
        self.assertNotIn("Observation {", out,
                         msg="事件名不得是完整 Observation 文本。输出：\n" + out)

    def test_render_escapes_label_quotes(self):
        """标签内的引号必须在渲染文本里转义，输出不得被裸引号截断。"""
        proc = self.run_driver("render")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="render 应退出 0。输出：\n" + out)
        self.assertIn(r'完成\"态\"', out, msg="标签引号须转义为 \\\"。输出：\n" + out)

    def test_snapshot_lists_required_witnesses(self):
        """snapshot 列出必需见证供诊断；屏障的无语料计数取自 graph 的 WITNESS DEFERRED。
        无见证的机器不得出现 WITNESS 行。"""
        proc = self.run_driver("snapshot", variant="witnessok")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="snapshot 应退出 0。输出：\n" + out)
        self.assertIn("WITNESS PRefWhenHeld|EvHold", out,
                      msg="须逐条列出见证：WITNESS <prop>|<Ev,…>。输出：\n" + out)
        proc = self.run_driver("snapshot", variant="witnessgap")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="snapshot 应退出 0。输出：\n" + out)
        self.assertIn("WITNESS PRefWhenHeld|EvHold,EvFinish,EvHold", out,
                      msg="见证路径须按声明顺序逐项列出。输出：\n" + out)
        proc = self.run_driver("snapshot")
        out = self.out(proc)
        self.assertEqual(proc.returncode, 0, msg="snapshot 应退出 0。输出：\n" + out)
        self.assertNotIn("WITNESS ", out,
                         msg="requiredWitnesses 为空的机器不得出现 WITNESS 行。输出：\n" + out)

    def test_render_rejects_dangling_detail_label(self):
        """detail 标签引用不存在的 detail（悬空）必须让 render 失败。"""
        proc = self.run_driver("render", variant="labeldangle")
        out = self.out(proc)
        self.assertNotEqual(proc.returncode, 0, msg="悬空 detail 标签必须让 render 非零退出。输出：\n" + out)
        self.assertIn("DStuck", out, msg="诊断须指出悬空的名字。输出：\n" + out)


# ---------------------------------------------------------------------------
# 夹具自身的契约检查（不依赖驱动落地）
# ---------------------------------------------------------------------------
class FixtureContractTests(unittest.TestCase):
    def test_observation_has_common_fields_and_payload(self):
        """只要求公共字段在场；域可扩 Payload —— **不**断言字段总数 / 不做 exact 字段数。"""
        self.assertEqual(len(OBS_FIELDS), 12)
        for field in OBS_FIELDS:
            self.assertRegex(
                FIXTURE_ORACLE,
                re.compile(r"^\s*[{,]?\s*%s\s*::" % re.escape(field), re.M),
                msg="夹具缺公共字段 %s" % field,
            )
        self.assertRegex(
            FIXTURE_ORACLE,
            re.compile(r"^\s*,?\s*obsPayload\s*::\s*Payload\s*$", re.M),
            msg="夹具须带域扩展位 obsPayload :: Payload",
        )
        self.assertIn("data Payload = NoPayload", FIXTURE_ORACLE)
        self.assertIn("data Seed = Seed { seedObject :: ObsId, seedGen :: Int }", FIXTURE_ORACLE)
        self.assertIn("data Model = Model", FIXTURE_ORACLE)

    def test_fixture_derivations_match_contract(self):
        """§2.1 派生清单：缺任一则域模型与驱动无法同编译。"""
        blocks = {}
        for match in re.finditer(r"^data (\w+)\b(.*?)(?=^data |\Z)", FIXTURE_ORACLE, re.S | re.M):
            blocks[match.group(1)] = match.group(2)
        for name in ("Model", "Seed", "Observation", "Payload", "Ev", "Err", "Effect",
                     "Phase", "Detail", "PropName", "Rejection", "FairnessTag", "Bound"):
            self.assertIn(name, blocks, msg="夹具缺类型 %s" % name)
            self.assertIn("deriving (Eq, Ord, Show, Read", blocks[name],
                          msg="%s 缺 Eq, Ord, Show, Read" % name)
        for name in ("Completion", "FairnessTag", "Ev"):
            self.assertIn("Enum, Bounded", blocks.get(name, ""),
                          msg="%s 缺 Enum, Bounded（标签完整性/穷尽枚举）" % name)
        self.assertNotIn("deriving", blocks.get("ProgressDecl", ""),
                         msg="ProgressDecl 含函数，无派生要求")

    def test_fixture_export_list_matches_final_contract(self):
        export = FIXTURE_ORACLE.split(") where", 1)[0]
        for present in ("Model (..)", "Seed (..)", "Ev (..)", "Observation (..)", "ObsId",
                        "Payload (..)", "Effect (..)", "Err (..)", "PropName (..)",
                        "Phase (..)", "Detail (..)", "Rejection (..)", "Completion (..)",
                        "ProgressDecl (..)", "FairnessTag (..)", "Bound (..)",
                        "samples", "initialModel", "step", "enabled", "completion",
                        "invariants", "requiredWitnesses", "bounded", "bound",
                        "progress", "marks", "phase"):
            self.assertIn(present, export, msg="导出面缺 %s" % present)
        for absent in ("states", "terminalStates", "events", "edges", "transition",
                       "observedFacts", "facts", "FactName", "FactValue"):
            self.assertNotIn(absent, export, msg="导出面不得含 %s" % absent)

    def test_fixture_exports_contract_surface(self):
        for name in ("samples, initialModel, step, enabled, completion, invariants",
                     "requiredWitnesses, bounded, bound, progress, marks, phase",
                     "Rejection (..), Completion (..), ProgressDecl (..)"):
            self.assertIn(name, FIXTURE_ORACLE)
        for name in ("NotEnabled", "UnknownEvent", "ObservedMismatch",
                     "Terminal", "Checkpoint", "Unfinished",
                     "AssumedFair", "Obligation"):
            self.assertIn(name, FIXTURE_ORACLE)

    def test_samples_cover_all_legit_events(self):
        missing = sorted(LEGIT_EVENTS - SAMPLED_EVENTS)
        self.assertFalse(missing, msg="合法迹线事件不可遗漏于 samples（否则误判 Unknown）：%s" % missing)
        for ev in sorted(SAMPLED_EVENTS):
            self.assertIn(ev, FIXTURE_ORACLE)
        for ev in sorted(LEGIT_EVENTS):
            self.assertIn(ev, FIXTURE_ORACLE)

    def test_round_advance_event_is_real_and_sampled(self):
        """round 前进必须是真实迁移（step 改 mRound）且事件在 samples 里，不是伪造模型。"""
        self.assertIn("EvNewRound", SAMPLED_EVENTS)
        self.assertIn("newRound :: Observation", FIXTURE_ORACLE)
        self.assertIn("mRound = obsRound o", FIXTURE_ORACLE)
        self.assertIn("obsRound o == mRound m + 1", FIXTURE_ORACLE)
        corpus = render_trace(round_advance_records(), outcome="checkpoint")
        self.assertIn("EvNewRound", corpus)
        self.assertIn("mRound = 2", corpus)

    def test_mode4_invariant_knob_present(self):
        self.assertEqual(VARIANTS["invfail-late"][0], 4)
        self.assertIn("fixtureMode == 4", FIXTURE_ORACLE)

    def test_holdflag_pair_differs_only_in_flag(self):
        """EvHold / EvHoldFlag 必须产出仅 mFlag 不同的同相位构型（供快照/TRANS 的 fact diff）。"""
        self.assertIn("EvHoldFlag", SAMPLED_EVENTS)
        self.assertIn("holdFlag :: Observation", FIXTURE_ORACLE)
        plain = model(phase="PHold", refs=("1",), last=100)
        flagged = model(phase="PHold", refs=("1",), last=100, flag=True)
        self.assertNotEqual(plain, flagged)
        self.assertEqual(plain.replace("mFlag = False", "mFlag = ?"),
                         flagged.replace("mFlag = True", "mFlag = ?"))

    def test_labels_module_escapes_quotes(self):
        src = labels_module()
        self.assertIn(r'("PDone","完成\"态\"")', src)

    def test_labeldangle_variant_appends_dangling_detail(self):
        src = labels_module(extra_label="detail DStuck = 卡住")
        self.assertIn('("DStuck","卡住")', src)
        self.assertNotIn('("DStuck","卡住")', labels_module())

    def test_every_declared_prop_has_samples_and_violation_variant(self):
        """Verify that each declared property is evaluated and has a registered counterexample or reasoned exemption.
        The passing direction is covered by normal-mode driver acceptance checks."""
        declared = declared_prop_names()
        self.assertTrue(declared, msg="解析不到夹具的 data PropName")
        body = FIXTURE_ORACLE.split("invariants ::", 1)[1].split("\nobj ::", 1)[0]
        for name in declared:
            self.assertIn("(%s," % name, body, msg="声明了却未求值的 PropName：%s" % name)
        self.assertEqual(
            prop_coverage_problems(declared, PROP_VIOLATION_VARIANTS, PROP_NO_VIOLATION, VARIANTS),
            [],
            msg="不变式样例覆盖门应通过（默认：每个性质都有违反 variant）")

    def test_prop_coverage_gate_is_not_vacuous(self):
        """Ensure malformed coverage tables are rejected instead of passing vacuously."""
        declared = declared_prop_names()
        self.assertTrue(declared, msg="解析不到夹具的 data PropName")
        good = {name: ["invfail"] for name in declared}
        bad_cases = [
            ("清空违反表", {}, dict(PROP_NO_VIOLATION)),
            ("豁免无理由", {}, {name: "   " for name in declared}),
            ("variant 未注册", {name: ["no-such-variant"] for name in declared}, {}),
            ("两表重叠", good, {declared[0]: "有理由"}),
            ("遗漏一个性质", {declared[0]: ["invfail"]}, {}),
        ]
        for label, violations, exemptions in bad_cases:
            with self.subTest(case=label):
                self.assertNotEqual(
                    prop_coverage_problems(declared, violations, exemptions, VARIANTS), [],
                    msg="负向自检：%s 时门必须报问题" % label)
        self.assertEqual(
            prop_coverage_problems(declared, PROP_VIOLATION_VARIANTS, PROP_NO_VIOLATION, VARIANTS),
            [],
            msg="当前真实配置必须通过（门不是恒失败）")

    def test_labels_module_exposes_errmap_path(self):
        src = labels_module()
        self.assertIn("module Driver.Oracle.Labels", src)
        self.assertIn("rawErrors :: [(String, String)]", src)
        for name in ("stateLabels", "eventLabels", "notes", "detailLabels"):
            self.assertIn("%s :: [(String, String)]" % name, src)
        self.assertIn('("FixtureFail","ErrFixtureFail")', src)
        self.assertIn('("none","ErrNone")', src)

    def test_labelgap_variant_actually_drops_a_label(self):
        self.assertNotIn('("PStart","起点")', labels_module("PStart"))

    def test_fixtures_land_only_under_tmp(self):
        scratch = str(SCRATCH_ROOT).replace("\\", "/")
        self.assertIn("/.tmp/", scratch)
        self.assertNotIn("/docs/", scratch)
        self.assertNotIn("traces", scratch)

    def test_corpus_builders_roundtrip_shape(self):
        corpus = legit_trace()
        self.assertTrue(corpus.startswith("ORACLE-BEGIN %s|%s|1\n" % (MACHINE, OBJECT)))
        self.assertTrue(corpus.rstrip().endswith("ORACLE-END %s|%s|1|terminal" % (MACHINE, OBJECT)))
        for line in corpus.splitlines():
            if line.startswith("ORACLE "):
                self.assertEqual(len(line[len("ORACLE "):].split("|")), 9)
        self.assertNotIn("|", model())
        self.assertNotIn("\n", obs("EvNoop"))


def _suite_for(argv):
    """CLI 选择：无参数 = 全部；`-k <substr>` = 过滤 id；否则参数按类/方法名加载。"""
    module = sys.modules[__name__]
    loader = unittest.TestLoader()
    if argv and argv[0] == "-k" and len(argv) > 1:
        pattern = argv[1].lower()
        flat = []

        def walk(test):
            if isinstance(test, unittest.TestSuite):
                for item in test:
                    walk(item)
            else:
                flat.append(test)

        walk(loader.loadTestsFromModule(module))
        return unittest.TestSuite(t for t in flat if pattern in t.id().lower())
    return loader.loadTestsFromNames(argv, module)


if __name__ == "__main__":
    _argv = sys.argv[1:]
    if _argv:
        _res = unittest.TextTestRunner(verbosity=2).run(_suite_for(_argv))
        sys.exit(0 if _res.wasSuccessful() else 1)
    unittest.main(verbosity=2)
