# -*- coding: utf-8 -*-
"""
ATS Engines Module

9 Refactored Trading Engines (all inherit from BaseEngine):

Single-Ticker Engines:
  1. LiquidityEngine - Detect liquidity traps & fake breakouts
  2. RunnerEngine - Score runner potential (0-100)
  3. PullbackEngine - Detect pullback entry zones
  4. PullbackQualityEngine - Rate pullback quality
  5. RegimeEngine - Detect market regime (BULLISH/SIDEWAYS/DIST)
  6. ProbabilityEngine - Runner probability with liquidity filter

Market-Wide Engines:
  7. HeatmapEngine - Market sentiment from sectors
  8. SectorEngine - Sector momentum analysis
  9. SectorLeaderEngine - Identify leading/weakest sectors

Each engine:
  ✅ Inherits from BaseEngine
  ✅ Implements calculate(df) → EngineScore
  ✅ Returns score 0-100 with reasoning
  ✅ Has backward-compatible legacy functions
  ✅ Can be used independently or chained

Usage:
  engine = LiquidityEngine()
  score = engine.run(df)  # Returns EngineScore
"""

from .base_engine import BaseEngine, SimpleEMAEngine
from .liquidity_engine import LiquidityEngine
from .runner_engine import RunnerEngine
from .pullback_engine import PullbackEngine
from .pullback_quality_engine import PullbackQualityEngine
from .regime_engine import RegimeEngine
from .probability_engine import ProbabilityEngine
from .heatmap_engine import HeatmapEngine
from .sector_engine import SectorEngine
from .sector_leader_engine import SectorLeaderEngine

__all__ = [
    'BaseEngine',
    'SimpleEMAEngine',
    'LiquidityEngine',
    'RunnerEngine',
    'PullbackEngine',
    'PullbackQualityEngine',
    'RegimeEngine',
    'ProbabilityEngine',
    'HeatmapEngine',
    'SectorEngine',
    'SectorLeaderEngine',
]