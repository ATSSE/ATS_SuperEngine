# -*- coding: utf-8 -*-
"""
BASE ENGINE - Parent class untuk semua MLX engines

Setiap engine (EMA, Market, RS, Structure, Volume, Pullback, Momentum)
HARUS inherit dari ini untuk konsistensi.

Principle: "One contract to rule them all"
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime
import pytz

from ..core import EngineScore

WIB = pytz.timezone('Asia/Jakarta')


class BaseEngine(ABC):
    """
    Abstract base class untuk semua trading engines
    
    Contract:
      - Input: DataFrame OHLCV
      - Output: EngineScore (0-100, dengan reason)
      - Properties: name, threshold, is_positive
    """
    
    def __init__(self, name: str, threshold: float = 50):
        """
        Args:
            name: Engine name (e.g., "EMA", "Market", "RS")
            threshold: Minimum value to be considered "positive" (default 50)
        """
        self.name = name
        self.threshold = threshold
        self.df = None
        self.current_idx = None
    
    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        """
        Calculate engine score for current bar
        
        Args:
            df: OHLCV DataFrame with at least 150 bars
            
        Returns:
            EngineScore with value (0-100), is_positive, reason
            
        Must be implemented by subclass
        """
        pass
    
    def run(self, df: pd.DataFrame, idx: int = -1) -> EngineScore:
        """
        Run engine for specific bar index
        
        Args:
            df: OHLCV DataFrame
            idx: Bar index (default -1 = latest bar)
            
        Returns:
            EngineScore
        """
        self.df = df.copy()
        self.current_idx = len(df) + idx if idx < 0 else idx
        
        # Validate minimum data
        if len(df) < 150:
            return EngineScore(
                name=self.name,
                value=0,
                threshold=self.threshold,
                is_positive=False,
                reason=f"Insufficient data: {len(df)} < 150 required"
            )
        
        # Calculate score
        score = self.calculate(df)
        
        # Validate score
        if not (0 <= score.value <= 100):
            score.value = max(0, min(100, score.value))
        
        return score
    
    # ────────────────────────────────────────────────────────────────────────
    # HELPER METHODS (Available to all engines)
    # ────────────────────────────────────────────────────────────────────────
    
    def _safe_get(self, col: str, idx: int) -> Optional[float]:
        """Safely get value from column at index"""
        try:
            val = self.df[col].iloc[idx]
            if pd.isna(val):
                return None
            return float(val)
        except (IndexError, KeyError, TypeError):
            return None
    
    def _ema(self, period: int) -> float:
        """Calculate EMA for period"""
        ema = self.df['Close'].ewm(span=period, adjust=False).mean()
        return float(ema.iloc[self.current_idx])
    
    def _sma(self, period: int) -> float:
        """Calculate SMA for period"""
        sma = self.df['Close'].rolling(window=period).mean()
        return float(sma.iloc[self.current_idx])
    
    def _atr(self, period: int = 14) -> float:
        """Calculate ATR"""
        high = self.df['High'].iloc[max(0, self.current_idx - period):self.current_idx]
        low = self.df['Low'].iloc[max(0, self.current_idx - period):self.current_idx]
        return float(high.max() - low.min()) if len(high) > 0 else 0
    
    def _rsi(self, period: int = 14) -> float:
        """Calculate RSI"""
        close = self.df['Close'].iloc[max(0, self.current_idx - period * 2):self.current_idx]
        
        if len(close) < 2:
            return 50
        
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        if loss.iloc[-1] == 0:
            return 100 if gain.iloc[-1] > 0 else 50
        
        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    def _get_range(self, period: int) -> Tuple[float, float]:
        """Get high/low range for period"""
        high = self.df['High'].iloc[max(0, self.current_idx - period):self.current_idx]
        low = self.df['Low'].iloc[max(0, self.current_idx - period):self.current_idx]
        
        return (float(high.max()) if len(high) > 0 else 0,
                float(low.min()) if len(low) > 0 else 0)
    
    def _volatility(self, period: int = 20) -> float:
        """Calculate volatility (std dev of returns)"""
        close = self.df['Close'].iloc[max(0, self.current_idx - period):self.current_idx]
        
        if len(close) < 2:
            return 0
        
        returns = close.pct_change()
        volatility = returns.std()
        
        return float(volatility) if not pd.isna(volatility) else 0
    
    def _volume_avg(self, period: int = 20) -> float:
        """Average volume for period"""
        vol = self.df['Volume'].iloc[max(0, self.current_idx - period):self.current_idx]
        return float(vol.mean()) if len(vol) > 0 else 0
    
    def _price_position_in_range(self, period: int = 20) -> float:
        """
        Where is current price in recent range?
        Returns: 0-100 (0=at low, 100=at high)
        """
        high, low = self._get_range(period)
        current = self._safe_get('Close', self.current_idx) or 0
        
        if high == low:
            return 50
        
        position = ((current - low) / (high - low)) * 100
        return float(max(0, min(100, position)))
    
    def _is_higher_high(self, period: int = 10) -> bool:
        """Is current high > recent high?"""
        current_high = self._safe_get('High', self.current_idx) or 0
        recent_high, _ = self._get_range(period)
        return current_high > recent_high
    
    def _is_higher_low(self, period: int = 10) -> bool:
        """Is current low > recent low?"""
        current_low = self._safe_get('Low', self.current_idx) or 0
        _, recent_low = self._get_range(period)
        return current_low > recent_low
    
    def _trend_direction(self) -> str:
        """Detect trend (UP, DOWN, SIDEWAYS)"""
        ema9 = self._ema(9)
        ema21 = self._ema(21)
        ema50 = self._ema(50)
        ema150 = self._ema(150)
        
        if ema9 > ema21 > ema50 > ema150:
            return "UP"
        elif ema9 < ema21 < ema50 < ema150:
            return "DOWN"
        else:
            return "SIDEWAYS"
    
    # ────────────────────────────────────────────────────────────────────────
    # LOGGING & DEBUGGING
    # ────────────────────────────────────────────────────────────────────────
    
    def __str__(self) -> str:
        """String representation"""
        return f"<{self.__class__.__name__}: {self.name} (threshold={self.threshold})>"
    
    def __repr__(self) -> str:
        return self.__str__()


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE: Simple EMA Engine (for reference)
# ════════════════════════════════════════════════════════════════════════════

class SimpleEMAEngine(BaseEngine):
    """Example engine: EMA alignment + slope + extension"""
    
    def __init__(self):
        super().__init__(name="EMA", threshold=50)
    
    def calculate(self, df: pd.DataFrame) -> EngineScore:
        """
        Score based on:
          - EMA alignment (perfect pyramid = 75)
          - Slope direction (40)
          - Price extension above EMA9 (30)
        """
        ema9 = self._ema(9)
        ema21 = self._ema(21)
        ema50 = self._ema(50)
        ema150 = self._ema(150)
        close = self._safe_get('Close', self.current_idx) or 0
        
        score = 0
        reasons = []
        
        # Alignment check (75 points max)
        if ema9 > ema21 > ema50 > ema150:
            score += 75
            reasons.append("Perfect alignment (9>21>50>150)")
        elif ema9 > ema21 > ema50:
            score += 60
            reasons.append("Good alignment (9>21>50)")
        elif ema9 > ema21:
            score += 40
            reasons.append("Partial alignment (9>21)")
        
        # Slope (40 points - always positive for now)
        score += 40
        reasons.append("Trend slope positive")
        
        # Extension (30 points if price > EMA9)
        if close > ema9:
            score += 30
            reasons.append(f"Price {close:.0f} above EMA9 {ema9:.0f}")
        
        is_positive = score >= self.threshold
        
        return EngineScore(
            name=self.name,
            value=min(100, score),
            threshold=self.threshold,
            is_positive=is_positive,
            reason=" | ".join(reasons)
        )
