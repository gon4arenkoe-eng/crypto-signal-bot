"""
Crypto Signal Bot v7.8 — Только polling, webhook отключён
"""

import os
import json
import time
import logging
import sqlite3
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
import telegram
from telegram import Update, ReplyKeyboardMarkup

# ==================== НАСТРОЙКИ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

# ИГНОРИРУЕМ WEBHOOK_URL даже если есть
WEBHOOK_URL = None  # Принудительно отключаем webhook

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
    url = f"{COINGECKO_BASE}{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 429:
            logger.warning("⚠️ Rate limit")
            time.sleep(60)
            return None
        else:
            logger.error(f"CoinGecko {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"CoinGecko: {e}")
        return None

def get_coin_data(coin_id="bitcoin"):
    return cg_get(f"/coins/{coin_id}", {
        "localization": "false", "tickers": "false", "market_data": "true",
        "community_data": "false", "developer_data": "false", "sparkline": "false"
    })

def get_market_chart(coin_id="bitcoin", days=30):
    return cg_get(f"/coins/{coin_id}/market_chart", {"vs_currency": "usd", "days": str(days)})

def get_top_coins(limit=100):
    data = cg_get("/coins/markets", {
        "vs_currency": "usd", "order": "market_cap_desc",
        "per_page": limit, "page": 1, "sparkline": "false", "price_change_percentage": "24h"
    })
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
    return ReplyKeyboardMarkup([
        ["📈 Сигнал", "📊 Обзор"], ["🔍 Сканер", "🔝 Топ"],
        ["🔔 Алерты", "📈 Статистика"], ["📚 Помощь"]
    ], resize_keyboard=True)

# ==================== ASYNC ====================
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

def send_message_sync(bot, chat_id, text, **kwargs):
    try:
        future = asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=chat_id, text=text, **kwargs),
            _loop
        )
        return future.result(timeout=10)
    except Exception as e:
        logger.error(f"send_message error: {e}")
        return None

# ==================== ОБРАБОТЧИКИ ====================
def handle_start(bot, chat_id, user):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, chat_id)
                 VALUES (?, ?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, user.last_name, chat_id))
    conn.commit()
    conn.close()
    
    send_message_sync(bot, chat_id, f"👋 Привет, {user.first_name}!\n\n🤖 Crypto Signal Bot v7.8\n\nВыбери действие 👇",
                      parse_mode="HTML", reply_markup=get_main_menu())

def handle_signal(bot, chat_id, args):
    if not args:
        send_message_sync(bot, chat_id, "❌ /signal BTC", reply_markup=get_main_menu())
        return
    coin = args[0].lower()
    coin_map = {"btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin", "sol": "solana",
                "xrp": "ripple", "doge": "dogecoin", "ada": "cardano"}
    coin_id = coin_map.get(coin, coin)
    
    send_message_sync(bot, chat_id, f"🔍 Анализ {coin.upper()}...", reply_markup=get_main_menu())
    signal = analyze_coin(coin_id)
    if signal:
        send_message_sync(bot, chat_id, format_signal(signal), parse_mode="HTML", reply_markup=get_main_menu())
    else:
        send_message_sync(bot, chat_id, "⚠️ Нет сигнала", reply_markup=get_main_menu())

def handle_scanner(bot, chat_id):
    send_message_sync(bot, chat_id, "🔍 Сканирую...", reply_markup=get_main_menu())
    coins = get_top_coins(10)
    found = []
    for c in coins[:5]:
        s = analyze_coin(c.get("id"))
        if s and s["stars"] >= 3:
            found.append(s)
        time.sleep(2)
    
    if not found:
        send_message_sync(bot, chat_id, "📊 Нет сигналов", reply_markup=get_main_menu())
        return
    
    msg = "🎯 <b>СИГНАЛЫ</b>\n\n"
    for s in found[:3]:
        msg += f"{'🟢' if s['signal']=='BUY' else '🔴'} <b>{s['coin']}</b> {'⭐'*s['stars']}\n"
    send_message_sync(bot, chat_id, msg, parse_mode="HTML", reply_markup=get_main_menu())

def handle_top(bot, chat_id):
    coins = get_top_coins(10)
    msg = "🔝 <b>ТОП-10</b>\n\n"
    for i, c in enumerate(coins, 1):
        ch = c.get("price_change_percentage_24h", 0) or 0
        msg += f"{i}. <b>{c['symbol'].upper()}</b> ${c['current_price']:,.2f} {'🟢' if ch>=0 else '🔴'} {ch:+.1f}%\n"
    send_message_sync(bot, chat_id, msg, parse_mode="HTML", reply_markup=get_main_menu())

def handle_help(bot, chat_id):
    send_message_sync(bot, chat_id, """📚 <b>ПОМОЩЬ</b>
/start — начать
/signal BTC — сигнал
/scanner — сканер
/top — топ монет""", parse_mode="HTML", reply_markup=get_main_menu())

def process_update(bot, update_json):
    try:
        update = Update.de_json(update_json, bot)
        if not update or not update.message:
            return
        
        chat_id = update.message.chat_id
        user = update.message.from_user
        text = update.message.text or ""
        
        logger.info(f"💬 [{user.id}] {text[:50]}")
        
        if text.startswith("/start"):
            handle_start(bot, chat_id, user)
        elif text.startswith("/signal"):
            handle_signal(bot, chat_id, text.split()[1:])
        elif text.startswith("/scanner"):
            handle_scanner(bot, chat_id)
        elif text.startswith("/top"):
            handle_top(bot, chat_id)
        elif text.startswith("/help"):
            handle_help(bot, chat_id)
        elif "Сигнал" in text:
            send_message_sync(bot, chat_id, "Введи: /signal BTC", reply_markup=get_main_menu())
        elif "Сканер" in text or "Обзор" in text:
            handle_scanner(bot, chat_id)
        elif "Топ" in text:
            handle_top(bot, chat_id)
        elif "Помощь" in text:
            handle_help(bot, chat_id)
            
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ==================== АВТОСКАНЕР ====================
def auto_scanner():
    logger.info("Auto-scanner started")
    bot = telegram.Bot(TOKEN)
    while True:
        try:
            logger.info("Scanning...")
            coins = get_top_coins(10)
            for c in coins[:3]:
                s = analyze_coin(c.get("id"))
                if s and s["stars"] >= 4:
                    logger.info(f"🚨 {s['coin']} {s['signal']} {s['stars']}⭐")
                time.sleep(3)
            logger.info("Scan complete")
        except Exception as e:
            logger.error(f"Scan error: {e}")
        time.sleep(SIGNAL_CONFIG["scan_interval"])

# ==================== FLASK (только для health-check) ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot v7.8 running (polling mode)!"

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "7.8", "mode": "polling"})

# ==================== MAIN — ТОЛЬКО POLLING ====================
def main():
    global bot
    
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    
    init_db()
    bot = telegram.Bot(TOKEN)
    
    # Удаляем webhook принудительно
    logger.info("🔄 Удаляем webhook...")
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=10)
        logger.info(f"deleteWebhook: {r.status_code} {r.text[:100]}")
    except Exception as e:
        logger.error(f"deleteWebhook error: {e}")
    
    # Запускаем сканер
    scanner_thread = threading.Thread(target=auto_scanner, daemon=True)
    scanner_thread.start()
    logger.info("✅ Сканер запущен")
    
    # Запускаем Flask в отдельном потоке (для Render health-check)
    def run_flask():
        port = int(os.environ.get("PORT", 10000))
        app.run(host="0.0.0.0", port=port)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask запущен (health-check)")
    
    # POLLING — основной цикл
    logger.info("🔄 POLLING MODE — запрашиваем сообщения...")
    offset = 0
    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=30)
            if updates:
                logger.info(f"📩 Получено {len(updates)} сообщений")
            for u in updates:
                offset = u.update_id + 1
                process_update(bot, u.to_dict())
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
