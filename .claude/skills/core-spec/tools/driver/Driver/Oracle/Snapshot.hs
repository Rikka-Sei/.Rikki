-- 快照（不判定）：可达构型的**完整事实** + 每条 checked legal transition 的
-- 完整 obs / from / to / effects + 不变式求值 + bound 取值。
--
-- TRANS 逐条来自 BFS 的 checked 迁移（同一构型只展开一次），不做 O(n²) 去重，
-- 以免大 bound（如 20000）下退化。只覆盖 bound 声明的有限前缀（样本前缀），
-- 不是无限状态证明；完整事实只在这里全量输出，视图（Render）不重复 dump 构型。
module Driver.Oracle.Snapshot (snapshotLines) where

import qualified Oracle as O
import Data.List (intercalate, nub)
import Driver.Oracle.Graph (Exploration (..), explore, yesno)

snapshotLines :: [String]
snapshotLines =
  [ "BOUNDED " ++ yesno O.bounded
  , "BOUND " ++ show (O.maxDepth (O.bound)) ++ "|" ++ show (O.maxModels (O.bound))
  , "PREFIX models=" ++ show (exModelCount e) ++ " truncated=" ++ yesno (exTruncated e)
  , "NOTE 只覆盖声明前缀（样本前缀），非无限状态证明"
  ]
    ++ map propLine invariantNames
    ++ [ "WITNESS " ++ show n ++ "|" ++ intercalate "," (map show evs)
       | (n, evs) <- O.requiredWitnesses
       ]
    ++ [ "MODEL " ++ show m ++ "|" ++ show (O.completion m) | m <- exModels e ]
    ++ [ "TRANS " ++ show o ++ "|" ++ show fm ++ "|" ++ show tm ++ "|" ++ show effs
       | (fm, o, tm, effs) <- exEdges e
       ]
  where
    e = explore

invariantNames :: [O.PropName]
invariantNames = nub [ n | m <- exModels exp0, (n, _) <- O.invariants m ]
  where
    exp0 = explore

propLine :: O.PropName -> String
propLine n =
  "PROP " ++ show n ++ "|" ++ (if bad == 0 then "PASS" else "FAIL") ++ "|" ++ show bad
  where
    bad = length [ () | m <- exModels exp0, (n', ok) <- O.invariants m, n' == n, not ok ]
    exp0 = explore
