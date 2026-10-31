-- SpecOracle.hs — ORACLE 固定驱动入口（契约 §9）。
--
-- 嵌入模块是纯模型（`module Oracle`），按观察值入参的 step 判定；
-- 驱动只做：解析迹线、注入 ERRMAP、调 step、核对后继/效果、枚举有限前缀、
-- 匹配业务见证、按 §4.4 给出三态退出码。实现侧只写观察，本驱动不参与运行时决策。
--
-- 用法：
--   SpecOracle snapshot
--   SpecOracle render
--   SpecOracle graph
--   SpecOracle tracecheck <machine>      -- 语料从 stdin 读入
--
-- 驱动退出码（契约 §4.4，tracecheck / graph 共用）：
--   0 = 全 PASS（strict pass）／1 = 用法错误／2 = 有 FAIL／3 = 无 FAIL 但有
--   UNVERIFIED 或 WARN（含 graph 的 `WITNESS DEFERRED`，非绿、不计 strict pass）。
module Main (main) where

import Driver.Oracle.Graph (graphReport)
import Driver.Oracle.Render (labelProblems, renderDiagram)
import Driver.Oracle.Snapshot (snapshotLines)
import Driver.Oracle.Trace (traceCheck)
import System.Environment (getArgs)
import System.Exit (ExitCode (ExitFailure), exitSuccess, exitWith)
import System.IO (hPutStrLn, hSetEncoding, stderr, stdout, utf8)

main :: IO ()
main = do
  hSetEncoding stdout utf8
  hSetEncoding stderr utf8
  args <- getArgs
  case args of
    ["snapshot"] -> mapM_ putStrLn snapshotLines
    ["render"] -> do
      let problems = labelProblems
      if null problems
        then mapM_ putStrLn renderDiagram >> exitSuccess
        else do
          mapM_ putStrLn problems
          exitWith (ExitFailure 2)  -- 标签门失败属 FAIL（§4.4 码 2）
    ["graph"] -> do
      let (ls, code) = graphReport
      mapM_ putStrLn ls
      exitWithCode code
    ["tracecheck", machine] -> do
      input <- getContents
      let (ls, code) = traceCheck machine input
      mapM_ putStrLn ls
      exitWithCode code
    _ -> do
      hPutStrLn stderr "用法: SpecOracle <snapshot|render|graph|tracecheck <machine>>"
      exitWith (ExitFailure 1)  -- 用法错误（§4.4 码 1）

-- | 驱动退出码映射：0 全 PASS；其余按 §4.4（2 FAIL / 3 UNVERIFIED·WARN）。
exitWithCode :: Int -> IO a
exitWithCode 0 = exitSuccess
exitWithCode n = exitWith (ExitFailure n)
