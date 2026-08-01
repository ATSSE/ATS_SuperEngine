# -*- coding: utf-8 -*-
"""
SectorEngine - RULE 1: 100% SINKRON DENGAN PINE SCRIPT
Pine Logic: Volume Participation vs SMA50 (Single Ticker)
"""
import pandas as pd
import numpy as np
from .base_engine import BaseEngine
from ..core import EngineScore

class SectorEngine(BaseEngine):
    def __init__(self, threshold: float = 60):
        super().__init__(name="Sector", threshold=threshold)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        try:
            volume = df["Volume"]
            
            avg_vol = volume.rolling(50).mean().iloc[-1]
            participation = volume.iloc[-1] / avg_vol if avg_vol > 0 else 0
            
            score = 40.0
            if participation > 1.5: score = 75.0
            elif participation > 1.0: score = 65.0
            elif participation > 0.8: score = 55.0
            
            score = max(0.0, min(100.0, score))
            reason = f"Participation: {participation:.2f}x"
            return EngineScore(name=self.name, value=score, threshold=self.threshold, is_positive=score >= self.threshold, reason=reason)
        except Exception as e:
            return EngineScore(name=self.name, value=50, threshold=self.threshold, is_positive=False, reason=f"Error: {str(e)[:30]}")