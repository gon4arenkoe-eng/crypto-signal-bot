"""
Crypto Signal Bot v9.0 — Bybit Integration + EMA Crossover Strategy
"""

import os
import json
import time
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests

# ==================== BYBIT API ====================
import hmac
import hashlib

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
BYBIT_MODE = os.environ.get("BYBIT_MODE", "testnet")

BASE_URLS = {
    "testnet": "https://api-testnet.bybit.com",
    "mainnet": "https://api.bybit.com",
    "demo": "https://api-demo.bybit.com"
}
BYBIT_BASE = BASE_URLS.get(BYBIT_MODE, BASE_URLS["testnet"])

logger = logging.getLogger(__name__)


def generate_signature(timestamp, api_key, recv_window, params_str):
    payload = timestamp + api_key + recv_window + params_str
    return hmac.new(
        BYBIT_API_SECRET.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


def get_bybit_headers(method, endpoint, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    if method == "GET" and params:
        params_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    elif method == "POST" and body:
        params_str = json.dumps(body)
    else:
        params_str = ""
    signature = generate_signature(timestamp, BYBIT_API_KEY, recv_window, params_str)
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }


def bybit_get_klines(symbol="BTCUSDT", interval="60", category="linear", limit=200):
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {"category": category, "symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"]
        logger.error(f"Bybit klines error: {data}")
        return []
    except Exception as e:
        logger.error(f"bybit_get_klines error: {e}")
        return []


def bybit_get_tickers(symbol="BTCUSDT", category="linear"):
    url = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": category, "symbol": symbol}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"][0]
        return None
    except Exception as e:
        logger.error(f"bybit_get_tickers error: {e}")
        return None


def bybit_get_wallet_balance(accountType="UNIFIED"):
    if not BYBIT_API_KEY:
        return None
    url = f"{BYBIT_BASE}/v5/account/wallet-balance"
    params = {"accountType": accountType}
    headers = get_bybit_headers("GET", "/v5/account/wallet-balance", params=params)
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"]
        logger.error(f"Bybit balance error: {data}")
        return None
    except Exception as e:
        logger.error(f"bybit_get_wallet_balance error: {e}")
        return None


def bybit_get_server_time():
    url = f"{BYBIT_BASE}/v5/market/time"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return int(data["result"]["timeSecond"])
        return None
    except Exception as e:
        logger.error(f"bybit_get_server_time error: {e}")
        return None


# ==================== НАСТРОЙКИ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

logger.info(f"🔍 TOKEN length: {len(TOKEN)}")
logger.info(f"🔌 Bybit Mode: {BYBIT_MODE}")
logger.info(f"🔌 Bybit URL: {BYBIT_BASE}")
logger.info(f"🔌 Bybit Key: {BYBIT_API_KEY[:10]}..." if BYBIT_API_KEY else "❌ Bybit Key не задан")

if TOKEN:
    try:
        resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ Бот: @{resp.json()['result']['username']}")
    except Exception as e:
        logger.error(f"getMe error: {e}")

SIGNAL_CONFIG = {
    "stop_loss_pct": 2.5,
    "min_risk_pct": 0.005,
    "atr_multiplier": 2.5,
    "scan_interval": 600,
    "scan_batch_size": 10,
    "scan_delay": 2,
}

SIGNAL_STARS = {
    5: "⭐⭐⭐⭐⭐ МОЩНЫЙ",
    4: "⭐⭐⭐⭐ Сильный",
    3: "⭐⭐⭐ Хороший",
    2: "⭐⭐ Слабый",
    1: "⭐ Очень слабый"
}

# ==================== TELEGRAM HTTP API ====================
TG_API = f"https://api.telegram.org/bot{TOKEN}"

def send_message(chat_id, text, parse_mode="HTML", reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
            logger.error(f"TG API error: {data}")
        else:
            logger.error(f"TG HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"send_message error: {e}")
    return None

def get_updates(offset=0, limit=100):
    params = {"offset": offset, "limit": limit, "timeout": 30}
    try:
        resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=35)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result", [])
    except requests.exceptions.ReadTimeout:
        logger.warning("⏱️ getUpdates timeout")
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
    return []

# ==================== БАЗА ДАННЫХ ====================
DB_PATH = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
        chat_id INTEGER, auto_signals INTEGER DEFAULT 1, min_stars INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, signal_type TEXT, entry REAL,
        stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
        risk_reward REAL, stars INTEGER, rsi REAL, change_24h REAL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, coin TEXT, condition TEXT,
        target_price REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, triggered INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sent_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, signal_type TEXT, stars INTEGER,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, date_text TEXT DEFAULT (date('now')),
        UNIQUE(coin, signal_type, date_text)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, signal_type TEXT, entry REAL,
        stop_loss REAL, take_profit REAL, exit_price REAL, pnl_pct REAL,
        status TEXT, opened_at TIMESTAMP, closed_at TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

# ==================== ТЕХНИЧЕСКИЙ АНАЛИЗ (BYBIT) ====================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_ema(prices, period):
    multiplier = 2 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append(price * multiplier + ema[-1] * (1 - multiplier))
    return ema

def analyze_coin(coin_id="bitcoin"):
    """Новая стратегия: EMA8/EMA50 crossover + RSI на Bybit данных"""
    symbol_map = {
        "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT",
        "binancecoin": "BNBUSDT", "ripple": "XRPUSDT", "dogecoin": "DOGEUSDT", "cardano": "ADAUSDT"
    }
    symbol = symbol_map.get(coin_id, coin_id.upper() + "USDT")

    klines = bybit_get_klines(symbol, interval="60", category="linear", limit=100)
    if not klines or len(klines) < 50:
        logger.warning(f"Недостаточно данных для {symbol}")
        return None

    klines_sorted = sorted(klines, key=lambda x: int(x[0]))
    closes = [float(k[4]) for k in klines_sorted]
    highs = [float(k[2]) for k in klines_sorted]
    lows = [float(k[3]) for k in klines_sorted]

    ema8 = calculate_ema(closes, 8)
    ema50 = calculate_ema(closes, 50)
    rsi = calculate_rsi(closes)

    current_close = closes[-1]

    ema_cross_up = (ema8[-2] <= ema50[-2] and ema8[-1] > ema50[-1])
    ema_cross_down = (ema8[-2] >= ema50[-2] and ema8[-1] < ema50[-1])

    recent_highs = highs[-14:]
    recent_lows = lows[-14:]
    atr = (max(recent_highs) - min(recent_lows)) / 14

    support = min(lows[-20:])
    resistance = max(highs[-20:])

    signal = None
    if ema_cross_up:
        signal = "BUY"
    elif ema_cross_down:
        signal = "SELL"

    if not signal:
        return None

    min_risk = current_close * 0.005

    if signal == "BUY":
        sl = min(current_close - atr * 2.5, current_close * 0.975, support * 0.998)
        if current_close - sl < min_risk:
            sl = current_close - min_risk
        risk = current_close - sl
        if risk <= 0:
            return None
        return {
            "coin": symbol.replace("USDT", ""), "signal": signal,
            "entry": round(current_close, 2), "stop_loss": round(sl, 2),
            "take_profit_1": round(current_close + risk * 1.5, 2),
            "take_profit_2": round(current_close + risk * 3.0, 2),
            "take_profit_3": round(current_close + risk * 5.0, 2),
            "risk_reward": 1.5, "stars": 4 if rsi < 40 else 3,
            "rsi": round(rsi, 2), "change_24h": 0,
            "price": current_close, "ema8": round(ema8[-1], 2), "ema50": round(ema50[-1], 2)
        }
    else:
        sl = max(current_close + atr * 2.5, current_close * 1.025, resistance * 1.002)
        if sl - current_close < min_risk:
            sl = current_close + min_risk
        risk = sl - current_close
        if risk <= 0:
            return None
        return {
            "coin": symbol.replace("USDT", ""), "signal": signal,
            "entry": round(current_close, 2), "stop_loss": round(sl, 2),
            "take_profit_1": round(current_close - risk * 1.5, 2),
            "take_profit_2": round(current_close - risk * 3.0, 2),
            "take_profit_3": round(current_close - risk * 5.0, 2),
            "risk_reward": 1.5, "stars": 4 if rsi > 60 else 3,
            "rsi": round(rsi, 2), "change_24h": 0,
            "price": current_close, "ema8": round(ema8[-1], 2), "ema50": round(ema50[-1], 2)
        }

# ==================== ФОРМАТИРОВАНИЕ ====================
def format_signal(s):
    stars_text = SIGNAL_STARS.get(s.get("stars", 3), "⭐")
    emoji = "🟢" if s["signal"] == "BUY" else "🔴"
    ema_info = f"EMA8: {s.get('ema8', 'N/A')} | EMA50: {s.get('ema50', 'N/A')}"
    return f"""
{emoji} <b>{s['coin']} — {s['signal']}</b> {stars_text}
💰 Вход: ${s['entry']:,.2f}
🛑 SL: ${s['stop_loss']:,.2f}
🎯 TP1: ${s['take_profit_1']:,.2f}
📊 R:R 1:{s['risk_reward']}
📈 {ema_info}
RSI: {s['rsi']}"""

def get_main_menu():
    return {
        "keyboard": [
            [{"text": "📈 Сигнал"}, {"text": "📊 Обзор"}],
            [{"text": "🔍 Сканер"}, {"text": "🔝 Топ"}],
            [{"text": "🔔 Алерты"}, {"text": "📈 Статистика"}],
            [{"text": "💼 Баланс"}, {"text": "📚 Помощь"}]
        ],
        "resize_keyboard": True
    }

# ==================== ОБРАБОТЧИКИ ====================
def handle_start(chat_id, user):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, chat_id)
                 VALUES (?, ?, ?, ?, ?)''',
              (user["id"], user.get("username"), user.get("first_name"), user.get("last_name"), chat_id))
    conn.commit()
    conn.close()
    send_message(chat_id, f"👋 Привет, {user.get('first_name', 'друг')}!\n\n🤖 Crypto Signal Bot v9.0\nBybit Integration\n\nВыбери действие 👇")
    send_message(chat_id, "Меню:", reply_markup=get_main_menu())

def handle_signal(chat_id, args):
    if not args:
        send_message(chat_id, "❌ Укажи монету: /signal BTC")
        return
    coin = args[0].lower()
    send_message(chat_id, f"🔍 Анализ {coin.upper()}...")
    signal = analyze_coin(coin)
    if signal:
        send_message(chat_id, format_signal(signal))
    else:
        send_message(chat_id, "⚠️ Нет сигнала")

def handle_scanner(chat_id):
    send_message(chat_id, "🔍 Сканирую рынок...")
    coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    found = []
    for symbol in coins:
        coin_id = symbol.replace("USDT", "").lower()
        s = analyze_coin(coin_id)
        if s and s["stars"] >= 3:
            found.append(s)
        time.sleep(1)
    if not found:
        send_message(chat_id, "📊 Сейчас нет сильных сигналов (3+⭐).")
        return
    msg = "🎯 <b>СИГНАЛЫ</b>\n\n"
    for s in found[:3]:
        msg += f"{s['coin']} {'🟢' if s['signal']=='BUY' else '🔴'} {'⭐'*s['stars']} | R:R 1:{s['risk_reward']}\n"
    send_message(chat_id, msg)

def handle_top(chat_id):
    coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    msg = "🔝 <b>ТОП МОНЕТ</b>\n\n"
    for symbol in coins:
        ticker = bybit_get_tickers(symbol, "linear")
        if ticker:
            price = float(ticker.get("lastPrice", 0))
            ch24 = float(ticker.get("price24hPcnt", 0)) * 100
            msg += f"<b>{symbol.replace('USDT', '')}</b> ${price:,.2f} {'🟢' if ch24>=0 else '🔴'} {ch24:+.2f}%\n"
    send_message(chat_id, msg)

def handle_balance(chat_id):
    if not BYBIT_API_KEY:
        send_message(chat_id, "❌ Bybit API не настроен. Добавь BYBIT_API_KEY в переменные окружения.")
        return
    balance = bybit_get_wallet_balance("UNIFIED")
    if not balance:
        send_message(chat_id, "❌ Не удалось получить баланс. Проверь API ключи.")
        return
    msg = "💼 <b>БАЛАНС BYBIT</b>\n\n"
    for acc in balance:
        msg += f"Account: <b>{acc.get('accountType', 'N/A')}</b>\n"
        for coin in acc.get('coin', []):
            name = coin.get('coin', '')
            bal = float(coin.get('walletBalance', 0))
            if bal > 0:
                msg += f"  {name}: {bal:,.4f}\n"
    send_message(chat_id, msg)

def handle_alert(chat_id, user_id, args):
    if len(args) < 3:
        send_message(chat_id, "❌ Формат: /alert BTC above 70000")
        return
    coin = args[0].upper()
    condition = args[1].lower()
    try:
        target = float(args[2])
    except ValueError:
        send_message(chat_id, "❌ Цена должна быть числом")
        return
    if condition not in ["above", "below"]:
        send_message(chat_id, "❌ Условие: above или below")
        return
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO alerts (user_id, coin, condition, target_price) VALUES (?, ?, ?, ?)''',
              (user_id, coin, condition, target))
    conn.commit()
    conn.close()
    emoji = "⬆️" if condition == "above" else "⬇️"
    send_message(chat_id, f"🔔 <b>Алерт установлен!</b>\n\n{coin} {emoji} {condition} ${target:,.2f}")

def handle_alerts(chat_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT coin, condition, target_price, triggered FROM alerts WHERE user_id = ? ORDER BY created_at DESC''', (user_id,))
    alerts = c.fetchall()
    conn.close()
    if not alerts:
        send_message(chat_id, "🔕 У тебя нет активных алертов.")
        return
    message = "🔔 <b>ТВОИ АЛЕРТЫ</b>\n\n"
    for coin, condition, price, triggered in alerts:
        status = "✅" if triggered else "⏳"
        emoji = "⬆️" if condition == "above" else "⬇️"
        message += f"{status} {coin} {emoji} {condition} ${price:,.2f}\n"
    send_message(chat_id, message)

def handle_stats(chat_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM signals')
    total = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM signals WHERE signal_type = "BUY"')
    buys = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM signals WHERE signal_type = "SELL"')
    sells = c.fetchone()[0]
    c.execute('SELECT AVG(stars) FROM signals')
    avg_stars = c.fetchone()[0] or 0
    c.execute('''SELECT coin, signal_type, entry, risk_reward, stars, sent_at FROM signals ORDER BY sent_at DESC LIMIT 5''')
    recent = c.fetchall()
    conn.close()
    message = f"""
📈 <b>СТАТИСТИКА СИГНАЛОВ</b>

Всего сигналов: {total}
🟢 BUY: {buys} | 🔴 SELL: {sells}
⭐ Средняя сила: {avg_stars:.1f}

<b>Последние сигналы:</b>
"""
    for coin, sig_type, entry, rr, stars, date in recent:
        emoji = "🟢" if sig_type == "BUY" else "🔴"
        star_str = "⭐" * stars
        message += f"{emoji} {coin} {star_str} | R:R 1:{rr} | ${entry:,.2f}\n"
    send_message(chat_id, message)

def handle_help(chat_id):
    send_message(chat_id, """📚 <b>ПОМОЩЬ</b>

<b>Команды:</b>
/start — начать
/signal BTC — сигнал на монету
/scanner — сканер рынка
/top — топ монет
/balance — баланс Bybit
/alert BTC above 70000 — алерт
/alerts — мои алерты
/stats — статистика

<b>Стратегия v9.0:</b>
📈 EMA8/EMA50 Crossover + RSI
📊 Данные с Bybit API
🎯 Таймфрейм: 1 час
""")

def process_update(update):
    try:
        if "message" not in update:
            return
        message = update["message"]
        chat_id = message["chat"]["id"]
        user = message.get("from", {})
        text = message.get("text", "")
        user_id = user.get("id")

        logger.info(f"💬 [{user_id}] RAW TEXT: {repr(text)}")

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
        elif text.startswith("/balance"):
            handle_balance(chat_id); handled = True
        elif text.startswith("/alert"):
            handle_alert(chat_id, user_id, text.split()[1:]); handled = True
        elif text.startswith("/alerts"):
            handle_alerts(chat_id, user_id); handled = True
        elif text.startswith("/stats"):
            handle_stats(chat_id); handled = True
        elif text.startswith("/help"):
            handle_help(chat_id); handled = True
        elif "Сигнал" in text:
            send_message(chat_id, "Введи команду: /signal BTC"); handled = True
        elif "Сканер" in text or "Обзор" in text:
            handle_scanner(chat_id); handled = True
        elif "Топ" in text:
            handle_top(chat_id); handled = True
        elif "Алерты" in text:
            handle_alerts(chat_id, user_id); handled = True
        elif "Статистика" in text:
            handle_stats(chat_id); handled = True
        elif "Баланс" in text:
            handle_balance(chat_id); handled = True
        elif "Помощь" in text:
            handle_help(chat_id); handled = True

        if not handled:
            logger.warning(f"💬 [{user_id}] → NO HANDLER for: {repr(text)}")
            send_message(chat_id, "❓ Не понял команду. Используй меню или /help")
    except Exception as e:
        logger.error(f"❌ Error in process_update: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ==================== ПРОВЕРКА АЛЕРТОВ ====================
def check_alerts():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT id, user_id, coin, condition, target_price FROM alerts WHERE triggered = 0''')
    alerts = c.fetchall()
    for alert_id, user_id, coin, condition, target in alerts:
        try:
            ticker = bybit_get_tickers(coin + "USDT", "linear")
            if not ticker:
                continue
            price = float(ticker.get("lastPrice", 0))
            triggered = False
            if condition == "above" and price >= target:
                triggered = True
            elif condition == "below" and price <= target:
                triggered = True
            if triggered:
                c.execute('UPDATE alerts SET triggered = 1 WHERE id = ?', (alert_id,))
                conn.commit()
                emoji = "🚀" if condition == "above" else "📉"
                send_message(user_id, f"🔔 <b>АЛЕРТ СРАБОТАЛ!</b>\n\n{emoji} {coin} {condition} ${target:,.2f}\nТекущая цена: ${price:,.2f}")
        except Exception as e:
            logger.error(f"Alert check error: {e}")
    conn.close()

# ==================== АВТОСКАНЕР ====================
def should_send_signal(coin, signal_type, stars):
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute('''SELECT 1 FROM sent_signals WHERE coin = ? AND signal_type = ? AND date_text = ?''', (coin, signal_type, today))
    was_sent = c.fetchone() is not None
    if stars >= 4:
        c.execute('''INSERT OR REPLACE INTO sent_signals (coin, signal_type, stars, sent_at, date_text) VALUES (?, ?, ?, ?, ?)''',
                  (coin, signal_type, stars, datetime.now(), today))
        conn.commit(); conn.close(); return True
    if not was_sent:
        c.execute('''INSERT INTO sent_signals (coin, signal_type, stars, sent_at, date_text) VALUES (?, ?, ?, ?, ?)''',
                  (coin, signal_type, stars, datetime.now(), today))
        conn.commit(); conn.close(); return True
    conn.close(); return False

def send_signal_to_users(signal):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT chat_id, min_stars FROM users WHERE auto_signals = 1')
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

def auto_scanner():
    logger.info("Auto-scanner started")
    while True:
        try:
            logger.info("🔍 Scanning...")
            check_alerts()
            coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
            for symbol in coins[:SIGNAL_CONFIG["scan_batch_size"]]:
                try:
                    coin_id = symbol.replace("USDT", "").lower()
                    signal = analyze_coin(coin_id)
                    if signal and signal["stars"] >= 2:
                        if should_send_signal(signal["coin"], signal["signal"], signal["stars"]):
                            conn = get_db()
                            cur = conn.cursor()
                            try:
                                cur.execute('''INSERT INTO signals (coin, signal_type, entry, stop_loss, take_profit_1, take_profit_2, take_profit_3, risk_reward, stars, rsi, change_24h)
                                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                          (signal["coin"], signal["signal"], signal["entry"], signal["stop_loss"], signal["take_profit_1"], signal["take_profit_2"], signal["take_profit_3"], signal["risk_reward"], signal["stars"], signal["rsi"], signal["change_24h"]))
                                conn.commit()
                            except Exception as e:
                                logger.error(f"DB insert error: {e}")
                            conn.close()
                            send_signal_to_users(signal)
                            if signal["stars"] == 5 and ADMIN_CHAT_ID:
                                try:
                                    send_message(ADMIN_CHAT_ID, f"⚡ <b>МОЩНЫЙ СИГНАЛ 5⭐</b>\n\n{format_signal(signal)}")
                                except:
                                    pass
                            time.sleep(1)
                    time.sleep(SIGNAL_CONFIG["scan_delay"])
                except Exception as e:
                    logger.error(f"Scan error for {symbol}: {e}")
            logger.info("✅ Scan complete")
        except Exception as e:
            logger.error(f"Auto-scanner error: {e}")
        try:
            time.sleep(SIGNAL_CONFIG["scan_interval"])
        except Exception as e:
            logger.error(f"Sleep error: {e}")
            time.sleep(60)

# ==================== BYBIT TEST ====================
def test_bybit_connection():
    logger.info("=" * 60)
    logger.info("🔌 ТЕСТ ПОДКЛЮЧЕНИЯ К BYBIT API")
    logger.info("=" * 60)
    logger.info(f"Режим: {BYBIT_MODE}")
    logger.info(f"URL: {BYBIT_BASE}")
    logger.info(f"API Key: {BYBIT_API_KEY[:15]}..." if BYBIT_API_KEY else "❌ Не задан")

    server_time = bybit_get_server_time()
    if server_time:
        logger.info(f"✅ Серверное время: {datetime.fromtimestamp(server_time)}")
    else:
        logger.error("❌ Не удалось получить серверное время")

    klines = bybit_get_klines("BTCUSDT", "60", "linear", 5)
    if klines:
        logger.info(f"✅ Свечи BTC: {len(klines)} получено")
    else:
        logger.error("❌ Не удалось получить свечи")

    ticker = bybit_get_tickers("BTCUSDT", "linear")
    if ticker:
        logger.info(f"✅ BTC цена: ${ticker.get('lastPrice', 'N/A')}")
    else:
        logger.error("❌ Не удалось получить цену")

    if BYBIT_API_KEY:
        balance = bybit_get_wallet_balance()
        if balance:
            logger.info("✅ Баланс получен")
        else:
            logger.error("❌ Не удалось получить баланс")

    signal = analyze_coin("bitcoin")
    if signal:
        logger.info(f"✅ Сигнал: {signal['signal']} {signal['coin']} @ ${signal['entry']}")
    else:
        logger.info("⚠️ Нет сигнала")

    logger.info("=" * 60)

# ==================== FLASK ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot v9.0 Bybit Integration running!"

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "9.0", "bybit_mode": BYBIT_MODE})

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

# ==================== MAIN ====================
def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    init_db()

    # Тест Bybit API
    test_bybit_connection()

    # Удаляем webhook
    logger.info("🔄 Удаляем webhook...")
    try:
        resp = requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=true", timeout=10)
        logger.info(f"deleteWebhook: {resp.status_code}")
    except Exception as e:
        logger.error(f"deleteWebhook error: {e}")

    # Запускаем сканер
    scanner_thread = threading.Thread(target=auto_scanner, daemon=True)
    scanner_thread.start()
    logger.info("✅ Сканер запущен")

    # Запускаем Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask запущен")

    # POLLING
    logger.info("🔄 POLLING MODE")
    offset = 0
    try:
        while True:
            try:
                updates = get_updates(offset=offset)
                if updates:
                    logger.info(f"📩 Получено {len(updates)} сообщений")
                    for u in updates:
                        offset = u["update_id"] + 1
                        process_update(u)
                else:
                    time.sleep(1)
            except requests.exceptions.ReadTimeout:
                logger.warning("⏱️ Polling timeout, retrying...")
                time.sleep(1)
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(5)
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")

if __name__ == "__main__":
    main()
