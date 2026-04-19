#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import functools
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List

import ccxt
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import ta
import yfinance as yf
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# 🔥 Keep-alive import for 24/7 running
from keep_alive import keep_alive

# ==================== CONFIGURATION ====================
load_dotenv()

# API credentials (use environment variables - SECURE!)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

# Fallback for testing only (remove in production!)
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = "8746379336:AAErpaGPOsbK8woEhFvAXFLfabAKZhZUkeM"
if not BINANCE_API_KEY:
    BINANCE_API_KEY = "nSUdUf8uYxACNTL5vUJR0HMBSpddkqoDQgIRw7o6oSwruLbuCY1khvIzROvOlIdh"
if not BINANCE_SECRET_KEY:
    BINANCE_SECRET_KEY = "lxBr97DrXpV3oG0qRKCFTCowiqoM51hGlb9G2Gp9lNsYBCmTsBNcXtQAOSOvDhST"

TESTNET = True
DEFAULT_LEVERAGE = 1
RISK_PER_TRADE = 1.0               # % of balance per trade
MAX_DAILY_LOSS = 5.0               # %MAX_MONTHLY_LOSS = 15.0            # %
MAX_PORTFOLIO_DRAWDOWN = 30.0      # %

ATR_PERIOD = 14
ATR_TIMEFRAME = "1h"
STOP_LOSS_ATR_MULT = 1.5
TAKE_PROFIT_ATR_MULT = 2.5
TRAILING_ACTIVATION_ATR_MULT = 1.0
TRAILING_DISTANCE_ATR_MULT = 1.0

STRATEGY_WEIGHTS = {
    "rsi": 1.0,
    "ema_trend": 2.0,
    "macd": 2.0,
    "bollinger": 1.0,
    "volume_breakout": 1.5,
    "support_resistance": 1.0,
    "tradfi_correlation": 1.5,
}
STRONG_SIGNAL_THRESHOLD = 4.5
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "bot_state.json")
TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "trades.csv")
ALERTS_FILE = os.path.join(DATA_DIR, "price_alerts.json")
HEALTH_LOG = "bot_health.log"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler(HEALTH_LOG), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== UTILS ====================
def retry_async(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            _delay = delay
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"Retry {func.__name__} ({attempt+1}/{max_retries}): {e}")
                    await asyncio.sleep(_delay)                    _delay *= backoff
            return None
        return wrapper
    return decorator

# ==================== DATA FETCHER ====================
class DataFetcher:
    def __init__(self):
        self.exchange = ccxt.binance({
            'apiKey': BINANCE_API_KEY,
            'secret': BINANCE_SECRET_KEY,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        })
        if TESTNET:
            self.exchange.set_sandbox_mode(True)
            self.exchange.urls['api'] = self.exchange.urls['test']
            logger.info("✅ Using Binance Testnet")

    @retry_async(max_retries=3, delay=2)
    async def fetch_ohlcv(self, symbol: str, timeframe='1h', limit=100) -> pd.DataFrame:
        loop = asyncio.get_event_loop()
        ohlcv = await loop.run_in_executor(
            None, self.exchange.fetch_ohlcv, symbol, timeframe, None, limit
        )
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df

    @retry_async(max_retries=2, delay=1)
    async def fetch_ticker(self, symbol: str) -> Dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.exchange.fetch_ticker, symbol)

    def fetch_gold_sp500(self):
        try:
            gold = yf.download("GC=F", period="5d", interval="1h", progress=False)
            sp500 = yf.download("^GSPC", period="5d", interval="1h", progress=False)
            return gold, sp500
        except Exception as e:
            logger.error(f"TradFi fetch failed: {e}")
            return None, None

# ==================== STRATEGY ENGINE ====================
class StrategyEngine:
    def __init__(self):
        self.fetcher = DataFetcher()
        self.weights = STRATEGY_WEIGHTS
    async def calculate_signals(self, symbol: str) -> Dict:
        df = await self.fetcher.fetch_ohlcv(symbol, timeframe=ATR_TIMEFRAME, limit=100)
        if df.empty:
            return {'signals': {}, 'weighted_score': 0, 'action': 'WAIT'}

        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']
        open_ = df['open']
        signals = {}

        # 1. RSI
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        prev_rsi = rsi.shift(1)
        rsi_buy = (rsi.iloc[-1] < 30) and (prev_rsi.iloc[-1] < rsi.iloc[-1])
        rsi_sell = (rsi.iloc[-1] > 70) and (prev_rsi.iloc[-1] > rsi.iloc[-1])
        signals['rsi'] = 1 if rsi_buy else (-1 if rsi_sell else 0)

        # 2. EMA Trend
        ema20 = ta.trend.EMAIndicator(close, window=20).ema_indicator()
        ema50 = ta.trend.EMAIndicator(close, window=50).ema_indicator()
        price_above_ema20 = close.iloc[-1] > ema20.iloc[-1]
        ema20_above_ema50 = ema20.iloc[-1] > ema50.iloc[-1]
        signals['ema_trend'] = 1 if (price_above_ema20 and ema20_above_ema50) else -1

        # 3. MACD
        macd = ta.trend.MACD(close)
        macd_line = macd.macd()
        signal_line = macd.macd_signal()
        hist = macd.macd_diff()
        macd_buy = (macd_line.iloc[-1] > signal_line.iloc[-1]) and (hist.iloc[-1] > 0)
        macd_sell = (macd_line.iloc[-1] < signal_line.iloc[-1]) and (hist.iloc[-1] < 0)
        signals['macd'] = 1 if macd_buy else (-1 if macd_sell else 0)

        # 4. Bollinger Bands
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        lower_band = bb.bollinger_lband()
        price_below_lower = close.iloc[-1] < lower_band.iloc[-1]
        price_above_prev_low = close.iloc[-1] > low.iloc[-2]
        signals['bollinger'] = 1 if (price_below_lower and price_above_prev_low) else 0

        # 5. Volume Breakout
        avg_vol_20 = volume.rolling(20).mean()
        vol_break = volume.iloc[-1] > (avg_vol_20.iloc[-1] * 1.5)
        bullish_candle = close.iloc[-1] > open_.iloc[-1]
        signals['volume_breakout'] = 1 if (vol_break and bullish_candle) else 0

        # 6. Support/Resistance
        pivot_high = high.rolling(5, center=True).max()        pivot_low = low.rolling(5, center=True).min()
        near_resistance = (close.iloc[-1] >= pivot_high.iloc[-1] * 0.99)
        near_support = (close.iloc[-1] <= pivot_low.iloc[-1] * 1.01)
        signals['support_resistance'] = 1 if near_support else (-1 if near_resistance else 0)

        # 7. TradFi Correlation
        gold_df, sp500_df = self.fetcher.fetch_gold_sp500()
        if gold_df is not None and sp500_df is not None:
            gold_change = gold_df['Close'].pct_change(4).iloc[-1] * 100
            sp500_change = sp500_df['Close'].pct_change(4).iloc[-1] * 100
            btc_change = close.pct_change(4).iloc[-1] * 100
            gold_signal = 1 if gold_change > 1.2 and btc_change > 0 else 0
            sp500_signal = 1 if sp500_change > 1.0 and btc_change > 0 else 0
            signals['tradfi_correlation'] = 1 if (gold_signal or sp500_signal) else 0
        else:
            signals['tradfi_correlation'] = 0

        weighted_score = sum(self.weights.get(k, 0) * v for k, v in signals.items())
        action = 'BUY' if weighted_score >= STRONG_SIGNAL_THRESHOLD else ('SELL' if weighted_score <= -STRONG_SIGNAL_THRESHOLD else 'WAIT')
        return {'signals': signals, 'weighted_score': weighted_score, 'action': action}

# ==================== RISK MANAGER ====================
class RiskManager:
    def __init__(self):
        self.fetcher = DataFetcher()
        self.state = self.load_state()
        self.daily_pnl = 0.0
        self.monthly_pnl = 0.0
        self.ath_balance = self.state.get('ath_balance', 0.0)
        self.trading_halted = self.state.get('trading_halted', False)
        self.halt_reason = self.state.get('halt_reason', '')

    def load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_state(self):
        self.state.update({
            'ath_balance': self.ath_balance,
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
            'last_update': datetime.now(timezone.utc).isoformat()
        })
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)

    async def get_account_balance(self) -> float:
        balance = await self.fetcher.exchange.fetch_balance()        return balance['total'].get('USDT', 0.0)

    async def calculate_position_size(self, symbol: str, entry_price: float, atr: float) -> float:
        balance = await self.get_account_balance()
        risk_amount = balance * (RISK_PER_TRADE / 100)
        stop_distance = atr * STOP_LOSS_ATR_MULT
        if stop_distance <= 0:
            return 0.0
        return risk_amount / stop_distance

    async def update_pnl(self):
        if not os.path.exists(TRADE_HISTORY_FILE):
            return
        df = pd.read_csv(TRADE_HISTORY_FILE)
        if df.empty:
            return
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(days=1)
        month_ago = now - timedelta(days=30)
        daily_df = df[df['timestamp'] >= day_ago]
        monthly_df = df[df['timestamp'] >= month_ago]
        self.daily_pnl = daily_df['pnl'].sum() if 'pnl' in daily_df else 0.0
        self.monthly_pnl = monthly_df['pnl'].sum() if 'pnl' in monthly_df else 0.0

        balance = await self.get_account_balance()
        if balance > self.ath_balance:
            self.ath_balance = balance

        drawdown_pct = ((self.ath_balance - balance) / self.ath_balance * 100) if self.ath_balance > 0 else 0

        if not self.trading_halted:
            if self.daily_pnl < -MAX_DAILY_LOSS * balance / 100:
                self.trading_halted = True
                self.halt_reason = f"Daily loss > {MAX_DAILY_LOSS}%"
            elif self.monthly_pnl < -MAX_MONTHLY_LOSS * balance / 100:
                self.trading_halted = True
                self.halt_reason = f"Monthly loss > {MAX_MONTHLY_LOSS}%"
            elif drawdown_pct >= MAX_PORTFOLIO_DRAWDOWN:
                self.trading_halted = True
                self.halt_reason = "PERMANENT HALT: Drawdown > 30%"
        self.save_state()

    def is_trading_allowed(self) -> bool:
        return not self.trading_halted

    async def get_risk_report(self) -> str:
        await self.update_pnl()
        balance = await self.get_account_balance()
        drawdown_pct = ((self.ath_balance - balance) / self.ath_balance * 100) if self.ath_balance > 0 else 0        return (
            f"📊 *Risk Report*\n"
            f"Balance: {balance:.2f} USDT\n"
            f"ATH: {self.ath_balance:.2f} USDT\n"
            f"Drawdown: {drawdown_pct:.2f}%\n"
            f"Daily PnL: {self.daily_pnl:.2f} USDT ({self.daily_pnl/balance*100:.2f}%)\n"
            f"30d PnL: {self.monthly_pnl:.2f} USDT ({self.monthly_pnl/balance*100:.2f}%)\n"
            f"Auto Trading: {'✅ Allowed' if self.is_trading_allowed() else '⛔ Halted'}"
            + (f"\nReason: {self.halt_reason}" if not self.is_trading_allowed() else "")
        )

# ==================== CHART GENERATOR ====================
class ChartGenerator:
    def __init__(self):
        self.fetcher = DataFetcher()

    async def generate_signal_chart(self, symbol: str) -> Optional[io.BytesIO]:
        df = await self.fetcher.fetch_ohlcv(symbol, timeframe=ATR_TIMEFRAME, limit=72)
        if df.empty:
            return None

        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=ATR_PERIOD).mean()
        current_atr = atr.iloc[-1]
        last_close = close.iloc[-1]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]})
        fig.suptitle(f"{symbol} - ATR Bands Signal", fontsize=14)

        ax1.plot(df.index, close, label='Close', color='black')
        ax1.fill_between(df.index, last_close - current_atr, last_close + current_atr,
                         alpha=0.2, color='blue', label=f'ATR ({current_atr:.4f})')
        ax1.axhline(y=last_close, color='gray', linestyle='--', linewidth=0.8)
        ax1.legend(loc='upper left')
        ax1.set_ylabel('Price (USDT)')
        ax1.grid(True, alpha=0.3)

        ax2.bar(df.index, df['volume'], color='green', alpha=0.6, width=0.02)
        ax2.set_ylabel('Volume')
        ax2.grid(True, alpha=0.3)

        for ax in [ax1, ax2]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close(fig)
        return buf

# ==================== TELEGRAM BOT ====================
class TradingBot:
    def __init__(self):
        self.engine = StrategyEngine()
        self.risk = RiskManager()
        self.fetcher = DataFetcher()
        self.chart = ChartGenerator()
        self.auto_trade_active = False
        self.monitored_symbols = SYMBOLS
        self.active_trades: Dict[str, Dict] = {}
        self.max_positions = 5
        self.trailing_enabled = False
        self.price_alerts = self.load_alerts()
        self.start_time = datetime.now(timezone.utc)

    # ---------- Alert Helpers ----------
    def load_alerts(self) -> List[Dict]:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, 'r') as f:
                return json.load(f)
        return []

    def save_alerts(self):
        with open(ALERTS_FILE, 'w') as f:
            json.dump(self.price_alerts, f, indent=2)

    # ---------- Trade Recording ----------
    def _record_trade(self, symbol: str, side: str, price: float, size: float, trade_type: str, pnl: float = 0.0):
        """Record trade to CSV history file"""
        trade_record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'symbol': symbol,
            'side': side,
            'price': price,
            'size': size,
            'type': trade_type,
            'pnl': pnl
        }
        
        file_exists = os.path.exists(TRADE_HISTORY_FILE)
        df = pd.DataFrame([trade_record])
        df.to_csv(TRADE_HISTORY_FILE, mode='a', header=not file_exists, index=False)        logger.info(f"📝 Trade recorded: {trade_record}")

    # ---------- Base Commands ----------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🤖 *PRO MAX Trading Bot v3.1*\nType /help for commands.", parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📋 *Available Commands*\n\n"
            "📊 *Market Data*\n"
            "/signal <symbol> - Get signal & chart\n"
            "/ticker <symbol> - Current price & 24h change\n"
            "/orderbook <symbol> - Top 5 bids/asks\n"
            "/volatility <symbol> - ATR & volatility\n\n"
            "💼 *Trading*\n"
            "/trade <symbol> <buy/sell> - Manual market order\n"
            "/limitorder <symbol> <side> <price> <amount> - Place limit order\n"
            "/close <symbol> - Close position\n"
            "/closeprofit - Close all profitable\n"
            "/closeloss - Close all losing\n"
            "/cancelorder <id> - Cancel limit order\n"
            "/openorders - Show open orders\n\n"
            "📈 *Portfolio*\n"
            "/balance - USDT balance\n"
            "/live - Active positions (simple)\n"
            "/positions - Detailed PnL per position\n"
            "/history - Last 10 trades\n"
            "/stats - Account statistics\n"
            "/daily - Today's PnL\n"
            "/monthly - This month's PnL\n"
            "/yearly - YTD PnL\n"
            "/exporttrades - CSV export\n\n"
            "⚙️ *Automation*\n"
            "/startauto - Enable auto trading\n"
            "/stopauto - Disable auto trading\n"
            "/enabletrailing - Activate trailing stop\n"
            "/disabletrailing - Deactivate trailing\n"
            "/setrisk <percent> - Update risk per trade\n"
            "/maxpositions <num> - Set max concurrent trades\n\n"
            "🔔 *Alerts*\n"
            "/pricealert <symbol> <price> - Set alert\n"
            "/alerts - List alerts\n"
            "/deletealert <id> - Remove alert\n\n"
            "📐 *Analysis*\n"
            "/strategy - Show strategy weights\n"
            "/backtest <symbol> <days> - Quick backtest\n"
            "/risk - Circuit breaker status\n\n"
            "🛠 *System*\n"
            "/status - Bot uptime & health\n"
            "/restart - Restart bot\n"            "/cancel - Cancel all open orders"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            balance = await self.risk.get_account_balance()
            await update.message.reply_text(f"💰 Balance: {balance:.2f} USDT")
        except Exception as e:
            await update.message.reply_text(f"❌ Error fetching balance: {str(e)}")

    async def signal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            symbol = context.args[0].upper() if context.args else "BTC/USDT"
            await update.message.reply_text(f"🔍 Analyzing {symbol} ...")
            signals = await self.engine.calculate_signals(symbol)
            score = signals['weighted_score']
            action = signals['action']
            details = "\n".join([f"{k}: {v}" for k, v in signals['signals'].items()])
            text = (
                f"📊 *{symbol} Signal*\n"
                f"Weighted Score: {score:.2f}\n"
                f"Action: {action}\n\n"
                f"*Strategy Details*:\n{details}"
            )
            chart_buf = await self.chart.generate_signal_chart(symbol)
            if chart_buf:
                await update.message.reply_photo(photo=chart_buf, caption=text, parse_mode='Markdown')
            else:
                await update.message.reply_text(text, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

    async def trade_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self.risk.is_trading_allowed():
                await update.message.reply_text("⛔ Trading halted: " + self.risk.halt_reason)
                return
            if len(context.args) < 2:
                await up
