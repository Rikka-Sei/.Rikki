-- 预言机快照。嵌入模块导出的边与性质是「合法观察」，不是实现步骤清单。
module Driver.Snapshot (snapshotLines) where

import qualified Semantics as S
import Data.List (intercalate, nub)

snapshotLines :: [String]
snapshotLines = concat
  [ [ "STATE " ++ show s | s <- S.states ]
  , markLines
  , [ "TERMINAL " ++ show s | s <- S.terminalStates ]
  , [ "EVENT " ++ show e | e <- S.events ]
  , map edgeLine reachableEdges
  , propResults
  ]

markLines :: [String]
markLines =
  [ "MARK " ++ show (S.msState m) ++ "|" ++ intercalate "," (map show ds)
  | m <- explored
  , let ds = S.marks m
  , not (null ds)
  ]
  where
    explored = nub (map S.eFrom S.edges ++ map S.eTo S.edges)

reachableEdges :: [S.Edge]
reachableEdges = [e | e <- S.edges, all ((/= "Unreachable") . show) (S.eEffects e)]

edgeLine :: S.Edge -> String
edgeLine (S.Edge f ev t effs) =
  "TRANS " ++ intercalate "|"
    [ show (S.msState f), show ev, show (S.msState t)
    , intercalate "," (map show effs) ]

propResults :: [String]
propResults = map fmt props
  where
    fmt (n, c) = "PROP " ++ show n ++ "|" ++ (if c == 0 then "PASS" else "FAIL") ++ "|" ++ show c
    props = S.propertyResults
