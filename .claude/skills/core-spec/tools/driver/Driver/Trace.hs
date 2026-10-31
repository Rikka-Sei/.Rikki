-- 迹线重放是预言机核对：实现记录观察到的事件、相位与效果，
-- stepWith 给出唯一合法后继。效果名是必须成立的观察，不是必须调用的函数。
module Driver.Trace (traceCheck) where

import qualified Semantics as S
import Data.List (nub)
import Data.Maybe (mapMaybe)
import Text.Read (readMaybe)

traceCheck :: String -> [String]
traceCheck input = parseErrors ++ concatMap checkScan (groupByScan parsed)
  where
    numbered = zip [1 :: Int ..] (lines input)
    traceLines = [ (n, drop 6 l) | (n, l) <- numbered, take 6 l == "TRACE " ]
    parseOne (n, body) = case splitOn '|' body of
      [sid, ev, from, to, effs] -> do
        ev'   <- readMaybe ev
        from' <- readMaybe from
        to'   <- readMaybe to
        effs' <- traverse readMaybe (filter (not . null) (splitOn ',' effs))
        let _ = S.stepWith S.transition (S.MS from' False) ev'
            _ = effs' == snd (S.stepWith S.transition (S.MS to' False) ev')
        pure (n, sid, ev', from', to', effs')
      _ -> Nothing
    parsed = mapMaybe parseOne traceLines
    isParseFail (_, l) = take 6 l == "TRACE " && noParse (drop 6 l)
    noParse body = case parseOne (0 :: Int, body) of
      Nothing -> True
      Just _  -> False
    parseErrors =
      [ "FAIL #" ++ show n ++ " parse error [" ++ l ++ "]"
      | (n, l) <- numbered, isParseFail (n, l) ]
    groupByScan rs =
      [ (sid, [ r | r@(_, s, _, _, _, _) <- rs, s == sid ])
      | sid <- nub [ s | (_, s, _, _, _, _) <- rs ] ]

    checkScan (sid, recs) = go Nothing recs
      where
        go _ [] = []
        go ctx ((n, _, ev, from, to, effs) : rest) =
          let (base, cont) = case ctx of
                Nothing  -> (S.MS from False, [])
                Just cur -> (cur,
                  [ "FAIL #" ++ show n ++ " scan=" ++ sid ++ " discontinuity: expected From="
                    ++ show (S.msState cur) ++ " got From=" ++ show from
                  | S.msState cur /= from ])
              (expected, wantEffs) = S.stepWith S.transition base ev
          in if any ((== "Unreachable") . show) wantEffs
               then cont ++ ["FAIL #" ++ show n ++ " id=" ++ sid ++ " event=" ++ show ev ++ " unreachable"]
               else cont ++ validate n sid ev (S.msState expected) to wantEffs effs
                    ++ go (Just expected) rest

    validate n sid ev wantTo to wantEffs effs
      | to == wantTo && effs == wantEffs = []
      | S.traceExempt ev wantTo to wantEffs effs =
          [ "WARN #" ++ show n ++ " id=" ++ sid ++ " trace-exempt" ]
      | otherwise =
          [ "FAIL #" ++ show n ++ " id=" ++ sid ++ " event=" ++ show ev
            ++ " expected=" ++ show wantTo ++ "/" ++ show wantEffs
            ++ " got=" ++ show to ++ "/" ++ show effs ]

splitOn :: Char -> String -> [String]
splitOn c s = case break (== c) s of
  (a, [])  -> [a]
  (a, _:b) -> a : splitOn c b
