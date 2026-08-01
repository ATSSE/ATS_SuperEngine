# -*- coding: utf-8 -*-
"""
RunnerEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

class RunnerEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="Runner", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            volume = df["Volume"]
            high = df["High"]
            
            score = 50.0
            
            # Momentum
            mom_pct = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100.0
            if mom_pct > 3.0: score += 20.0
            elif mom_pct > 2.0: score += 15.0
            elif mom_pct > 1.0: score += 10.0
            
            # Acceleration
            m1 = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            m2 = (close.iloc[-2] - close.iloc[-3]) / close.iloc[-3]
            m3 = (close.iloc[-3] - close.iloc[-4]) / close.iloc[-4]
            if m1 > m2 and m2 > m3: score += 15.0
            
            # Volume
            avg_vol = volume.rolling(20).mean().iloc[-1]
            if volume.iloc[-1] > avg_vol * 2.0: score += 20.0
            elif volume.iloc[-1] > avg_vol * 1.5: score += 10.0
            
            # Near High
            high20 = high.tail(20).max()
            if close.iloc[-1] >= high20: score += 15.0
            elif close.iloc[-1] >= high20 * 0.98: score += 8.0
            
            score = max(0.0, min(100.0, score))
            reason = f"Mom: {mom_pct:.1f}% | Near High: {close.iloc[-1] >= high20*0.98}"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")