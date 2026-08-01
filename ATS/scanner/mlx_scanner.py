# -*- coding: utf-8 -*-
"""
MLXScanner - Central hub that aggregates all 9 engines into one DecisionResult
OPTIMIZED FOR DAILY TRADE (INTRADAY)
Timeframe: 1 Hour (1H) - SINKRON DENGAN TRADINGVIEW
"""
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from typing import Dict, Optional
import yfinance as yf

from ..core import (
    DecisionResult, EngineScore, Decision, State, Regime, ConfidenceLevel,
)
from ..engines import (
    LiquidityEngine, RunnerEngine, PullbackEngine, PullbackQualityEngine,
    RegimeEngine, ProbabilityEngine, HeatmapEngine, SectorEngine, SectorLeaderEngine,
)

class MLXScanner:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.logger = self._setup_logger()
        self.engines = {
            "Liquidity": LiquidityEngine(threshold=50),
            "Runner": RunnerEngine(threshold=60),
            "Pullback": PullbackEngine(threshold=50),
            "PullbackQuality": PullbackQualityEngine(threshold=60),
            "Regime": RegimeEngine(threshold=60),
            "Probability": ProbabilityEngine(threshold=60),
            "Heatmap": HeatmapEngine(threshold=60),
            "Sector": SectorEngine(threshold=60),
            "SectorLeader": SectorLeaderEngine(threshold=60),
        }
        self.logger.info(f"✅ MLXScanner initialized with {len(self.engines)} engines")

    def scan(self, ticker: str, df: pd.DataFrame) -> DecisionResult:
        try:
            if not self._validate_input(ticker, df):
                return self._empty_decision(ticker, "Invalid input data")
            
            current_price = df["Close"].iloc[-1]
            timestamp = datetime.now()
            
            engine_scores = {}
            for name, engine in self.engines.items():
                try:
                    score = engine.run(df)
                    engine_scores[name] = score
                except Exception as e:
                    self.logger.error(f"Error in {name}: {str(e)}")
                    engine_scores[name] = EngineScore(
                        name=name, value=0, threshold=60,
                        is_positive=False, reason=f"Error: {str(e)[:50]}"
                    )
            
            return self._aggregate_scores(ticker, df, engine_scores, current_price, timestamp)
        except Exception as e:
            self.logger.error(f"Scanner error for {ticker}: {str(e)}")
            return self._empty_decision(ticker, f"Scanner error: {str(e)}")

    def _aggregate_scores(self, ticker, df, engine_scores, current_price, timestamp):
        scores = [s.value for s in engine_scores.values()]
        positive_count = sum(1 for s in engine_scores.values() if s.is_positive)
        avg_score = np.mean(scores)

        if positive_count >= 6:
            decision, state = Decision.BUY, State.ARMED
            confidence = min(100, avg_score + (positive_count * 3))
        elif positive_count >= 4:
            decision, state = Decision.WAIT, State.CAUTION_HALF_SIZE if positive_count == 4 else State.READY
            confidence = avg_score
        else:
            decision, state = Decision.AVOID, State.MONITOR
            confidence = avg_score

        conf_level = ConfidenceLevel.VERY_HIGH if confidence >= 80 else \
                     ConfidenceLevel.HIGH if confidence >= 65 else \
                     ConfidenceLevel.MEDIUM if confidence >= 50 else ConfidenceLevel.LOW

        regime_score = engine_scores.get("Regime")
        regime = Regime.BULLISH if regime_score and regime_score.value >= 70 else \
                 Regime.DISTRIBUTION if regime_score and regime_score.value <= 30 else Regime.SIDEWAYS

        entry_price, stoploss, takeprofit = self._calculate_price_levels(df, decision)
        
        risk = entry_price - stoploss if entry_price > stoploss else 0
        reward = takeprofit - entry_price
        reward_risk_ratio = reward / risk if risk > 0 else 0
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0
        reward_pct = (reward / entry_price) * 100 if entry_price > 0 else 0

        reasons = self._build_reasons(engine_scores, decision, positive_count)
        warnings = self._build_warnings(engine_scores, df)

        return DecisionResult(
            ticker=ticker, timestamp=timestamp, decision=decision, state=state,
            confidence=confidence, confidence_level=conf_level, regime=regime,
            price=current_price, entry_price=entry_price, stoploss=stoploss,
            takeprofit=takeprofit, risk_pct=risk_pct, reward_pct=reward_pct,
            reward_risk_ratio=reward_risk_ratio, engines=engine_scores,
            evidence_count=positive_count, positive_engines=positive_count,
            reasons=reasons, warnings=warnings, engine_used="MLXScanner v1.0", version="5.0"
        )

    def _calculate_price_levels(self, df, decision):
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        current = close.iloc[-1]
        recent_low = low.tail(10).min()
        
        if decision == Decision.BUY:
            ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
            entry = ema20 if current < ema20 else current
            atr = (high - low).tail(14).mean()
            stoploss = recent_low - (atr * 0.5)
            takeprofit = entry + ((entry - stoploss) * 2.5)
        else:
            entry, stoploss, takeprofit = current, current, current
        return (entry, stoploss, takeprofit)

    def _build_reasons(self, engine_scores, decision, positive_count):
        reasons = [f"{'Strong' if decision == Decision.BUY else 'Mixed' if decision == Decision.WAIT else 'Weak'} consensus: {positive_count}/9 engines positive"]
        positive_engines = sorted([(n, s) for n, s in engine_scores.items() if s.is_positive], key=lambda x: x[1].value, reverse=True)
        for name, score in positive_engines[:3]:
            reasons.append(f"{name}: {score.value:.0f}")
        return reasons

    def _build_warnings(self, engine_scores, df):
        warnings = []
        if not engine_scores.get("Liquidity", EngineScore("",0,0,False,"")).is_positive:
            warnings.append("⚠️ Liquidity trap risk")
        if df["Volume"].iloc[-1] < df["Volume"].tail(20).mean() * 0.8:
            warnings.append("️ Low volume")
        if engine_scores.get("Regime", EngineScore("",0,0,False,"")).value < 30:
            warnings.append("️ Bearish regime")
        return warnings

    def _validate_input(self, ticker, df):
        # Untuk 1H, kita butuh minimal 150 candle 1 jam
        if not ticker or df is None or len(df) < 150:
            return False
        return all(col in df.columns for col in ["Open", "High", "Low", "Close", "Volume"])

    def _empty_decision(self, ticker, reason):
        return DecisionResult(ticker=ticker, timestamp=datetime.now(), decision=Decision.UNKNOWN, state=State.NO_TRADE, confidence=0, confidence_level=ConfidenceLevel.LOW, regime=Regime.SIDEWAYS, price=0, entry_price=0, stoploss=0, takeprofit=0, risk_pct=0, reward_pct=0, reward_risk_ratio=0, engines={}, evidence_count=0, positive_engines=0, reasons=[reason], warnings=[], engine_used="MLXScanner", version="5.0")

    def _setup_logger(self):
        logger = logging.getLogger("MLXScanner")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

# ═══════════════════════════════════════════════════════════════════════════
# BATCH SCANNER (OPTIMIZED FOR DAILY TRADE / INTRADAY - 1H TIMEFRAME)
# ══════════════════════════════════════════════════════════════════════════
class MLXBatchScanner:
    def __init__(self):
        self.scanner = MLXScanner(verbose=False)
        from ..universe import ISSI_TICKERS
        
        self.issi_tickers = ISSI_TICKERS

    def scan_universe(self, market: str = "IDX") -> list:
        """
        Scan all tickers using 1-HOUR (1H) data for Daily Trade / Intraday.
        """
        decisions = []
        for ticker in self.issi_tickers:
            try:
                # PERUBAHAN UTAMA: interval="1h" untuk Daily Trade
                # period="6mo" untuk memastikan kita dapat >150 candle 1 jam
                df = yf.download(ticker, period="6mo", interval="1h", progress=False)
                
                if df.empty or len(df) < 100:
                    continue
                    
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                    
                clean_ticker = ticker.replace(".JK", "")
                decision = self.scanner.scan(clean_ticker, df)
                decisions.append(decision)
            except Exception:
                continue
        return decisions

    def get_armed_signals(self, decisions: list) -> list:
        return [d for d in decisions if d.state == State.ARMED]

    def get_caution_signals(self, decisions: list) -> list:
        return [d for d in decisions if d.state == State.CAUTION_HALF_SIZE]

    def get_ready_signals(self, decisions: list) -> list:
        return [d for d in decisions if d.state == State.READY]

    def summary(self, decisions: list) -> dict:
        return {
            "total": len(decisions),
            "armed": len(self.get_armed_signals(decisions)),
            "caution": len(self.get_caution_signals(decisions)),
            "ready": len(self.get_ready_signals(decisions)),
            "monitor": len([d for d in decisions if d.state == State.MONITOR]),
            "avg_confidence": np.mean([d.confidence for d in decisions]) if decisions else 0,
        }