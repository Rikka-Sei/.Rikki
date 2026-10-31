-- 视图渲染（非判定证据，非无限证明）：状态图 + 标签完整性门（契约 §2.3）。
--
-- 图里**不** dump 完整构型：同一 (from,to) 相位的边合并成一条，标签 = 事件标签
-- ＋效果 ＋ guard 差异 summary（取自 from 构型的 marks/标签）。这样
-- 「同相位、同事件但效果/守卫不同」不会画成同一条无差异的边；而完整事实
-- （full obs/from/to/effects）由 Snapshot 承担，避免大 bound 下把图撑爆。
--
-- 名字全集取自可达构型的 phase、samples 的事件、marks 的 detail；
-- 标签缺失或引用不存在的名字（含 detail 悬空）即报错。
module Driver.Oracle.Render (labelProblems, renderDiagram) where

import qualified Driver.Oracle.Labels as L
import qualified Oracle as O
import Data.List (intercalate, nub)
import Data.Maybe (fromMaybe, isNothing)
import Driver.Oracle.Graph (Exploration (..), explore, seedModel)

stateNames :: [String]
stateNames = nub (map (show . O.phase) (exModels exp0))
  where
    exp0 = explore

eventNames :: [String]
eventNames = nub (map (show . O.obsEvent) O.samples)

detailNames :: [String]
detailNames = nub [ show d | m <- exModels exp0, d <- O.marks m ]
  where
    exp0 = explore

labelProblems :: [String]
labelProblems =
  [ "标签缺失：state " ++ s | s <- stateNames, isNothing (lookup s L.stateLabels) ]
    ++ [ "标签缺失：event " ++ e | e <- eventNames, isNothing (lookup e L.eventLabels) ]
    ++ [ "标签缺失：detail " ++ d | d <- detailNames, isNothing (lookup d L.detailLabels) ]
    ++ [ "标签引用了不存在的 state：" ++ k | (k, _) <- L.stateLabels, k `notElem` stateNames ]
    ++ [ "标签引用了不存在的 event：" ++ k | (k, _) <- L.eventLabels, k `notElem` eventNames ]
    ++ [ "标签引用了不存在的 detail：" ++ k | (k, _) <- L.detailLabels, k `notElem` detailNames ]
    ++ [ "note 引用了不存在的 state：" ++ k | (k, _) <- L.notes, k `notElem` stateNames ]

renderDiagram :: [String]
renderDiagram =
  [ "stateDiagram-v2" ]
    ++ [ "    state " ++ quote (labelOr L.stateLabels s) ++ " as " ++ s
       | s <- stateNames
       , not (isNothing (lookup s L.stateLabels))
       ]
    ++ [ "    [*] --> " ++ show (O.phase seedModel) ]
    ++ edgeLines
    ++ [ "    note right of " ++ k ++ ": " ++ escapeLabel v | (k, v) <- L.notes ]
  where
    edges = exEdges exp0
    exp0 = explore
    pairs = nub [ (show (O.phase fm), show (O.phase tm)) | (fm, _, tm, _) <- edges ]
    edgeLines =
      [ "    " ++ f ++ " --> " ++ t ++ ": " ++ intercalate "，" labels
      | (f, t) <- pairs
      , let labels =
              nub [ labelOr L.eventLabels (show (O.obsEvent ev))
                      ++ effectSuffix effs ++ guardSummary fm
                  | (fm, ev, tm, effs) <- edges
                  , show (O.phase fm) == f
                  , show (O.phase tm) == t
                  ]
      ]

-- | guard 差异 summary：来自构型的 marks（标签化），空则不带摘要。
guardSummary :: O.Model -> String
guardSummary m = case nub (map (labelOr L.detailLabels . show) (O.marks m)) of
  [] -> ""
  ds -> "[" ++ intercalate "、" ds ++ "]"

labelOr :: [(String, String)] -> String -> String
labelOr pairs k = fromMaybe k (lookup k pairs)

effectSuffix :: [O.Effect] -> String
effectSuffix [] = ""
effectSuffix effs = "（" ++ intercalate "、" (map show effs) ++ "）"

quote :: String -> String
quote s = "\"" ++ escapeLabel s ++ "\""

-- | 图内文本转义：反斜杠与引号（引号裸写会把 mermaid 标签截断）。
escapeLabel :: String -> String
escapeLabel = concatMap esc
  where
    esc '"' = "\\\""
    esc '\\' = "\\\\"
    esc c = [c]
