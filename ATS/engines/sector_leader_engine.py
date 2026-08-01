# -*- coding: utf-8 -*-
"""
SectorLeaderEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
Pine Logic: Strength Ratio vs SMA200 (Single Ticker)
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

class SectorLeaderEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="SectorLeader", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            close = df["Close"]
            
            sma_200 = close.rolling(200).mean().iloc[-1]
            strength_ratio = close.iloc[-1] / sma_200 if sma_200 > 0 else 0
            
            score = 40.0
            if strength_ratio > 1.10: score = 80.0
            elif strength_ratio > 1.05: score = 70.0
            elif strength_ratio > 1.00: score = 60.0
            
            score = max(0.0, min(100.0, score))
            reason = f"Ratio vs SMA200: {strength_ratio:.3f}"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")