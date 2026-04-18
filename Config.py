import os
from dotenv import load_dotenv

load_dotenv()

# ==================== 硬编码凭证（用户可移入 .env） ====================
TELEGRAM_BOT_TOKEN = "8746379336:AAErpaGPOsbK8woEhFvAXFLfabAKZhZUkeM"
BINANCE_API_KEY = "nSUdUf8uYxACNTL5vUJR0HMBSpddkqoDQgIRw7o6oSwruLbuCY1khvIzROvOlIdh"
BINANCE_SECRET_KEY = "lxBr97DrXpV3oG0qRKCFTCowiqoM51hGlb9G2Gp9lNsYBCmTsBNcXtQAOSOvDhST"

# 环境变量优先（若存在则覆盖）
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", BINANCE_API_KEY)
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", BINANCE_SECRET_KEY)

# ==================== 交易配置 ====================
TESTNET = True                     # 使用币安测试网
DEFAULT_LEVERAGE = 1               # 现货模式，无杠杆
RISK_PER_TRADE = 1.0               # 每笔交易风险 1%
MAX_DAILY_LOSS = 5.0               # 单日最大亏损 5%
MAX_MONTHLY_LOSS = 15.0            # 30日内最大亏损 15%
MAX_PORTFOLIO_DRAWDOWN = 30.0      # 从最高净值回撤 30% 则永久停止

# ATR 参数
ATR_PERIOD = 14
ATR_TIMEFRAME = "1h"
STOP_LOSS_ATR_MULT = 1.5
TAKE_PROFIT_ATR_MULT = 2.5
TRAILING_ACTIVATION_ATR_MULT = 1.0
TRAILING_DISTANCE_ATR_MULT = 1.0

# 策略权重
STRATEGY_WEIGHTS = {
    "rsi": 1.0,
    "ema_trend": 2.0,
    "macd": 2.0,
    "bollinger": 1.0,
    "volume_breakout": 1.5,
    "support_resistance": 1.0,
    "tradfi_correlation": 1.5,
}
STRONG_SIGNAL_THRESHOLD = 4.5      # 净得分 ≥ 4.5 才执行交易

# 监控符号列表（可根据需要扩展）
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]

# 数据文件路径
DATA_DIR = "/app/data"
STATE_FILE = os.path.join(DATA_DIR, "bot_state.json")
TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "trades.csv")
HEALTH_LOG = "bot_health.log"
