from bybit_api import (
    get_klines, get_tickers, get_wallet_balance,
    get_positions, place_order, analyze_bybit_signal,
    test_connection
)
"""
Crypto Signal Bot v8.1 — Исправленная версия (Render-ready)
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

# ==================== НАСТРОЙКИ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

logger.info(f"🔍 TOKEN length: {len(TOKEN)}")

if TOKEN:
    try:
        resp = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ Бот: @{resp.json()['result']['username']}")
    except Exception as e:
        logger.error(f"getMe error: {e}")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
HEADERS = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}

if COINGECKO_API_KEY:
    logger.info(f"✅ CoinGecko API Key: {COINGECKO_API_KEY[:10]}...")

SIGNAL_CONFIG = {
    "stop_loss_pct": 2.5,
    "min_risk_pct": 0.005,
    "atr_multiplier": 2.5,
    "scan_interval": 600,
    "scan_batch_size": 10 if COINGECKO_API_KEY else 3,
    "scan_delay": 2 if COINGECKO_API_KEY else 20,
}

SIGNAL_STARS = {
    5: "⭐⭐⭐⭐⭐ МОЩНЫЙ",
    4: "⭐⭐⭐⭐ Сильный",
    3: "⭐⭐⭐ Хороший",
    2: "⭐⭐ Слабый",
    1: "⭐ Очень слабый"
}

# ==================== БЛОКИРОВКА ДЛЯ API ====================
# Защита от одновременных запросов к CoinGecko (rate limit)
cg_lock = threading.Lock()

# Кэш для get_top_coins (обновляется раз в 5 минут)
top_coins_cache = None
top_coins_cache_time = 0
CACHE_TTL = 300  # 5 минут

# ==================== TELEGRAM HTTP API ====================
TG_API = f"https://api.telegram.org/bot{TOKEN}"

def send_message(chat_id, text, parse_mode="HTML", reply_markup=None):
    """Отправить сообщение через HTTP API"""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        resp = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
            else:
                logger.error(f"TG API error: {data}")
        else:
            logger.error(f"TG HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"send_message error: {e}")
    return None

def get_updates(offset=0, limit=100):
    """Получить обновления (polling)"""
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
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        chat_id INTEGER,
        auto_signals INTEGER DEFAULT 1,
        min_stars INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin TEXT,
        signal_type TEXT,
        entry REAL,
        stop_loss REAL,
        take_profit_1 REAL,
        take_profit_2 REAL,
        take_profit_3 REAL,
        risk_reward REAL,
        stars INTEGER,
        rsi REAL,
        change_24h REAL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        coin TEXT,
        condition TEXT,
        target_price REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        triggered INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sent_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin TEXT,
        signal_type TEXT,
        stars INTEGER,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        date_text TEXT DEFAULT (date('now')),
        UNIQUE(coin, signal_type, date_text)
    )''')

    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

# ==================== COINGECKO API ====================
def cg_get(endpoint, params=None):
    """Запрос к CoinGecko с блокировкой и retry"""
    url = f"{COINGECKO_BASE}{endpoint}"
    with cg_lock:
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                logger.warning("⚠️ Rate limit CoinGecko, жду 60 сек...")
                time.sleep(60)
                return None
            else:
                logger.error(f"CoinGecko {r.status_code}: {r.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"CoinGecko error: {e}")
            return None

def get_coin_data(coin_id="bitcoin"):
    return cg_get(f"/coins/{coin_id}", {
        "localization": "false", "tickers": "false", "market_data": "true",
        "community_data": "false", "developer_data": "false", "sparkline": "false"
    })

def get_market_chart(coin_id="bitcoin", days=30):
    return cg_get(f"/coins/{coin_id}/market_chart", {"vs_currency": "usd", "days": str(days)})

def get_top_coins(limit=100):
    """Получить топ монет с кэшированием"""
    global top_coins_cache, top_coins_cache_time

    now = time.time()
    if top_coins_cache and (now - top_coins_cache_time) < CACHE_TTL:
        logger.info("📦 Использую кэшированный топ")
        return top_coins_cache

    data = cg_get("/coins/markets", {
        "vs_currency": "usd", "order": "market_cap_desc",
        "per_page": limit, "page": 1, "sparkline": "false", "price_change_percentage": "24h"
    })

    if data:
        top_coins_cache = data
        top_coins_cache_time = now

    return data or []

# ==================== ТЕХНИЧЕСКИЙ АНАЛИЗ ====================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    if len(gains) < period:
        return 50
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

def calculate_levels(signal_type, entry, atr, support, resistance):
    min_risk = entry * SIGNAL_CONFIG["min_risk_pct"]
    if signal_type == "BUY":
        sl = min(entry - atr * 2.5, entry * 0.975, support * 0.998)
        if entry - sl < min_risk:
            sl = entry - min_risk
        sl = round(sl, 8)
        risk = entry - sl
        if risk <= 0:
            return None
        return {
            "entry": round(entry, 8), "stop_loss": sl,
            "take_profit_1": round(entry + risk * 1.5, 8),
            "take_profit_2": round(entry + risk * 3.0, 8),
            "take_profit_3": round(entry + risk * 5.0, 8),
            "risk_reward": round(1.5, 2)
        }
    else:
        sl = max(entry + atr * 2.5, entry * 1.025, resistance * 1.002)
        if sl - entry < min_risk:
            sl = entry + min_risk
        sl = round(sl, 8)
        risk = sl - entry
        if risk <= 0:
            return None
        return {
            "entry": round(entry, 8), "stop_loss": sl,
            "take_profit_1": round(entry - risk * 1.5, 8),
            "take_profit_2": round(entry - risk * 3.0, 8),
            "take_profit_3": round(entry - risk * 5.0, 8),
            "risk_reward": round(1.5, 2)
        }

def analyze_coin(coin_id="bitcoin"):
    coin_data = get_coin_data(coin_id)
    if not coin_data:
        return None
    chart = get_market_chart(coin_id, days=30)
    if not chart or "prices" not in chart:
        return None

    prices = [p[1] for p in chart["prices"]]
    if len(prices) < 20:
        return None

    current = prices[-1]
    rsi = calculate_rsi(prices)
    atr = (max(prices[-14:]) - min(prices[-14:])) / 14 or current * 0.02

    recent = prices[-20:]
    support, resistance = min(recent), max(recent)

    signal_type = "NEUTRAL"
    if rsi < 35:
        signal_type = "BUY"
    elif rsi > 65:
        signal_type = "SELL"

    if signal_type == "NEUTRAL":
        return None

    levels = calculate_levels(signal_type, current, atr, support, resistance)
    if not levels:
        return None

    change = coin_data.get("market_data", {}).get("price_change_percentage_24h", 0) or 0

    stars = 3
    if rsi < 20 or rsi > 80:
        stars = 5
    elif rsi < 30 or rsi > 70:
        stars = 4

    return {
        "coin": coin_data.get("symbol", coin_id).upper(),
        "signal": signal_type,
        "entry": levels["entry"],
        "stop_loss": levels["stop_loss"],
        "take_profit_1": levels["take_profit_1"],
        "take_profit_2": levels["take_profit_2"],
        "take_profit_3": levels["take_profit_3"],
        "risk_reward": levels["risk_reward"],
        "stars": stars,
        "rsi": rsi,
        "change_24h": round(change, 2),
        "price": current,
    }

# ==================== ФОРМАТИРОВАНИЕ ====================
def format_signal(s):
    stars_text = SIGNAL_STARS.get(s["stars"], "⭐")
    emoji = "🟢" if s["signal"] == "BUY" else "🔴"
    return f"""
{emoji} <b>{s['coin']} — {s['signal']}</b> {stars_text}
💰 Вход: ${s['entry']:,.8f}
🛑 SL: ${s['stop_loss']:,.8f}
🎯 TP1: ${s['take_profit_1']:,.8f}
📊 R:R 1:{s['risk_reward']}
RSI: {s['rsi']} | 24ч: {s['change_24h']}%"""

def get_main_menu():
    return {
        "keyboard": [
            [{"text": "📈 Сигнал"}, {"text": "📊 Обзор"}],
            [{"text": "🔍 Сканер"}, {"text": "🔝 Топ"}],
            [{"text": "🔔 Алерты"}, {"text": "📈 Статистика"}],
            [{"text": "📚 Помощь"}]
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

    send_message(chat_id, f"👋 Привет, {user.get('first_name', 'друг')}!\n\n🤖 Crypto Signal Bot v8.1\n\nВыбери действие 👇")
    send_message(chat_id, "Меню:", reply_markup=get_main_menu())

def handle_signal(chat_id, args):
    if not args:
        send_message(chat_id, "❌ Укажи монету: /signal BTC")
        return
    coin = args[0].lower()
    coin_map = {"btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin", "sol": "solana",
                "xrp": "ripple", "doge": "dogecoin", "ada": "cardano"}
    coin_id = coin_map.get(coin, coin)

    send_message(chat_id, f"🔍 Анализ {coin.upper()}...")
    signal = analyze_coin(coin_id)
    if signal:
        send_message(chat_id, format_signal(signal))
    else:
        send_message(chat_id, "⚠️ Нет сигнала")

def handle_scanner(chat_id):
    send_message(chat_id, "🔍 Сканирую рынок...")
    coins = get_top_coins(10)
    if not coins:
        send_message(chat_id, "⚠️ Не удалось получить данные. Попробуй позже.")
        return

    found = []
    for c in coins[:5]:
        s = analyze_coin(c.get("id"))
        if s and s["stars"] >= 3:
            found.append(s)
        time.sleep(2)

    if not found:
        send_message(chat_id, "📊 Сейчас нет сильных сигналов (3+⭐). Попробуй позже.")
        return

    msg = "🎯 <b>СИГНАЛЫ</b>\n\n"
    for s in found[:3]:
        msg += f"{s['coin']} {'🟢' if s['signal']=='BUY' else '🔴'} {'⭐'*s['stars']} | R:R 1:{s['risk_reward']}\n"
    send_message(chat_id, msg)

def handle_top(chat_id):
    coins = get_top_coins(10)
    if not coins:
        send_message(chat_id, "⚠️ Не удалось получить данные. Попробуй позже.")
        return

    msg = "🔝 <b>ТОП-10</b>\n\n"
    for i, c in enumerate(coins, 1):
        ch = c.get("price_change_percentage_24h", 0) or 0
        msg += f"{i}. <b>{c['symbol'].upper()}</b> ${c['current_price']:,.2f} {'🟢' if ch>=0 else '🔴'} {ch:+.1f}%\n"
    send_message(chat_id, msg)

def handle_alert(chat_id, user_id, args):
    """Установка алерта: /alert BTC above 70000"""
    if len(args) < 3:
        send_message(chat_id, "❌ Формат: /alert BTC above 70000\nили: /alert ETH below 2000")
        return

    coin = args[0].upper()
    condition = args[1].lower()
    try:
        target = float(args[2])
    except ValueError:
        send_message(chat_id, "❌ Цена должна быть числом")
        return

    if condition not in ["above", "below"]:
        send_message(chat_id, "❌ Условие: above (выше) или below (ниже)")
        return

    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO alerts (user_id, coin, condition, target_price)
                 VALUES (?, ?, ?, ?)''',
              (user_id, coin, condition, target))
    conn.commit()
    conn.close()

    emoji = "⬆️" if condition == "above" else "⬇️"
    send_message(chat_id, f"🔔 <b>Алерт установлен!</b>\n\n{coin} {emoji} {condition} ${target:,.2f}")

def handle_alerts(chat_id, user_id):
    """Показать мои алерты"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT coin, condition, target_price, triggered 
                 FROM alerts WHERE user_id = ? ORDER BY created_at DESC''',
              (user_id,))
    alerts = c.fetchall()
    conn.close()

    if not alerts:
        send_message(chat_id, "🔕 У тебя нет активных алертов.\nУстанови: /alert BTC above 70000")
        return

    message = "🔔 <b>ТВОИ АЛЕРТЫ</b>\n\n"
    for coin, condition, price, triggered in alerts:
        status = "✅" if triggered else "⏳"
        emoji = "⬆️" if condition == "above" else "⬇️"
        message += f"{status} {coin} {emoji} {condition} ${price:,.2f}\n"

    send_message(chat_id, message)

def handle_stats(chat_id):
    """Статистика сигналов"""
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

    c.execute('''SELECT coin, signal_type, entry, risk_reward, stars, sent_at 
                 FROM signals ORDER BY sent_at DESC LIMIT 5''')
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
        message += f"{emoji} {coin} {star_str} | R:R 1:{rr} | ${entry:,.6f}\n"

    send_message(chat_id, message)

def handle_help(chat_id):
    send_message(chat_id, """📚 <b>ПОМОЩЬ</b>

<b>Команды:</b>
/start — начать
/signal BTC — сигнал на монету
/scanner — сканер рынка
/top — топ-10 монет
/alert BTC above 70000 — алерт на цену
/alerts — мои алерты
/stats — статистика сигналов

<b>Меню:</b>
📈 Сигнал — быстрый сигнал
📊 Обзор — обзор рынка
🔍 Сканер — поиск сигналов
🔝 Топ — топ-10 монет
🔔 Алерты — управление алертами
📈 Статистика — история сигналов

<b>Автосканер:</b>
Бот проверяет рынок каждые 10 минут и шлёт сигналы:
• 4-5⭐ — мощные, повторяются
• 1-3⭐ — отправляются один раз""")

def process_update(update):
    try:
        if "message" not in update:
            return

        message = update["message"]
        chat_id = message["chat"]["id"]
        user = message.get("from", {})
        text = message.get("text", "")
        user_id = user.get("id")

        # === ДЕБАГ: логируем всё подробно ===
        logger.info(f"💬 [{user_id}] RAW TEXT: {repr(text)}")

        if not text:
            logger.warning(f"💬 [{user_id}] No text in message!")
            return

        # Проверяем условия по порядку с логированием
        handled = False

        if text.startswith("/start"):
            logger.info(f"💬 [{user_id}] → /start handler")
            handle_start(chat_id, user)
            handled = True
        elif text.startswith("/signal"):
            logger.info(f"💬 [{user_id}] → /signal handler")
            handle_signal(chat_id, text.split()[1:])
            handled = True
        elif text.startswith("/scanner"):
            logger.info(f"💬 [{user_id}] → /scanner handler")
            handle_scanner(chat_id)
            handled = True
        elif text.startswith("/top"):
            logger.info(f"💬 [{user_id}] → /top handler")
            handle_top(chat_id)
            handled = True
        elif text.startswith("/alert"):
            logger.info(f"💬 [{user_id}] → /alert handler")
            handle_alert(chat_id, user_id, text.split()[1:])
            handled = True
        elif text.startswith("/alerts"):
            logger.info(f"💬 [{user_id}] → /alerts handler")
            handle_alerts(chat_id, user_id)
            handled = True
        elif text.startswith("/stats"):
            logger.info(f"💬 [{user_id}] → /stats handler")
            handle_stats(chat_id)
            handled = True
        elif text.startswith("/help"):
            logger.info(f"💬 [{user_id}] → /help handler")
            handle_help(chat_id)
            handled = True
        elif "Сигнал" in text:
            logger.info(f"💬 [{user_id}] → 'Сигнал' menu handler")
            send_message(chat_id, "Введи команду: /signal BTC")
            handled = True
        elif "Сканер" in text or "Обзор" in text:
            logger.info(f"💬 [{user_id}] → 'Сканер/Обзор' menu handler")
            handle_scanner(chat_id)
            handled = True
        elif "Топ" in text:
            logger.info(f"💬 [{user_id}] → 'Топ' menu handler")
            handle_top(chat_id)
            handled = True
        elif "Алерты" in text:
            logger.info(f"💬 [{user_id}] → 'Алерты' menu handler")
            handle_alerts(chat_id, user_id)
            handled = True
        elif "Статистика" in text:
            logger.info(f"💬 [{user_id}] → 'Статистика' menu handler")
            handle_stats(chat_id)
            handled = True
        elif "Помощь" in text:
            logger.info(f"💬 [{user_id}] → 'Помощь' menu handler")
            handle_help(chat_id)
            handled = True

        if not handled:
            logger.warning(f"💬 [{user_id}] → NO HANDLER for: {repr(text)}")
            send_message(chat_id, "❓ Не понял команду. Используй меню или /help")

    except Exception as e:
        logger.error(f"❌ Error in process_update: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            chat_id = update.get("message", {}).get("chat", {}).get("id")
            if chat_id:
                send_message(chat_id, "❌ Произошла ошибка. Попробуй ещё раз.")
        except:
            pass

# ==================== ПРОВЕРКА АЛЕРТОВ ====================
def check_alerts():
    """Проверить и отправить сработавшие алерты"""
    conn = get_db()
    c = conn.cursor()

    c.execute('''SELECT id, user_id, coin, condition, target_price 
                 FROM alerts WHERE triggered = 0''')
    alerts = c.fetchall()

    for alert_id, user_id, coin, condition, target in alerts:
        try:
            data = get_coin_data(coin.lower())
            if not data:
                continue

            price = data.get("market_data", {}).get("current_price", {}).get("usd", 0)

            triggered = False
            if condition == "above" and price >= target:
                triggered = True
            elif condition == "below" and price <= target:
                triggered = True

            if triggered:
                c.execute('UPDATE alerts SET triggered = 1 WHERE id = ?', (alert_id,))
                conn.commit()

                emoji = "🚀" if condition == "above" else "📉"
                send_message(
                    user_id,
                    f"🔔 <b>АЛЕРТ СРАБОТАЛ!</b>\n\n{emoji} {coin} {condition} ${target:,.2f}\nТекущая цена: ${price:,.2f}"
                )
                logger.info(f"🔔 Алерт сработал: {coin} {condition} ${target}")
        except Exception as e:
            logger.error(f"Alert check error: {e}")

    conn.close()

# ==================== АВТОСКАНЕР ====================
def should_send_signal(coin, signal_type, stars):
    """Проверить, нужно ли отправлять сигнал"""
    conn = get_db()
    c = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")

    c.execute('''SELECT 1 FROM sent_signals 
                 WHERE coin = ? AND signal_type = ? AND date_text = ?''',
              (coin, signal_type, today))

    was_sent = c.fetchone() is not None

    if stars >= 4:
        c.execute('''INSERT OR REPLACE INTO sent_signals 
                     (coin, signal_type, stars, sent_at, date_text)
                     VALUES (?, ?, ?, ?, ?)''',
                  (coin, signal_type, stars, datetime.now(), today))
        conn.commit()
        conn.close()
        return True

    if not was_sent:
        c.execute('''INSERT INTO sent_signals 
                     (coin, signal_type, stars, sent_at, date_text)
                     VALUES (?, ?, ?, ?, ?)''',
                  (coin, signal_type, stars, datetime.now(), today))
        conn.commit()
        conn.close()
        return True

    conn.close()
    return False

def send_signal_to_users(signal):
    """Отправить сигнал всем пользователям"""
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
    """Фоновый сканер рынка"""
    logger.info("Auto-scanner started")
    while True:
        try:
            logger.info("🔍 Scanning...")

            # Проверяем алерты
            check_alerts()

            # Сканируем рынок
            coins = get_top_coins(50)
            scanned = 0

            for c in coins[:SIGNAL_CONFIG["scan_batch_size"]]:
                coin_id = c.get("id")
                if not coin_id:
                    continue

                try:
                    signal = analyze_coin(coin_id)
                    if signal and signal["stars"] >= 2:
                        if should_send_signal(signal["coin"], signal["signal"], signal["stars"]):
                            # Сохраняем в БД
                            conn = get_db()
                            cur = conn.cursor()
                            try:
                                cur.execute('''INSERT INTO signals 
                                             (coin, signal_type, entry, stop_loss, take_profit_1, 
                                              take_profit_2, take_profit_3, risk_reward, stars, rsi, change_24h)
                                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                          (signal["coin"], signal["signal"], signal["entry"], 
                                           signal["stop_loss"], signal["take_profit_1"], signal["take_profit_2"],
                                           signal["take_profit_3"], signal["risk_reward"], signal["stars"],
                                           signal["rsi"], signal["change_24h"]))
                                conn.commit()
                            except Exception as e:
                                logger.error(f"DB insert error: {e}")
                            conn.close()

                            # Отправляем пользователям
                            send_signal_to_users(signal)

                            # Админу о мощных сигналах
                            if signal["stars"] == 5 and ADMIN_CHAT_ID:
                                try:
                                    send_message(
                                        ADMIN_CHAT_ID,
                                        f"⚡ <b>МОЩНЫЙ СИГНАЛ 5⭐</b>\n\n{format_signal(signal)}"
                                    )
                                except:
                                    pass

                            time.sleep(1)

                    scanned += 1
                    if scanned < SIGNAL_CONFIG["scan_batch_size"]:
                        time.sleep(SIGNAL_CONFIG["scan_delay"])

                except Exception as e:
                    logger.error(f"Scan error for {coin_id}: {e}")
                    continue

            logger.info(f"✅ Scan complete. Scanned: {scanned}")
        except Exception as e:
            logger.error(f"Auto-scanner error: {e}")

        try:
            time.sleep(SIGNAL_CONFIG["scan_interval"])
        except Exception as e:
            logger.error(f"Sleep error: {e}")
            time.sleep(60)

# ==================== FLASK ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot v8.1 running!"

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "8.1"})

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

# ==================== MAIN ====================
def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    init_db()
    # ====== ТЕСТ BYBIT API ======
    logger.info("🔌 Тестирую подключение к Bybit API...")
    test_connection()
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

    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask запущен")

    # POLLING с обработкой прерываний
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
