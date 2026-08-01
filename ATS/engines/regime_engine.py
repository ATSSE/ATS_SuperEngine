# -*- coding: utf-8 -*-
"""
RegimeEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

class RegimeEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="Regime", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            
            ema20 = close.ewm(span=20, adjust=False).mean()
            ema50 = close.ewm(span=50, adjust=False).mean()
            
            ema20_slope = (ema20.iloc[-1] - ema20.iloc[-6]) / ema20.iloc[-6] * 100.0
            ema50_slope = (ema50.iloc[-1] - ema50.iloc[-6]) / ema50.iloc[-6] * 100.0
            
            score = 50.0
            if ema20.iloc[-1] > ema50.iloc[-1] and ema20_slope > 0.0 and ema50_slope > 0.0:
                score = 80.0
            elif ema20.iloc[-1] > ema50.iloc[-1]:
                score = 65.0
            elif ema20.iloc[-1] < ema50.iloc[-1] and ema20_slope < -1.0:
                score = 20.0
                
            score = max(0.0, min(100.0, score))
            reason = f"E20>E50: {ema20.iloc[-1]>ema50.iloc[-1]} | Slope: {ema20_slope:.2f}%"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")