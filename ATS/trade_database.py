# -*- coding: utf-8 -*-
"""
Trade Database - Jurnal Trading untuk Learning System
Menyimpan history trade dan hasil (TP/SL) untuk analisa performa engine.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "mlx_trades.db"

class TradeDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.cursor = self.conn.cursor()
        self._create_table()

    def _create_table(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL,
                stoploss REAL,
                takeprofit REAL,
                confidence REAL,
                regime TEXT,
                sector TEXT,
                engines_json TEXT,
                result TEXT DEFAULT 'PENDING', 
                exit_price REAL,
                exit_time TEXT
            )
        ''')
        self.conn.commit()

    def save_trade(self, ticker, entry_price, stoploss, takeprofit, confidence, regime, sector, engines_dict):
        """Simpan trade baru saat diceklis di dashboard"""
        engines_json = json.dumps(engines_dict)
        self.cursor.execute('''
            INSERT INTO trades (ticker, entry_time, entry_price, stoploss, takeprofit, confidence, regime, sector, engines_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            ticker, datetime.now().isoformat(), entry_price, stoploss, takeprofit, 
            confidence, regime, sector, engines_json
        ))
        self.conn.commit()

    def update_result(self, trade_id, result, exit_price=None):
        """Update hasil trade: 'TP' (Win) atau 'SL' (Loss)"""
        self.cursor.execute('''
            UPDATE trades SET result = ?, exit_price = ?, exit_time = ?
            WHERE id = ?
        ''', (result, exit_price, datetime.now().isoformat(), trade_id))
        self.conn.commit()

    def get_all_trades(self):
        self.cursor.execute("SELECT * FROM trades")
        return self.cursor.fetchall()

    def get_trade_count(self):
        self.cursor.execute("SELECT COUNT(*) FROM trades WHERE result != 'PENDING'")
        return self.cursor.fetchone()[0]

    def get_win_rate(self):
        self.cursor.execute("SELECT COUNT(*) FROM trades WHERE result = 'TP'")
        wins = self.cursor.fetchone()[0]
        total = self.get_trade_count()
        return (wins / total * 100) if total > 0 else 0.0

    def get_stats_by_confidence(self):
        """Analisa Win Rate berdasarkan level Confidence"""
        self.cursor.execute("SELECT confidence, result FROM trades WHERE result != 'PENDING'")
        rows = self.cursor.fetchall()
        
        stats = {}
        for conf, result in rows:
            level = "80-90%" if conf >= 80 else "65-80%" if conf >= 65 else "50-65%"
            if level not in stats:
                stats[level] = {'count': 0, 'wins': 0, 'returns': []}
            
            stats[level]['count'] += 1
            if result == 'TP':
                stats[level]['wins'] += 1
                stats[level]['returns'].append(5.0) # Asumsi profit 5% untuk TP
            else:
                stats[level]['returns'].append(-2.0) # Asumsi loss 2% untuk SL

        for level, data in stats.items():
            data['win_rate'] = (data['wins'] / data['count'] * 100) if data['count'] > 0 else 0
            data['avg_return'] = sum(data['returns']) / len(data['returns']) if data['returns'] else 0
            
        return stats

    def get_stats_by_regime(self):
        """Analisa Win Rate berdasarkan Regime (Bullish/Sideways/Bearish)"""
        self.cursor.execute("SELECT regime, result FROM trades WHERE result != 'PENDING'")
        rows = self.cursor.fetchall()
        
        stats = {}
        for regime, result in rows:
            if regime not in stats:
                stats[regime] = {'count': 0, 'wins': 0}
            stats[regime]['count'] += 1
            if result == 'TP':
                stats[regime]['wins'] += 1
                
        for regime, data in stats.items():
            data['win_rate'] = (data['wins'] / data['count'] * 100) if data['count'] > 0 else 0
            
        return stats

    def get_stats_by_sector(self):
        """Analisa Win Rate berdasarkan Sektor"""
        self.cursor.execute("SELECT sector, result FROM trades WHERE result != 'PENDING'")
        rows = self.cursor.fetchall()
        
        stats = {}
        for sector, result in rows:
            if sector not in stats:
                stats[sector] = {'count': 0, 'wins': 0}
            stats[sector]['count'] += 1
            if result == 'TP':
                stats[sector]['wins'] += 1
                
        for sector, data in stats.items():
            data['win_rate'] = (data['wins'] / data['count'] * 100) if data['count'] > 0 else 0
            
        return stats

    def close(self):
        self.conn.close()