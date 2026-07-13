"""
Crypto Signal Bot v11.3 STABLE — Paper Trading + Charts + Backtest + Auto-Optimize
Kraken API + Yahoo Finance | Render-ready | Polling + Flask + Keep-alive
"""

import os
import json
import time
import logging
import sqlite3
import threading
import io
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# Paper Trading + Charts + Backtest + Auto-Optimize
from paper_trading import get_paper_engine, PaperTradingDB
from chart_generator import get_chart_generator
from backtest_engine import run_backtest_command
from auto_optimize import get_optimizer, AutoOptimizer

from binance_api import (
    get_price, get_prices_multi,
    analyze_binance_signal, test_binance_connection,
    init_market_db, get_strategy_params, reset_strategy_params
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

logger.info(f"TOKEN length: {len(TOKEN)}")

if TOKEN:
    try:
        resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"Bot: @{resp.json()['result']['username']}")
    except Exception as e:
        logger.error(f"getMe error: {e}")

# ===== INIT =====
paper_engine = get_paper_engine()
paper_db = PaperTradingDB()
chart_gen = get_chart_generator()
optimizer = get_optimizer(symbol="BTC")

SIGNAL_CONFIG = {
    "stop_loss_pct": 2.5,
    "min_risk_pct": 0.005,
    "atr_multiplier": 2.5,
    "scan_interval": 600,
    "scan_batch_size": 5,
    "scan_delay": 3,
}

SIGNAL_STARS = {
    5: "⭐⭐⭐⭐⭐ POWERFUL",
    4: "⭐⭐⭐⭐ Strong",
    3: "⭐⭐⭐ Good",
    2: "⭐⭐ Weak",
    1: "⭐ Very weak"
}

TG_API = f"https://api.telegram.org/bot{TOKEN}"


# ===== TELEGRAM HELPERS =====

def send_message(chat_id, text, parse_mode="HTML", reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
    except Exception as e:
        logger.error(f"send_message error: {e}")
    return None


def send_photo_bytes(chat_id, photo_bytes, caption="", parse_mode="HTML"):
    try:
        files = {"photo": ("chart.png", io.BytesIO(photo_bytes), "image/png")}
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
        resp = requests.post(f"{TG_API}/sendPhoto", data=data, files=files, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("result")
    except Exception as e:
        logger.error(f"send_photo error: {e}")
    return None


def get_updates(offset=0, limit=100):
    params = {"offset": offset, "limit": limit, "timeout": 30}
    try:
        resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=35)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result", [])
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
    return []


# ===== DATABASE =====

DB_PATH = "bot_data.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
            chat_id INTEGER, auto_signals INTEGER DEFAULT 1, min_stars INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, signal_type TEXT, entry REAL,
            stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
            risk_reward REAL, stars INTEGER, rsi REAL, change_24h REAL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, coin TEXT, condition TEXT,
            target_price REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, triggered INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sent_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, signal_type TEXT, stars INTEGER,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, date_text TEXT DEFAULT (date('now')),
            UNIQUE(coin, signal_type, date_text)
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    return sqlite3.connect(DB_PATH)


# ===== SIGNAL FORMATTING =====

def format_signal(s):
    stars_text = SIGNAL_STARS.get(s.get("stars", 3), "⭐")
    emoji = "🟢" if s["signal"] == "BUY" else "🔴"
    trend_emoji = "📈" if s.get("trend") == "BULLISH" else "📉"
    coin = s["coin"]
    sig = s["signal"]
    entry = s["entry"]
    sl = s["stop_loss"]
    tp1 = s["take_profit_1"]
    tp2 = s["take_profit_2"]
    tp3 = s["take_profit_3"]
    rr = s["risk_reward"]
    trend = s.get("trend", "N/A")
    ema_fast = s.get("ema_fast", "N/A")
    ema_slow = s.get("ema_slow", "N/A")
    ema_trend = s.get("ema_trend", "N/A")
    rsi = s["rsi"]
    ch24 = s["change_24h"]
    parts = []
    parts.append("")
    parts.append(emoji + " <b>" + coin + " -- " + sig + "</b> " + stars_text)
    parts.append("💰 Entry: $" + f"{entry:,.2f}")
    parts.append("🛑 SL: $" + f"{sl:,.2f}")
    parts.append("🎯 TP1: $" + f"{tp1:,.2f}")
    parts.append("🎯 TP2: $" + f"{tp2:,.2f}")
    parts.append("🎯 TP3: $" + f"{tp3:,.2f}")
    parts.append("📊 R:R 1:" + str(rr))
    parts.append(trend_emoji + " Trend (4h): " + trend)
    parts.append("📈 EMA" + str(ema_fast) + "(1h): " + str(ema_fast) + " | EMA" + str(ema_slow) + "(1h): " + str(ema_slow))
    parts.append("📊 EMA" + str(ema_trend) + "(4h): " + str(ema_trend))
    parts.append("RSI: " + str(rsi) + " | 24h: " + str(ch24) + "%")
    return "\n".join(parts)


# ===== KEYBOARD MENU =====

def get_main_menu():
    return {
        "keyboard": [
            [{"text": "📈 Signal"}, {"text": "📊 Overview"}],
            [{"text": "🔍 Scanner"}, {"text": "🔝 Top"}],
            [{"text": "🔔 Alerts"}, {"text": "📈 Stats"}],
            [{"text": "💰 Paper Trading"}, {"text": "📚 Help"}]
        ],
        "resize_keyboard": True
    }


# ===== COMMAND HANDLERS =====

def handle_start(chat_id, user):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, chat_id)
        VALUES (?, ?, ?, ?, ?)
    """, (user["id"], user.get("username"), user.get("first_name"), user.get("last_name"), chat_id))
    conn.commit()
    conn.close()
    fname = user.get("first_name", "friend")
    msg = "👋 Hello, " + fname + "!\n\n🤖 Crypto Signal Bot v11.3\nMulti-Timeframe EMA + Paper Trading\n4h Trend + 1h EMA Crossover\n\nChoose action 👇"
    send_message(chat_id, msg)
    send_message(chat_id, "Menu:", reply_markup=get_main_menu())


def handle_signal(chat_id, args):
    if not args:
        send_message(chat_id, "❌ Specify coin: /signal BTC")
        return
    coin = args[0].lower()
    send_message(chat_id, "🔍 Analyzing " + coin.upper() + "...")
    signal = analyze_coin(coin)
    if signal:
        send_message(chat_id, format_signal(signal))
    else:
        send_message(chat_id, "⚠️ No signal")


def handle_scanner(chat_id):
    send_message(chat_id, "🔍 Scanning market...")
    coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    found = []
    for symbol in coins:
        coin_id = symbol.replace("USDT", "").lower()
        s = analyze_coin(coin_id)
        if s and s["stars"] >= 3:
            found.append(s)
        time.sleep(1)
    if not found:
        send_message(chat_id, "📊 No strong signals (3+⭐) right now.")
        return
    msg = "🎯 <b>SIGNALS</b>\n\n"
    for s in found[:3]:
        c = s["coin"]
        e = "🟢" if s["signal"] == "BUY" else "🔴"
        st = "⭐" * s["stars"]
        rr = s["risk_reward"]
        tr = s.get("trend", "N/A")
        msg += c + " " + e + " " + st + " | R:R 1:" + str(rr) + " | Trend: " + tr + "\n"
    send_message(chat_id, msg)


def handle_top(chat_id):
    coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    msg = "🔝 <b>TOP COINS</b>\n\n"
    for symbol in coins:
        price = get_price(symbol.replace("USDT", ""))
        from binance_api import get_ohlcv, _calculate_change_24h
        ohlcv = get_ohlcv(symbol.replace("USDT", ""), "hour", 30)
        ch24 = _calculate_change_24h(ohlcv) if ohlcv else 0
        if price:
            e = "🟢" if ch24 >= 0 else "🔴"
            msg += "<b>" + symbol.replace("USDT", "") + "</b> $" + f"{price:,.2f}" + " " + e + " " + f"{ch24:+.2f}" + "%\n"
    send_message(chat_id, msg)


def handle_alert(chat_id, user_id, args):
    if len(args) < 3:
        send_message(chat_id, "❌ Format: /alert BTC above 70000")
        return
    coin = args[0].upper()
    condition = args[1].lower()
    try:
        target = float(args[2])
    except ValueError:
        send_message(chat_id, "❌ Price must be a number")
        return
    if condition not in ["above", "below"]:
        send_message(chat_id, "❌ Condition: above or below")
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO alerts (user_id, coin, condition, target_price) VALUES (?, ?, ?, ?)",
              (user_id, coin, condition, target))
    conn.commit()
    conn.close()
    emoji = "⬆️" if condition == "above" else "⬇️"
    send_message(chat_id, "🔔 <b>Alert set!</b>\n\n" + coin + " " + emoji + " " + condition + " $" + f"{target:,.2f}")


def handle_alerts(chat_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coin, condition, target_price, triggered FROM alerts WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    alerts = c.fetchall()
    conn.close()
    if not alerts:
        send_message(chat_id, "🔕 No active alerts.")
        return
    message = "🔔 <b>YOUR ALERTS</b>\n\n"
    for coin, condition, price, triggered in alerts:
        status = "✅" if triggered else "⏳"
        emoji = "⬆️" if condition == "above" else "⬇️"
        message += status + " " + coin + " " + emoji + " " + condition + " $" + f"{price:,.2f}" + "\n"
    send_message(chat_id, message)


# ===== PAPER TRADING COMMANDS =====

def handle_paper_toggle(chat_id):
    new_state = paper_db.toggle_paper()
    status = "✅ ON" if new_state else "❌ OFF"
    balance = paper_db.get_balance()
    msg = f"📊 <b>Paper Trading</b>: {status}\n\n💵 Balance: <code>${balance:,.2f}</code>\n\n<i>When OFF, bot sends REAL orders to Kraken!</i>"
    send_message(chat_id, msg)


def handle_portfolio(chat_id):
    msg = paper_engine.format_portfolio_message()
    send_message(chat_id, msg)


def handle_stats(chat_id):
    msg = paper_engine.format_stats_message()
    send_message(chat_id, msg)

    send_message(chat_id, "📈 Generating chart...")
    try:
        png_bytes = chart_gen.generate_equity_chart(30)
        send_photo_bytes(chat_id, png_bytes, caption="📊 <b>Equity Curve</b> — 30 days", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Chart error: {e}")
        send_message(chat_id, "❌ Chart error")


def handle_chart(chat_id, args):
    try:
        days = int(args[0]) if args else 30
        days = min(max(days, 1), 365)
    except:
        days = 30

    send_message(chat_id, f"📈 Generating chart ({days}d)...")
    try:
        png_bytes = chart_gen.generate_equity_chart(days)
        send_photo_bytes(chat_id, png_bytes, caption=f"📊 Equity Curve — {days} days", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Chart error: {e}")


def handle_history(chat_id, args):
    try:
        limit = int(args[0]) if args else 10
        limit = min(max(limit, 1), 50)
    except:
        limit = 10
    msg = paper_engine.format_history_message(limit)
    send_message(chat_id, msg)


def handle_fullreport(chat_id):
    send_message(chat_id, paper_engine.format_portfolio_message())
    send_message(chat_id, paper_engine.format_stats_message())
    try:
        png_bytes = chart_gen.generate_equity_chart(30)
        send_photo_bytes(chat_id, png_bytes, caption="📈 Equity Curve")
    except:
        pass
    send_message(chat_id, paper_engine.format_history_message(5))


def handle_reset(chat_id):
    paper_db.reset(10000.0)
    send_message(chat_id, "🔄 <b>Paper Trading Reset</b>\n\nBalance: <code>$10,000.00</code>\nHistory cleared.")


# ===== BACKTEST COMMANDS =====

def handle_backtest(chat_id, args):
    symbol = args[0].upper() if args else "BTC"
    months = 6
    if len(args) > 1:
        try:
            months = int(args[1])
        except:
            pass

    send_message(chat_id, f"🔬 <b>Backtest</b> {symbol}/USD {months}mo...\n<i>Running in background...</i>")

    def backtest_task():
        try:
            result = run_backtest_command(symbol, months)
            send_message(chat_id, result)
        except Exception as e:
            send_message(chat_id, f"❌ Backtest error: {str(e)}")

    threading.Thread(target=backtest_task, daemon=True).start()


def handle_optimize(chat_id, args):
    symbol = args[0].upper() if args else "BTC"
    send_message(chat_id, f"🔬 <b>Optimizing</b> {symbol}...\n<i>Background task...</i>")

    def optimize_task():
        try:
            engine = BacktestEngine(symbol=symbol, initial_balance=10000.0)
            param_grid = {"ema_fast": [5, 8, 13], "ema_slow": [30, 50, 100], "sl_multiplier": [2.0, 2.5, 3.0]}
            results = engine.optimize_params(param_grid)

            msg = "🔬 <b>OPTIMIZATION RESULTS</b>\n\nTop 3:\n\n"
            for i, r in enumerate(results[:3], 1):
                emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
                msg += f"{emoji} #{i} — Return: <code>{r.total_return_pct:+.2f}%</code>\n"
                msg += f"   EMA{r.params['ema_fast']}/{r.params['ema_slow']}, SL: {r.params['sl_multiplier']}x ATR\n"
                msg += f"   Winrate: {r.winrate:.1f}%, PF: {r.profit_factor:.2f}, Sharpe: {r.sharpe_ratio:.2f}\n\n"
            send_message(chat_id, msg)
        except Exception as e:
            send_message(chat_id, f"❌ Optimize error: {str(e)}")

    threading.Thread(target=optimize_task, daemon=True).start()


def handle_autooptimize(chat_id, args):
    symbol = args[0].upper() if args else "BTC"
    months = 6
    if len(args) > 1:
        try:
            months = int(args[1])
        except:
            pass

    send_message(chat_id, f"🤖 <b>Auto-Optimize</b> {symbol} {months}mo...\n<i>Background...</i>")

    def auto_task():
        try:
            opt = AutoOptimizer(symbol=symbol)
            result = opt.optimize(months=months, auto_select=True)

            if result:
                msg = (f"✅ <b>Optimization Complete!</b>\n\n"
                       f"🎯 EMA{result.ema_fast}/{result.ema_slow} (Trend: EMA{result.ema_trend})\n\n"
                       f"📊 Return: <code>{result.total_return_pct:+.2f}%</code>\n"
                       f"Winrate: <code>{result.winrate:.1f}%</code>\n"
                       f"PF: <code>{result.profit_factor:.2f}</code> | Sharpe: <code>{result.sharpe_ratio:.2f}</code>\n"
                       f"Max DD: <code>{result.max_drawdown_pct:.2f}%</code>\n\n"
                       f"⚙️ Saved! Use /params to view.")
                send_message(chat_id, msg)
                # Сбрасываем кэш параметров чтобы бот использовал новые
                reset_strategy_params()
            else:
                send_message(chat_id, "⚠️ No valid strategy found. Using defaults.")
        except Exception as e:
            send_message(chat_id, f"❌ Auto-optimize error: {str(e)}")

    threading.Thread(target=auto_task, daemon=True).start()


def handle_params(chat_id):
    msg = optimizer.format_params_message()
    send_message(chat_id, msg)


def handle_useoptimized(chat_id):
    active = optimizer.get_active_params()
    if active:
        params = active["params"]
        reset_strategy_params()
        send_message(chat_id, f"✅ <b>Using Optimized</b>\n\nEMA{params['ema_fast']}/{params['ema_slow']}\nReturn: <code>{active['metrics']['total_return_pct']:+.2f}%</code>")
    else:
        send_message(chat_id, "⚠️ No optimized params. Run /autooptimize first.")


def handle_old_stats(chat_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM signals")
    total = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM signals WHERE signal_type = "BUY"')
    buys = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM signals WHERE signal_type = "SELL"')
    sells = c.fetchone()[0]
    c.execute("SELECT AVG(stars) FROM signals")
    avg_stars = c.fetchone()[0] or 0
    c.execute("SELECT coin, signal_type, entry, risk_reward, stars, sent_at FROM signals ORDER BY sent_at DESC LIMIT 5")
    recent = c.fetchall()
    conn.close()
    lines = [
        "📈 <b>SIGNAL STATISTICS</b>",
        " ",
        "Total signals: " + str(total),
        "🟢 BUY: " + str(buys) + " | 🔴 SELL: " + str(sells),
        "⭐ Avg strength: " + f"{avg_stars:.1f}",
        " ",
        "<b>Recent signals:</b>",
    ]
    for coin, sig_type, entry, rr, stars, date in recent:
        emoji = "🟢" if sig_type == "BUY" else "🔴"
        star_str = "⭐" * stars
        lines.append(emoji + " " + coin + " " + star_str + " | R:R 1:" + str(rr) + " | $" + f"{entry:,.2f}")
    send_message(chat_id, "\n".join(lines))


def handle_help(chat_id):
    text = (
        "📚 <b>HELP v11.3</b>\n\n"
        "<b>Trading:</b>\n"
        "/start — start\n"
        "/signal BTC — signal for coin\n"
        "/scanner — market scanner\n"
        "/top — top coins\n"
        "/alert BTC above 70000 — price alert\n"
        "/alerts — my alerts\n\n"
        "<b>Paper Trading:</b>\n"
        "/paper — toggle ON/OFF\n"
        "/portfolio — open positions\n"
        "/stats — stats + equity chart\n"
        "/chart [days] — equity curve\n"
        "/history [N] — trade history\n"
        "/fullreport — complete report\n"
        "/reset — reset paper trading\n\n"
        "<b>Backtest & Optimize:</b>\n"
        "/backtest BTC 6 — backtest 6 months\n"
        "/optimize BTC — optimize params\n"
        "/autooptimize BTC 6 — auto-optimize\n"
        "/params — current strategy params\n"
        "/useoptimized — use optimized params\n\n"
        "<b>Strategy:</b>\n"
        "📈 4h EMA — trend filter\n"
        "📊 1h EMA Cross — entry signal\n"
        "🎯 ATR-based stop-loss\n"
        "📈 TP: 1.5x, 3x, 5x risk\n"
        "💰 Paper trading enabled by default"
    )
    send_message(chat_id, text)


# ===== ANALYSIS =====

def analyze_coin(coin_id="bitcoin"):
    from binance_api import SYMBOL_MAP
    symbol = SYMBOL_MAP.get(coin_id.lower(), coin_id.upper())
    return analyze_binance_signal(symbol + "USDT", "1h")


# ===== UPDATE PROCESSOR =====

def process_update(update):
    try:
        if "message" not in update:
            return
        message = update["message"]
        chat_id = message["chat"]["id"]
        user = message.get("from", {})
        text = message.get("text", "")
        user_id = user.get("id")

        if not text:
            return

        handled = False

        if text.startswith("/start"):
            handle_start(chat_id, user); handled = True
        elif text.startswith("/signal"):
            handle_signal(chat_id, text.split()[1:]); handled = True
        elif text.startswith("/scanner"):
            handle_scanner(chat_id); handled = True
        elif text.startswith("/top"):
            handle_top(chat_id); handled = True
        elif text.startswith("/alert"):
            handle_alert(chat_id, user_id, text.split()[1:]); handled = True
        elif text.startswith("/alerts"):
            handle_alerts(chat_id, user_id); handled = True
        elif text.startswith("/stats"):
            handle_stats(chat_id); handled = True
        elif text.startswith("/paper"):
            handle_paper_toggle(chat_id); handled = True
        elif text.startswith("/portfolio"):
            handle_portfolio(chat_id); handled = True
        elif text.startswith("/chart"):
            handle_chart(chat_id, text.split()[1:]); handled = True
        elif text.startswith("/history"):
            handle_history(chat_id, text.split()[1:]); handled = True
        elif text.startswith("/fullreport"):
            handle_fullreport(chat_id); handled = True
        elif text.startswith("/reset"):
            handle_reset(chat_id); handled = True
        elif text.startswith("/backtest"):
            handle_backtest(chat_id, text.split()[1:]); handled = True
        elif text.startswith("/optimize"):
            handle_optimize(chat_id, text.split()[1:]); handled = True
        elif text.startswith("/autooptimize"):
            handle_autooptimize(chat_id, text.split()[1:]); handled = True
        elif text.startswith("/params"):
            handle_params(chat_id); handled = True
        elif text.startswith("/useoptimized"):
            handle_useoptimized(chat_id); handled = True
        elif text.startswith("/help"):
            handle_help(chat_id); handled = True
        elif "Signal" in text:
            send_message(chat_id, "Enter command: /signal BTC"); handled = True
        elif "Scanner" in text or "Overview" in text:
            handle_scanner(chat_id); handled = True
        elif "Top" in text:
            handle_top(chat_id); handled = True
        elif "Alerts" in text:
            handle_alerts(chat_id, user_id); handled = True
        elif "Stats" in text and "Paper" not in text:
            handle_old_stats(chat_id); handled = True
        elif "Paper Trading" in text:
            handle_paper_toggle(chat_id); handled = True
        elif "Help" in text:
            handle_help(chat_id); handled = True

        if not handled:
            send_message(chat_id, "❓ Unknown command. Use menu or /help")
    except Exception as e:
        logger.error(f"process_update error: {e}")


# ===== ALERTS & AUTO-SCANNER =====

def check_alerts():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, user_id, coin, condition, target_price FROM alerts WHERE triggered = 0")
        alerts = c.fetchall()
        for alert_id, user_id, coin, condition, target in alerts:
            try:
                price = get_price(coin)
                if price is None:
                    continue
                triggered = False
                if condition == "above" and price >= target:
                    triggered = True
                elif condition == "below" and price <= target:
                    triggered = True
                if triggered:
                    c.execute("UPDATE alerts SET triggered = 1 WHERE id = ?", (alert_id,))
                    conn.commit()
                    emoji = "🚀" if condition == "above" else "📉"
                    send_message(user_id, "🔔 <b>ALERT!</b>\n\n" + emoji + " " + coin + " " + condition + " $" + f"{target:,.2f}" + "\nCurrent: $" + f"{price:,.2f}")
            except Exception as e:
                logger.error(f"Alert check error: {e}")
        conn.close()
    except Exception as e:
        logger.error(f"Alerts DB error: {e}")


def should_send_signal(coin, signal_type, stars):
    try:
        conn = get_db()
        c = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT 1 FROM sent_signals WHERE coin = ? AND signal_type = ? AND date_text = ?", (coin, signal_type, today))
        was_sent = c.fetchone() is not None
        if stars >= 4:
            c.execute("INSERT OR REPLACE INTO sent_signals (coin, signal_type, stars, sent_at, date_text) VALUES (?, ?, ?, ?, ?)",
                      (coin, signal_type, stars, datetime.now(), today))
            conn.commit(); conn.close(); return True
        if not was_sent:
            c.execute("INSERT INTO sent_signals (coin, signal_type, stars, sent_at, date_text) VALUES (?, ?, ?, ?, ?)",
                      (coin, signal_type, stars, datetime.now(), today))
            conn.commit(); conn.close(); return True
        conn.close(); return False
    except Exception as e:
        logger.error(f"should_send_signal error: {e}")
        return False


def send_signal_to_users(signal):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT chat_id, min_stars FROM users WHERE auto_signals = 1")
        users = c.fetchall()
        conn.close()
        msg = format_signal(signal)
        for chat_id, min_stars in users:
            if signal["stars"] >= min_stars:
                try:
                    send_message(chat_id, msg)
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Failed to send to {chat_id}: {e}")
    except Exception as e:
        logger.error(f"send_signal_to_users error: {e}")


# ===== PAPER TRADING INTEGRATION =====

def process_paper_signal(signal):
    if not paper_db.is_paper_enabled():
        return

    symbol = signal["coin"]
    price = signal["entry"]
    side = signal["signal"]

    if side == "BUY":
        open_trades = paper_db.get_open_trades(symbol)
        if open_trades:
            return

        trade = paper_engine.open_position(
            symbol=symbol, side="LONG", price=price,
            strategy="EMA_Cross", timeframe="1h",
            stop_loss=signal["stop_loss"],
            take_profit=signal["take_profit_1"],
            risk_percent=2.0
        )

        if trade:
            logger.info(f"Paper trade opened: {symbol} @ ${price}")

    elif side == "SELL":
        open_trades = paper_db.get_open_trades(symbol)
        for trade in open_trades:
            closed = paper_engine.close_position(trade.id, price, "SIGNAL")
            if closed:
                logger.info(f"Paper trade closed: {symbol} P&L: {closed.pnl:+.2f}")


def check_paper_stops_auto():
    if not paper_db.is_paper_enabled():
        return

    open_trades = paper_db.get_open_trades()
    symbols = set(t.symbol for t in open_trades)

    for symbol in symbols:
        try:
            current_price = get_price(symbol)
            if current_price is None:
                continue
            closed = paper_engine.check_stop_take(symbol, current_price)
            for trade in closed:
                pnl_emoji = "🟢" if trade.pnl > 0 else "🔴"
                reason_emoji = "🛑" if trade.close_reason == "STOP_LOSS" else "🎯"
                logger.info(f"Paper {trade.close_reason}: {trade.symbol} P&L: {trade.pnl:+.2f}")
        except Exception as e:
            logger.error(f"check_paper_stops error: {e}")


# ===== AUTO-SCANNER (lightweight) =====

def auto_scanner():
    logger.info("Auto-scanner started")
    while True:
        try:
            check_alerts()
            check_paper_stops_auto()

            coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            for symbol in coins:
                try:
                    coin_id = symbol.replace("USDT", "").lower()
                    signal = analyze_coin(coin_id)
                    if signal and signal["stars"] >= 2:
                        process_paper_signal(signal)

                        if should_send_signal(signal["coin"], signal["signal"], signal["stars"]):
                            try:
                                conn = get_db()
                                cur = conn.cursor()
                                cur.execute("""
                                    INSERT INTO signals (coin, signal_type, entry, stop_loss, take_profit_1, take_profit_2, take_profit_3, risk_reward, stars, rsi, change_24h)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (signal["coin"], signal["signal"], signal["entry"], signal["stop_loss"],
                                      signal["take_profit_1"], signal["take_profit_2"], signal["take_profit_3"],
                                      signal["risk_reward"], signal["stars"], signal["rsi"], signal["change_24h"]))
                                conn.commit()
                                conn.close()
                            except Exception as e:
                                logger.error(f"DB insert error: {e}")

                            send_signal_to_users(signal)

                            if signal["stars"] == 5 and ADMIN_CHAT_ID:
                                try:
                                    send_message(ADMIN_CHAT_ID, "⚡ <b>POWERFUL 5⭐</b>\n\n" + format_signal(signal))
                                except:
                                    pass
                            time.sleep(1)
                    time.sleep(SIGNAL_CONFIG["scan_delay"])
                except Exception as e:
                    logger.error(f"Scan error for {symbol}: {e}")
            logger.info("Scan complete, sleeping 10min")
        except Exception as e:
            logger.error(f"Auto-scanner error: {e}")

        try:
            time.sleep(SIGNAL_CONFIG["scan_interval"])
        except Exception as e:
            logger.error(f"Sleep error: {e}")
            time.sleep(60)


# ===== KEEP-ALIVE =====

def keep_alive():
    """Пингует сам себя чтобы Render не засыпал"""
    url = "https://crypto-signal-bot-zpjy.onrender.com/health"
    while True:
        try:
            requests.get(url, timeout=10)
            logger.info("Keep-alive ping OK")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")
        time.sleep(300)  # 5 минут


# ===== FLASK APP =====

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot v11.3 STABLE — Running!"

@app.route("/health")
def health():
    try:
        return jsonify({
            "status": "ok",
            "version": "11.3",
            "paper_trading": paper_db.is_paper_enabled(),
            "balance": paper_db.get_balance(),
            "time": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/paper-stats")
def paper_stats_api():
    try:
        stats = paper_engine.get_statistics()
        p = stats["portfolio"]
        return jsonify({
            "balance": p.balance,
            "initial_balance": p.initial_balance,
            "total_return_pct": round((p.balance / p.initial_balance - 1) * 100, 2),
            "total_trades": p.total_trades,
            "winrate": round(stats["winrate"], 1),
            "profit_factor": round(stats["profit_factor"], 2),
            "sharpe_ratio": round(stats["sharpe_ratio"], 2),
            "max_drawdown_pct": round(p.max_drawdown_percent, 2),
            "open_positions": p.open_positions,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===== MAIN =====

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    init_db()
    init_market_db()
    test_binance_connection()

    # Удаляем webhook
    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=true", timeout=10)
    except Exception as e:
        logger.error(f"deleteWebhook error: {e}")

    # Запускаем потоки
    threads = []

    # 1. Scanner (daemon — не блокирует выход)
    scanner_thread = threading.Thread(target=auto_scanner, daemon=True)
    scanner_thread.start()
    threads.append(("scanner", scanner_thread))
    logger.info("Scanner thread started")

    # 2. Keep-alive (daemon)
    keepalive_thread = threading.Thread(target=keep_alive, daemon=True)
    keepalive_thread.start()
    threads.append(("keep-alive", keepalive_thread))
    logger.info("Keep-alive thread started")

    # 3. Flask (в основном потоке)
    logger.info("Starting Flask...")
    port = int(os.environ.get("PORT", 10000))

    # 4. Polling (в отдельном потоке)
    def polling_loop():
        logger.info("POLLING started")
        offset = 0
        while True:
            try:
                updates = get_updates(offset=offset)
                if updates:
                    for u in updates:
                        offset = u["update_id"] + 1
                        process_update(u)
                else:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(5)

    polling_thread = threading.Thread(target=polling_loop, daemon=True)
    polling_thread.start()
    threads.append(("polling", polling_thread))
    logger.info("Polling thread started")

    # Flask в основном потоке (Render требует это)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
