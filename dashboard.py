# -*- coding: utf-8 -*-
"""
MLX Trading Dashboard v5.4 - COMPLETE WITH JOURNAL & LEARNING
- Full execution tracking with checkboxes
- Auto-save to trade database
- Complete table view with filters
- Export executed trades to CSV
- Learning system integration
- Action-oriented UI for fast trading
"""
import sys
import os
from pathlib import Path

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))
os.chdir(str(parent_dir))

import streamlit as st
import pandas as pd
from datetime import datetime
from typing import List, Dict

from ATS.core import DecisionResult, State, Decision
from ATS.scanner import MLXBatchScanner
from ATS.notification.telegram import TelegramNotifier

# ═══════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="MLX Trading Dashboard v5.4",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ══════════════════════════════════════════════════════════════
# SESSION STATE INITIALIZATION
# ═══════════════════════════════════════════════════════════════
if 'last_scan_time' not in st.session_state:
    st.session_state.last_scan_time = None
if 'last_scan_count' not in st.session_state:
    st.session_state.last_scan_count = 0
if 'signals' not in st.session_state:
    st.session_state.signals = []
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = datetime.now()
if 'show_audit' not in st.session_state:
    st.session_state.show_audit = False
if 'show_analytics' not in st.session_state:
    st.session_state.show_analytics = False
if 'show_summary' not in st.session_state:
    st.session_state.show_summary = False
if 'executed_trades' not in st.session_state:
    st.session_state.executed_trades = {}
if 'selected_tickers' not in st.session_state:
    st.session_state.selected_tickers = {}

# ══════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════
st.title("📊 MLX Trading Dashboard v5.4")
st.subheader("Live Trading Signals + Journal + Learning System")

# ══════════════════════════════════════════════════════════════
# HEADER METRICS
# ══════════════════════════════════════════════════════════════
col_info1, col_info2, col_info3 = st.columns(3)
with col_info1:
    st.metric("🟢 Server Status", "ONLINE", delta="Ready")
with col_info2:
    st.metric("Version", "v5.4", delta="Latest")
with col_info3:
    st.metric("⏰ Current Time", datetime.now().strftime("%H:%M:%S"), delta="Live")

st.divider()

# ═══════════════════════════════════════════════════════════════
# TELEGRAM INITIALIZATION
# ═══════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
telegram_notifier = None

if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    try:
        telegram_notifier = TelegramNotifier(
            token=TELEGRAM_BOT_TOKEN,
            chat_id=TELEGRAM_CHAT_ID,
            verbose=True
        )
        st.sidebar.success("✅ Telegram connected!")
    except Exception as e:
        st.sidebar.error(f"❌ Telegram error: {e}")
else:
    st.sidebar.warning("⚠️ Telegram credentials not set")

# ═══════════════════════════════════════════════════════════════
# SIDEBAR CONTROLS
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("⚙️ Controls")
    
    # SYSTEM STATUS
    st.subheader("📊 System Status")
    status_cols = st.columns(2)
    with status_cols[0]:
        st.metric("Server", "🟢 ONLINE")
    with status_cols[1]:
        if st.session_state.last_scan_time:
            time_diff = (datetime.now() - st.session_state.last_scan_time).seconds
            st.metric("Last Scan", f"{time_diff}s ago")
        else:
            st.metric("Last Scan", "Never")
            
    if st.session_state.last_scan_count > 0:
        st.sidebar.success(f"✅ Last scan: {st.session_state.last_scan_count} ARMED signals")
    
    st.divider()
    
    # SCANNER - MAIN ACTION
    st.subheader("🔍 Market Scanner")
    if st.button("🚀 Scan ISSI Universe (70 Saham)", key="scan_issi_btn", use_container_width=True):
        try:
            with st.spinner(" Scanning 70 ISSI stocks... Please wait 30-60 seconds."):
                scanner = MLXBatchScanner()
                results = scanner.scan_universe("IDX")
                armed_signals = scanner.get_armed_signals(results)
                
                st.session_state.signals = armed_signals
                st.session_state.last_refresh = datetime.now()
                st.session_state.last_scan_time = datetime.now()
                st.session_state.last_scan_count = len(armed_signals)
                
                st.success(f"✅ SCAN COMPLETE! Found {len(armed_signals)} ARMED signals.")
                
                if telegram_notifier and armed_signals:
                    sent = telegram_notifier.send_armed_batch(armed_signals)
                    st.sidebar.success(f"📱 Sent {sent} Telegram alerts!")
                
                st.rerun()
        except Exception as e:
            st.error(f"❌ Scan error: {str(e)}")
    
    st.divider()
    
    # TELEGRAM RE-SEND
    if st.session_state.signals:
        st.subheader(" Telegram Alerts")
        if st.button("🔄 Re-send Alerts", key="send_telegram_btn", use_container_width=True):
            if telegram_notifier:
                armed_signals = [s for s in st.session_state.signals if s.state == State.ARMED]
                if armed_signals:
                    try:
                        sent = telegram_notifier.send_armed_batch(armed_signals)
                        st.success(f"✅ Re-sent {sent} ARMED signals!")
                    except Exception as e:
                        st.error(f" Telegram error: {str(e)}")
                else:
                    st.warning("⚠️ No ARMED signals to send")
            else:
                st.error("❌ Telegram not configured")
        st.divider()
    
    # LEARNING SYSTEM
    st.subheader("🧠 Learning System")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Engine Audit", key="audit_btn", use_container_width=True):
            st.session_state.show_audit = True
    with col2:
        if st.button("Analytics", key="analytics_btn", use_container_width=True):
            st.session_state.show_analytics = True
            
    if st.button(" Trade Summary", key="summary_btn", use_container_width=True):
        st.session_state.show_summary = True
    
    st.divider()
    
    # FILTERS
    st.subheader(" Filters")
    min_confidence = st.slider("Min Confidence", min_value=0, max_value=100, value=50, step=5)
    min_rr = st.slider("Min R:R", min_value=1.0, max_value=5.0, value=2.0, step=0.5)
    
    st.divider()
    st.caption(f"Last refresh: {st.session_state.last_refresh.strftime('%H:%M:%S')}")

# ═══════════════════════════════════════════════════════════════
# LEARNING SYSTEM - GRACEFUL HANDLING
# ══════════════════════════════════════════════════════════════
def safe_import_learning_system():
    try:
        from ATS.trade_database import TradeDatabase
        from ATS.engine_audit import EngineAudit
        return TradeDatabase, EngineAudit
    except ImportError:
        return None, None
    except Exception:
        return None, None

if st.session_state.get('show_audit', False):
    st.subheader("🔍 Engine Audit Report")
    TradeDatabase, EngineAudit = safe_import_learning_system()
    
    if TradeDatabase is None:
        st.info("📝 **Engine Audit belum tersedia.**\n\nFitur ini memerlukan modul `trade_database.py` dan `engine_audit.py`. Fitur utama (Scanner + Telegram) sudah berjalan normal.")
    else:
        try:
            db = TradeDatabase()
            audit = EngineAudit(db)
            audit.audit_all_engines()
            if audit.audits:
                ranking = audit.get_engine_ranking()
                st.write("**Engines Ranked by Expected Value:**")
                for i, eng_audit in enumerate(ranking, 1):
                    ev = eng_audit.calculate_expected_value()
                    col1, col2, col3, col4, col5 = st.columns(5)
                    with col1: st.metric("Engine", eng_audit.engine_name)
                    with col2: st.metric("EV", f"{ev:+.3f}%")
                    with col3: st.metric("Accuracy", f"{eng_audit.accuracy_rate:.1f}%")
                    with col4: st.metric("Appearances", eng_audit.total_appearances)
                    with col5: st.metric("Verdict", eng_audit.verdict)
            else:
                st.info("No engine audit data yet - run trades first!")
            db.close()
        except Exception as e:
            st.warning(f"⚠️ Engine Audit belum siap: {str(e)[:80]}")
    st.session_state.show_audit = False

if st.session_state.get('show_analytics', False):
    st.subheader(" Win Rate Analytics")
    TradeDatabase, _ = safe_import_learning_system()
    
    if TradeDatabase is None:
        st.info("📝 **Analytics belum tersedia.**\n\nFitur ini memerlukan modul `trade_database.py`. Fitur utama (Scanner + Telegram) sudah berjalan normal.")
    else:
        try:
            db = TradeDatabase()
            tab1, tab2, tab3 = st.tabs(["By Confidence", "By Regime", "By Sector"])
            
            with tab1:
                st.write("**Win Rate by Confidence Level**")
                by_conf = db.get_stats_by_confidence()
                if by_conf:
                    for conf_level, stats in by_conf.items():
                        if stats is None:
                            continue
                        col1, col2, col3, col4 = st.columns(4)
                        with col1: st.metric(conf_level, f"{stats.get('win_rate', 0):.1f}%")
                        with col2: st.metric("Trades", stats.get('count', 0))
                        with col3: st.metric("Wins", stats.get('wins', 0))
                        with col4: st.metric("Avg Return", f"{stats.get('avg_return', 0):+.2f}%")
                else: st.info("No data yet")
                
            with tab2:
                st.write("**Win Rate by Market Regime**")
                by_regime = db.get_stats_by_regime()
                if by_regime:
                    for regime, stats in by_regime.items():
                        if stats is None:
                            continue
                        col1, col2, col3, col4 = st.columns(4)
                        with col1: st.metric(regime, f"{stats.get('win_rate', 0):.1f}%")
                        with col2: st.metric("Trades", stats.get('count', 0))
                        with col3: st.metric("Wins", stats.get('wins', 0))
                        with col4: st.metric("Avg RR", f"{stats.get('avg_rr', 0):.2f}x")
                else: st.info("No data yet")
                
            with tab3:
                st.write("**Win Rate by Sector**")
                by_sector = db.get_stats_by_sector()
                if by_sector:
                    for sector, stats in by_sector.items():
                        if stats is None:
                            continue
                        col1, col2, col3, col4 = st.columns(4)
                        with col1: st.metric(sector, f"{stats.get('win_rate', 0):.1f}%")
                        with col2: st.metric("Trades", stats.get('count', 0))
                        with col3: st.metric("Wins", stats.get('wins', 0))
                        with col4: st.metric("Avg Return", f"{stats.get('avg_return', 0):+.2f}%")
                else: st.info("No data yet")
            db.close()
        except Exception as e:
            st.warning(f"⚠️ Analytics belum siap: {str(e)[:80]}")
    st.session_state.show_analytics = False

if st.session_state.get('show_summary', False):
    st.subheader("📊 Trade Summary")
    TradeDatabase, _ = safe_import_learning_system()
    
    if TradeDatabase is None:
        st.info("📝 **Trade Summary belum tersedia.**\n\nFitur ini memerlukan modul `trade_database.py`. Fitur utama (Scanner + Telegram) sudah berjalan normal.")
    else:
        try:
            db = TradeDatabase()
            total_trades = db.get_trade_count()
            win_rate = db.get_win_rate()
            col1, col2, col3, col4 = st.columns(4)
            with col1: st.metric("Total Trades", total_trades)
            with col2: st.metric("Win Rate", f"{win_rate:.1f}%")
            with col3: st.metric("Avg Confidence", "N/A")
            with col4: st.metric("Status", "Learning..." if total_trades > 0 else "Waiting")
            db.close()
        except Exception as e:
            st.warning(f"⚠️ Trade Summary belum siap: {str(e)[:80]}")
    st.session_state.show_summary = False

# ═══════════════════════════════════════════════════════════════
# MAIN DISPLAY - TRADING SIGNALS WITH INLINE CHECKBOXES
# ══════════════════════════════════════════════════════════════
st.divider()

if not st.session_state.signals:
    st.info("ℹ️ No signals loaded. Click '🚀 Scan ISSI Universe' in the sidebar to start scanning.")
else:
    # Filter signals by confidence & R:R
    filtered_signals = [
        s for s in st.session_state.signals
        if s.confidence >= min_confidence and s.reward_risk_ratio >= min_rr
    ]
    
    # SORT BY CONFIDENCE (Highest to Lowest)
    filtered_signals.sort(key=lambda x: x.confidence, reverse=True)
    
    # Count by state
    armed = len([s for s in filtered_signals if s.state == State.ARMED])
    caution = len([s for s in filtered_signals if s.state == State.CAUTION_HALF_SIZE])
    ready = len([s for s in filtered_signals if s.state == State.READY])
    monitor = len([s for s in filtered_signals if s.state == State.MONITOR])
    
    # Summary metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: st.metric("🟢 ARMED", armed)
    with col2: st.metric("🟡 CAUTION", caution)
    with col3: st.metric("🔵 READY", ready)
    with col4: st.metric("⚪ MONITOR", monitor)
    with col5:
        avg_conf = sum(s.confidence for s in filtered_signals) / len(filtered_signals) if filtered_signals else 0
        st.metric("Avg Confidence", f"{avg_conf:.0f}%")
    
    st.divider()
    
    # ARMED SIGNALS - ACTION ORIENTED
    armed_signals = [s for s in filtered_signals if s.state == State.ARMED]
    if armed_signals:
        st.subheader(f"🔥 EXECUTE TRADES - {len(armed_signals)} ARMED Signals")
        st.caption("✅ Centang kolom 'Pilih' untuk menandai ticker yang akan di-entry")
        
        # Filter options
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            show_filter = st.radio(
                "Filter View:",
                ["All Signals", "Selected", "Added to Journal"],
                horizontal=True
            )
        with filter_col2:
            if st.button("🗑️ Clear All", key="clear_all"):
                st.session_state.selected_tickers = {}
                st.rerun()
        
        # Apply filter
        if show_filter == "Selected":
            display_signals = [s for s in armed_signals if s.ticker in st.session_state.selected_tickers]
        elif show_filter == "Added to Journal":
            display_signals = [s for s in armed_signals if s.ticker in st.session_state.executed_trades]
        else:
            display_signals = armed_signals
        
        if not display_signals:
            st.info("No signals to display with current filter.")
        else:
            st.write(f"**Showing {len(display_signals)} signals (Sorted by Confidence)**")
            
            # Build table with inline checkboxes using data_editor
            table_data = []
            for idx, signal in enumerate(display_signals, 1):
                ticker = signal.ticker
                is_selected = ticker in st.session_state.selected_tickers
                is_executed = ticker in st.session_state.executed_trades
                
                table_data.append({
                    "No": idx,
                    "Pilih": is_selected or is_executed,
                    "Ticker": ticker,
                    "Confidence": f"{signal.confidence:.0f}%",
                    "Entry": int(signal.entry_price),
                    "SL": int(signal.stoploss),
                    "TP": int(signal.takeprofit),
                    "R:R": f"1:{signal.reward_risk_ratio:.1f}",
                    "Engines": f"{signal.positive_engines}/9",
                    "Risk%": f"{signal.risk_pct:.1f}%",
                    "Reward%": f"{signal.reward_pct:.1f}%",
                    "Status": "✅ Journal" if is_executed else ("☑️ Selected" if is_selected else "☐")
                })
            
            df = pd.DataFrame(table_data)
            
            # Configure column types for data_editor
            column_config = {
                "No": st.column_config.NumberColumn("No", width="small"),
                "Pilih": st.column_config.CheckboxColumn(
                    "Pilih",
                    help="Centang untuk memilih ticker ini",
                    width="small"
                ),
                "Ticker": st.column_config.TextColumn("Ticker", width="medium"),
                "Confidence": st.column_config.TextColumn("Confidence", width="small"),
                "Entry": st.column_config.NumberColumn("Entry", width="small"),
                "SL": st.column_config.NumberColumn("SL", width="small"),
                "TP": st.column_config.NumberColumn("TP", width="small"),
                "R:R": st.column_config.TextColumn("R:R", width="small"),
                "Engines": st.column_config.TextColumn("Engines", width="small"),
                "Risk%": st.column_config.TextColumn("Risk%", width="small"),
                "Reward%": st.column_config.TextColumn("Reward%", width="small"),
                "Status": st.column_config.TextColumn("Status", width="medium"),
            }
            
            # Display editable dataframe
            edited_df = st.data_editor(
                df,
                column_config=column_config,
                use_container_width=True,
                hide_index=True,
                disabled=["No", "Ticker", "Confidence", "Entry", "SL", "TP", "R:R", "Engines", "Risk%", "Reward%", "Status"],
                num_rows="fixed"
            )
            
            # Detect changes in checkbox column
            selected_tickers_now = set(edited_df[edited_df["Pilih"] == True]["Ticker"].tolist())
            previous_selected = set(st.session_state.selected_tickers.keys())
            
            # Update session state based on changes
            for ticker in selected_tickers_now:
                if ticker not in previous_selected and ticker not in st.session_state.executed_trades:
                    for s in display_signals:
                        if s.ticker == ticker:
                            st.session_state.selected_tickers[ticker] = s
                            break
            
            for ticker in previous_selected:
                if ticker not in selected_tickers_now and ticker not in st.session_state.executed_trades:
                    if ticker in st.session_state.selected_tickers:
                        del st.session_state.selected_tickers[ticker]
            
            # Action buttons
            st.divider()
            col_btn1, col_btn2, col_btn3 = st.columns(3)
            with col_btn1:
                if st.button("📝 Add Selected to Journal", type="primary", key="add_to_journal"):
                    if st.session_state.selected_tickers:
                        saved_count = 0
                        for ticker, signal in st.session_state.selected_tickers.items():
                            if ticker not in st.session_state.executed_trades:
                                try:
                                    TradeDatabase, _ = safe_import_learning_system()
                                    if TradeDatabase:
                                        db = TradeDatabase()
                                        engines_dict = {name: score.value for name, score in signal.engines.items()}
                                        db.save_trade(
                                            ticker=ticker,
                                            entry_price=signal.entry_price,
                                            stoploss=signal.stoploss,
                                            takeprofit=signal.takeprofit,
                                            confidence=signal.confidence,
                                            regime=signal.regime.value if signal.regime else "UNKNOWN",
                                            sector=signal.engines.get("Sector", None).value if signal.engines.get("Sector") else "UNKNOWN",
                                            engines_dict=engines_dict
                                        )
                                        db.close()
                                        saved_count += 1
                                        
                                        st.session_state.executed_trades[ticker] = {
                                            "timestamp": datetime.now(),
                                            "entry_price": signal.entry_price,
                                            "confidence": signal.confidence,
                                            "signal": signal
                                        }
                                except Exception as e:
                                    st.error(f" Error saving {ticker}: {str(e)[:50]}")
                        
                        st.session_state.selected_tickers = {}
                        st.success(f"✅ {saved_count} trades added to journal!")
                        st.rerun()
                    else:
                        st.warning("⚠️ Please select at least one ticker")
                
            with col_btn2:
                selected_count = len(st.session_state.selected_tickers)
                st.metric("Selected", f"{selected_count} tickers")
            
            with col_btn3:
                executed_count = len(st.session_state.executed_trades)
                st.metric("In Journal", f"{executed_count}")
            
            # Export button
            if executed_count > 0:
                if st.button("📥 Export Journal to CSV", key="export_exec"):
                    executed_df = []
                    for ticker, data in st.session_state.executed_trades.items():
                        signal = data["signal"]
                        executed_df.append({
                            "Ticker": ticker,
                            "Added At": data["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                            "Entry Price": signal.entry_price,
                            "Stop Loss": signal.stoploss,
                            "Take Profit": signal.takeprofit,
                            "Confidence": f"{signal.confidence:.1f}%",
                            "R:R": f"1:{signal.reward_risk_ratio:.1f}",
                            "Risk %": f"{signal.risk_pct:.2f}%",
                            "Reward %": f"{signal.reward_pct:.2f}%",
                            "Engines": f"{signal.positive_engines}/9",
                            "Regime": signal.regime.value if signal.regime else "UNKNOWN"
                        })
                    
                    export_df = pd.DataFrame(executed_df)
                    csv = export_df.to_csv(index=False)
                    st.download_button(
                        label="📥 Download CSV",
                        data=csv,
                        file_name=f"trading_journal_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv"
                    )
    
    # ACTIVE TRADES - SHOW JOURNAL TRADES (MAX 20, AUTO-ROLL)
    if st.session_state.executed_trades:
        st.divider()
        st.subheader(f"📋 Active Trades ({len(st.session_state.executed_trades)} in Journal)")
        st.caption("Menampilkan 20 ticker terakhir. Ticker lama akan di-roll otomatis.")
        
        # Get executed trades sorted by timestamp (newest first)
        executed_list = sorted(
            st.session_state.executed_trades.items(),
            key=lambda x: x[1]["timestamp"],
            reverse=True
        )
        
        # Limit to 20 trades (auto-roll older ones)
        max_trades = 20
        if len(executed_list) > max_trades:
            executed_list = executed_list[:max_trades]
            st.info(f"️ Showing latest {max_trades} trades. Older trades are rolled out.")
        
        # Build table for active trades
        active_trades_data = []
        for idx, (ticker, data) in enumerate(executed_list, 1):
            signal = data["signal"]
            active_trades_data.append({
                "No": idx,
                "Ticker": ticker,
                "Added At": data["timestamp"].strftime("%H:%M"),
                "Entry": f"{signal.entry_price:.0f}",
                "SL": f"{signal.stoploss:.0f}",
                "TP": f"{signal.takeprofit:.0f}",
                "Confidence": f"{signal.confidence:.0f}%",
                "R:R": f"1:{signal.reward_risk_ratio:.1f}",
                "Engines": f"{signal.positive_engines}/9",
                "Status": "🟢 Active"
            })
        
        # Display active trades table
        st.dataframe(
            pd.DataFrame(active_trades_data),
            use_container_width=True,
            hide_index=True
        )
        
        # Action buttons for active trades
        col_act1, col_act2 = st.columns(2)
        with col_act1:
            if st.button("️ Clear All Active Trades", key="clear_active"):
                st.session_state.executed_trades = {}
                st.success("✅ All active trades cleared!")
                st.rerun()
        
        with col_act2:
            if st.button("📥 Export Active Trades", key="export_active"):
                export_df = []
                for ticker, data in executed_list:
                    signal = data["signal"]
                    export_df.append({
                        "Ticker": ticker,
                        "Added At": data["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                        "Entry Price": signal.entry_price,
                        "Stop Loss": signal.stoploss,
                        "Take Profit": signal.takeprofit,
                        "Confidence": f"{signal.confidence:.1f}%",
                        "R:R": f"1:{signal.reward_risk_ratio:.1f}",
                        "Engines": f"{signal.positive_engines}/9"
                    })
                
                export_df = pd.DataFrame(export_df)
                csv = export_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name=f"active_trades_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )
    
    # CAUTION SIGNALS
    caution_signals = [s for s in filtered_signals if s.state == State.CAUTION_HALF_SIZE]
    if caution_signals:
        st.divider()
        st.subheader("🟡 CAUTION Signals - Monitor Carefully")
        caution_df = pd.DataFrame({
            'Ticker': [s.ticker for s in caution_signals],
            'Confidence': [f"{s.confidence:.0f}%" for s in caution_signals],
            'Entry': [f"{s.entry_price:.2f}" for s in caution_signals],
            'TP': [f"{s.takeprofit:.2f}" for s in caution_signals],
            'R:R': [f"{s.reward_risk_ratio:.2f}x" for s in caution_signals],
        })
        st.dataframe(caution_df, use_container_width=True)
    
    # MONITOR SIGNALS
    monitor_signals = [s for s in filtered_signals if s.state == State.MONITOR]
    if monitor_signals:
        st.divider()
        with st.expander(f" MONITOR Signals - {len(monitor_signals)} tickers", expanded=False):
            monitor_df = pd.DataFrame({
                'Ticker': [s.ticker for s in monitor_signals],
                'Confidence': [f"{s.confidence:.0f}%" for s in monitor_signals],
            })
            st.dataframe(monitor_df, use_container_width=True)

# ═══════════════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════════════
st.divider()
st.caption("Dashboard v5.4 | Journal + Learning System | Ready for Paper Trading ✅")