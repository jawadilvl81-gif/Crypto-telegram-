import asyncio
import logging
import ccxt
import pandas as pd
import yfinance as yf
from utils import retry_async
import config

logger = logging.getLogger(__name__)

class DataFetcher:
    def __init__(self):
        self.exchange = ccxt.binance({
            'apiKey': config.BINANCE_API_KEY,
            'secret': config.BINANCE_SECRET_KEY,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'testnet': config.TESTNET,
            },
        })
        if config.TESTNET:
            self.exchange.set_sandbox_mode(True)
            self.exchange.urls['api'] = self.exchange.urls['test']
            logger.info("✅ 使用币安测试网")

    @retry_async(max_retries=3, delay=2)
    async def fetch_ohlcv(self, symbol: str, timeframe='1h', limit=100):
        """异步获取 OHLCV 数据，返回 DataFrame"""
        loop = asyncio.get_event_loop()
        ohlcv = await loop.run_in_executor(
            None, self.exchange.fetch_ohlcv, symbol, timeframe, None, limit
        )
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df

    @retry_async(max_retries=2, delay=1)
    async def fetch_ticker(self, symbol: str):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.exchange.fetch_ticker, symbol)

    def fetch_gold_sp500(self):
        """使用 yfinance 获取黄金 (GC=F) 和标普500 (^GSPC) 数据"""
        try:
            gold = yf.download("GC=F", period="5d", interval="1h", progress=False)
            sp500 = yf.download("^GSPC", period="5d", interval="1h", progress=False)
            # 只返回最近几根K线用于计算变化
            return gold, sp500
        except Exception as e:
            logger.error(f"获取 TradFi 数据失败: {e}")
            return None, None
