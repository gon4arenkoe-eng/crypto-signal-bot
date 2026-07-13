"""
Auto Optimize Module v1.0 для Crypto Signal Bot v11.2
Автоматически тестирует параметры и выбирает лучшие
"""

import sqlite3
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

from backtest_engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)

DB_PATH = "bot_config.db"

# Сетка параметров для оптимизации
DEFAULT_PARAM_GRID = {
    "ema_fast": [5, 8, 13, 21],
    "ema_slow": [21, 30, 50, 100],
    "ema_trend": [30, 50, 100],
    "sl_multiplier": [2.0, 2.5, 3.0],
    "tp1_multiplier": [1.0, 1.5, 2.0],
    "risk_per_trade": [1.0, 2.0, 3.0]
}

# Минимальные требования для "хорошей" стратегии
MIN_REQUIREMENTS = {
    "min_winrate": 50.0,      # минимум 50% winrate
    "min_profit_factor": 1.3, # минимум PF 1.3
    "max_drawdown": 20.0,     # максимум 20% просадка
    "min_trades": 20,         # минимум 20 сделок
}


@dataclass
class OptimizedParams:
    symbol: str
    ema_fast: int
    ema_slow: int
    ema_trend: int
    sl_multiplier: float
    tp1_multiplier: float
    tp2_multiplier: float
    tp3_multiplier: float
    risk_per_trade: float
    total_return_pct: float
    winrate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    optimized_at: str
    backtest_period: str


class AutoOptimizer:
    def __init__(self, symbol: str = "BTC", initial_balance: float = 10000.0):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self._init_db()

    def _init_db(self):
        """Создаёт таблицу для хранения оптимизированных параметров"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS optimized_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                params_json TEXT NOT NULL,
                total_return_pct REAL,
                winrate REAL,
                profit_factor REAL,
                sharpe_ratio REAL,
                max_drawdown_pct REAL,
                optimized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 0,
                backtest_period TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS optimization_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def _log(self, message: str):
        """Логирует в БД"""
        logger.info(message)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO optimization_log (symbol, message) VALUES (?, ?)",
                  (self.symbol, message))
        conn.commit()
        conn.close()

    def optimize(self, months: int = 6, param_grid: Dict = None,
                 auto_select: bool = True) -> Optional[OptimizedParams]:
        """
        Запускает полную оптимизацию.

        Args:
            months: Сколько месяцев бэктеста
            param_grid: Сетка параметров (или DEFAULT_PARAM_GRID)
            auto_select: Автоматически выбрать лучшие и сохранить

        Returns:
            OptimizedParams или None
        """
        grid = param_grid or DEFAULT_PARAM_GRID

        self._log(f"Starting auto-optimization for {self.symbol}, {months} months")
        self._log(f"Testing {len(grid)} parameter combinations")

        # Генерируем все комбинации
        import itertools
        keys = list(grid.keys())
        values = [grid[k] for k in keys]
        total_combos = 1
        for v in values:
            total_combos *= len(v)

        self._log(f"Total combinations to test: {total_combos}")

        results = []
        tested = 0

        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            tested += 1

            # Пропускаем невалидные (fast > slow)
            if params["ema_fast"] >= params["ema_slow"]:
                continue

            try:
                engine = BacktestEngine(
                    symbol=self.symbol,
                    initial_balance=self.initial_balance
                )

                end = datetime.now()
                start = end - timedelta(days=30 * months)

                result = engine.run(
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                    **params
                )

                # Проверяем минимальные требования
                if self._passes_requirements(result):
                    results.append((params, result))
                    self._log(f"✓ Combo {tested}/{total_combos}: EMA{params['ema_fast']}/{params['ema_slow']} "
                             f"Return: {result.total_return_pct:+.2f}%, PF: {result.profit_factor:.2f}")
                else:
                    self._log(f"✗ Combo {tested}/{total_combos}: EMA{params['ema_fast']}/{params['ema_slow']} "
                             f"FAILED requirements (Return: {result.total_return_pct:+.2f}%, PF: {result.profit_factor:.2f})")

                # Небольшая задержка чтобы не перегружать API
                time.sleep(0.5)

            except Exception as e:
                self._log(f"Error testing combo {params}: {e}")
                continue

        if not results:
            self._log("No valid strategies found! Using defaults.")
            return None

        # Сортируем по доходности (можно изменить на Sharpe или PF)
        results.sort(key=lambda x: x[1].total_return_pct, reverse=True)

        best_params, best_result = results[0]

        self._log(f"Best strategy found: EMA{best_params['ema_fast']}/{best_params['ema_slow']}")
        self._log(f"Return: {best_result.total_return_pct:+.2f}%, Winrate: {best_result.winrate:.1f}%, PF: {best_result.profit_factor:.2f}")

        optimized = OptimizedParams(
            symbol=self.symbol,
            ema_fast=best_params["ema_fast"],
            ema_slow=best_params["ema_slow"],
            ema_trend=best_params["ema_trend"],
            sl_multiplier=best_params["sl_multiplier"],
            tp1_multiplier=best_params["tp1_multiplier"],
            tp2_multiplier=best_params["tp1_multiplier"] * 2,  # TP2 = 2x TP1
            tp3_multiplier=best_params["tp1_multiplier"] * 3,  # TP3 = 3x TP1
            risk_per_trade=best_params["risk_per_trade"],
            total_return_pct=best_result.total_return_pct,
            winrate=best_result.winrate,
            profit_factor=best_result.profit_factor,
            sharpe_ratio=best_result.sharpe_ratio,
            max_drawdown_pct=best_result.max_drawdown_pct,
            optimized_at=datetime.now().isoformat(),
            backtest_period=f"{months} months"
        )

        if auto_select:
            self.save_params(optimized)
            self._log("Parameters saved and activated!")

        return optimized

    def _passes_requirements(self, result: BacktestResult) -> bool:
        """Проверяет, проходит ли стратегия минимальные требования"""
        return (
            result.winrate >= MIN_REQUIREMENTS["min_winrate"] and
            result.profit_factor >= MIN_REQUIREMENTS["min_profit_factor"] and
            result.max_drawdown_pct <= MIN_REQUIREMENTS["max_drawdown"] and
            result.total_trades >= MIN_REQUIREMENTS["min_trades"] and
            result.total_return_pct > 0  # должна быть прибыльной
        )

    def save_params(self, params: OptimizedParams):
        """Сохраняет параметры в БД и активирует их"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Деактивируем старые
        c.execute("UPDATE optimized_params SET is_active = 0 WHERE symbol = ?", (params.symbol,))

        # Сохраняем новые
        params_dict = {
            "ema_fast": params.ema_fast,
            "ema_slow": params.ema_slow,
            "ema_trend": params.ema_trend,
            "sl_multiplier": params.sl_multiplier,
            "tp1_multiplier": params.tp1_multiplier,
            "tp2_multiplier": params.tp2_multiplier,
            "tp3_multiplier": params.tp3_multiplier,
            "risk_per_trade": params.risk_per_trade
        }

        c.execute("""
            INSERT INTO optimized_params 
            (symbol, params_json, total_return_pct, winrate, profit_factor, 
             sharpe_ratio, max_drawdown_pct, is_active, backtest_period)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (
            params.symbol, json.dumps(params_dict),
            params.total_return_pct, params.winrate, params.profit_factor,
            params.sharpe_ratio, params.max_drawdown_pct, params.backtest_period
        ))

        conn.commit()
        conn.close()

    def get_active_params(self, symbol: str = None) -> Optional[Dict]:
        """Получает активные параметры для символа"""
        sym = symbol or self.symbol
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT params_json, total_return_pct, winrate, profit_factor, 
                   sharpe_ratio, max_drawdown_pct, optimized_at, backtest_period
            FROM optimized_params 
            WHERE symbol = ? AND is_active = 1 
            ORDER BY optimized_at DESC LIMIT 1
        """, (sym,))
        row = c.fetchone()
        conn.close()

        if not row:
            return None

        params = json.loads(row[0])
        return {
            "params": params,
            "metrics": {
                "total_return_pct": row[1],
                "winrate": row[2],
                "profit_factor": row[3],
                "sharpe_ratio": row[4],
                "max_drawdown_pct": row[5],
                "optimized_at": row[6],
                "backtest_period": row[7]
            }
        }

    def get_default_params(self) -> Dict:
        """Возвращает дефолтные параметры если оптимизации не было"""
        return {
            "ema_fast": 8,
            "ema_slow": 50,
            "ema_trend": 50,
            "sl_multiplier": 2.5,
            "tp1_multiplier": 1.5,
            "tp2_multiplier": 3.0,
            "tp3_multiplier": 5.0,
            "risk_per_trade": 2.0
        }

    def get_params_for_trading(self, symbol: str = None) -> Dict:
        """
        Получает параметры для торговли.
        Сначала проверяет оптимизированные, потом дефолтные.
        """
        active = self.get_active_params(symbol)
        if active:
            logger.info(f"Using optimized params for {symbol or self.symbol}")
            return active["params"]

        logger.info(f"Using default params for {symbol or self.symbol}")
        return self.get_default_params()

    def format_params_message(self, symbol: str = None) -> str:
        """Форматирует сообщение о текущих параметрах"""
        active = self.get_active_params(symbol)

        if not active:
            params = self.get_default_params()
            return f"""⚙️ <b>Current Parameters (DEFAULT)</b>

EMA Fast: <code>{params['ema_fast']}</code>
EMA Slow: <code>{params['ema_slow']}</code>
EMA Trend (4h): <code>{params['ema_trend']}</code>
SL Multiplier: <code>{params['sl_multiplier']}x ATR</code>
TP1: <code>{params['tp1_multiplier']}x risk</code>
TP2: <code>{params['tp2_multiplier']}x risk</code>
TP3: <code>{params['tp3_multiplier']}x risk</code>
Risk per Trade: <code>{params['risk_per_trade']}%</code>

<i>No optimization done yet. Use /autooptimize to find best params.</i>"""

        params = active["params"]
        m = active["metrics"]

        return f"""⚙️ <b>Current Parameters (OPTIMIZED)</b>

EMA Fast: <code>{params['ema_fast']}</code>
EMA Slow: <code>{params['ema_slow']}</code>
EMA Trend (4h): <code>{params['ema_trend']}</code>
SL Multiplier: <code>{params['sl_multiplier']}x ATR</code>
TP1: <code>{params['tp1_multiplier']}x risk</code>
TP2: <code>{params['tp2_multiplier']}x risk</code>
TP3: <code>{params['tp3_multiplier']}x risk</code>
Risk per Trade: <code>{params['risk_per_trade']}%</code>

📊 <b>Backtest Results</b>
Return: <code>{m['total_return_pct']:+.2f}%</code>
Winrate: <code>{m['winrate']:.1f}%</code>
Profit Factor: <code>{m['profit_factor']:.2f}</code>
Sharpe: <code>{m['sharpe_ratio']:.2f}</code>
Max DD: <code>{m['max_drawdown_pct']:.2f}%</code>

<i>Optimized: {m['optimized_at'][:10]} | Period: {m['backtest_period']}</i>"""


# Глобальный инстанс
_optimizer = None

def get_optimizer(symbol: str = "BTC") -> AutoOptimizer:
    global _optimizer
    if _optimizer is None:
        _optimizer = AutoOptimizer(symbol=symbol)
    return _optimizer
