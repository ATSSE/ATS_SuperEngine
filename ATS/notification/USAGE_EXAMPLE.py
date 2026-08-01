# -*- coding: utf-8 -*-
"""
TelegramNotifier - Usage Examples

Shows how to send trading signals to Telegram
"""

from ATS.notification import TelegramNotifier, create_telegram_callbacks
from ATS.scheduler import MLXScheduler
import pandas as pd


# ════════════════════════════════════════════════════════════════════════════
# STEP 1: Get Telegram Bot Token
# ════════════════════════════════════════════════════════════════════════════

"""
1. Open Telegram and search for @BotFather
2. Create new bot: /newbot
3. Copy your bot token (long alphanumeric string)
4. Add token to your code below

Your token should look like:
6234567890:AAGdrvGQBLqXXXXXXXXXXXXXXXXXXXXXXXX
"""


# ════════════════════════════════════════════════════════════════════════════
# STEP 2: Get Your Chat ID
# ════════════════════════════════════════════════════════════════════════════

"""
1. Start your bot: find it in Telegram and click START or type /start
2. Send any message to your bot
3. Visit: https://api.telegram.org/bot{YOUR_TOKEN}/getUpdates
4. Look for "chat": {"id": 123456789} - that's your chat ID!

Your chat ID should look like:
123456789
"""


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 1: Test Connection
# ════════════════════════════════════════════════════════════════════════════

def example_test_connection():
    """Test Telegram bot is working"""
    
    # YOUR CREDENTIALS (replace with yours!)
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"
    
    # Create notifier
    notifier = TelegramNotifier(
        token=BOT_TOKEN,
        chat_id=CHAT_ID,
        verbose=True
    )
    
    # Test connection
    if notifier.test_connection():
        print("✅ Bot connected successfully!")
    else:
        print("❌ Connection failed - check token & chat ID")


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 2: Send Single ARMED Signal
# ════════════════════════════════════════════════════════════════════════════

def example_send_armed_signal():
    """Send single ARMED signal to Telegram"""
    
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"
    
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Create mock decision
    from ATS.core import DecisionResult, Decision, State, Regime, ConfidenceLevel
    from datetime import datetime
    
    decision = DecisionResult(
        ticker="ESIP",
        timestamp=datetime.now(),
        decision=Decision.BUY,
        state=State.ARMED,
        confidence=82,
        confidence_level=ConfidenceLevel.HIGH,
        regime=Regime.BULLISH,
        price=1075,
        entry_price=1050,
        stoploss=1000,
        takeprofit=1180,
        risk_pct=4.8,
        reward_pct=12.4,
        reward_risk_ratio=2.6,
        engines={},
        evidence_count=7,
        positive_engines=7,
        reasons=["Strong momentum", "Above EMAs", "Volume confirmed"],
        warnings=[]
    )
    
    # Send
    if notifier.send_armed(decision):
        print("✅ ARMED signal sent!")
    else:
        print("❌ Failed to send")


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 3: Send Batch ARMED Signals
# ════════════════════════════════════════════════════════════════════════════

def example_send_armed_batch():
    """Send multiple ARMED signals"""
    
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"
    
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Mock decisions (from MLXScanner)
    decisions = []  # Would come from scanner.scan_universe()
    
    # Send batch
    sent = notifier.send_armed_batch(decisions)
    print(f"✅ Sent {sent} ARMED signals")


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 4: Send Scan Summary
# ════════════════════════════════════════════════════════════════════════════

def example_send_summary():
    """Send scan summary (statistics)"""
    
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"
    
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Mock decisions (from MLXScanner)
    decisions = []  # Would come from scanner.scan_universe()
    
    # Send summary only
    if notifier.send_scan_summary(decisions):
        print("✅ Summary sent!")


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 5: Custom Alert
# ════════════════════════════════════════════════════════════════════════════

def example_custom_alert():
    """Send custom alert"""
    
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"
    
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Send info alert
    notifier.send_alert(
        title="Scheduler Started",
        content="MLXScanner running. Next scan: 09:30",
        alert_type="info"
    )
    
    # Send warning alert
    notifier.send_alert(
        title="High Volume Alert",
        content="ESIP volume 3x average - possible breakout!",
        alert_type="warning"
    )
    
    # Send error alert
    notifier.send_alert(
        title="Connection Lost",
        content="Market data connection error. Retrying...",
        alert_type="error"
    )
    
    # Send success alert
    notifier.send_alert(
        title="Trade Executed",
        content="ESIP BUY @ 1050 | SL: 1000 | TP: 1180",
        alert_type="success"
    )


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 6: FULL INTEGRATION - Scheduler + Telegram
# ════════════════════════════════════════════════════════════════════════════

def example_full_integration():
    """Complete setup: Scheduler → Telegram"""
    
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    CHAT_ID = "YOUR_CHAT_ID_HERE"
    
    # Create notifier
    notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)
    
    # Create callbacks
    on_scan_complete, on_armed_signal = create_telegram_callbacks(notifier)
    
    # Define market loader
    def load_market_data():
        """Load 70 tickers"""
        # Your loading logic
        return {}
    
    # Create scheduler with Telegram callbacks
    scheduler = MLXScheduler(
        market_loader=load_market_data,
        on_scan_complete=on_scan_complete,  # ← Send summary
        on_armed_signal=on_armed_signal,    # ← Send ARMED alerts
        verbose=True
    )
    
    # Start
    scheduler.start()
    
    print("✅ Full integration running!")
    print("   MLXScanner → DecisionResult → Telegram Notifier")
    print("\nYou'll receive:")
    print("  📊 Scan summary every 30 minutes")
    print("  🟢 ARMED alerts when signals found")


# ════════════════════════════════════════════════════════════════════════════
# EXAMPLE 7: Production Setup
# ════════════════════════════════════════════════════════════════════════════

"""
PRODUCTION DEPLOYMENT:

1. Set environment variables (safer than hardcoding):
   
   Windows (CMD):
   set TELEGRAM_BOT_TOKEN=6234567890:AAGdrvGQBLqXXXXXXXX
   set TELEGRAM_CHAT_ID=123456789
   
   Linux/Mac:
   export TELEGRAM_BOT_TOKEN=6234567890:AAGdrvGQBLqXXXXXXXX
   export TELEGRAM_CHAT_ID=123456789

2. Load from environment:
   
   import os
   BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
   CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
   
   notifier = TelegramNotifier(BOT_TOKEN, CHAT_ID)

3. Setup scheduler with notifier

4. Run indefinitely:
   
   while True:
       scheduler.start()
       # Runs automatically
"""


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 TelegramNotifier Examples\n")
    
    print("=" * 60)
    print("EXAMPLE 1: Test Connection")
    print("=" * 60)
    print("(Uncomment to test)")
    # example_test_connection()
    
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Send ARMED Signal")
    print("=" * 60)
    print("(Uncomment to send)")
    # example_send_armed_signal()
    
    print("\n" + "=" * 60)
    print("EXAMPLE 6: Full Integration")
    print("=" * 60)
    print("(Recommended - combines Scheduler + Telegram)")
    # example_full_integration()
    
    print("\n✅ Examples ready! Replace credentials and uncomment to run.")
