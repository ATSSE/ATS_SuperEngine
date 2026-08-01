# -*- coding: utf-8 -*-
"""
HeatmapEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
Pine Logic: ATR Ratio & Near Support (Single Ticker)
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

def calculate_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

class HeatmapEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="Heatmap", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            high = df["High"]
            low = df["Low"]
            close = df["Close"]
            
            hl2 = (high.iloc[-1] + low.iloc[-1]) / 2.0
            atr = calculate_atr(high, low, close, 14).iloc[-1]
            atr_ratio = (atr / hl2 * 100.0) if hl2 > 0 else 0
            
            swing_low = low.tail(20).min()
            swing_high = high.tail(20).max()
            
            score = 50.0
            if atr_ratio < 0.5: score += 20.0
            else: score -= 10.0
            
            if close.iloc[-1] < (swing_low + (swing_high - swing_low) * 0.33): score += 25.0
            
            score = max(0.0, min(100.0, score))
            reason = f"ATR%: {atr_ratio:.2f} | Near Sup: {close.iloc[-1] < (swing_low + (swing_high - swing_low) * 0.33)}"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")