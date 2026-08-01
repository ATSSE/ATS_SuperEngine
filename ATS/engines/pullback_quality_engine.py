# -*- coding: utf-8 -*-
"""
PullbackQualityEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

class PullbackQualityEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="PullbackQuality", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            low = df["Low"]
            volume = df["Volume"]
            
            ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
            ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
            ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            swing_low = low.tail(20).min()
            avg_vol = volume.rolling(20).mean().iloc[-1]
            
            score = 50.0
            
            if ema9 > ema21 and ema21 > ema50: score += 30.0
            if close.iloc[-1] > swing_low: score += 20.0
            if volume.iloc[-1] > avg_vol: score += 15.0
            
            score = max(0.0, min(100.0, score))
            reason = f"Aligned: {ema9>ema21>ema50} | Holds Low: {close.iloc[-1]>swing_low}"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")