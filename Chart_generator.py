import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
import io
from data_fetcher import DataFetcher
import config

class ChartGenerator:
    def __init__(self):
        self.fetcher = DataFetcher()

    async def generate_signal_chart(self, symbol: str):
        df = await self.fetcher.fetch_ohlcv(symbol, timeframe=config.ATR_TIMEFRAME, limit=72)
        if df.empty:
            return None

        # 计算 ATR
        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=config.ATR_PERIOD).mean()
        current_atr = atr.iloc[-1]
        last_close = close.iloc[-1]

        # 创建图表
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]})
        fig.suptitle(f"{symbol} - ATR 通道信号", fontsize=14)

        # 主图：K线 + ATR 通道
        ax1.plot(df.index, close, label='Close', color='black')
        ax1.fill_between(df.index, last_close - current_atr, last_close + current_atr,
                         alpha=0.2, color='blue', label=f'ATR ({current_atr:.4f})')
        ax1.axhline(y=last_close, color='gray', linestyle='--', linewidth=0.8)
        ax1.legend(loc='upper left')
        ax1.set_ylabel('Price (USDT)')
        ax1.grid(True, alpha=0.3)

        # 成交量图
        ax2.bar(df.index, df['volume'], color='green', alpha=0.6, width=0.02)
        ax2.set_ylabel('Volume')
        ax2.grid(True, alpha=0.3)

        # 格式化x轴
        for ax in [ax1, ax2]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close(fig)
        return buf
