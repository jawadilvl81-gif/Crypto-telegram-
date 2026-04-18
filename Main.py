import asyncio
import logging
import os
from datetime import datetime
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import config
from strategies import StrategyEngine
from risk_manager import RiskManager
from data_fetcher import DataFetcher
from chart_generator import ChartGenerator

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(config.HEALTH_LOG),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self):
        self.engine = StrategyEngine()
        self.risk = RiskManager()
        self.fetcher = DataFetcher()
        self.chart = ChartGenerator()
        self.auto_trade_active = False
        self.monitored_symbols = config.SYMBOLS
        self.active_trades = {}  # symbol -> trade_info (用于跟踪止损)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *机构级加密货币交易机器人已启动*\n"
            "使用 /help 查看所有命令。",
            parse_mode='Markdown'
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📋 *可用命令*\n"
            "/start - 启动机器人\n"
            "/help - 显示帮助\n"
            "/balance - 查询账户余额\n"
            "/signal <symbol> - 获取交易信号与图表 (例如 /signal BTC/USDT)\n"
            "/trade <symbol> <方向> - 手动开仓 (例如 /trade BTC/USDT buy)\n"
            "/manualtrade - 交互式手动交易\n"
            "/live - 查看活跃持仓\n"
            "/history - 查看交易历史\n"
            "/startauto - 启动自动交易\n"
            "/stopauto - 停止自动交易\n"
            "/risk - 查看风险指标\n"
            "/backtest - 回测 (开发中)\n"
            "/report - 生成报告\n"
            "/alert - 设置价格提醒\n"
            "/cancel - 取消所有未成交订单"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        balance = await self.risk.get_account_balance()
        await update.message.reply_text(f"💰 账户余额: {balance:.2f} USDT")

    async def signal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        symbol = context.args[0] if context.args else "BTC/USDT"
        await update.message.reply_text(f"🔍 正在分析 {symbol} ...")
        signals = await self.engine.calculate_signals(symbol)
        score = signals['weighted_score']
        action = signals['action']
        details = "\n".join([f"{k}: {v}" for k,v in signals['signals'].items()])
        text = (
            f"📊 *{symbol} 信号*\n"
            f"加权得分: {score:.2f}\n"
            f"建议操作: {action}\n\n"
            f"*策略明细*:\n{details}"
        )
        # 生成图表
        chart_buf = await self.chart.generate_signal_chart(symbol)
        if chart_buf:
            await update.message.reply_photo(photo=chart_buf, caption=text, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, parse_mode='Markdown')

    async def trade_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.risk.is_trading_allowed():
            await update.message.reply_text("⛔ 交易已被熔断暂停。原因: " + self.risk.halt_reason)
            return
        if len(context.args) < 2:
            await update.message.reply_text("用法: /trade BTC/USDT buy")
            return
        symbol = context.args[0].upper()
        side = context.args[1].lower()
        if side not in ['buy', 'sell']:
            await update.message.reply_text("方向只能是 buy 或 sell")
            return
        # 获取 ATR 与价格
        df = await self.fetcher.fetch_ohlcv(symbol, config.ATR_TIMEFRAME, limit=20)
        atr = df['high'].sub(df['low']).rolling(config.ATR_PERIOD).mean().iloc[-1]
        ticker = await self.fetcher.fetch_ticker(symbol)
        price = ticker['last']
        size = await self.risk.calculate_position_size(symbol, price, atr)
        if size <= 0:
            await update.message.reply_text("❌ 仓位计算错误，无法交易。")
            return
        # 执行市价单（测试网模拟）
        order = await self.fetcher.exchange.create_market_order(symbol, side, size)
        logger.info(f"手动开仓: {order}")
        # 记录交易
        self._record_trade(symbol, side, price, size, "manual")
        # 设置止损止盈跟踪
        self.active_trades[symbol] = {
            'entry': price,
            'side': side,
            'size': size,
            'atr': atr,
            'stop_loss': price - atr * config.STOP_LOSS_ATR_MULT if side=='buy' else price + atr * config.STOP_LOSS_ATR_MULT,
            'take_profit': price + atr * config.TAKE_PROFIT_ATR_MULT if side=='buy' else price - atr * config.TAKE_PROFIT_ATR_MULT,
            'trailing_activated': False,
            'highest_price': price if side=='buy' else 0,
            'lowest_price': price if side=='sell' else 1e9,
        }
        await update.message.reply_text(
            f"✅ 已开仓 {side.upper()} {size:.6f} {symbol.split('/')[0]} @ {price:.4f}\n"
            f"止损: {self.active_trades[symbol]['stop_loss']:.4f} | 止盈: {self.active_trades[symbol]['take_profit']:.4f}"
        )

    def _record_trade(self, symbol, side, price, size, tag):
        trade = {
            'timestamp': datetime.utcnow().isoformat(),
            'symbol': symbol,
            'side': side,
            'price': price,
            'size': size,
            'pnl': 0.0,  # 未平仓暂为0
            'tag': tag
        }
        df = pd.DataFrame([trade])
        if os.path.exists(config.TRADE_HISTORY_FILE):
            df.to_csv(config.TRADE_HISTORY_FILE, mode='a', header=False, index=False)
        else:
            df.to_csv(config.TRADE_HISTORY_FILE, index=False)

    async def manualtrade_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 简化：提示使用 /trade 命令
        await update.message.reply_text("请使用 /trade <symbol> <buy|sell> 手动开仓。")

    async def live_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.active_trades:
            await update.message.reply_text("📭 当前无活跃持仓。")
            return
        msg = "📌 *活跃持仓*\n"
        for sym, t in self.active_trades.items():
            msg += f"{sym}: {t['side'].upper()} {t['size']:.4f} @ {t['entry']:.4f}\n"
        await update.message.reply_text(msg, parse_mode='Markdown')

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not os.path.exists(config.TRADE_HISTORY_FILE):
            await update.message.reply_text("无历史记录。")
            return
        df = pd.read_csv(config.TRADE_HISTORY_FILE).tail(10)
        text = "📜 *最近10笔交易*\n" + df.to_string(index=False)
        await update.message.reply_text(f"```\n{text}\n```", parse_mode='Markdown')

    async def startauto_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.auto_trade_active = True
        await update.message.reply_text("🟢 自动交易已启动。")
        asyncio.create_task(self._auto_trading_loop(context))

    async def stopauto_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.auto_trade_active = False
        await update.message.reply_text("🔴 自动交易已停止。")

    async def _auto_trading_loop(self, context):
        while self.auto_trade_active and self.risk.is_trading_allowed():
            for symbol in self.monitored_symbols:
                if symbol in self.active_trades:
                    continue  # 已有持仓，不重复开仓
                signals = await self.engine.calculate_signals(symbol)
                action = signals['action']
                if action in ('BUY', 'SELL'):
                    # 执行自动开仓
                    await self._execute_auto_trade(symbol, action.lower())
            await asyncio.sleep(300)  # 每5分钟扫描一次

    async def _execute_auto_trade(self, symbol, side):
        # 与手动开仓逻辑相同
        df = await self.fetcher.fetch_ohlcv(symbol, config.ATR_TIMEFRAME, limit=20)
        atr = df['high'].sub(df['low']).rolling(config.ATR_PERIOD).mean().iloc[-1]
        ticker = await self.fetcher.fetch_ticker(symbol)
        price = ticker['last']
        size = await self.risk.calculate_position_size(symbol, price, atr)
        if size <= 0:
            return
        order = await self.fetcher.exchange.create_market_order(symbol, side, size)
        logger.info(f"自动开仓: {order}")
        self._record_trade(symbol, side, price, size, "auto")
        self.active_trades[symbol] = {
            'entry': price, 'side': side, 'size': size, 'atr': atr,
            'stop_loss': price - atr * config.STOP_LOSS_ATR_MULT if side=='buy' else price + atr * config.STOP_LOSS_ATR_MULT,
            'take_profit': price + atr * config.TAKE_PROFIT_ATR_MULT if side=='buy' else price - atr * config.TAKE_PROFIT_ATR_MULT,
            'trailing_activated': False,
            'highest_price': price if side=='buy' else 0,
            'lowest_price': price if side=='sell' else 1e9,
        }

    async def risk_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        report = await self.risk.get_risk_report()
        await update.message.reply_text(report, parse_mode='Markdown')

    # 其他命令占位
    async def backtest_command(self, update, context):
        await update.message.reply_text("回测功能开发中...")

    async def report_command(self, update, context):
        await update.message.reply_text("报告功能开发中...")

    async def alert_command(self, update, context):
        await update.message.reply_text("价格提醒功能开发中...")

    async def cancel_command(self, update, context):
        orders = await self.fetcher.exchange.cancel_all_orders()
        await update.message.reply_text(f"已取消 {len(orders)} 个未成交订单。")

def main():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    bot = TradingBot()

    # 注册命令
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("balance", bot.balance_command))
    app.add_handler(CommandHandler("signal", bot.signal_command))
    app.add_handler(CommandHandler("trade", bot.trade_command))
    app.add_handler(CommandHandler("manualtrade", bot.manualtrade_command))
    app.add_handler(CommandHandler("live", bot.live_command))
    app.add_handler(CommandHandler("history", bot.history_command))
    app.add_handler(CommandHandler("startauto", bot.startauto_command))
    app.add_handler(CommandHandler("stopauto", bot.stopauto_command))
    app.add_handler(CommandHandler("risk", bot.risk_command))
    app.add_handler(CommandHandler("backtest", bot.backtest_command))
    app.add_handler(CommandHandler("report", bot.report_command))
    app.add_handler(CommandHandler("alert", bot.alert_command))
    app.add_handler(CommandHandler("cancel", bot.cancel_command))

    logger.info("🤖 Bot 启动中...")
    app.run_polling()

if __name__ == "__main__":
    main()
