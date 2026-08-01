# -*- coding: utf-8 -*-
"""
CORE CONTRACT - ATS System

DecisionResult adalah central contract yang menghubungkan:
  Scanner Engine → DecisionResult → Notification → Dashboard

Setiap keputusan HARUS diekspresikan dalam bentuk ini.
Tidak ada ambiguitas. Semua audit-able.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
import pytz

WIB = pytz.timezone('Asia/Jakarta')


# ════════════════════════════════════════════════════════════════════════════
# ENUMS (Standardized Values)
# ════════════════════════════════════════════════════════════════════════════

class Decision(str, Enum):
    """Primary trading decision"""
    BUY = "BUY"           # 🟢 Ready to buy NOW
    WAIT = "WAIT"         # 🟡 Monitor, entry soon
    AVOID = "AVOID"       # 🔴 Do not trade this
    UNKNOWN = "UNKNOWN"   # ⚪ Cannot decide


class State(str, Enum):
    """MLX State Machine"""
    NO_TRADE = "NO_TRADE"                  # ⚫ No opportunity
    MONITOR = "MONITOR"                    # 🔵 Monitor only
    READY = "READY"                        # 🔵 Preparing entry
    CAUTION_HALF_SIZE = "CAUTION_HALF_SIZE"  # 🟡 Entry with half size
    ARMED = "ARMED"                        # 🟢 Ready to execute
    FULL = "FULL"                          # 🟢🟢 High confidence


class Regime(str, Enum):
    """Market regime detection"""
    BULLISH = "BULLISH"           # Strong uptrend
    SIDEWAYS = "SIDEWAYS"         # Range-bound
    DISTRIBUTION = "DISTRIBUTION"  # Weak/downtrend


class ConfidenceLevel(str, Enum):
    """Confidence categorization"""
    LOW = "LOW"           # < 50%
    MEDIUM = "MEDIUM"     # 50-70%
    HIGH = "HIGH"         # 70-85%
    VERY_HIGH = "VERY_HIGH"  # 85%+


# ════════════════════════════════════════════════════════════════════════════
# ENGINE SCORE STRUCTURE
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class EngineScore:
    """Single engine score with metadata"""
    name: str                   # e.g., "EMA", "Market", "RS"
    value: float               # 0-100
    threshold: float           # What value triggers "positive"
    is_positive: bool          # value >= threshold?
    reason: str                # Why this score
    
    def __str__(self):
        emoji = "✅" if self.is_positive else "❌"
        return f"{emoji} {self.name}: {self.value:.0f}/100"


# ════════════════════════════════════════════════════════════════════════════
# CORE: DecisionResult CONTRACT
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class DecisionResult:
    """
    🎯 CENTRAL CONTRACT - Every trading decision MUST be expressed in this form
    
    This is the single source of truth for:
      - Scanner engines → what they decided
      - Telegram notifications → what to send
      - Dashboard → what to display
      - Audit trail → what happened and why
      - Backtesting → how well did we decide
    
    PRINCIPLE: "If it can't be expressed as DecisionResult, it's not a valid trading decision"
    """
    
    # IDENTITY
    ticker: str                           # e.g., "ESIP"
    timestamp: datetime                   # When decision was made (WIB)
    
    # PRIMARY DECISION
    decision: Decision                    # BUY / WAIT / AVOID / UNKNOWN
    state: State                         # MLX state machine
    confidence: float                     # 0-100, usually Confidence %
    confidence_level: ConfidenceLevel    # LOW / MEDIUM / HIGH / VERY_HIGH
    
    # MARKET CONTEXT
    regime: Regime                        # BULLISH / SIDEWAYS / DISTRIBUTION
    price: float                          # Current price
    
    # PRICE LEVELS (For BUY decisions)
    entry_price: float                    # Where to buy
    stoploss: float                       # SL level
    takeprofit: float                     # TP level (usually TP2)
    
    # RISK/REWARD
    risk_pct: float                       # % risk from entry to SL
    reward_pct: float                     # % reward from entry to TP
    reward_risk_ratio: float              # TP - Entry / Entry - SL
    
    # ENGINE EVIDENCE (7 engines, scored 0-100)
    engines: Dict[str, EngineScore] = field(default_factory=dict)
    
    # SCORING SUMMARY
    evidence_count: int = 0              # How many engines agree (0-7)
    positive_engines: int = 0            # How many are positive
    
    # REASONING
    reasons: List[str] = field(default_factory=list)  # Why BUY/WAIT/AVOID
    warnings: List[str] = field(default_factory=list) # Caveats/risks
    
    # AUDIT TRAIL
    engine_used: str = "MLX_v0.21e"      # Which engine generated this
    version: str = "5.0"                 # ATS version
    
    # OPTIONAL: Additional context
    liquidity_value: Optional[float] = None   # Daily trading value (Rp)
    recent_trend: Optional[str] = None        # "UP 3 days", "DOWN 1 day", etc
    
    # ────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ────────────────────────────────────────────────────────────────────────
    
    def is_valid(self) -> bool:
        """Validate that this decision is complete and coherent"""
        # Must have decision
        if self.decision == Decision.UNKNOWN:
            return False
        
        # BUY decisions must have price levels
        if self.decision == Decision.BUY:
            if not all([self.entry_price, self.stoploss, self.takeprofit]):
                return False
            if self.entry_price <= self.stoploss:
                return False
            if self.takeprofit <= self.entry_price:
                return False
        
        # Must have at least one engine
        if len(self.engines) == 0:
            return False
        
        # Confidence must match level
        if self.confidence < 50:
            assert self.confidence_level == ConfidenceLevel.LOW
        elif self.confidence < 70:
            assert self.confidence_level == ConfidenceLevel.MEDIUM
        elif self.confidence < 85:
            assert self.confidence_level == ConfidenceLevel.HIGH
        else:
            assert self.confidence_level == ConfidenceLevel.VERY_HIGH
        
        return True
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON/storage"""
        return {
            'ticker': self.ticker,
            'timestamp': self.timestamp.isoformat(),
            'decision': self.decision.value,
            'state': self.state.value,
            'confidence': round(self.confidence, 1),
            'confidence_level': self.confidence_level.value,
            'regime': self.regime.value,
            'price': round(self.price, 0),
            'entry_price': round(self.entry_price, 0) if self.decision == Decision.BUY else None,
            'stoploss': round(self.stoploss, 0) if self.decision == Decision.BUY else None,
            'takeprofit': round(self.takeprofit, 0) if self.decision == Decision.BUY else None,
            'risk_pct': round(self.risk_pct, 2) if self.decision == Decision.BUY else None,
            'reward_pct': round(self.reward_pct, 2) if self.decision == Decision.BUY else None,
            'reward_risk_ratio': round(self.reward_risk_ratio, 2) if self.decision == Decision.BUY else None,
            'engines': {name: {'value': score.value, 'positive': score.is_positive} 
                       for name, score in self.engines.items()},
            'evidence_count': self.evidence_count,
            'positive_engines': self.positive_engines,
            'reasons': self.reasons,
            'warnings': self.warnings,
            'engine_used': self.engine_used,
            'version': self.version,
        }
    
    def summary(self) -> str:
        """One-line summary for logging"""
        if self.decision == Decision.BUY:
            return f"{self.ticker} {self.decision.value} | Conf: {self.confidence:.0f}% | Entry: {self.entry_price:.0f} | R:R {self.reward_risk_ratio:.1f}"
        elif self.decision == Decision.WAIT:
            return f"{self.ticker} {self.decision.value} | Conf: {self.confidence:.0f}% | {self.state.value}"
        else:
            return f"{self.ticker} {self.decision.value} | {self.state.value}"
    
    def telegram_summary(self) -> str:
        """Format for Telegram summary message"""
        if self.decision == Decision.BUY:
            emoji = "🟢"
            return f"{emoji} <b>{self.ticker}</b> {self.state.value} | {self.confidence:.0f}%"
        elif self.decision == Decision.WAIT:
            emoji = "🟡"
            return f"{emoji} <b>{self.ticker}</b> {self.state.value} | {self.confidence:.0f}%"
        else:
            emoji = "⚫"
            return f"{emoji} {self.ticker} {self.state.value}"
    
    def telegram_detail(self) -> str:
        """Format for Telegram detail message"""
        if self.decision != Decision.BUY:
            return ""
        
        msg = f"""⚡ <b>{self.ticker} — {self.state.value}</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 <b>Price:</b> {self.price:.0f}
📈 <b>Confidence:</b> {self.confidence:.1f}% | <b>Evidence:</b> {self.evidence_count}/7

<b>ENTRY ZONE:</b>
  Entry: {self.entry_price:.0f}
  🛑 SL: {self.stoploss:.0f} ({-self.risk_pct:.1f}%)
  ✅ TP: {self.takeprofit:.0f} ({self.reward_pct:.1f}%)
  ⚖️ R:R: 1:{self.reward_risk_ratio:.1f}

<b>7-ENGINE SCORES:</b>
"""
        for name, score in self.engines.items():
            msg += f"  {name}: {score.value:.0f}\n"
        
        if self.warnings:
            msg += f"\n<b>⚠️ WARNINGS:</b>\n"
            for warning in self.warnings:
                msg += f"  • {warning}\n"
        
        msg += f"\n✅ Verify chart sebelum entry!"
        return msg


# ════════════════════════════════════════════════════════════════════════════
# DECISION BATCH (Multiple results)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class DecisionBatch:
    """
    Batch of decisions from one scan cycle
    
    Represents: "Scan at 14:30 generated these 70 decisions for 70 tickers"
    """
    
    scan_time: datetime                        # When scan happened (WIB)
    decisions: List[DecisionResult]            # All 70 decisions
    
    # SUMMARY COUNTS
    buy_count: int = 0                         # How many BUY?
    wait_count: int = 0                        # How many WAIT?
    avoid_count: int = 0                       # How many AVOID?
    
    def __post_init__(self):
        """Calculate counts after init"""
        self.buy_count = sum(1 for d in self.decisions if d.decision == Decision.BUY)
        self.wait_count = sum(1 for d in self.decisions if d.decision == Decision.WAIT)
        self.avoid_count = sum(1 for d in self.decisions if d.decision == Decision.AVOID)
    
    @property
    def buy_tickers(self) -> List[str]:
        """Get list of tickers with BUY decision"""
        return [d.ticker for d in self.decisions if d.decision == Decision.BUY]
    
    @property
    def wait_tickers(self) -> List[str]:
        """Get list of tickers with WAIT decision"""
        return [d.ticker for d in self.decisions if d.decision == Decision.WAIT]
    
    def summary(self) -> str:
        """Summary for logging"""
        return f"Scan {self.scan_time.strftime('%H:%M')} | BUY: {self.buy_count} | WAIT: {self.wait_count} | AVOID: {self.avoid_count}"
