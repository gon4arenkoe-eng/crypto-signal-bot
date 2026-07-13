"""
Backtest Engine v1.0 для Crypto Signal Bot v11.2
Полноценный бэктест: комиссии Kraken, slippage, оптимизация параметров, сравнение с buy-and-hold
"""

import sqlite3
import json
import math
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
from enum import Enum
import numpy as np

from binance_api import get_ohlcv, calculate_ema, calculate_rsi, calculate_atr
from chart_generator import ChartGenerator

logger = logging.getLogger(__name__)

# Kraken комиссии (Spot, Tier 0)
KRAKEN_MAKER_FEE = 0.0016  # 0.16%
KRAKEN_TAKER_FEE = 0.0026  # 0.26%

# Проскальзывание (slippage) — среднее для крипто
SLIPPAGE_PCT = 0.0005  # 0.05%


class TradeSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class BacktestTrade:
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    entry_time: str
    exit_time: Optional[str]
    pnl_gross: float  # без комиссий
    pnl_net: float    # с комиссиями
    pnl_percent: float
    status: str
    exit_reason: str
    fees_paid: float
    slippage_paid: float


@dataclass
class BacktestResult:
    symbol: str
    period: str
    initial_balance: float
    final_balance: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    winrate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    avg_trade_return: float
    avg_win: float
    avg_loss: float
    best_trade: float
    worst_trade: float
    total_fees: float
    total_slippage: float
    buy_hold_return_pct: float
    outperformance_pct: float
    equity_curve: List[Dict]
    trades: List[BacktestTrade]
    params: Dict  # использованные параметры


class BacktestEngine:
    def __init__(self, symbol: str = "BTC", initial_balance: float = 10000.0):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.peak_balance = initial_balance
        self.equity_curve = []
        self.trades = []
        self.total_fees = 0.0
        self.total_slippage = 0.0

    def _apply_fees(self, amount: float, is_taker: bool = True) -> float:
        """Применяет комиссии Kraken"""
        fee_rate = KRAKEN_TAKER_FEE if is_taker else KRAKEN_MAKER_FEE
        fee = amount * fee_rate
        self.total_fees += fee
        return amount - fee

    def _apply_slippage(self, price: float, side: str) -> float:
        """Применяет проскальзывание"""
        slip = price * SLIPPAGE_PCT
        self.total_slippage += slip
        if side == "BUY":
            return price + slip  # покупаем дороже
        return price - slip  # продаём дешевле

    def _calculate_signal(self, ohlcv_1h: List[Dict], ohlcv_4h: List[Dict],
                          ema_fast: int = 8, ema_slow: int = 50,
                          ema_trend: int = 50, rsi_period: int = 14) -> Optional[str]:
        """
        Рассчитывает сигнал на основе EMA стратегии.
        Возвращает "BUY", "SELL" или None.
        """
        if not ohlcv_1h or len(ohlcv_1h) < max(ema_slow, rsi_period) + 5:
            return None

        closes_1h = [float(k["close"]) for k in ohlcv_1h]
        highs_1h = [float(k["high"]) for k in ohlcv_1h]
        lows_1h = [float(k["low"]) for k in ohlcv_1h]
        volumes_1h = [float(k.get("volumeto", 0)) for k in ohlcv_1h]

        # EMA на 1h
        ema_fast_vals = calculate_ema(closes_1h, ema_fast)
        ema_slow_vals = calculate_ema(closes_1h, ema_slow)
        rsi = calculate_rsi(closes_1h, rsi_period)

        # EMA на 4h (тренд)
        if ohlcv_4h and len(ohlcv_4h) >= ema_trend:
            closes_4h = [float(k["close"]) for k in ohlcv_4h]
            ema_trend_vals = calculate_ema(closes_4h, ema_trend)
            trend_bullish = closes_1h[-1] > ema_trend_vals[-1]
            trend_bearish = closes_1h[-1] < ema_trend_vals[-1]
        else:
            # Если нет 4h данных, используем 1h EMA50 как тренд
            trend_bullish = closes_1h[-1] > ema_slow_vals[-1]
            trend_bearish = closes_1h[-1] < ema_slow_vals[-1]

        # Volume confirmation
        avg_volume = sum(volumes_1h[-20:]) / 20 if len(volumes_1h) >= 20 else sum(volumes_1h) / len(volumes_1h)
        current_volume = volumes_1h[-1]
        volume_confirmed = current_volume > avg_volume * 0.8

        # Crossover signals
        ema_cross_up = (ema_fast_vals[-2] <= ema_slow_vals[-2] and ema_fast_vals[-1] > ema_slow_vals[-1])
        ema_cross_down = (ema_fast_vals[-2] >= ema_slow_vals[-2] and ema_fast_vals[-1] < ema_slow_vals[-1])

        if ema_cross_up and volume_confirmed and trend_bullish:
            return "BUY"
        elif ema_cross_down and volume_confirmed and trend_bearish:
            return "SELL"

        return None

    def _calculate_stops(self, entry: float, atr: float, signal: str,
                         sl_multiplier: float = 2.5,
                         tp1_multiplier: float = 1.5,
                         tp2_multiplier: float = 3.0,
                         tp3_multiplier: float = 5.0) -> Tuple[float, float, float, float]:
        """Рассчитывает стоп-лосс и тейк-профиты"""
        if signal == "BUY":
            sl = entry - atr * sl_multiplier
            risk = entry - sl
            tp1 = entry + risk * tp1_multiplier
            tp2 = entry + risk * tp2_multiplier
            tp3 = entry + risk * tp3_multiplier
        else:
            sl = entry + atr * sl_multiplier
            risk = sl - entry
            tp1 = entry - risk * tp1_multiplier
            tp2 = entry - risk * tp2_multiplier
            tp3 = entry - risk * tp3_multiplier

        return sl, tp1, tp2, tp3

    def run(self, start_date: str = None, end_date: str = None,
            ema_fast: int = 8, ema_slow: int = 50, ema_trend: int = 50,
            risk_per_trade: float = 2.0,
            sl_multiplier: float = 2.5,
            tp1_multiplier: float = 1.5,
            tp2_multiplier: float = 3.0,
            tp3_multiplier: float = 5.0) -> BacktestResult:
        """
        Запускает бэктест.

        Args:
            start_date: "2024-01-01" (опционально)
            end_date: "2025-01-01" (опционально)
            ema_fast: Период быстрой EMA (по умолчанию 8)
            ema_slow: Период медленной EMA (по умолчанию 50)
            ema_trend: Период EMA для тренда на 4h (по умолчанию 50)
            risk_per_trade: % риска на сделку (по умолчанию 2%)
        """
        logger.info(f"Starting backtest for {self.symbol}")
        logger.info(f"Parameters: EMA{ema_fast}/{ema_slow}, Trend EMA{ema_trend}, Risk {risk_per_trade}%")

        # Загружаем данные
        logger.info("Loading 1h data...")
        ohlcv_1h_full = get_ohlcv(self.symbol, "hour", limit=5000)
        if not ohlcv_1h_full or len(ohlcv_1h_full) < 200:
            raise ValueError(f"Insufficient 1h data for {self.symbol}")

        logger.info("Loading 4h data...")
        ohlcv_4h_full = get_ohlcv(self.symbol, "4h", limit=5000)

        # Фильтруем по датам если указаны
        if start_date or end_date:
            from datetime import datetime
            if start_date:
                start_ts = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
                ohlcv_1h_full = [k for k in ohlcv_1h_full if k["time"] >= start_ts]
                if ohlcv_4h_full:
                    ohlcv_4h_full = [k for k in ohlcv_4h_full if k["time"] >= start_ts]
            if end_date:
                end_ts = datetime.strptime(end_date, "%Y-%m-%d").timestamp()
                ohlcv_1h_full = [k for k in ohlcv_1h_full if k["time"] <= end_ts]
                if ohlcv_4h_full:
                    ohlcv_4h_full = [k for k in ohlcv_4h_full if k["time"] <= end_ts]

        logger.info(f"Data loaded: {len(ohlcv_1h_full)} 1h candles")

        # Подготовка
        self.balance = self.initial_balance
        self.peak_balance = self.initial_balance
        self.equity_curve = []
        self.trades = []
        self.total_fees = 0.0
        self.total_slippage = 0.0

        open_trade = None

        # Проходим по каждой свече (начиная с 100-й, чтобы EMA рассчитались)
        for i in range(100, len(ohlcv_1h_full)):
            current_candle = ohlcv_1h_full[i]
            current_price = float(current_candle["close"])
            current_time = datetime.fromtimestamp(current_candle["time"]).isoformat()

            # Обновляем equity curve
            self.equity_curve.append({
                "timestamp": current_time,
                "balance": self.balance,
                "price": current_price
            })

            # Обновляем peak
            if self.balance > self.peak_balance:
                self.peak_balance = self.balance

            # Проверяем открытую позицию
            if open_trade:
                # Проверяем стоп-лосс и тейк-профиты
                hit = False
                exit_price = current_price
                exit_reason = ""

                if open_trade.side == "LONG":
                    # Проверяем SL
                    if current_price <= open_trade.stop_loss:
                        exit_price = open_trade.stop_loss
                        exit_reason = "STOP_LOSS"
                        hit = True
                    # Проверяем TP3
                    elif current_price >= open_trade.tp3:
                        exit_price = open_trade.tp3
                        exit_reason = "TAKE_PROFIT_3"
                        hit = True
                    # Проверяем TP2
                    elif current_price >= open_trade.tp2:
                        exit_price = open_trade.tp2
                        exit_reason = "TAKE_PROFIT_2"
                        hit = True
                    # Проверяем TP1
                    elif current_price >= open_trade.tp1:
                        exit_price = open_trade.tp1
                        exit_reason = "TAKE_PROFIT_1"
                        hit = True

                if hit:
                    # Закрываем позицию
                    gross_pnl = (exit_price - open_trade.entry_price) * open_trade.quantity

                    # Применяем slippage и комиссии
                    exit_with_slip = self._apply_slippage(exit_price, "SELL")
                    gross_pnl = (exit_with_slip - open_trade.entry_price) * open_trade.quantity

                    # Комиссии на вход и выход
                    entry_fees = open_trade.entry_price * open_trade.quantity * KRAKEN_TAKER_FEE
                    exit_fees = exit_with_slip * open_trade.quantity * KRAKEN_TAKER_FEE
                    total_fees = entry_fees + exit_fees
                    self.total_fees += total_fees

                    net_pnl = gross_pnl - total_fees
                    pnl_percent = (net_pnl / (open_trade.entry_price * open_trade.quantity)) * 100

                    self.balance += net_pnl

                    trade = BacktestTrade(
                        id=len(self.trades) + 1,
                        symbol=self.symbol,
                        side=open_trade.side,
                        entry_price=open_trade.entry_price,
                        exit_price=exit_with_slip,
                        quantity=open_trade.quantity,
                        entry_time=open_trade.entry_time,
                        exit_time=current_time,
                        pnl_gross=gross_pnl,
                        pnl_net=net_pnl,
                        pnl_percent=pnl_percent,
                        status=TradeStatus.CLOSED.value,
                        exit_reason=exit_reason,
                        fees_paid=total_fees,
                        slippage_paid=exit_price * SLIPPAGE_PCT
                    )
                    self.trades.append(trade)
                    open_trade = None
                    logger.info(f"Trade closed: {exit_reason} P&L: ${net_pnl:.2f} ({pnl_percent:+.2f}%)")

            # Ищем новый сигнал если нет открытой позиции
            if not open_trade:
                # Берём окно данных для расчёта
                window_1h = ohlcv_1h_full[max(0, i-200):i+1]
                window_4h = None
                if ohlcv_4h_full:
                    # Находим соответствующие 4h свечи
                    current_ts = current_candle["time"]
                    window_4h = [k for k in ohlcv_4h_full if k["time"] <= current_ts]
                    window_4h = window_4h[-50:] if len(window_4h) > 50 else window_4h

                signal = self._calculate_signal(
                    window_1h, window_4h,
                    ema_fast=ema_fast, ema_slow=ema_slow, ema_trend=ema_trend
                )

                if signal == "BUY":
                    # Рассчитываем позицию
                    entry_price = self._apply_slippage(current_price, "BUY")

                    # ATR для стопа
                    highs = [float(k["high"]) for k in window_1h[-20:]]
                    lows = [float(k["low"]) for k in window_1h[-20:]]
                    closes = [float(k["close"]) for k in window_1h[-20:]]
                    atr = calculate_atr(highs, lows, closes, period=14)

                    sl, tp1, tp2, tp3 = self._calculate_stops(
                        entry_price, atr, "BUY",
                        sl_multiplier, tp1_multiplier, tp2_multiplier, tp3_multiplier
                    )

                    risk_amount = self.balance * (risk_per_trade / 100)
                    risk_per_unit = entry_price - sl
                    if risk_per_unit > 0:
                        quantity = risk_amount / risk_per_unit
                    else:
                        quantity = 0

                    if quantity > 0:
                        open_trade = type('obj', (object,), {
                            'symbol': self.symbol,
                            'side': "LONG",
                            'entry_price': entry_price,
                            'quantity': quantity,
                            'entry_time': current_time,
                            'stop_loss': sl,
                            'tp1': tp1,
                            'tp2': tp2,
                            'tp3': tp3
                        })()
                        logger.info(f"Trade opened: BUY {self.symbol} @ ${entry_price:.2f} Qty: {quantity:.6f}")

        # Закрываем открытую позицию по последней цене если осталась
        if open_trade:
            last_price = float(ohlcv_1h_full[-1]["close"])
            gross_pnl = (last_price - open_trade.entry_price) * open_trade.quantity

            exit_with_slip = self._apply_slippage(last_price, "SELL")
            gross_pnl = (exit_with_slip - open_trade.entry_price) * open_trade.quantity

            entry_fees = open_trade.entry_price * open_trade.quantity * KRAKEN_TAKER_FEE
            exit_fees = exit_with_slip * open_trade.quantity * KRAKEN_TAKER_FEE
            total_fees = entry_fees + exit_fees
            self.total_fees += total_fees

            net_pnl = gross_pnl - total_fees
            pnl_percent = (net_pnl / (open_trade.entry_price * open_trade.quantity)) * 100
            self.balance += net_pnl

            trade = BacktestTrade(
                id=len(self.trades) + 1,
                symbol=self.symbol,
                side=open_trade.side,
                entry_price=open_trade.entry_price,
                exit_price=exit_with_slip,
                quantity=open_trade.quantity,
                entry_time=open_trade.entry_time,
                exit_time=datetime.fromtimestamp(ohlcv_1h_full[-1]["time"]).isoformat(),
                pnl_gross=gross_pnl,
                pnl_net=net_pnl,
                pnl_percent=pnl_percent,
                status=TradeStatus.CLOSED.value,
                exit_reason="END_OF_TEST",
                fees_paid=total_fees,
                slippage_paid=last_price * SLIPPAGE_PCT
            )
            self.trades.append(trade)

        # Рассчитываем метрики
        return self._calculate_metrics(ohlcv_1h_full)

    def _calculate_metrics(self, ohlcv_data: List[Dict]) -> BacktestResult:
        """Рассчитывает итоговые метрики"""
        closed_trades = [t for t in self.trades if t.status == TradeStatus.CLOSED.value]

        total_trades = len(closed_trades)
        winning = [t for t in closed_trades if t.pnl_net > 0]
        losing = [t for t in closed_trades if t.pnl_net <= 0]

        winning_trades = len(winning)
        losing_trades = len(losing)
        winrate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        total_wins = sum(t.pnl_net for t in winning)
        total_losses = abs(sum(t.pnl_net for t in losing))
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        avg_win = total_wins / len(winning) if winning else 0
        avg_loss = -total_losses / len(losing) if losing else 0

        best_trade = max((t.pnl_net for t in closed_trades), default=0)
        worst_trade = min((t.pnl_net for t in closed_trades), default=0)

        # Sharpe Ratio
        if len(closed_trades) > 1:
            returns = [t.pnl_percent for t in closed_trades]
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = math.sqrt(variance) if variance > 0 else 0
            sharpe = (avg_return / std_dev) * math.sqrt(252 * 24) if std_dev > 0 else 0.0  # annualized hourly
        else:
            sharpe = 0.0

        # Max Drawdown
        max_dd = 0.0
        peak = self.initial_balance
        for point in self.equity_curve:
            if point["balance"] > peak:
                peak = point["balance"]
            dd = (peak - point["balance"]) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Buy & Hold comparison
        first_price = float(ohlcv_data[0]["close"])
        last_price = float(ohlcv_data[-1]["close"])
        buy_hold_return = ((last_price - first_price) / first_price) * 100

        total_return = ((self.balance - self.initial_balance) / self.initial_balance) * 100
        outperformance = total_return - buy_hold_return

        # Period
        start = datetime.fromtimestamp(ohlcv_data[0]["time"]).strftime("%Y-%m-%d")
        end = datetime.fromtimestamp(ohlcv_data[-1]["time"]).strftime("%Y-%m-%d")
        period = f"{start} to {end}"

        return BacktestResult(
            symbol=self.symbol,
            period=period,
            initial_balance=self.initial_balance,
            final_balance=self.balance,
            total_return_pct=round(total_return, 2),
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            winrate=round(winrate, 1),
            profit_factor=round(profit_factor, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd, 2),
            avg_trade_return=round(sum(t.pnl_percent for t in closed_trades) / len(closed_trades), 2) if closed_trades else 0,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            best_trade=round(best_trade, 2),
            worst_trade=round(worst_trade, 2),
            total_fees=round(self.total_fees, 2),
            total_slippage=round(self.total_slippage, 2),
            buy_hold_return_pct=round(buy_hold_return, 2),
            outperformance_pct=round(outperformance, 2),
            equity_curve=self.equity_curve,
            trades=closed_trades,
            params={"ema_fast": 8, "ema_slow": 50, "ema_trend": 50, "risk_per_trade": 2.0}
        )

    def optimize_params(self, param_grid: Dict) -> List[BacktestResult]:
        """
        Оптимизация параметров стратегии.

        Args:
            param_grid: {"ema_fast": [5, 8, 13], "ema_slow": [30, 50, 100], ...}

        Returns:
            Список BacktestResult, отсортированных по доходности
        """
        import itertools

        results = []
        keys = list(param_grid.keys())
        values = [param_grid[k] for k in keys]

        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            logger.info(f"Testing params: {params}")

            # Сброс состояния
            self.balance = self.initial_balance
            self.peak_balance = self.initial_balance
            self.equity_curve = []
            self.trades = []
            self.total_fees = 0.0
            self.total_slippage = 0.0

            try:
                result = self.run(**params)
                result.params = params
                results.append(result)
            except Exception as e:
                logger.error(f"Optimization error with {params}: {e}")

        # Сортируем по доходности
        results.sort(key=lambda x: x.total_return_pct, reverse=True)
        return results


def format_backtest_report(result: BacktestResult) -> str:
    """Форматирует отчёт бэктеста для Telegram"""
    emoji = "🟢" if result.total_return_pct > 0 else "🔴"
    vs_emoji = "🟢" if result.outperformance_pct > 0 else "🔴"

    report = f"""📊 <b>BACKTEST REPORT</b>

<b>{result.symbol}/USD</b> | {result.period}

{emoji} <b>Strategy Return: {result.total_return_pct:+.2f}%</b>
💰 Initial: ${result.initial_balance:,.2f}
💵 Final: ${result.final_balance:,.2f}

📋 <b>Trades</b>: {result.total_trades}
   ✅ Wins: {result.winning_trades} ({result.winrate:.1f}%)
   ❌ Losses: {result.losing_trades}

⚡ <b>Metrics</b>
   Profit Factor: <code>{result.profit_factor:.2f}</code>
   Sharpe Ratio: <code>{result.sharpe_ratio:.2f}</code>
   Max Drawdown: <code>{result.max_drawdown_pct:.2f}%</code>
   Avg Trade: <code>{result.avg_trade_return:+.2f}%</code>
   Avg Win: <code>${result.avg_win:,.2f}</code>
   Avg Loss: <code>${result.avg_loss:,.2f}</code>

💸 <b>Costs</b>
   Total Fees: <code>${result.total_fees:,.2f}</code>
   Slippage: <code>${result.total_slippage:,.2f}</code>

📈 <b>Comparison</b>
   Buy & Hold: <code>{result.buy_hold_return_pct:+.2f}%</code>
   {vs_emoji} Outperformance: <code>{result.outperformance_pct:+.2f}%</code>

🏆 Best: <code>+${result.best_trade:,.2f}</code>
💀 Worst: <code>${result.worst_trade:,.2f}</code>

<i>Includes Kraken fees (0.26% taker) + slippage (0.05%)</i>"""

    return report


def run_backtest_command(symbol: str = "BTC", months: int = 12) -> str:
    """Запускает бэктест и возвращает отчёт для Telegram"""
    from datetime import datetime, timedelta

    end = datetime.now()
    start = end - timedelta(days=30 * months)

    engine = BacktestEngine(symbol=symbol, initial_balance=10000.0)

    try:
        result = engine.run(
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d")
        )
        return format_backtest_report(result)
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        return f"❌ Backtest error: {str(e)}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Пример запуска
    print("Running backtest...")
    result = run_backtest_command("BTC", months=6)
    print(result)
