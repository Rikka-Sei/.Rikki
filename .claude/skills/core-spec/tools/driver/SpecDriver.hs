-- SpecDriver.hs — core-spec 预言机驱动入口。
--
-- 嵌入模块是预言机：给定观察到的事件，stepWith 给出唯一合法后继与必须成立的观察。
-- 它不规定实现调用了哪个函数。实现侧只记录迹线；本驱动核对迹线是否被预言机接受。
--
-- 用法：SpecDriver <snapshot|render|tracecheck>
-- 嵌入块编译时改名为 module Semantics。一个 spec 可以有多台预言机，每台单独编译。
-- 驱动不写死状态名。嵌入模块必须导出：
--   states, terminalStates, events, initialState, edges
--   MS（位置构造 MS state Bool）、msState、stepWith、transition
--   Edge（eFrom, eEvent, eTo, eEffects）
--   propertyResults :: [(propName, Int)]
--   traceExempt :: event -> state -> state -> [effect] -> [effect] -> Bool
--   data Detail、marks :: MS -> [Detail]

module Main (main) where

import Driver.Render (labelProblems, renderDiagram)
import Driver.Snapshot (snapshotLines)
import Driver.Trace (traceCheck)
import System.Environment (getArgs)
import System.Exit (exitFailure)
import System.IO (hPutStrLn, hSetEncoding, stderr, stdout, utf8)

main :: IO ()
main = do
  hSetEncoding stdout utf8
  args <- getArgs
  case args of
    ["snapshot"]   -> mapM_ putStrLn snapshotLines
    ["render"]     -> do
      let problems = labelProblems
      if null problems
        then mapM_ putStrLn renderDiagram
        else do
          mapM_ (hPutStrLn stderr) problems
          exitFailure
    ["tracecheck"] -> getContents >>= mapM_ putStrLn . traceCheck
    _              -> fail "用法: SpecDriver <snapshot|render|tracecheck>"
