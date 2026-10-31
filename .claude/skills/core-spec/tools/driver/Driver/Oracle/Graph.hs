-- 前缀 BFS（samples × step）：最短反例（含真实事件路径）、覆盖数值、声明前缀（非全图证明）。
--
-- 契约 §3.1/§3.2：图探索只覆盖 bound 声明的有限前缀，报告写“样本前缀”，
-- 不写“全可达集”；`bounded = False` 时必须有 AssumedFair 声明（缺即门红）。
-- Bound 退化（maxDepth < 0 或 maxModels < 1）或 samples 为空时必须 FAIL，而不是静默通过。
module Driver.Oracle.Graph
  ( Exploration (..)
  , explore
  , seedFromSamples
  , seedModel
  , graphReport
  , yesno
  ) where

import qualified Oracle as O
import Data.List (foldl', intercalate)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set

data Exploration = Exploration
  { exModels :: [O.Model]
  , exModelCount :: Int
  , exEdges :: [(O.Model, O.Observation, O.Model, [O.Effect])]
  , exCells :: Int
  , exTruncated :: Bool
  , exViolations :: [String]
  , exCompletionReachable :: Bool
  , exFairness :: [String]
  }

yesno :: Bool -> String
yesno b = if b then "yes" else "no"

-- | 图的起点：种子由首个样本派生（样本身份由非字符串字面量产生）。
seedFromSamples :: O.Seed
seedFromSamples = case O.samples of
  (o : _) -> O.Seed (O.obsObject o) (O.obsGen o)
  [] -> O.Seed mempty 1

seedModel :: O.Model
seedModel = O.initialModel seedFromSamples

boundDepth :: Int
boundDepth = O.maxDepth (O.bound)

boundModels :: Int
boundModels = O.maxModels (O.bound)

-- | 退化 bound / 空 samples：判定不成立，必须 FAIL（不是 strict pass）。
boundProblems :: [String]
boundProblems =
  [ "FAIL bound-invalid maxDepth 必须 >= 0：" ++ show boundDepth | boundDepth < 0 ]
    ++ [ "FAIL bound-invalid maxModels 必须 >= 1：" ++ show boundModels | boundModels < 1 ]
    ++ [ "FAIL samples-missing samples 为空：没有可枚举的抽象输入" | null O.samples ]

data Acc = Acc
  { aVisited :: Set.Set O.Model
  , aQueue :: [(Int, O.Model)]
  , aEdges :: [(O.Model, O.Observation, O.Model, [O.Effect])]
  , aParent :: Map.Map O.Model (Maybe (O.Model, O.Observation))
  , aCells :: Int
  , aViolations :: [String]  -- 逆序累积；BFS 顺序 → 首个即最短
  , aSeen :: Set.Set String
  , aTruncated :: Bool
  , aCompletionOk :: Bool
  }

explore :: Exploration
explore = finish (loop acc0)
  where
    acc0 = Acc (Set.singleton seedModel) [(0, seedModel)] []
             (Map.singleton seedModel Nothing) 0 [] Set.empty False False

    loop a = case aQueue a of
      [] -> a
      ((d, m) : rest)
        | d > boundDepth -> loop (a { aQueue = rest, aTruncated = True })
        | otherwise ->
            let rs = [ (o, O.step m o) | o <- O.samples ]
                a1 = a { aQueue = rest
                       , aCells = aCells a + length rs
                       , aCompletionOk = aCompletionOk a || O.completion m /= O.Unfinished
                       }
                a2 = foldl' (addNeighbour d m) a1 rs
            in loop (markViolations a2 d m rs)

    addNeighbour d m a (o, Right (m2, effs))
      | d + 1 > boundDepth = a { aTruncated = True, aEdges = edge : aEdges a }
      | Set.member m2 (aVisited a) = a { aEdges = edge : aEdges a }
      | Set.size (aVisited a) >= boundModels = a { aTruncated = True, aEdges = edge : aEdges a }
      | otherwise =
          a { aVisited = Set.insert m2 (aVisited a)
            , aQueue = aQueue a ++ [(d + 1, m2)]
            , aEdges = edge : aEdges a
            , aParent = Map.insert m2 (Just (m, o)) (aParent a)
            }
      where
        edge = (m, o, m2, effs)
    addNeighbour _ _ a _ = a

    -- 首次发现即最短父边，故沿 parent 回溯就是最短路径的真实事件序列。
    pathOf a m = go m
      where
        go cur = case Map.lookup cur (aParent a) of
          Just (Just (p, o)) -> go p ++ [show (O.obsEvent o)]
          _ -> []

    showPath events = " path=[" ++ intercalate "," events ++ "]"

    markViolations a d m rs =
      let a1 = foldl' reportInvariant a (filter (not . snd) (O.invariants m))
          a2
            | O.completion m == O.Unfinished && null rightsOf =
                report "deadend"
                  ("FAIL deadend depth=" ++ show d ++ showPath (pathOf a m)
                    ++ " model=" ++ show m)
                  a1
            | otherwise = a1
          a3
            | O.completion m == O.Terminal && not (null rightsOf) =
                report "terminal-enabled"
                  ("FAIL terminal-enabled depth=" ++ show d ++ showPath (pathOf a m)
                    ++ " model=" ++ show m)
                  a2
            | otherwise = a2
      in foldl' checkObligation a3 obligations
      where
        rightsOf = [ () | (_, Right _) <- rs ]
        obligations = [ (n, app, meas) | O.Obligation n app meas <- O.progress ]

        reportInvariant acc (n, _) =
          report ("invariant|" ++ show n)
            ("FAIL invariant " ++ show n ++ " depth=" ++ show d ++ showPath (pathOf a m)
              ++ " model=" ++ show m)
            acc

        checkObligation acc (n, app, meas) =
          foldl' (oneObligation n app meas) acc
            [ (o, m2) | (o, Right (m2, _)) <- rs ]

        oneObligation n app meas acc (o, m2)
          | app m o && not (meas m2 < meas m) =
              report ("obligation|" ++ show n)
                ("FAIL obligation " ++ show n ++ " depth=" ++ show (d + 1)
                  ++ showPath (pathOf a m ++ [show (O.obsEvent o)])
                  ++ " from=" ++ show m ++ " to=" ++ show m2)
                acc
          | otherwise = acc

    report key line a
      | Set.member key (aSeen a) = a
      | otherwise = a { aSeen = Set.insert key (aSeen a), aViolations = line : aViolations a }

    finish a = Exploration
      { exModels = Set.toList (aVisited a)
      , exModelCount = Set.size (aVisited a)
      , exEdges = reverse (aEdges a)
      , exCells = aCells a
      , exTruncated = aTruncated a
      , exViolations = reverse (aViolations a)
      , exCompletionReachable = aCompletionOk a
      , exFairness = [ show tag | O.AssumedFair tag <- O.progress ]
      }

-- | 图门报告：只报“样本前缀”，反例取最短（BFS 顺序）并带真实事件路径。
-- 驱动退出码（契约 §4.4）：0 全 PASS／2 有 FAIL／3 无 FAIL 但有 WARN 或 WITNESS DEFERRED。
-- **无条件**输出 `WITNESS DEFERRED <n>`（n = length requiredWitnesses，可为 0）；
-- `n > 0` 才置 strict=no（退 3），`n = 0` 不因该行而置非绿（退 0 + STRICT yes）。
-- 图不判见证、也不得自报 `UNVERIFIED`；该标记对屏障层中性（§4.4 L256）。
graphReport :: ([String], Int)
graphReport =
  let e = explore
      witnesses = O.requiredWitnesses
      fairness = exFairness e
      fairOk = O.bounded || not (null fairness)
      completionOk = exCompletionReachable e
      counts =
        " models=" ++ show (exModelCount e)
          ++ " transitions=" ++ show (length (exEdges e))
          ++ " cells=" ++ show (exCells e)
      head_ =
        [ "BOUNDED " ++ yesno O.bounded
        , "BOUND " ++ show boundDepth ++ "|" ++ show boundModels
        , "CHECKEDPREFIX" ++ counts
        , "COVERAGE" ++ counts
            ++ " depthLimit=" ++ show boundDepth
            ++ " modelLimit=" ++ show boundModels
            ++ " truncated=" ++ yesno (exTruncated e)
        , "NOTE 只覆盖声明前缀（样本前缀）：transitions 只计被 step 接受的样本边（不是全部样本格），"
            ++ "非无限状态/全图证明；初始构型本身就是 Checkpoint 时零步即满足“可达完成”，"
            ++ "不构成对终点的覆盖证明"
        ]
      fairnessLines = [ "ASSUMEDFAIR " ++ t | t <- fairness ]
      -- 无条件输出固定格式 `WITNESS DEFERRED <n>`
      -- （n = length requiredWitnesses，可为 0）；这是屏障判“无语料”的唯一强制来源。
      deferredLines = [ "WITNESS DEFERRED " ++ show (length witnesses) ]
      fails =
        boundProblems
          ++ exViolations e
          ++ [ "FAIL assumedfair-missing 无 AssumedFair 声明（bounded = False）"
             | not fairOk
             ]
          ++ [ "FAIL completion-unreachable 可达集内无 Terminal/Checkpoint 构型"
             | not completionOk
             ]
      code
        | not (null fails) = 2
        | not (null witnesses) = 3  -- n > 0 才置 strict=no（n = 0 仍可 strict pass）
        | otherwise = 0
      strictLine
        | code == 0 = [ "STRICT yes" ]
        | otherwise = [ "STRICT no" ]
  in (head_ ++ deferredLines ++ fairnessLines ++ fails ++ strictLine, code)
