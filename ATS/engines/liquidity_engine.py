# -*- coding: utf-8 -*-
"""
LiquidityEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

class LiquidityEngine(BaseEngine):
    def __init__(self, threshold: float = 50):
        super().__init__(name="Liquidity", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            low = df["Low"]
            high = df["High"]
            volume = df["Volume"]
            open_price = df["Open"]
            
            # PINE: highest_5 = ta.highest(low, 5) -> Highest low of previous 5 bars
            highest_5 = low.iloc[-6:-1].max()
            avg_vol_20 = volume.rolling(20).mean().iloc[-1]
            highest_high_6 = high.iloc[-7:-1].max()
            
            score = 75.0
            
            # PINE: trap_detected
            trap_detected = (highest_5 < low.iloc[-1]) and (close.iloc[-1] > open_price.iloc[-1]) and (volume.iloc[-1] > avg_vol_20 * 1.5)
            trap_penalty = 40.0 if trap_detected else 0.0
            
            # PINE: fake_breakout
            fake_breakout = (close.iloc[-1] < highest_high_6) and (close.iloc[-2] >= highest_high_6)
            breakout_penalty = 25.0 if fake_breakout else 0.0
            
            # PINE: vol_score
            vol_ratio = volume.iloc[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
            if vol_ratio > 2.0: vol_score = 75.0
            elif vol_ratio > 1.5: vol_score = 65.0
            elif vol_ratio > 1.0: vol_score = 55.0
            else: vol_score = 35.0
            
            # PINE: pa_bonus
            range_total = high.iloc[-1] - low.iloc[-1]
            upper_wick = high.iloc[-1] - close.iloc[-1]
            lower_wick = close.iloc[-1] - low.iloc[-1]
            upper_ratio = upper_wick / range_total if range_total > 0 else 0
            lower_ratio = lower_wick / range_total if range_total > 0 else 0
            pa_strong = (upper_ratio < 0.4 or lower_ratio < 0.4) and volume.iloc[-1] > avg_vol_20
            pa_bonus = 10.0 if pa_strong else 0.0
            
            # PINE: Final Score
            score = 75.0 - trap_penalty - breakout_penalty + (vol_score - 50.0) / 2.0 + pa_bonus
            score = max(0.0, min(100.0, score))
            
            reason = f"Trap: {'Yes' if trap_detected else 'No'} | Vol: {vol_ratio:.1f}x"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")