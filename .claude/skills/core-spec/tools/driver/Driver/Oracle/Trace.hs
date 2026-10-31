-- 迹线解析与严格拒绝矩阵（契约 §4 / §5 / §6）。
--
-- 只把「每一条记录都被 Oracle 接受」的真实执行判为通过；任何结构/信封/观察值/
-- 后继/效果偏差都返回非零退出。观察点写原始身份（obsRawErr），迹线文本里的
-- obsErr 必须为 Nothing；驱动按 ORACLE-ERRMAP（生成模块 rawErrors）注入后调 step。
--
-- 诊断行形如 `FAIL <case> [#<n> ...]`：`#<n>` 是**语料行号**（1-based，整文件）；
-- `#0` 表示无行可指（整文件无记录 / 声明错误），真实行号一律 ≥1。
module Driver.Oracle.Trace (traceCheck) where

import qualified Driver.Oracle.Labels as L
import qualified Oracle as O
import Data.Char (isAlphaNum, isSpace, isUpper)
import Data.List (isInfixOf, isPrefixOf)
import Text.Read (readMaybe)

-- | (machine, 语料文本) -> (诊断行, 驱动退出码；契约 §4.4：0 PASS／2 FAIL／3 UNVERIFIED)
traceCheck :: String -> String -> ([String], Int)
traceCheck machine input
  | not (null badWitnesses) =
      ( [ "FAIL bad-witness #0 见证声明含空路径（不是恒真）：" ++ show n | n <- badWitnesses ]
          ++ [ "STRICT no" ],
        2
      )
  | null cleaned = ([ "FAIL empty #0 输入无任何记录", "STRICT no" ], 2)
  | otherwise = case runLines machine cleaned of
      Left (caseName, detail) ->
        ([ "FAIL " ++ caseName ++ " " ++ detail, "STRICT no" ], 2)
      Right st ->
        let unreached =
              [ n | (n, evs) <- O.requiredWitnesses, not (witnessHit evs (stSegments st)) ]
        in if null unreached
             then
               ( [ "TRACE " ++ machine ++ " records=" ++ show (stRecords st)
                 , "STRICT yes"
                 ],
                 0
               )
             else
               ( [ "TRACE " ++ machine ++ " records=" ++ show (stRecords st) ]
                   ++ [ "UNVERIFIED " ++ show n ++ " required-path-unreached" | n <- unreached ]
                   ++ [ "STRICT no" ],
                 3
               )
  where
    cleaned = [ (n, trim l) | (n, l) <- zip [1 :: Int ..] (lines input), not (null (trim l)) ]
    badWitnesses = [ n | (n, evs) <- O.requiredWitnesses, null evs ]

-- | 见证匹配（§3.1 L182）：同一段内、按记录顺序、**连续**命中至少一次；
-- 跨段拼接不算，子序列跳跃不算。空路径已作 `bad-witness` 拒绝，此处不视为命中。
witnessHit :: [O.Ev] -> [[O.Ev]] -> Bool
witnessHit path segments = not (null path) && any (isInfixOf path) segments

data Seg = Seg
  { sgLine :: Int
  , sgMachine :: String
  , sgObject :: String
  , sgGen :: Int
  , sgRound :: Int
  , sgSeq :: Int
  , sgCount :: Int
  , sgEvents :: [O.Ev]
  , sgModel :: O.Model
  }

data St = St
  { stSeg :: Maybe Seg
  , stHistory :: [(String, Int)]  -- 已结束段 (object, gen)，最新在前
  , stSegments :: [[O.Ev]]        -- 已结束段内按记录顺序的事件序列（见证匹配用）
  , stRecords :: Int
  }

type Bad = (String, String)

-- | 诊断定位前缀：`#<语料行号>`（1-based，整文件）。`#0` = 无行可指
-- （整文件无记录 / 声明错误）；真实行号一律 >= 1。
at :: Int -> String -> String
at n msg = "#" ++ show n ++ " " ++ msg

runLines :: String -> [(Int, String)] -> Either Bad St
runLines machine = go (St Nothing [] [] 0)
  where
    go st [] = case stSeg st of
      Nothing -> Right st
      Just seg
        | sgCount seg == 0 -> Left ("empty", at (sgLine seg) "BEGIN 无 END 且段内零记录")
        | otherwise -> Left ("missing", at (sgLine seg) ("BEGIN 无 END：" ++ sgObject seg))
    go st ((n, l) : ls)
      | "ORACLE-BEGIN " `isPrefixOf` l =
          begin machine n st (drop (length "ORACLE-BEGIN ") l) >>= \st' -> go st' ls
      | "ORACLE-END " `isPrefixOf` l =
          endSeg machine n st (drop (length "ORACLE-END ") l) >>= \st' -> go st' ls
      | "ORACLE " `isPrefixOf` l =
          record machine n st (drop (length "ORACLE ") l) >>= \st' -> go st' ls
      | otherwise = Left ("garbage", at n ("行不匹配 §4.1 语法：" ++ l))

begin :: String -> Int -> St -> String -> Either Bad St
begin machine n st body = case splitOn '|' body of
  [m, o, g] -> do
    g' <- parseInt n "gen" g
    if g' < 1
      then Left ("bad-observation", at n ("gen 必须 >= 1：" ++ show g'))
      else if m /= machine
        then Left ("machine-mismatch", at n ("语料机器 " ++ m ++ " != 路由机器 " ++ machine))
        else case stSeg st of
          Just _ -> Left ("missing", at n "BEGIN 未配 END，不能重开段")
          Nothing -> case lookup o (stHistory st) of
            Just pg
              | g' == pg ->
                  Left ("duplicate", at n ("同 (object, gen) 段重开：" ++ o ++ "|" ++ show g'))
              | g' < pg ->
                  Left ("foreign-generation", at n ("gen 回退：" ++ show pg ++ " -> " ++ show g'))
              | otherwise -> Right (newSeg n machine o g' st)
            Nothing -> Right (newSeg n machine o g' st)
  _ -> Left ("garbage", at n ("BEGIN 字段数错误：" ++ body))

newSeg :: Int -> String -> String -> Int -> St -> St
newSeg n machine o g' st =
  st { stSeg = Just (Seg n machine o g' 1 1 0 [] (O.initialModel (O.Seed o g'))) }

-- | seq 的作用域是 (machine, object, gen, round)：round 变化即从 1 重新计数。
expectedSeq :: Int -> Seg -> Int
expectedSeq r' seg = if r' == sgRound seg then sgSeq seg else 1

record :: String -> Int -> St -> String -> Either Bad St
record machine n st body = case splitOn '|' body of
  [m, o, g, r, q, ob, fr, to, ef] -> do
    seg <- maybe (Left ("missing", at n "记录在 BEGIN 前")) Right (stSeg st)
    if m /= machine || m /= sgMachine seg
      then Left ("machine-mismatch", at n ("记录机器 " ++ m ++ " != " ++ sgMachine seg))
      else if o /= sgObject seg
        then Left ("envelope-mismatch", at n ("object 与段不符：" ++ o ++ " != " ++ sgObject seg))
        else do
          g' <- parseInt n "gen" g
          r' <- parseInt n "round" r
          q' <- parseInt n "seq" q
          if g' < 1 || r' < 1
            then Left ("bad-observation", at n ("gen/round 必须 >= 1：" ++ show (g', r')))
            else if g' /= sgGen seg
              then Left ("envelope-mismatch",
                         at n ("信封 gen " ++ show g' ++ " != 段 gen " ++ show (sgGen seg)))
              else if r' < sgRound seg
                then Left ("foreign-generation",
                           at n ("段内 round 回退：" ++ show (sgRound seg) ++ " -> " ++ show r'))
                else do
                  obs <- readObs n ob
                  if O.obsObject obs /= o
                    then Left ("envelope-mismatch",
                               at n ("obsObject " ++ O.obsObject obs ++ " != 信封 object " ++ o))
                    else if O.obsGen obs /= g' || O.obsRound obs /= r'
                      then Left ("envelope-mismatch",
                                 at n ("信封 " ++ show (g', r') ++ " != obs "
                                        ++ show (O.obsGen obs, O.obsRound obs)))
                      else if O.obsErr obs /= Nothing
                        then Left ("bad-observation", at n "迹线 obsErr 必须为 Nothing（防伪造映射）")
                        else if not (O.obsEvent obs `elem` map O.obsEvent O.samples)
                          then Left ("unknown-event",
                                     at n ("事件不属于本机器 samples 事件集："
                                            ++ show (O.obsEvent obs)))
                          else do
                            fr' <- readModel n "from" fr
                            to' <- readModel n "to" to
                            effs <- parseEffects n ef
                            if q' /= expectedSeq r' seg
                              then Left ("duplicate",
                                         at n ("seq 应为 " ++ show (expectedSeq r' seg)
                                                ++ "，实为 " ++ show q'))
                              else if O.completion (sgModel seg) == O.Terminal
                                then Left ("after-terminal",
                                           at n ("终态构型后仍有记录：" ++ show (sgModel seg)))
                                else if fr' /= sgModel seg
                                  then Left ("from-mismatch",
                                             at n ("expected=" ++ show (sgModel seg)
                                                    ++ " got=" ++ show fr'))
                                  else do
                                    obs' <- injectErr n obs
                                    case O.step (sgModel seg) obs' of
                                      Left O.NotEnabled ->
                                        Left ("not-enabled",
                                              at n ("该事件在构型不可达：" ++ show (sgModel seg)))
                                      Left O.ObservedMismatch ->
                                        Left ("obs-mismatch",
                                              at n ("observation=" ++ show obs'
                                                     ++ " model=" ++ show (sgModel seg)))
                                      Left O.UnknownEvent ->
                                        Left ("unknown-event", at n "事件不属于本机器 samples 事件集")
                                      Right (m2, effs')
                                        | to' /= m2 ->
                                            Left ("step-mismatch",
                                                  at n ("seq=" ++ show q' ++ " target expected="
                                                         ++ show m2 ++ " got=" ++ show to'))
                                        | effs /= effs' ->
                                            Left ("step-mismatch",
                                                  at n ("seq=" ++ show q' ++ " effects expected="
                                                         ++ show effs' ++ " got=" ++ show effs))
                                        | otherwise ->
                                            -- 每条记录的 `to`（含末条）都必须满足 invariants（§5 `invariant`／§9 迹线门）
                                            case [ p | (p, ok) <- O.invariants m2, not ok ] of
                                              (p : _) ->
                                                Left ("invariant " ++ show p,
                                                      at n ("seq=" ++ show q' ++ " to=" ++ show m2
                                                             ++ " invariants 为 False"))
                                              [] ->
                                                let seg' = seg
                                                      { sgSeq = expectedSeq r' seg + 1
                                                      , sgRound = r'
                                                      , sgCount = sgCount seg + 1
                                                      , sgEvents = sgEvents seg ++ [O.obsEvent obs]
                                                      , sgModel = m2
                                                      }
                                                in Right (st
                                                     { stSeg = Just seg'
                                                     , stRecords = stRecords st + 1
                                                     })
  _ -> Left ("bad-observation", at n "记录字段数错误（应为 9 字段）：" ++ body)

endSeg :: String -> Int -> St -> String -> Either Bad St
endSeg machine n st body = case splitOn '|' body of
  [m, o, g, oc] -> do
    seg <- maybe (Left ("missing", at n "END 无 BEGIN")) Right (stSeg st)
    g' <- parseInt n "gen" g
    if g' < 1
      then Left ("bad-observation", at n ("gen 必须 >= 1：" ++ show g'))
      else if m /= machine || m /= sgMachine seg
        then Left ("machine-mismatch", at n ("END 机器 " ++ m ++ " != " ++ machine))
        else if o /= sgObject seg || g' /= sgGen seg
          then Left ("envelope-mismatch", at n ("END 段标识与 BEGIN 不符：" ++ o ++ "|" ++ show g'))
          else if sgCount seg == 0
            then Left ("empty", at n "段内零记录：无可校验的执行证据")
            else case O.completion (sgModel seg) of
              O.Unfinished ->
                Left ("unfinished-segment",
                      at n ("段止于在途态，不能作为可暂停检查点（END outcome=" ++ oc ++ "）"))
              O.Terminal
                | oc == "terminal" -> closeSeg seg g' st
                | otherwise -> Left ("outcome-mismatch", at n ("expected=terminal got=" ++ oc))
              O.Checkpoint
                | oc == "checkpoint" -> closeSeg seg g' st
                | otherwise -> Left ("outcome-mismatch", at n ("expected=checkpoint got=" ++ oc))
  _ -> Left ("garbage", at n ("END 字段数错误：" ++ body))

closeSeg :: Seg -> Int -> St -> Either Bad St
closeSeg seg g' st =
  Right (st { stSeg = Nothing
            , stHistory = (sgObject seg, g') : stHistory st
            , stSegments = sgEvents seg : stSegments st
            })

injectErr :: Int -> O.Observation -> Either Bad O.Observation
injectErr n o = case O.obsRawErr o of
  Nothing -> Right (o { O.obsErr = Nothing })
  Just raw -> case lookup raw L.rawErrors of
    Nothing -> Left ("unmapped-error", at n ("原始身份不在 ORACLE-ERRMAP：" ++ raw))
    Just ctor -> case readMaybe ctor :: Maybe O.Err of
      Nothing -> Left ("unmapped-error", at n ("ERRMAP 目标不是 Err 构造子：" ++ ctor))
      Just e -> Right (o { O.obsErr = Just e })

readObs :: Int -> String -> Either Bad O.Observation
readObs n s = case readMaybe s :: Maybe O.Observation of
  Just x -> Right x
  Nothing -> Left ("bad-observation", at n ("observation 不可 Read：" ++ s))

readModel :: Int -> String -> String -> Either Bad O.Model
readModel n what s = case readMaybe s :: Maybe O.Model of
  Just x -> Right x
  Nothing -> Left ("bad-observation", at n (what ++ " 不可 Read：" ++ s))

parseInt :: Int -> String -> String -> Either Bad Int
parseInt n what s = case readMaybe (trim s) :: Maybe Int of
  Just k -> Right k
  Nothing -> Left ("garbage", at n (what ++ " 不是整数：" ++ s))

-- | effects 必须是可 read 的 [Effect]（严格）；不可 read 时再区分
-- 「列表形但含非 Effect 构造子/空项」= unknown-effect 与「不成形」= bad-observation。
parseEffects :: Int -> String -> Either Bad [O.Effect]
parseEffects n raw
  | Just xs <- readMaybe (trim raw) :: Maybe [O.Effect] = Right xs
  | otherwise = case stripList (trim raw) of
      Just toks
        | any (not . isEffectToken) toks ->
            Left ("unknown-effect", at n ("effects 含非 Effect 构造子：" ++ raw))
      _ -> Left ("bad-observation", at n ("effects 不可 Read：" ++ raw))

stripList :: String -> Maybe [String]
stripList ('[' : rest) = case reverse rest of
  ']' : reversedBody -> Just (map trim (splitOn ',' (reverse reversedBody)))
  _ -> Nothing
stripList _ = Nothing

isEffectToken :: String -> Bool
isEffectToken t = case t of
  (c : _) ->
    isUpper c
      && all (\x -> isAlphaNum x || x == '_' || x == '\'') t
      && maybe False (const True) (readMaybe t :: Maybe O.Effect)
  [] -> False

splitOn :: Char -> String -> [String]
splitOn c s = case break (== c) s of
  (a, []) -> [a]
  (a, _ : b) -> a : splitOn c b

trim :: String -> String
trim = dropWhile isSpace . reverse . dropWhile isSpace . reverse
