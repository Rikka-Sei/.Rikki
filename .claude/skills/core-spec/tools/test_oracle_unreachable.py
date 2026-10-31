#!/usr/bin/env python3
"""验证预言机驱动拒绝不可达事件，并不把不可达格渲染为业务边。"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parent
DRIVER = TOOLS / "driver" / "SpecDriver.hs"

SEMANTICS = r"""module Semantics
  ( Phase (..), Ev (..), Effect (..), MS (..), Edge (..), PropName (..), Detail (..)
  , states, terminalStates, events, initialState, transition, stepWith, edges
  , propertyResults, traceExempt, marks
  ) where

data Phase = Ready deriving (Eq, Ord, Show, Read, Enum, Bounded)
data Ev = EvAllowed | EvForbidden deriving (Eq, Ord, Show, Read, Enum, Bounded)
data Effect = Observed | Unreachable deriving (Eq, Ord, Show, Read)
data Detail = NoDetail deriving (Eq, Ord, Show, Read)
data MS = MS { msState :: Phase, msFlag :: Bool } deriving (Eq, Ord, Show)
data Edge = Edge { eFrom :: MS, eEvent :: Ev, eTo :: MS, eEffects :: [Effect] } deriving (Show)
data PropName = Closed deriving (Eq, Ord, Show, Read)
states :: [Phase]
states = [Ready]
terminalStates :: [Phase]
terminalStates = []
events :: [Ev]
events = [EvAllowed, EvForbidden]
initialState :: Phase
initialState = Ready
transition :: Phase -> Ev -> (Phase, [Effect])
transition Ready EvAllowed = (Ready, [Observed])
transition Ready EvForbidden = (Ready, [Unreachable])
stepWith :: (Phase -> Ev -> (Phase, [Effect])) -> MS -> Ev -> (MS, [Effect])
stepWith trans (MS state _) event = let (next, effects) = trans state event in (MS next False, effects)
edges :: [Edge]
edges = [ Edge (MS Ready False) event next effects | event <- events, let (next, effects) = stepWith transition (MS Ready False) event ]
propertyResults :: [(PropName, Int)]
propertyResults = [(Closed, 0)]
traceExempt :: Ev -> Phase -> Phase -> [Effect] -> [Effect] -> Bool
traceExempt _ _ _ _ _ = False
marks :: MS -> [Detail]
marks _ = []
"""

LABELS = r"""module Labels (stateLabels, eventLabels, notes, detailLabels) where
stateLabels :: [(String, String)]
stateLabels = [("Ready", "就绪")]
eventLabels :: [(String, String)]
eventLabels = [("EvAllowed", "允许"), ("EvForbidden", "禁止")]
notes :: [(String, String)]
notes = []
detailLabels :: [(String, String)]
detailLabels = []
"""


@unittest.skipIf(shutil.which("ghc") is None, "ghc not installed")
class OracleUnreachableTest(unittest.TestCase):
    def test_unreachable_is_hidden_and_trace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="oracle-unreachable-") as tmp:
            root = pathlib.Path(tmp)
            (root / "Semantics.hs").write_text(SEMANTICS, encoding="utf-8")
            (root / "Labels.hs").write_text(LABELS, encoding="utf-8")
            exe = root / "driver.exe"
            compiled = subprocess.run(
                [
                    "ghc", "-Wall", "-Werror=incomplete-patterns",
                    "-i" + str(root), "-i" + str(DRIVER.parent),
                    "-outputdir", str(root / "build"), "-o", str(exe), str(DRIVER),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)

            snapshot = subprocess.run([str(exe), "snapshot"], capture_output=True, text=True, encoding="utf-8")
            self.assertIn("EvAllowed", snapshot.stdout)
            self.assertNotIn("TRANS Ready|EvForbidden", snapshot.stdout)

            rendered = subprocess.run([str(exe), "render"], capture_output=True, text=True, encoding="utf-8")
            self.assertIn("允许", rendered.stdout)
            self.assertNotIn("禁止", rendered.stdout)

            trace = subprocess.run(
                [str(exe), "tracecheck"],
                input="TRACE x|EvForbidden|Ready|Ready|Unreachable\n",
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertIn("FAIL #1 id=x event=EvForbidden unreachable", trace.stdout)


if __name__ == "__main__":
    unittest.main()
