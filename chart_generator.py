"""
Генератор графиков эквити для Paper Trading
PNG через matplotlib, dark theme, 4 панели
"""

import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from datetime import datetime
from typing import List, Dict
import io

from paper_trading import PaperTradingDB, PaperTradingEngine, TradeStatus

plt.style.use('dark_background')
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False


class ChartGenerator:
    def __init__(self, db: PaperTradingDB = None):
        self.db = db or PaperTradingDB()
        self.engine = PaperTradingEngine(self.db)

    def generate_equity_chart(self, days: int = 30) -> bytes:
        """
        Генерирует PNG график эквити
        Возвращает bytes для отправки в Telegram
        """
        history = self.db.get_balance_history(days)
        trades = self.db.get_closed_trades(10000)
        stats = self.engine.get_statistics()
        portfolio = stats['portfolio']

        if not history:
            return self._generate_empty_chart("No data yet. Start trading to see your equity curve!")

        dates = [datetime.fromisoformat(h['timestamp']) for h in history]
        balances = [h['balance'] for h in history]

        fig = plt.figure(figsize=(14, 10), facecolor='#0d1117')
        gs = fig.add_gridspec(3, 2, height_ratios=[2, 1, 1], hspace=0.3, wspace=0.3)

        # ===== ПАНЕЛЬ 1: ГРАФИК ЭКВИТИ =====
        ax1 = fig.add_subplot(gs[0, :])
        ax1.set_facecolor('#0d1117')

        color = '#22c55e' if balances[-1] >= balances[0] else '#ef4444'
        ax1.plot(dates, balances, color=color, linewidth=2.5, label='Equity')
        ax1.fill_between(dates, balances, alpha=0.15, color=color)
        ax1.axhline(y=portfolio.initial_balance, color='#6b7280', 
                   linestyle='--', linewidth=1, alpha=0.7, label='Initial Balance')

        # Зона просадки
        peak = portfolio.initial_balance
        for i, bal in enumerate(balances):
            if bal > peak:
                peak = bal
            if bal < peak:
                ax1.fill_between([dates[i]], [bal], [peak], alpha=0.2, color='#ef4444')

        ax1.set_title(f'📊 Paper Trading Equity Curve ({days}d)', 
                     fontsize=16, fontweight='bold', color='white', pad=15)
        ax1.set_ylabel('Balance ($)', fontsize=12, color='#9ca3af')
        ax1.grid(True, alpha=0.1, color='#6b7280')
        ax1.legend(loc='upper left', facecolor='#1f2937', edgecolor='#374151')
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax1.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, days//7)))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, color='#9ca3af')
        ax1.tick_params(colors='#9ca3af')
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        ax1.spines['left'].set_color('#374151')
        ax1.spines['bottom'].set_color('#374151')

        # ===== ПАНЕЛЬ 2: P&L ПО СДЕЛКАМ =====
        ax2 = fig.add_subplot(gs[1, :])
        ax2.set_facecolor('#0d1117')

        if trades:
            trade_dates = []
            trade_pnls = []
            trade_colors = []

            for trade in trades:
                if trade.exit_time and trade.pnl is not None:
                    exit_dt = datetime.fromisoformat(trade.exit_time)
                    if dates[0] <= exit_dt <= dates[-1]:
                        trade_dates.append(exit_dt)
                        trade_pnls.append(trade.pnl)
                        trade_colors.append('#22c55e' if trade.pnl > 0 else '#ef4444')

            if trade_dates:
                ax2.bar(trade_dates, trade_pnls, color=trade_colors, 
                       width=0.8, alpha=0.8, edgecolor='none')
                ax2.axhline(y=0, color='#6b7280', linewidth=0.5)

        ax2.set_title('Individual Trade P&L', fontsize=12, color='#9ca3af', pad=10)
        ax2.set_ylabel('P&L ($)', fontsize=10, color='#9ca3af')
        ax2.grid(True, alpha=0.1, color='#6b7280', axis='y')
        ax2.tick_params(colors='#9ca3af')
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        ax2.spines['left'].set_color('#374151')
        ax2.spines['bottom'].set_color('#374151')

        # ===== ПАНЕЛЬ 3: МЕТРИКИ (ТЕКСТ) =====
        ax3 = fig.add_subplot(gs[2, 0])
        ax3.set_facecolor('#161b22')
        ax3.axis('off')

        metrics_text = f"""📈 KEY METRICS

💰 Balance:    ${portfolio.balance:,.2f}
📊 Return:     {((portfolio.balance/portfolio.initial_balance-1)*100):+.2f}%
📉 Max DD:     {portfolio.max_drawdown_percent:.2f}%

📋 Trades:     {portfolio.total_trades}
✅ Winrate:    {stats['winrate']:.1f}%
⚡ P.Factor:   {stats['profit_factor']:.2f}
🎯 Sharpe:     {stats['sharpe_ratio']:.2f}"""

        ax3.text(0.1, 0.5, metrics_text, transform=ax3.transAxes,
                fontsize=11, verticalalignment='center', color='white',
                family='monospace', linespacing=1.6)

        # ===== ПАНЕЛЬ 4: РАСПРЕДЕЛЕНИЕ P&L =====
        ax4 = fig.add_subplot(gs[2, 1])
        ax4.set_facecolor('#161b22')

        if trades:
            pnls = [t.pnl for t in trades if t.pnl is not None]
            if pnls:
                n, bins, patches = ax4.hist(pnls, bins=20, 
                                           color='#3b82f6', alpha=0.7, edgecolor='none')
                for i, patch in enumerate(patches):
                    if bins[i] < 0:
                        patch.set_facecolor('#ef4444')
                ax4.axvline(x=0, color='white', linewidth=1, linestyle='-')
                ax4.axvline(x=np.mean(pnls), color='#22c55e', linewidth=2, 
                           linestyle='--', label=f'Mean: ${np.mean(pnls):.2f}')

        ax4.set_title('P&L Distribution', fontsize=12, color='#9ca3af', pad=10)
        ax4.set_xlabel('P&L ($)', fontsize=10, color='#9ca3af')
        ax4.set_ylabel('Count', fontsize=10, color='#9ca3af')
        ax4.grid(True, alpha=0.1, color='#6b7280', axis='y')
        ax4.legend(loc='upper right', facecolor='#1f2937', edgecolor='#374151')
        ax4.tick_params(colors='#9ca3af')
        ax4.spines['top'].set_visible(False)
        ax4.spines['right'].set_visible(False)
        ax4.spines['left'].set_color('#374151')
        ax4.spines['bottom'].set_color('#374151')

        # Сохраняем в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', 
                   facecolor='#0d1117', edgecolor='none')
        buf.seek(0)
        plt.close(fig)

        return buf.getvalue()

    def _generate_empty_chart(self, message: str) -> bytes:
        """График-заглушка"""
        fig, ax = plt.subplots(figsize=(10, 6), facecolor='#0d1117')
        ax.set_facecolor('#0d1117')
        ax.text(0.5, 0.5, message, transform=ax.transAxes,
               fontsize=16, ha='center', va='center', color='#6b7280')
        ax.axis('off')

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight',
                   facecolor='#0d1117')
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()


_chart_generator = None

def get_chart_generator() -> ChartGenerator:
    global _chart_generator
    if _chart_generator is None:
        _chart_generator = ChartGenerator()
    return _chart_generator
