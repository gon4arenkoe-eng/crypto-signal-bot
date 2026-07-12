"""
Paper Trading Engine для Crypto Signal Bot v11.2
SQLite storage, полная статистика, риск-менеджмент
"""

import sqlite3
import os
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional, Dict
from enum import Enum
import math

DB_PATH = os.getenv("PAPER_DB_PATH", "paper_trading.db")


class TradeSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class Trade:
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    entry_time: str
    exit_time: Optional[str]
    pnl: Optional[float]
    pnl_percent: Optional[float]
    status: str
    strategy: str
    timeframe: str
    stop_loss: Optional[float]
    take_profit: Optional[float]
    close_reason: Optional[str]


@dataclass
class Portfolio:
    balance: float
    initial_balance: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    max_drawdown: float
    max_drawdown_percent: float
    peak_balance: float
    current_drawdown: float
    open_positions: int


class PaperTradingDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                pnl REAL,
                pnl_percent REAL,
                status TEXT NOT NULL,
                strategy TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                close_reason TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL NOT NULL,
                total_pnl REAL NOT NULL,
                drawdown_percent REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cursor.execute("SELECT value FROM settings WHERE key = 'initial_balance'")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO settings VALUES (?, ?)", ("initial_balance", "10000.0"))
            cursor.execute("INSERT INTO settings VALUES (?, ?)", ("current_balance", "10000.0"))
            cursor.execute("INSERT INTO settings VALUES (?, ?)", ("peak_balance", "10000.0"))
            cursor.execute("INSERT INTO settings VALUES (?, ?)", ("paper_trading_enabled", "true"))
            conn.commit()

        conn.close()

    def get_setting(self, key: str, default=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def is_paper_enabled(self) -> bool:
        return self.get_setting("paper_trading_enabled", "true").lower() == "true"

    def toggle_paper(self) -> bool:
        current = self.is_paper_enabled()
        new_state = not current
        self.set_setting("paper_trading_enabled", "true" if new_state else "false")
        return new_state

    def get_balance(self) -> float:
        return float(self.get_setting("current_balance", "10000.0"))

    def get_initial_balance(self) -> float:
        return float(self.get_setting("initial_balance", "10000.0"))

    def get_peak_balance(self) -> float:
        return float(self.get_setting("peak_balance", "10000.0"))

    def update_balance(self, new_balance: float):
        self.set_setting("current_balance", str(new_balance))
        peak = self.get_peak_balance()
        if new_balance > peak:
            self.set_setting("peak_balance", str(new_balance))

        drawdown = ((new_balance - peak) / peak) * 100 if peak > 0 else 0.0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO balance_history (timestamp, balance, total_pnl, drawdown_percent) VALUES (?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), new_balance, 
             new_balance - self.get_initial_balance(), drawdown)
        )
        conn.commit()
        conn.close()

    def add_trade(self, trade: Trade) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (symbol, side, entry_price, exit_price, quantity, entry_time,
                exit_time, pnl, pnl_percent, status, strategy, timeframe,
                stop_loss, take_profit, close_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade.symbol, trade.side, trade.entry_price, trade.exit_price,
              trade.quantity, trade.entry_time, trade.exit_time, trade.pnl,
              trade.pnl_percent, trade.status, trade.strategy, trade.timeframe,
              trade.stop_loss, trade.take_profit, trade.close_reason))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return trade_id

    def close_trade(self, trade_id: int, exit_price: float, pnl: float, 
                    pnl_percent: float, close_reason: str = "SIGNAL"):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades SET exit_price = ?, exit_time = ?, pnl = ?, pnl_percent = ?,
                status = ?, close_reason = ? WHERE id = ?
        """, (exit_price, datetime.utcnow().isoformat(), pnl, pnl_percent,
              TradeStatus.CLOSED.value, close_reason, trade_id))
        conn.commit()
        conn.close()

    def get_open_trades(self, symbol: str = None) -> List[Trade]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if symbol:
            cursor.execute("SELECT * FROM trades WHERE status = ? AND symbol = ? ORDER BY entry_time DESC",
                          (TradeStatus.OPEN.value, symbol))
        else:
            cursor.execute("SELECT * FROM trades WHERE status = ? ORDER BY entry_time DESC",
                          (TradeStatus.OPEN.value,))

        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_trade(row) for row in rows]

    def get_all_trades(self, limit: int = 100) -> List[Trade]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_trade(row) for row in rows]

    def get_closed_trades(self, limit: int = 100) -> List[Trade]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE status = ? ORDER BY exit_time DESC LIMIT ?",
                      (TradeStatus.CLOSED.value, limit))
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_trade(row) for row in rows]

    def _row_to_trade(self, row) -> Trade:
        return Trade(
            id=row['id'], symbol=row['symbol'], side=row['side'],
            entry_price=row['entry_price'], exit_price=row['exit_price'],
            quantity=row['quantity'], entry_time=row['entry_time'],
            exit_time=row['exit_time'], pnl=row['pnl'], pnl_percent=row['pnl_percent'],
            status=row['status'], strategy=row['strategy'], timeframe=row['timeframe'],
            stop_loss=row['stop_loss'], take_profit=row['take_profit'],
            close_reason=row['close_reason']
        )

    def get_balance_history(self, days: int = 30) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute("SELECT * FROM balance_history WHERE timestamp > ? ORDER BY timestamp", (since,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def reset(self, new_balance: float = 10000.0):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades")
        cursor.execute("DELETE FROM balance_history")
        conn.commit()
        conn.close()
        self.set_setting("initial_balance", str(new_balance))
        self.set_setting("current_balance", str(new_balance))
        self.set_setting("peak_balance", str(new_balance))


class PaperTradingEngine:
    def __init__(self, db: PaperTradingDB = None):
        self.db = db or PaperTradingDB()

    def open_position(self, symbol: str, side: str, price: float, 
                      strategy: str, timeframe: str,
                      stop_loss: float = None, take_profit: float = None,
                      risk_percent: float = 2.0) -> Optional[Trade]:

        if not self.db.is_paper_enabled():
            return None

        open_trades = self.db.get_open_trades(symbol)
        if open_trades:
            return None

        balance = self.db.get_balance()
        position_size = (balance * risk_percent / 100) / price

        trade = Trade(
            id=0, symbol=symbol, side=side, entry_price=price,
            exit_price=None, quantity=position_size,
            entry_time=datetime.utcnow().isoformat(), exit_time=None,
            pnl=None, pnl_percent=None, status=TradeStatus.OPEN.value,
            strategy=strategy, timeframe=timeframe,
            stop_loss=stop_loss, take_profit=take_profit, close_reason=None
        )

        trade_id = self.db.add_trade(trade)
        trade.id = trade_id
        return trade

    def close_position(self, trade_id: int, exit_price: float, 
                       close_reason: str = "SIGNAL") -> Optional[Trade]:

        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = cursor.fetchone()
        conn.close()

        if not row or row['status'] != TradeStatus.OPEN.value:
            return None

        entry_price = row['entry_price']
        quantity = row['quantity']
        side = row['side']

        if side == TradeSide.LONG.value:
            pnl = (exit_price - entry_price) * quantity
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl = (entry_price - exit_price) * quantity
            pnl_percent = ((entry_price - exit_price) / entry_price) * 100

        current_balance = self.db.get_balance()
        new_balance = current_balance + pnl
        self.db.update_balance(new_balance)
        self.db.close_trade(trade_id, exit_price, pnl, pnl_percent, close_reason)

        return self.db._row_to_trade(row)

    def check_stop_take(self, symbol: str, current_price: float) -> List[Trade]:
        open_trades = self.db.get_open_trades(symbol)
        closed = []

        for trade in open_trades:
            if trade.stop_loss and trade.side == TradeSide.LONG.value:
                if current_price <= trade.stop_loss:
                    self.close_position(trade.id, trade.stop_loss, "STOP_LOSS")
                    closed.append(trade)
            elif trade.take_profit and trade.side == TradeSide.LONG.value:
                if current_price >= trade.take_profit:
                    self.close_position(trade.id, trade.take_profit, "TAKE_PROFIT")
                    closed.append(trade)

        return closed

    def get_portfolio(self) -> Portfolio:
        balance = self.db.get_balance()
        initial = self.db.get_initial_balance()
        peak = self.db.get_peak_balance()

        all_trades = self.db.get_all_trades(10000)
        closed_trades = [t for t in all_trades if t.status == TradeStatus.CLOSED.value]
        open_trades = [t for t in all_trades if t.status == TradeStatus.OPEN.value]

        total_trades = len(closed_trades)
        winning = len([t for t in closed_trades if t.pnl and t.pnl > 0])
        losing = total_trades - winning

        total_pnl = sum(t.pnl for t in closed_trades if t.pnl) if closed_trades else 0.0

        history = self.db.get_balance_history(365)
        max_dd_percent = 0.0
        max_dd = 0.0
        if history:
            max_dd_percent = min(h['drawdown_percent'] for h in history)
            max_dd = initial * abs(max_dd_percent) / 100

        current_dd = ((balance - peak) / peak) * 100 if peak > 0 else 0.0

        return Portfolio(
            balance=balance, initial_balance=initial, total_trades=total_trades,
            winning_trades=winning, losing_trades=losing, total_pnl=total_pnl,
            max_drawdown=max_dd, max_drawdown_percent=max_dd_percent,
            peak_balance=peak, current_drawdown=current_dd,
            open_positions=len(open_trades)
        )

    def get_statistics(self) -> Dict:
        portfolio = self.get_portfolio()
        closed_trades = self.db.get_closed_trades(10000)

        if not closed_trades:
            return {
                "portfolio": portfolio, "winrate": 0.0, "profit_factor": 0.0,
                "sharpe_ratio": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "best_trade": None, "worst_trade": None, "avg_trade_duration": 0.0
            }

        wins = [t for t in closed_trades if t.pnl and t.pnl > 0]
        losses = [t for t in closed_trades if t.pnl and t.pnl <= 0]

        total_wins = sum(t.pnl for t in wins) if wins else 0
        total_losses = abs(sum(t.pnl for t in losses)) if losses else 0

        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        avg_win = total_wins / len(wins) if wins else 0
        avg_loss = -total_losses / len(losses) if losses else 0

        best = max(closed_trades, key=lambda t: t.pnl or 0) if closed_trades else None
        worst = min(closed_trades, key=lambda t: t.pnl or 0) if closed_trades else None

        if len(closed_trades) > 1:
            returns = [t.pnl_percent for t in closed_trades if t.pnl_percent is not None]
            if returns:
                avg_return = sum(returns) / len(returns)
                variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
                std_dev = math.sqrt(variance) if variance > 0 else 0
                sharpe = (avg_return / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        durations = []
        for t in closed_trades:
            if t.entry_time and t.exit_time:
                entry = datetime.fromisoformat(t.entry_time)
                exit = datetime.fromisoformat(t.exit_time)
                durations.append((exit - entry).total_seconds() / 3600)

        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "portfolio": portfolio,
            "winrate": (portfolio.winning_trades / portfolio.total_trades * 100) if portfolio.total_trades > 0 else 0,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best_trade": best,
            "worst_trade": worst,
            "avg_trade_duration": avg_duration
        }

    def format_stats_message(self) -> str:
        stats = self.get_statistics()
        p = stats['portfolio']

        emoji_pnl = "🟢" if p.total_pnl >= 0 else "🔴"
        emoji_balance = "📈" if p.balance >= p.initial_balance else "📉"

        message = f"""📊 <b>Paper Trading Statistics</b>

{emoji_balance} <b>Balance</b>
   Initial: <code>${p.initial_balance:,.2f}</code>
   Current: <code>${p.balance:,.2f}</code>
   Peak: <code>${p.peak_balance:,.2f}</code>

{emoji_pnl} <b>P&L</b>
   Total: <code>${p.total_pnl:,.2f}</code> ({((p.balance/p.initial_balance-1)*100):+.2f}%)
   Current DD: <code>{p.current_drawdown:.2f}%</code>
   Max DD: <code>{p.max_drawdown_percent:.2f}%</code>

📋 <b>Trades</b>: {p.total_trades}
   ✅ Wins: {p.winning_trades} ({stats['winrate']:.1f}%)
   ❌ Losses: {p.losing_trades}
   🔄 Open: {p.open_positions}

⚡ <b>Metrics</b>
   Profit Factor: <code>{stats['profit_factor']:.2f}</code>
   Sharpe Ratio: <code>{stats['sharpe_ratio']:.2f}</code>
   Avg Win: <code>${stats['avg_win']:,.2f}</code>
   Avg Loss: <code>${stats['avg_loss']:,.2f}</code>"""

        if stats['best_trade']:
            message += f"""
   Best: <code>+${stats['best_trade'].pnl:,.2f}</code> ({stats['best_trade'].symbol})
   Worst: <code>${stats['worst_trade'].pnl:,.2f}</code> ({stats['worst_trade'].symbol})
   Avg Duration: <code>{stats['avg_trade_duration']:.1f}h</code>"""

        message += f"""

<i>Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>"""
        return message

    def format_portfolio_message(self) -> str:
        open_trades = self.db.get_open_trades()
        balance = self.db.get_balance()

        if not open_trades:
            return f"📭 <b>No open positions</b>

💵 Paper Balance: <code>${balance:,.2f}</code>"

        message = f"📂 <b>Open Positions</b> ({len(open_trades)})
💵 Balance: <code>${balance:,.2f}</code>

"

        for trade in open_trades:
            emoji = "🟢" if trade.side == "LONG" else "🔴"
            sl = f"
   SL: <code>${trade.stop_loss:,.2f}</code>" if trade.stop_loss else ""
            tp = f"
   TP: <code>${trade.take_profit:,.2f}</code>" if trade.take_profit else ""

            message += f"""{emoji} <b>{trade.symbol}</b> | {trade.side}
   Entry: <code>${trade.entry_price:,.2f}</code> | Qty: <code>{trade.quantity:.6f}</code>
   Time: <code>{trade.entry_time[:16].replace('T', ' ')}</code>
   Strategy: <code>{trade.strategy}</code> ({trade.timeframe}){sl}{tp}

"""
        return message

    def format_history_message(self, limit: int = 10) -> str:
        trades = self.db.get_closed_trades(limit)

        if not trades:
            return "📭 <b>No closed trades yet</b>"

        message = f"📜 <b>Last {len(trades)} Closed Trades</b>

"

        for trade in trades:
            emoji = "🟢" if trade.pnl and trade.pnl > 0 else "🔴"
            reason = f" ({trade.close_reason})" if trade.close_reason else ""

            message += f"""{emoji} <b>{trade.symbol}</b> | {trade.side}{reason}
   Entry: <code>${trade.entry_price:,.2f}</code> → Exit: <code>${trade.exit_price:,.2f}</code>
   P&L: <code>${trade.pnl:,.2f}</code> ({trade.pnl_percent:+.2f}%)
   Time: <code>{trade.entry_time[:16].replace('T', ' ')}</code> → <code>{trade.exit_time[:16].replace('T', ' ')}</code>

"""
        return message


_paper_engine = None

def get_paper_engine() -> PaperTradingEngine:
    global _paper_engine
    if _paper_engine is None:
        _paper_engine = PaperTradingEngine()
    return _paper_engine
