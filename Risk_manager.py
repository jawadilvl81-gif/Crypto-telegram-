import json
import os
import logging
import pandas as pd
from datetime import datetime, timedelta
import config
from data_fetcher import DataFetcher

logger = logging.getLogger(__name__)

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
        if os.path.exists(config.STATE_FILE):
            with open(config.STATE_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_state(self):
        self.state.update({
            'ath_balance': self.ath_balance,
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
            'last_update': datetime.utcnow().isoformat()
        })
        with open(config.STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)

    async def get_account_balance(self):
        """获取总余额（USDT）"""
        balance = await self.fetcher.exchange.fetch_balance()
        return balance['total'].get('USDT', 0.0)

    async def calculate_position_size(self, symbol: str, entry_price: float, atr: float):
        """根据风险百分比和 ATR 计算仓位大小"""
        balance = await self.get_account_balance()
        risk_amount = balance * (config.RISK_PER_TRADE / 100)
        stop_distance = atr * config.STOP_LOSS_ATR_MULT
        if stop_distance <= 0:
            return 0.0
        position_size = risk_amount / stop_distance
        # 转换为数量（现货按 USDT 计价）
        return position_size

    async def update_pnl(self):
        """更新日/月盈亏并检查熔断"""
        # 简化：读取历史交易记录计算净盈亏
        if not os.path.exists(config.TRADE_HISTORY_FILE):
            return
        df = pd.read_csv(config.TRADE_HISTORY_FILE)
        if df.empty:
            return
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        now = datetime.utcnow()
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

        # 检查熔断条件
        if not self.trading_halted:
            if self.daily_pnl < -config.MAX_DAILY_LOSS * balance / 100:
                self.trading_halted = True
                self.halt_reason = f"日亏损超过 {config.MAX_DAILY_LOSS}%"
            elif self.monthly_pnl < -config.MAX_MONTHLY_LOSS * balance / 100:
                self.trading_halted = True
                self.halt_reason = f"月亏损超过 {config.MAX_MONTHLY_LOSS}%"
            elif drawdown_pct >= config.MAX_PORTFOLIO_DRAWDOWN:
                self.trading_halted = True
                self.halt_reason = "永久停止：净值回撤超过30%"

        self.save_state()

    def is_trading_allowed(self):
        return not self.trading_halted

    async def get_risk_report(self):
        await self.update_pnl()
        balance = await self.get_account_balance()
        drawdown_pct = ((self.ath_balance - balance) / self.ath_balance * 100) if self.ath_balance > 0 else 0
        return (
            f"📊 *当前风险报告*\n"
            f"余额: {balance:.2f} USDT\n"
            f"历史最高: {self.ath_balance:.2f} USDT\n"
            f"回撤: {drawdown_pct:.2f}%\n"
            f"今日净盈亏: {self.daily_pnl:.2f} USDT ({self.daily_pnl/balance*100:.2f}%)\n"
            f"30日净盈亏: {self.monthly_pnl:.2f} USDT ({self.monthly_pnl/balance*100:.2f}%)\n"
            f"自动交易: {'✅ 允许' if self.is_trading_allowed() else '⛔ 暂停'}"
            + (f"\n原因: {self.halt_reason}" if not self.is_trading_allowed() else "")
        )
