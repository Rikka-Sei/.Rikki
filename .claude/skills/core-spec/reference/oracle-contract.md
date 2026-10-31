# 嵌入语义观察/模型契约（Oracle，共享工具）

**状态：** Draft（待维护者复核）。本文是显式契约，不是历史设计说明；签名定稿前不得据本文宣称 G0 通过。
**适用范围：** core-spec 共享工具 `.claude/skills/core-spec/tools/`（驱动 + 屏障 + 语料协议）。
**读者：** spec 作者、实现者、屏障维护者。
**依赖：** 由目标仓库选择并记录所需工具链与版本；AI/LLM 不进入确定性路径。

**命名约定：** 共享侧模型模块名用 `Oracle`（工具侧围栏 `ORACLE:<name>`），不以版本号作为语义名称。目标实现语言的类型、函数、常量、模块、配置项与文件名遵循目标仓库的命名约定；wire 协议既有版本字段除外。

---

## 0. 版本策略（旧语义保留，新语义 opt-in）

| 代 | 围栏标记 | 模型模块名 | 能力 |
|---|---|---|---|
| 旧（存量） | `<!-- SEMANTICS:<name> BEGIN/END -->` | `Semantics` | legacy/unverified：兼容路径；不满足本契约的新代严格判定条件 |
| 新（本契约） | `<!-- ORACLE:<name> BEGIN/END -->` | `Oracle` | 观察值入参的纯模型 + 严格迹线协议 + 显式拒绝 |

规则：

1. 新代按机器逐一 opt-in，不为迁移而改动未迁移的机器。
2. 两代可在同一 spec 共存期并行跑门；旧代门的结论只能标 `legacy/unverified`，不计入 strict pass。
3. 某机器新代落地并全门绿后，原地删除该机器旧代块与旧代专属条文（不留“已被 Dxx 取代”补丁标记）。
4. 旧代通过**不**证明新代能力；新代通过也不追认旧代结论。

---

## 1. 确定性边界与实现约束

### 1.1 确定性路径

1. 判定路径由固定驱动编译提取出的 `Oracle`，并由屏障编排；具体编译器、驱动入口与依赖由目标仓库契约决定。
2. **驱动是离线判定器。** 实现不得链接、移植、转写或调用驱动，也不得把驱动判定结果作为运行时决策复用。实现只写观察。
3. **观察点只记录原始事实，零语义判断。** 观察点不读模型、不算后继、不把原始错误身份归类。
4. **effects 是真实发生的观察**，不是 `step()` 算出后写日志。
5. **模型只做判定，不生成数据**：真实时间、尝试次数、原始错误身份、对象身份、代际等由观察点以 `Observation` 传入；模型不得在自身内部“算出”这些值再回填。

### 1.2 实现侧约束

1. 实现平台、语言、依赖政策、FFI 边界及构建/测试入口由目标仓库契约决定，不由本契约预设。
2. 实现不得把离线驱动判定结果作为运行时决策复用；实现只记录观察。
3. 构建与测试结论如实报告。

---

## 2. 纯模型接口（`Oracle`，固定驱动调用）

### 2.1 观察值入参的 step

```haskell
-- 观察值：观察点在事件触发处捕获的原始输入，Read 进模型。
-- 公共 12 字段（driver 依赖；域不得删改）：
data Observation = Observation
  { obsEvent    :: Ev            -- 事件
  , obsObject   :: ObsId         -- 被观察对象的原始标识
  , obsGen      :: Int           -- 代际
  , obsRound    :: Int           -- 轮次
  , obsAtMs     :: Int           -- 本次事件实测时刻：**同一单调 elapsed 毫秒**（非 Unix 墙钟）
  , obsWaitStartMs  :: Int       -- 若本事件为一次等待完成点：本次等待开始（同一单调源）；否则 0
  , obsWaitEndMs    :: Int       -- 本次等待结束（= obsAtMs，同一单调源）；否则 0
  , obsScheduledMs  :: Int       -- 计划等待时长（模型按构型算出并核对；无等待为 0）
  , obsCooldownUntilMs :: Int    -- 本次实测的冷却截止时刻；无冷却为 0
  , obsRawErr   :: Maybe ObsId   -- **原始错误身份原文**（观察点逐字写；无错误为 Nothing）
  , obsErr      :: Maybe Err     -- 驱动按 ORACLE-ERRMAP 注入；迹线文本必须写 Nothing
  , obsRefs     :: [ObsId]       -- 相关对象身份，顺序即观测顺序
  , obsPayload  :: Payload       -- 领域自带类型化载荷
  } deriving (Eq, Ord, Show, Read)

-- 域可扩展的类型化载荷：driver 只 `read`/`show`，不解释其构子。
-- 通用 Read 天然支持域自定义值；**禁止**把计数塞进 obsRefs，也禁止假设初值为空。
data Payload = NoPayload | ...  deriving (Eq, Ord, Show, Read)

type ObsId = String   -- 观察点写的原始标识；模型不对其做字符串拼接

-- 模型种子：BEGIN 信封的 object/gen 注入，避免真实 object 与初始构型不符。
data Seed = Seed { seedObject :: ObsId, seedGen :: Int } deriving (Eq, Ord, Show, Read)
initialModel :: Seed -> Model

data Model = Model { ... }  deriving (Eq, Ord, Show, Read)   -- 完整构型，非 phase 投影

-- 有限抽象输入样本集（spec 声明）；BFS 只遍历它 × 可达构型。
-- samples 是抽象输入：直接携带抽象 obsErr（Err 构造子），不带原始身份、obsRawErr = Nothing；
-- 迹线才是 obsRawErr = 原始身份原文 + obsErr = Nothing（驱动注入）。
-- samples 有限 ≠ 全状态有界：带时间戳的构型实际无界，见 §3.1。
samples :: [Observation]

-- 唯一语义入口：观察值入参。Right = 合法观察；Left = 显式拒绝（含观察值与构型不符）。
step :: Model -> Observation -> Either Rejection (Model, [Effect])

-- 由 step 派生，禁止独立定义（单一事实源）。
enabled :: Model -> Observation -> Bool
enabled m o = case step m o of { Right _ -> True; Left _ -> False }

completion   :: Model -> Completion
invariants   :: Model -> [(PropName, Bool)]   -- 该构型的性质求值（Bool）
-- 业务见证：每条 (性质, 事件路径) 必须由真实迹线至少走通一次；图探索本身不满足它。
requiredWitnesses :: [(PropName, [Ev])]
bounded      :: Bool
bound        :: Bound                          -- bounded = False 时必填
progress     :: [ProgressDecl]
phase        :: Model -> Phase                 -- 仅视图/标签，非判定证据
marks        :: Model -> [Detail]
```

约束：

- **无 `edges`、无 `transition`。** 有界图枚举只靠 `samples` × `step`，不依赖模型自报边表。
- **观察值只能由 `Observation` 进模型**。模型可把实测值写进自身字段（构型承载事实），但不得凭空生成时间/次数/错误身份；`step` 对观察值的判定（等待是否等于期望、错误身份是否匹配、代际是否当前、身份是否在集内）返回 `Left` 即拒。
- `Model`/`Observation` 必须 `deriving (Eq, Ord, Show, Read)`；`Show` 输出**不得含 `|` 与 CR/LF**。空格、逗号、括号、引号允许（记录字段只按 `|` 切分，不在字段内部按逗号切分）。
- **派生类清单（缺任一则域模型与驱动无法同编译）**：`Model`/`Seed`/`Observation`/`Payload`/`Ev`/`Err`/`Effect`/`Phase`/`Detail`/`PropName`/`Rejection`/`FairnessTag`/`Bound` 都要 `Eq, Ord, Show, Read`；`Completion`/`FairnessTag` 另加 `Enum, Bounded`；`Ev` 必须全零参并加 `Enum, Bounded`（标签完整性与穷尽枚举），`Phase` 若全零参同样加。`ProgressDecl` 含函数，**无派生要求**（其构造子不参与 `Read`）。驱动用 `Model` 的 `Ord` 建 `Set`/`Map`，用 `Observation`/`Err`/`Ev`/`Effect` 的 `Eq` 判定。
- 嵌入代码**零字符串字面量**；`ObsId` 的值来自迹线的 `read`，不在代码里出现。
- **`samples` 的身份 token 只能由非字符串字面量派生**（如 `show (n :: Int)`；不得写领域身份字面量）。真实身份只从迹线 `read`。
- **需要身份证据的事件不得留空 `obsRefs`**：当具体契约要求关联对象身份时，样本必须带派生 token；`obsRefs` 为空或缺失即 `Left ObservedMismatch`，**不做宽判**（宽判会让身份断言永不执行）。
- **幂等吸收写 `invariants`，不写 `ObservedMismatch`**：若具体契约规定重复输入被保留首次结果吸收，该观察是**合法 no-op**（`Right (m, [])`，可带域声明的效果），**不得**用 `Left ObservedMismatch` 表达设计意图——那会把合法迹线判红。重复不得改变构型这一性质写进 `invariants`，并可用连续重复事件作见证路径证明该分支被走通。`ObservedMismatch` 只留给“观察值与构型期望矛盾”（编号错、身份不在集合内、代际回退等）。
- **不可达占位显式拒绝**：不适用的事件返回 `Left NotEnabled`，不得自环 no-op。合法事件**可以**效果为空（`Right (m, [])`）；合法性只能由 `step` 的 `Right/Left` 判定，**绝不能**由“效果非空”推定。

### 2.2 拒绝与完成类型

```haskell
data Rejection
  = NotEnabled              -- 该事件/观察在该构型不适用（不可达占位 / 已终止 / 代际不符）
  | UnknownEvent            -- 事件不属于本机器的 samples 事件集
  | ObservedMismatch        -- 观察值与构型期望不符（等待时长、次数、错误身份、身份集、代际）
  deriving (Eq, Ord, Show, Read)

data Completion
  = Terminal                -- 设计终点：此后无 enabled 观察
  | Checkpoint              -- 非终止会话的合法暂停点（可被外部暂停/继续的稳定态）
  | Unfinished              -- 尚可前进（含瞬时在途态）
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

data Bound = Bound { maxDepth :: Int, maxModels :: Int }
  deriving (Eq, Ord, Show, Read)

data FairnessTag
  = TaskStarted | TaskFinished | TaskCancelled
  deriving (Eq, Ord, Show, Read, Enum, Bounded)

-- 进展义务：只约束声明覆盖的迁移，不为异步环境事件证明降秩。
data ProgressDecl
  = Obligation PropName (Model -> Observation -> Bool) (Model -> Int)
    -- 名字、适用谓词、测度；适用时该迁移测度必须严格下降
  | AssumedFair FairnessTag
    -- 示例事件仅用于说明外部环境公平性假设；不绑定具体领域或固定驱动依赖
```

### 2.3 标签与围栏（新代独立命名）

- 新代标签围栏用 ```` ```ORACLE-LABEL:<machine> ````，与旧代 ```` ```LABEL:<machine> ```` 分离。旧代门只读旧围栏并校验标签不得引用不存在的 event；新代事件写进旧围栏会让旧门变红，故两代不得共用。
- 键：`state`（`Phase`）、`event`（`Ev`）、`detail`（`Detail`）、`note`（可选）。
- 完整性门：render 出现过的 `Phase`/`Ev`/`Detail` 都必须有标签；引用不存在的名字即报错。
- Labels 由屏障从 `ORACLE-LABEL:<machine>` 提取，生成驱动使用的数据模块；与旧代标签模块互不覆盖。
- 工具实现细节由工具源码及其维护文档定义；本契约说明模型文本与围栏规则。

---

## 3. 有界性、覆盖阈值与公平性

### 3.1 `bounded = True`（样本图前缀有限）

**`bounded = True` 只表示“在声明的 `samples` 与 `bound` 上限内的样本图前缀有限”，不表示全状态有界**：带时间戳/代数/身份的构型实际无界。无论 `bounded` 取值，**图探索总受 `bound` 上限约束**，报告必须写“样本前缀”，不得写“全可达集”。

驱动从 `initialModel (Seed …)` 起 BFS，后继由 `samples × step` 生成，给**最短反例**：

- **可达完成**：可达集内存在 `completion == Terminal` 或 `Checkpoint` 的构型；一个都没有即反例（最短路径）。
- **死路**：可达 `Unfinished` 构型若对所有 `sample` 都 `Left`，是死路反例；`Terminal` 构型若仍有 `Right`，同样反例。
- **进展义务**：对每条 `Obligation`，仅在其适用谓词为真的迁移上要求测度严格下降；不适用迁移不受约束。违反即最短反例（含路径）。
- **不变式**：对每个可达构型求 `invariants`；任一 `False` 即最短反例。
- **业务见证（由迹线判定，不由图判定）**：`requiredWitnesses` 的每条 `(性质, 事件路径)` 必须由真实迹线至少走通一次。**`graph` 看不到语料，因此不判见证**：**无条件输出固定格式 `WITNESS DEFERRED <n>`（`n = length requiredWitnesses`，可为 0）**，`n > 0` 时置 `strict=no`（不因 `n = 0` 而省略该行，也不自报 `UNVERIFIED`）；**初始构型是 `Checkpoint` 不构成完成证据**，仅凭“起点即完成”直接 PASS 是禁止的。
  **同一 `PropName` 可带多条不同路径**：每条都要被真实迹线各自走通一次；不得为省条目把两族事件合并成需要跨族连续的长序列（真实迹线不保证该连续）。
  **匹配口径（定死）**：在**同一 `(machine, object, gen)` 段内**按记录顺序找**连续**事件序列，至少命中一次；跨段拼接不算，子序列跳跃不算（需要跳过的事件必须显式写进路径）。**空路径 `[]` 不是恒真**：声明空路径即 `FAIL bad-witness`（退 2）。
- **见证未触达**：`tracecheck` 报 `UNVERIFIED <prop> required-path-unreached`（第三态，不是 `FAIL` 也不是 `PASS`），`strict=no`；语料补齐路径后才能转 PASS。
- **见证名与路径语义必须相符**：`(PropName, [Ev])` 的性质名要描述该事件路径的真实效果。名字与路径名实不符并不由机器判定，属评审门（G9）人工阻断项；路径语义漂移时改名字或改路径，不得靠改名迁就。
- **覆盖**：报告 `可达构型数 / 合法迁移数 / 覆盖的 (构型×样本) 格数`；写实际数值，不写“全覆盖”断言。

### 3.2 `bounded = False`（状态空间无界）

- 驱动只检查 `bound` 声明的有限前缀（`maxDepth`、`maxModels`）；作为近似，用 `samples` 枚举前缀。
- 必须至少一条 `AssumedFair`；缺声明即门红。
- **禁止伪称无限状态证明**：报告写“前缀有界检查，非无限状态证明”，列出假设的公平性名。
- 真实值校验由迹线重放承担，不由前缀枚举宣称。

### 3.3 target-ahead（spec 领先实现）

实现未落地而 spec 已拍板时，判定报 `WARN`，不是 `FAIL`。任何 `WARN` 使本次结论为 `strict=no`；覆盖数值单列 target-ahead 格，不并入“已通过”。

---

## 4. 迹线协议

### 4.1 语法（行协议）

```text
ORACLE-BEGIN <machine>|<object>|<gen>
ORACLE <machine>|<object>|<gen>|<round>|<seq>|<observation>|<from>|<to>|<effects>
...
ORACLE-END <machine>|<object>|<gen>|<outcome>
```

- 信封字段 `<machine>`/`<object>`/`<gen>`/`<round>`/`<seq>`/`<outcome>` 是简单 token（不含 `|`）。
- `<observation>` = `show Observation`；`<from>`/`<to>` = `show Model`；`<effects>` = `show [Effect]`（Haskell 列表，如 `[EffectA,EffectB]`，空表写 `[]`）。
- 三类值均允许空格/逗号/括号/引号；**只禁 `|` 与 CR/LF**。记录按 `|` 切分，字段内部不再按逗号切分。
- `<gen>`/`<round>` 必须等于 `obsGen`/`obsRound`（冗余校验，不等即 `envelope-mismatch`）。
- `<seq>` 同一 `(machine, object, gen, round)` 内从 1 起、每次 +1；重复/跳号即拒。

`outcome` ∈ `terminal` / `checkpoint` / `incomplete`，必须等于末条 `to` 的 `completion`。

### 4.2 BEGIN/END 语义

- 一个 `(machine, object, gen)` 段由一对 BEGIN/END 包裹；END 后同 `object` 的记录必须开新段且 `gen` 递增；`gen` 回退即 `foreign-generation`。
- **BEGIN/END 验证真实执行完成**：END 的 `outcome` 为 `incomplete` 即拒。
- 非终止机器必须把**合法等待态**（可被外部暂停/继续的稳定态）声明为 `Checkpoint`；**瞬时在途态**保持 `Unfinished`，段止于 `Unfinished` 即拒。
- 非终止机器没有 `Terminal` 构型，完成由 `Checkpoint` 承担；`states`/`terminalStates`/`events` **不是导出项**，驱动从 `samples`（事件集）与可达构型（按 `completion`）自行计算。

### 4.3 示例（合成）

以下仅演示协议形状，字段与身份均为合成值，不代表实际系统或运行数据：

```text
ORACLE-BEGIN sample|object-1|1
ORACLE sample|object-1|1|1|1|Observation {obsEvent = EvBegin, obsObject = "object-1", obsGen = 1, obsRound = 1, obsAtMs = 1000, obsWaitStartMs = 0, obsWaitEndMs = 0, obsScheduledMs = 0, obsCooldownUntilMs = 0, obsRawErr = Nothing, obsErr = Nothing, obsRefs = [], obsPayload = NoPayload}|Initial|Active|[Started]
ORACLE-END sample|object-1|1|checkpoint
```

时间事实规则：所有时间字段使用同一单调 elapsed 时间源，不是 Unix 墙钟；系统时间校正不得参与等待或期限判定。时间校验只用独立观测值；计划时长必须等于模型计算值，实测耗时不得短于计划时长。模型不得要求实测差值恰等于计划值。期限判定采用契约规定的时刻差阈值，不要求进程真实睡眠满该时长。

### 4.4 退出码与三态（`tracecheck` / `graph` 共用）

| 退出码 | 含义 | 是否 strict pass |
|---|---|---|
| `0` | 全 PASS，且无 `FAIL`、无 `WARN`、无 `UNVERIFIED` | 是 |
| `1` | 用法错误（缺机器名/参数非法） | 否 |
| `2` | 存在 `FAIL`（拒绝矩阵/迹线不一致/图反例） | 否 |
| `3` | 无 `FAIL`，但存在 `UNVERIFIED` 或 `WARN`（含 `WITNESS DEFERRED`） | **否** |

- **`UNVERIFIED` 不等于绿**：它是“证据未触达/待补”，退出码 `3`，不得计入 strict pass，也不得当作成功（这正是要避免的伪证明）。
- 屏障对 `--oracle`：只有退出码 `0` 记 strict pass；`3` 报告为“非绿·待补语料”；`2`/`1` 直接红。
- **空语料不得跳过即绿**：`requiredWitnesses` 非空而该机器 `traces/` 无任何 `.oracle` 时，屏障层报 `UNVERIFIED` 并置 `strict=no`（退 3），不得将缺少证据视为通过。屏障判定“非空”的唯一强制来源：`graph` 输出的 `WITNESS DEFERRED <n>`（`n = length requiredWitnesses`，无条件输出、`n` 可为 0）；`snapshot` 的 `WITNESS <prop>|<Ev,…>` 行只是附加报告（供人/测试阅读，不参与屏障判定，也不得在屏障里作为回退来源）。不得对该值做文本匹配或正则猜测；读不出该行时 fail-closed：视作 `n > 0`（不得当 0 误判通过）。此时不调用 `tracecheck`（无语料）。
- 只有 `requiredWitnesses` 为空（`n = 0`，或无 `ORACLE` 块的旧代机器）且无语料时才允许跳过（屏障不红），但**该机器不计 strict pass**：屏障整体此时退 `3`（非绿·待补语料），只有全部机器都 strict pass 时才退 `0`。零证据不得算绿。
- **`n = 0` 且有语料时照常 strict 判定**：正常运行 `tracecheck`（见证列表为空，不产生 `UNVERIFIED`），只要其全部语料 PASS 且 `graph` 无失败行，该机器可 strict pass。与“有语料”的充要条件不冲突。
- **空文件 ≠ 无文件**：`.oracle` 文件存在但零记录时，交 `tracecheck` 按 §5 `empty` 判 `FAIL`（退 2）。“无语料”是屏障层判定（不调驱动），“空文件”是驱动层拒绝；两者不得混为一谈。
- **屏障组合判定（`--oracle` 记 strict pass 的充要条件）**：该机器 `graph` **无任何行首 `FAIL` 行**（退出码 ∈ {0,3}）**且**该机器全部语料 `tracecheck` 退 `0`（见证全命中、无 `WARN`、无 `UNVERIFIED`）**且**该机器有语料（或 `requiredWitnesses` 为空时允许跳过、但该机器不计 strict pass）。
- **`WITNESS DEFERRED` 与 `graph` 自身的 `strict=no` 对屏障是中性**：它们只标记“图不判见证”与“图级未判”，**不得**被屏障直接判成非绿；见证是否绿由 `tracecheck` 决定（否则任何声明了 `requiredWitnesses` 的机器都将永远无法 strict pass）。`COVERAGE`/partial coverage 报告同样中性。
- **屏障退出码映射**：`spec_barrier.py --oracle` 用 `0`=全部机器 strict pass；`1`=存在 `FAIL`（含驱动退 `2`、结构失败）；`2`=用法错误；`3`=无 `FAIL` 但有 `UNVERIFIED`/`WARN`（非绿）。驱动退出码映射：`0`→继续判；`2`→屏障 `FAIL`；`3`→屏障 `UNVERIFIED`（非绿）；`1`→屏障用法错误。
- **失败行统一格式**：`graph`/`tracecheck` 的失败行一律以 `FAIL` 开头，后接小写 kind（集合开放：`deadend`/`invariant`/`obligation`/`terminal-enabled`/`bound-invalid`/`samples-missing`/`bad-witness`/§5 矩阵各 case 等），形如 `FAIL invariant PropertyA #7`。**迹线失败行的 `#<n>` 统一是语料行号（1-based，整文件）**，可另附 `seq=<seq>`；**`#0` 表示“无行可指”**（如 `FAIL empty` 整文件无记录、`FAIL bad-witness` 声明错误），不得用 `#0` 冒充真实行号；**图失败行不用 `#<n>`**，用 `depth=`/`path=[…]`/`model=…` 定位（图没有语料行）。屏障判非绿的依据是**行首 `FAIL` + 驱动退出码**；大写 token 形式的旧名（`OBLIGFAIL`/`INV`/`DEADEND`）**不是**判据，各处（含计划与报告）不得再引用两套名字。`WITNESS DEFERRED <n>`、`STRICT no`、`COVERAGE`/partial 报告**中性**。
- **退出码 1/3 的用法**：`1` 只用于用法错误；`3` 只用于 `UNVERIFIED`/`WARN`（含零证据跳过机器）。

---

## 5. 严格拒绝矩阵（必须产生 `FAIL <case>` 且退出码非零）

| case | 触发 | 新代要求 |
|---|---|---|
| `empty` | 输入无任何记录 | 拒 |
| `garbage` | 行不匹配 §4.1 语法 | 拒 |
| `bad-observation` | `observation`/`from`/`to`/`effects` 不可 `Read`，或值内含 `|`/CR/LF，或必填字段缺失 | 拒 |
| `not-enabled` | `step m o == Left NotEnabled`（不可达占位） | 拒 |
| `obs-mismatch` | `step` 因观察值不符返回 `Left ObservedMismatch` | 拒（含期望 vs 观测） |
| `after-terminal` | `completion m == Terminal` 后仍有记录 | 拒 |
| `missing` | 记录在 BEGIN 前 / BEGIN 无 END / END 无 BEGIN | 拒 |
| `duplicate` | `seq` 重复或跳号；同 `(object, gen)` 段重开 | 拒 |
| `foreign-generation` | `gen` 回退；段内 `round` 回退；END 无后继新段 | 拒 |
| `envelope-mismatch` | 信封 `gen`/`round` ≠ `obsGen`/`obsRound` | 拒 |
| `machine-mismatch` | 记录 `machine` ≠ 语料机器 | 拒 |
| `from-mismatch` | `from` ≠ 上一条 `to` | 拒（含期望/实际） |
| `step-mismatch` | `to`/`effects` ≠ `step` 结果 | 拒（含期望 vs 实际） |
| `unknown-effect` | `effects` 含非 `Effect` 构造子 | 拒 |
| `unmapped-error` | `obsRawErr` 原始身份不在 `ORACLE-ERRMAP` | 拒 |
| `bad-witness` | `requiredWitnesses` 含空路径 `[]`（不存在的事件名在类型化声明下不可表达，由编译期拒绝） | 拒（声明错误，退 2） |
| `invariant` | 某条记录的 `to` 使 `invariants` 为 `False`（含末条 `to`） | 拒（`FAIL invariant <prop> #<n>`，退 2） |


---

## 6. 原始错误身份映射（`ORACLE-ERRMAP:<machine>`）

- spec 用工具侧围栏 ```` ```ORACLE-ERRMAP:<machine> ```` 发布映射，逐行 `raw <原始身份字面量> = <Err 构造子>`。围栏是工具侧文本，不进入嵌入代码（保持零字符串字面量）。
- **观察点写原始身份，不归一化**：观察点在 `obsRawErr` 逐字写原始错误身份；迹线文本里 `obsErr` 必须为 `Nothing`（写 `Just _` 即 `bad-observation`，防伪造映射）。驱动按 `ORACLE-ERRMAP` 把 raw 映射为 `Err` 并注入 `obsErr` 后调用 `step`；表内没有的 raw 即 `unmapped-error`。
- 完整性：每个 `Err` 构造子至少一条 raw 映射。
- **占位禁止进语料**：表未补齐前不得落活采集语料。映射字面量由实现侧在观察点接线时提供（真实变体名与底层 IO 身份原文），spec 侧只登记。

---

## 7. 多机器路由

1. 每个 `ORACLE:<machine>` 是独立机器，单独编译、单独快照、单独评审。
2. 语料按机器分文件：`docs/specs/<name>/traces/<machine>.oracle`。
3. **一个机器只路由所属语料**：屏障按文件名取期望机器名，喂给对应机器的驱动实例；记录 `machine` 不一致即拒。
4. 跨机器不得混装同一文件；事件、效果、错误构造子不得跨机器解析。

---

## 8. 实现闭环（generate → replay → 反例负控）

1. **generate**：实现真实执行时，观察点在迁移与效果触发处写 `.oracle` 语料（原始 `Observation`）。
2. **replay**：屏障把语料喂给对应机器驱动；`FAIL` 即 spec↔实现漂移；`WARN` 标记 target-ahead。
3. **反例负控**：负例测试对 §5 每一类注入一个反例，必须全部被拒；任一未被拒即门红。
4. 每一次实跑只采集原始身份与事实；不得照模型伪造后继或效果。
5. 手工构造的语料只作工具测试夹具，不作为真实运行证据。

---

## 9. 门与工具布局

```text
.claude/skills/core-spec/tools/driver/SpecOracle.hs            # 固定驱动入口：snapshot / render / tracecheck / graph
.claude/skills/core-spec/tools/driver/Driver/Oracle/Trace.hs   # 迹线解析 + 拒绝矩阵
.claude/skills/core-spec/tools/driver/Driver/Oracle/Graph.hs   # 前缀 BFS / 最短反例 / 覆盖
.claude/skills/core-spec/tools/driver/Driver/Oracle/Snapshot.hs# 快照 + 不变式
.claude/skills/core-spec/tools/driver/Driver/Oracle/Render.hs  # 视图渲染（非证据）
.claude/skills/core-spec/tools/driver/Driver/Oracle/Labels.hs  # ORACLE-LABEL 数据模块生成目标
.claude/skills/core-spec/tools/test_spec_oracle.py             # 负控（§5 全矩阵）
.claude/skills/core-spec/tools/test_corespec_oracle_extract.py # 围栏提取自测（不误吞旧代块）
.claude/skills/core-spec/tools/spec_barrier.py                 # 增加 --oracle 模式；旧路径不变
docs/specs/<domain>/snapshots/<machine>.oracle.snap.gz        # 快照存储（检查门只认此形态）
```

| 门 | 判据 |
|---|---|
| 编译门 | `ghc -Wall` 编译 `Oracle` + `SpecOracle` 成功。警告如实报告，**不得主张零 warning**；驱动输出面：`graph` **必须无条件输出** `WITNESS DEFERRED <n>`（`n` 可为 0；屏障判“无语料”的**唯一强制来源**，读不出即 fail-closed 视作 `n>0`）；`snapshot` 的 `WITNESS <prop>|<Ev,…>` 行为附加报告（供人/测试，不参与屏障判定） |
| 快照存储门 | 工件 `snapshots/<machine>.oracle.snap.gz`：确定性无损 gzip（`MTIME=0`、无 `FNAME`/注释、level 9）——同一全文同一字节。读侧有界流式解压（防 gzip 炸弹；不拼接多成员）+ 严格 UTF-8，解压后按 `norm_eol` 语义与被求值输出做**完整全文**比较（不存 hash、不抽样、不做前缀/长度近似）。损坏 / 截断 / CRC 不符 / 非法 gzip / 尾随非 gzip 数据 / 非 UTF-8 / 解压超 512 MiB / 落盘超 64 MiB **一律硬失败退 1**（不得退 3 冒充未验证、不得退 0 伪绿、不得截断当通过）。旧明文 `.oracle.snap` 不作验收来源：单独存在或与 gz 并存都硬失败，检查门不就地迁移；迁移只在 `--oracle --update-snapshot`——先对全部机器编译求值合法，再整组落盘；既有 gz 解压后等价则不重写（字节与 mtime 稳定），不可信（含跨 zlib 版本字节差异）则按嵌入代码重建；旧明文在 gz 原子替换成功且预校验通过后才删除 |
| 迹线门 | 每个 `traces/*.oracle` 重放无 `FAIL`；`WARN` 只允许 target-ahead；**对每条记录的 `to` 求 `invariants`，任一 `False` 即 `FAIL invariant <prop> #<n>`（退 2）**；判 `requiredWitnesses` 连续序列命中，未触达报 `UNVERIFIED`（退 3）；`requiredWitnesses` 非空而语料为空 → `UNVERIFIED`+`strict=no`（不得跳过即绿） |
| 图门 | `bounded=True` 用 `samples` 前缀 BFS + 义务/不变式/死路 + 最短反例；`bounded=False` 只查声明前缀并报告假设；**无条件输出 `WITNESS DEFERRED <n>`；`n>0` 时置 `strict=no`**（**图不判见证，也不得自报 `UNVERIFIED`**） |
| 退出码 | `0` 全 PASS（strict pass，全部机器都 strict pass）；`1` 用法错误；`2` 有 FAIL；`3` 有 `UNVERIFIED`/`WARN`，**或存在因无语料而跳过（零证据）的机器**（非绿，不计 strict pass） |
| 屏障组合判定 | 某机器 strict pass ⇔ 该机器 `graph` 无失败行（退出码 ∈ {0,3}）**且**其全部语料 `tracecheck` 退 `0` **且**有语料；`WITNESS DEFERRED` 与 `graph` 自身 `strict=no` 对屏障**中性** |
| 负控门 | `test_spec_oracle.py` 覆盖 §5 全矩阵，每类必被拒 |
| 视图门 | `RENDERED:<name>` 围栏与驱动 render 一致（仅视图） |
| 评审门 | 评审材料哈希新鲜、无未闭环 `BLOCKING` |

旧代兼容显示：旧代门结论输出前缀 `[legacy]`，报告标注 `unverified`，不计入 strict pass。
