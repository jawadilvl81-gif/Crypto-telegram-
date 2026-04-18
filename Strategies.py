import pandas as pd
import numpy as np
import ta
from data_fetcher import DataFetcher
import config

class StrategyEngine:
    def __init__(self):
        self.fetcher = DataFetcher()
        self.weights = config.STRATEGY_WEIGHTS

    async def calculate_signals(self, symbol: str):
        """返回每个策略的买入/卖出得分（正为买入倾向，负为卖出倾向）"""
        df = await self.fetcher.fetch_ohlcv(symbol, timeframe=config.ATR_TIMEFRAME, limit=100)
        if df.empty:
            return {}

        signals = {}
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']

        # 1. RSI (14)
        rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
        prev_rsi = rsi.shift(1)
        rsi_buy = (rsi.iloc[-1] < 30) and (prev_rsi.iloc[-1] < rsi.iloc[-1])
        rsi_sell = (rsi.iloc[-1] > 70) and (prev_rsi.iloc[-1] > rsi.iloc[-1])
        signals['rsi'] = 1 if rsi_buy else (-1 if rsi_sell else 0)

        # 2. EMA 趋势 (20/50)
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

        # 4. Bollinger Bands (20,2)
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        lower_band = bb.bollinger_lband()
        price_below_lower = close.iloc[-1] < lower_band.iloc[-1]
        price_above_prev_low = close.iloc[-1] > low.iloc[-2]
        signals['bollinger'] = 1 if (price_below_lower and price_above_prev_low) else 0

        # 5. Volume Breakout
        avg_vol_20 = volume.rolling(20).mean()
        vol_break = volume.iloc[-1] > (avg_vol_20.iloc[-1] * 1.5)
        bullish_candle = close.iloc[-1] > open.iloc[-1]
        signals['volume_breakout'] = 1 if (vol_break and bullish_candle) else 0

        # 6. Support/Resistance (简单枢轴点)
        pivot_high = high.rolling(5, center=True).max()
        pivot_low = low.rolling(5, center=True).min()
        near_resistance = (close.iloc[-1] >= pivot_high.iloc[-1] * 0.99)
        near_support = (close.iloc[-1] <= pivot_low.iloc[-1] * 1.01)
        signals['support_resistance'] = 1 if near_support else (-1 if near_resistance else 0)

        # 7. TradFi Correlation (黄金、标普)
        gold_df, sp500_df = self.fetcher.fetch_gold_sp500()
        if gold_df is not None and sp500_df is not None:
            gold_change = gold_df['Close'].pct_change(4).iloc[-1] * 100  # 4h 变化百分比
            sp500_change = sp500_df['Close'].pct_change(4).iloc[-1] * 100
            btc_change = close.pct_change(4).iloc[-1] * 100

            # 简单相关性判断（实际可计算滚动相关系数，这里简化）
            gold_signal = 1 if gold_change > 1.2 and btc_change > 0 else 0
            sp500_signal = 1 if sp500_change > 1.0 and btc_change > 0 else 0
            signals['tradfi_correlation'] = 1 if (gold_signal or sp500_signal) else 0
        else:
            signals['tradfi_correlation'] = 0

        # 计算加权总分
        weighted_score = sum(self.weights.get(k, 0) * v for k, v in signals.items())
        return {
            'signals': signals,
            'weighted_score': weighted_score,
            'action': 'BUY' if weighted_score >= config.STRONG_SIGNAL_THRESHOLD else (
                'SELL' if weighted_score <= -config.STRONG_SIGNAL_THRESHOLD else 'WAIT'
            )
      }
