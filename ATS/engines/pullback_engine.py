# -*- coding: utf-8 -*-
"""
PullbackEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

class PullbackEngine(BaseEngine):
    def __init__(self, threshold: float = 50):
        super().__init__(name="Pullback", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            low = df["Low"]
            volume = df["Volume"]
            
            swing_low = low.tail(20).min()
            avg_vol = volume.rolling(20).mean().iloc[-1]
            rsi_val = calculate_rsi(close, 14).iloc[-1]
            
            score = 50.0
            
            in_zone = (close.iloc[-1] >= swing_low * 0.97) and (close.iloc[-1] <= swing_low * 1.03)
            if in_zone: score += 25.0
            
            if rsi_val < 35.0: score += 20.0
            elif rsi_val < 45.0: score += 10.0
            
            if volume.iloc[-1] < avg_vol * 0.8: score += 15.0
            
            score = max(0.0, min(100.0, score))
            reason = f"Zone: {'Yes' if in_zone else 'No'} | RSI: {rsi_val:.1f}"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")