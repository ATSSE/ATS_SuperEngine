# -*- coding: utf-8 -*-
"""
MLXScanner - Usage Examples

This file shows how to use MLXScanner in your code
"""

import pandas as pd
from datetime import datetime
from mlx_scanner import MLXScanner, MLXBatchScanner
from ..core import State


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 1: Scan Single Ticker
# ════════════════════════════════════════════════════════════════════════════

def example_single_ticker():
    """Scan one ticker"""
    
    # Initialize scanner
    scanner = MLXScanner(verbose=True)
    
    # Load OHLCV data (you need this from your data source)
    # df = load_data("ESIP", "150 days")
    df = pd.DataFrame({
        'Open': [100, 101, 102, 101, 102, 103] * 25,  # Mock data
        'High': [101, 102, 103, 102, 103, 104] * 25,
        'Low': [99, 100, 101, 100, 101, 102] * 25,
        'Close': [100.5, 101.5, 102.5, 101.5, 102.5, 103.5] * 25,
        'Volume': [1000000] * 150,
    })
    
    # Scan
    decision = scanner.scan(ticker="ESIP", df=df)
    
    # Display result
    print(f"\n{'='*60}")
    print(f"TICKER: {decision.ticker}")
    print(f"Decision: {decision.decision.name}")
    print(f"State: {decision.state.name}")
    print(f"Confidence: {decision.confidence:.0f}%")
    print(f"Entry: {decision.entry_price:.2f}")
    print(f"SL: {decision.stoploss:.2f}")
    print(f"TP: {decision.takeprofit:.2f}")
    print(f"R:R: {decision.reward_risk_ratio:.2f}")
    print(f"\nReasons:")
    for reason in decision.reasons:
        print(f"  - {reason}")
    print(f"\nWarnings:")
    for warning in decision.warnings:
        print(f"  {warning}")
    print(f"{'='*60}\n")
    
    return decision


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 2: Scan Multiple Tickers (Batch)
# ════════════════════════════════════════════════════════════════════════════

def example_batch_scan():
    """Scan 70 tickers at once"""
    
    batch_scanner = MLXBatchScanner()
    
    # Load market data (70 tickers)
    # market = load_market_data()  # Dict[ticker, df]
    market = {
        "ESIP": pd.DataFrame({...}),  # Mock
        "ASII": pd.DataFrame({...}),
        "BBCA": pd.DataFrame({...}),
        # ... 67 more tickers
    }
    
    # Scan all
    print("🔍 Scanning 70 tickers...")
    decisions = batch_scanner.scan_universe(market)
    
    # Get summary
    summary = batch_scanner.summary(decisions)
    print(f"\n📊 Scan Summary:")
    print(f"  Total tickers: {summary['total']}")
    print(f"  🟢 ARMED: {summary['armed']}")
    print(f"  🟡 CAUTION: {summary['caution']}")
    print(f"  🔵 READY: {summary['ready']}")
    print(f"  ⚪ MONITOR: {summary['monitor']}")
    print(f"  Avg Confidence: {summary['avg_confidence']:.0f}%\n")
    
    # Get ARMED signals (ready to trade NOW)
    armed = batch_scanner.get_armed_signals(decisions)
    print(f"🟢 ARMED SIGNALS ({len(armed)} total):")
    for decision in armed:
        print(f"  {decision.ticker}: {decision.confidence:.0f}% confidence")
        print(f"    Entry: {decision.entry_price:.2f}, SL: {decision.stoploss:.2f}, TP: {decision.takeprofit:.2f}")
        print(f"    R:R: {decision.reward_risk_ratio:.2f}")
    
    return decisions


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 3: Filter & Analyze Results
# ════════════════════════════════════════════════════════════════════════════

def example_filter_results(decisions: list):
    """Filter results by various criteria"""
    
    # Get only ARMED signals
    armed = [d for d in decisions if d.state == State.ARMED]
    print(f"✅ ARMED signals: {len(armed)}")
    
    # Get only high confidence
    high_conf = [d for d in decisions if d.confidence >= 80]
    print(f"✅ High confidence (80%+): {len(high_conf)}")
    
    # Get only good R:R
    good_rr = [d for d in decisions if d.reward_risk_ratio >= 2.0]
    print(f"✅ Good R:R (2.0+): {len(good_rr)}")
    
    # Combine: ARMED + High Conf + Good R:R
    premium_signals = [
        d for d in decisions
        if d.state == State.ARMED 
        and d.confidence >= 80
        and d.reward_risk_ratio >= 2.0
    ]
    print(f"✅ Premium signals (all criteria): {len(premium_signals)}")
    
    return premium_signals


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 4: Use Results with Telegram
# ════════════════════════════════════════════════════════════════════════════

def example_telegram_integration(decision):
    """Send decision to Telegram"""
    
    # Use DecisionResult built-in method
    message = decision.telegram_summary()
    
    # Send via telegram bot
    # telegram_bot.send_message(chat_id, message)
    print(f"\n📱 Telegram message:")
    print(message)
    
    # Or detailed message
    detail_message = decision.telegram_detail()
    print(f"\n📱 Detailed Telegram message:")
    print(detail_message)


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 5: Log Results to Database
# ════════════════════════════════════════════════════════════════════════════

def example_database_logging(decision):
    """Save decision to database"""
    
    # Convert to dict
    decision_dict = decision.to_dict()
    
    # Save to database
    # db.insert('decisions', decision_dict)
    
    # Or save to CSV
    import json
    with open('decisions.jsonl', 'a') as f:
        f.write(json.dumps(decision_dict) + '\n')
    
    print(f"✅ Decision logged for {decision.ticker}")


# ════════════════════════════════════════════════════════════════════════════
# MAIN - Run Examples
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 MLXScanner Examples\n")
    
    # Example 1: Single ticker
    print("=" * 60)
    print("EXAMPLE 1: Scan Single Ticker")
    print("=" * 60)
    decision = example_single_ticker()
    
    # Example 4: Telegram
    print("=" * 60)
    print("EXAMPLE 4: Telegram Integration")
    print("=" * 60)
    example_telegram_integration(decision)
    
    # Example 5: Database
    print("=" * 60)
    print("EXAMPLE 5: Database Logging")
    print("=" * 60)
    example_database_logging(decision)
    
    print("\n✅ All examples completed!")
