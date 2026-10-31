-- 预言机状态图。效果后缀是该步必须成立的观察名，不是函数名。
module Driver.Render (labelProblems, renderDiagram) where

import qualified Semantics as S
import qualified Labels as L
import Data.List (intercalate, nub, sortOn)
import Data.Maybe (isNothing)

labelProblems :: [String]
labelProblems =
  [ "标签缺失：state " ++ show st | st <- S.states, isNothing (lookup (show st) L.stateLabels) ]
  ++ [ "标签缺失：event " ++ show ev | ev <- S.events, isNothing (lookup (show ev) L.eventLabels) ]
  ++ [ "标签引用了不存在的 state：" ++ k | (k, _) <- L.stateLabels, k `notElem` map show S.states ]
  ++ [ "标签引用了不存在的 event：" ++ k | (k, _) <- L.eventLabels, k `notElem` map show S.events ]
  ++ [ "note 引用了不存在的 state：" ++ k | (k, _) <- L.notes, k `notElem` map show S.states ]
  ++ [ "标签缺失：detail " ++ d | d <- appearedDetails, isNothing (lookup d L.detailLabels) ]
  where
    appearedDetails = nub [ show d | e <- S.edges, m <- [S.eFrom e, S.eTo e], d <- S.marks m ]

renderDiagram :: [String]
renderDiagram =
  [ "stateDiagram-v2" ]
  ++ stateDecls
  ++ [ "    [*] --> " ++ show S.initialState ]
  ++ edgeLines
  ++ noteLines

stateDecls :: [String]
stateDecls =
  [ "    state " ++ quote (lbl ++ detailSuffix s) ++ " as " ++ show s
  | s <- S.states, Just lbl <- [lookup (show s) L.stateLabels] ]
  where
    detailSuffix s =
      let ds = nub [ labelOr L.detailLabels (show d)
                   | e <- S.edges
                   , m <- [S.eFrom e, S.eTo e]
                   , show (S.msState m) == show s
                   , d <- S.marks m ]
      in if null ds then "" else "｜" ++ intercalate "、" ds

edgeLines :: [String]
edgeLines = concatMap onePair sortedPairs
  where
    es = meaningfulEdges
    indexed = zip (map show S.states) [0 :: Int ..]
    idx st = maybe 99 id (lookup st indexed)
    sortedPairs = sortOn (\(f, t) -> (idx f, idx t)) (nub [ (f, t) | (f, _, t, _) <- es ])
    onePair (f, t) =
      let labels = nub [ labelOr L.eventLabels (show ev) ++ effectSuffix effs
                       | (f', ev, t', effs) <- es, f' == f, t' == t ]
      in [ "    " ++ f ++ " --> " ++ t ++ ": " ++ intercalate "，" labels ]

meaningfulEdges :: [(String, S.Ev, String, [S.Effect])]
meaningfulEdges =
  nub
  [ (show (S.msState (S.eFrom e)), S.eEvent e, show (S.msState (S.eTo e)), S.eEffects e)
  | e <- S.edges
  , all ((/= "Unreachable") . show) (S.eEffects e)
  , show (S.msState (S.eFrom e)) /= show (S.msState (S.eTo e)) || not (null (S.eEffects e)) ]

noteLines :: [String]
noteLines = [ "    note right of " ++ k ++ ": " ++ v | (k, v) <- L.notes ]

quote :: String -> String
quote s = "\"" ++ s ++ "\""

effectSuffix :: [S.Effect] -> String
effectSuffix [] = ""
effectSuffix effs = "（" ++ intercalate "、" (map show effs) ++ "）"

labelOr :: [(String, String)] -> String -> String
labelOr pairs k = maybe k id (lookup k pairs)
