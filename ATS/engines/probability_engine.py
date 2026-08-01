# -*- coding: utf-8 -*-
"""
ProbabilityEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore
from .liquidity_engine import LiquidityEngine

class ProbabilityEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="Probability", threshold=threshold)
        self.liq_engine = LiquidityEngine(threshold=50)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            volume = df["Volume"]
            high = df["High"]
            
            score = 20.0
            
            momentum_1d = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            if momentum_1d > 0.03: score += 25.0
            elif momentum_1d > 0.02: score += 18.0
            elif momentum_1d > 0.01: score += 10.0
            
            m1 = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            m2 = (close.iloc[-2] - close.iloc[-3]) / close.iloc[-3]
            m3 = (close.iloc[-3] - close.iloc[-4]) / close.iloc[-4]
            if m1 > m2 and m2 > m3: score += 15.0
            
            avg_vol = volume.rolling(20).mean().iloc[-1]
            if volume.iloc[-1] > avg_vol * 2.0: score += 25.0
            elif volume.iloc[-1] > avg_vol * 1.5: score += 15.0
            
            high20 = high.tail(20).max()
            if close.iloc[-1] >= high20: score += 20.0
            elif close.iloc[-1] >= high20 * 0.98: score += 10.0
            
            # Liquidity Filter
            liq_score = self.liq_engine.run(df).value
            if liq_score < 50.0: score -= 25.0
            
            score = max(0.0, min(100.0, score))
            reason = f"Mom: {momentum_1d*100:.1f}% | Liq: {liq_score:.0f}"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=20, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")