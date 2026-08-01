# -*- coding: utf-8 -*-
"""
Telegram Notifier - RULE 7
Format detail 9 engine scores untuk debugging & trading
"""
import os
import logging
import requests
from datetime import datetime
from typing import List

logger = logging.getLogger("TelegramNotifier")

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, verbose: bool = False):
        self.token = token
        self.chat_id = chat_id
        self.verbose = verbose
        self.base_url = f"https://api.telegram.org/bot{token}"
        logger.info("✅ TelegramNotifier initialized")

    def _send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message to Telegram"""
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Telegram API Error: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def send_armed_batch(self, signals: list) -> int:
        """
        RULE 7: Send detailed ARMED signals with 9 engine scores
        """
        if not signals:
            return 0
        
        sent_count = 0
        for signal in signals:
            try:
                # Format waktu scan
                scan_time = signal.timestamp.strftime("%H:%M WIB") if signal.timestamp else datetime.now().strftime("%H:%M WIB")
                
                # Bangun pesan detail 9 engine
                engine_lines = ""
                for name, score_obj in signal.engines.items():
                    # Singkatan nama engine agar rapi
                    short_name = name.replace("PullbackQuality", "PB Quality").replace("SectorLeader", "SectorLdr")
                    engine_lines += f"  {short_name:12s} : {int(score_obj.value)}\n"
                
                # Format pesan sesuai RULE 7
                msg = f"""
<b> MLX PRO v0.22 | ARMED SIGNAL</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>Ticker :</b> {signal.ticker}
<b>TF :</b> 1H
<b>Scan :</b> {scan_time}
━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>9 ENGINE SCORES:</b>
{engine_lines}
<b>Positive :</b> {signal.positive_engines}/9
<b>Decision :</b> {signal.state.value} ({signal.confidence:.1f}%)
━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>💰 PRICE LEVELS:</b>
  Entry : {signal.entry_price:.0f}
  SL    : {signal.stoploss:.0f} ({signal.risk_pct:.1f}%)
  TP    : {signal.takeprofit:.0f} ({signal.reward_pct:.1f}%)
  R:R   : 1:{signal.reward_risk_ratio:.1f}
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ <i>Verify chart sebelum entry!</i>
"""
                if self._send_message(msg):
                    sent_count += 1
                    if self.verbose:
                        logger.info(f"📱 Sent alert for {signal.ticker}")
            except Exception as e:
                logger.error(f"Error formatting signal for {signal.ticker}: {e}")
        
        return sent_count

    def send_startup_notification(self) -> bool:
        """Send server startup notification"""
        msg = f"""
🚀 <b>MLX Dashboard - SERVER ONLINE</b>

✅ Status: Active & Ready
📊 URL: http://localhost:8501
⏰ Time: {datetime.now().strftime('%H:%M:%S')}
📅 Date: {datetime.now().strftime('%Y-%m-%d')}

<b>System Ready for Trading!</b>
Ready to scan 70 ISSI stocks and send alerts.
"""
        return self._send_message(msg)