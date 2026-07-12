"""
Crypto Signal Bot v7.3 — Исправлены async webhook + CoinGecko rate limit
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

# Environment variables
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

# CoinGecko
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
HEADERS = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}

# Настройки сигналов
SIGNAL_CONFIG = {
    "stop_loss_pct": 2.5,
    "min_risk_pct": 0.005,
    "atr_multiplier": 2.5,
    "scan_interval": 600,  # 10 минут
    "scan_batch_size": 5,   # Сканировать 5 монет за раз
    "scan_delay": 12,       # Задержка между монетами (сек) — для rate limit
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
            logger.warning("CoinGecko rate limit hit, waiting...")
            time.sleep(60)  # Ждём минуту при rate limit
            return None
        else:
            logger.error(f"CoinGecko error {r.status_code}: {r.text}")
            return None
    except Exception as e:
        logger.error(f"CoinGecko request failed: {e}")
        return None

def get_coin_data(coin_id="bitcoin"):
    return cg_get(f"/coins/{coin_id}", {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
        "sparkline": "false"
    })

def get_market_chart(coin_id="bitcoin", days=30):
    return cg_get(f"/coins/{coin_id}/market_chart", {
        "vs_currency": "usd",
        "days": str(days)
    })

def get_top_coins(limit=100):
    data = cg_get("/coins/markets", {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": limit,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h"
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
    
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02 if closes else 0.01
    
    tr_list = []
    for i in range(1, len(closes)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i-1])
        tr3 = abs(lows[i] - closes[i-1])
        tr_list.append(max(tr1, tr2, tr3))
    
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else 0.01
    
    return sum(tr_list[-period:]) / period

def calculate_bollinger(prices, period=20, std_dev=2):
    if len(prices) < period:
        return None, None
    
    recent = prices[-period:]
    sma = sum(recent) / period
    variance = sum((p - sma) ** 2 for p in recent) / period
    std = variance ** 0.5
    
    return sma + (std * std_dev), sma - (std * std_dev)

def calculate_levels(signal_type, entry, atr, support, resistance):
    cfg = SIGNAL_CONFIG
    min_risk = entry * cfg["min_risk_pct"]
    
    if signal_type == "BUY":
        sl_atr = entry - (atr * cfg["atr_multiplier"])
        sl_pct = entry * (1 - cfg["stop_loss_pct"] / 100)
        sl_support = support * 0.998
        
        stop_loss = min(sl_atr, sl_pct, sl_support)
        
        if entry - stop_loss < min_risk:
            stop_loss = entry - min_risk
            
        stop_loss = round(stop_loss, 8)
        risk = entry - stop_loss
        
        if risk <= 0:
            return None
            
        tp1 = round(entry + risk * 1.5, 8)
        tp2 = round(entry + risk * 3.0, 8)
        tp3 = round(entry + risk * 5.0, 8)
        rr = round((tp1 - entry) / risk, 2)
        
    else:
        sl_atr = entry + (atr * cfg["atr_multiplier"])
        sl_pct = entry * (1 + cfg["stop_loss_pct"] / 100)
        sl_resistance = resistance * 1.002
        
        stop_loss = max(sl_atr, sl_pct, sl_resistance)
        
        if stop_loss - entry < min_risk:
            stop_loss = entry + min_risk
            
        stop_loss = round(stop_loss, 8)
        risk = stop_loss - entry
        
        if risk <= 0:
            return None
            
        tp1 = round(entry - risk * 1.5, 8)
        tp2 = round(entry - risk * 3.0, 8)
        tp3 = round(entry - risk * 5.0, 8)
        rr = round((entry - tp1) / risk, 2)
    
    return {
        "entry": round(entry, 8),
        "stop_loss": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "risk_reward": rr,
        "risk_amount": round(risk, 8)
    }

def calculate_stars(rsi, rr, trend_strength):
    stars = 1
    if rsi < 20 or rsi > 80:
        stars += 2
    elif rsi < 30 or rsi > 70:
        stars += 1
    
    if rr >= 3.0:
        stars += 1
    elif rr >= 2.0:
        stars += 0.5
    
    if abs(trend_strength) > 5:
        stars += 0.5
    
    return min(int(stars), 5)

def analyze_coin(coin_id="bitcoin"):
    coin_data = get_coin_data(coin_id)
    if not coin_data:
        return None
    
    chart_data = get_market_chart(coin_id, days=30)
    if not chart_data or "prices" not in chart_data:
        return None
    
    prices = [p[1] for p in chart_data["prices"]]
    highs = prices
    lows = prices
    
    if len(prices) < 20:
        return None
    
    current_price = prices[-1]
    market_data = coin_data.get("market_data", {})
    
    rsi = calculate_rsi(prices)
    atr = calculate_atr(highs, lows, prices)
    bb_upper, bb_lower = calculate_bollinger(prices)
    
    recent_prices = prices[-20:]
    support = min(recent_prices)
    resistance = max(recent_prices)
    
    signal_type = "NEUTRAL"
    
    if rsi < 35 and current_price <= (bb_lower or current_price) * 1.02:
        signal_type = "BUY"
    elif rsi > 65 and current_price >= (bb_upper or current_price) * 0.98:
        signal_type = "SELL"
    
    if signal_type == "NEUTRAL":
        return None
    
    levels = calculate_levels(signal_type, current_price, atr, support, resistance)
    if not levels:
        return None
    
    change_24h = market_data.get("price_change_percentage_24h", 0) or 0
    trend_strength = (prices[-1] - prices[-5]) / prices[-5] * 100 if len(prices) >= 5 else 0
    
    stars = calculate_stars(rsi, levels["risk_reward"], trend_strength)
    
    return {
        "coin": coin_data.get("symbol", coin_id).upper(),
        "name": coin_data.get("name", coin_id),
        "signal": signal_type,
        "entry": levels["entry"],
        "stop_loss": levels["stop_loss"],
        "take_profit_1": levels["take_profit_1"],
        "take_profit_2": levels["take_profit_2"],
        "take_profit_3": levels["take_profit_3"],
        "risk_reward": levels["risk_reward"],
        "stars": stars,
        "rsi": rsi,
        "change_24h": round(change_24h, 2),
        "price": current_price,
    }

# ==================== ФОРМАТИРОВАНИЕ ====================
def format_signal(signal):
    stars_text = SIGNAL_STARS.get(signal["stars"], "⭐ Неизвестно")
    emoji = "🟢" if signal["signal"] == "BUY" else "🔴"
    
    return f"""
{emoji} <b>{signal['coin']} — {signal['signal']}</b> {stars_text}
━━━━━━━━━━━━━━━━━━━━━
💰 <b>Вход:</b> ${signal['entry']:,.8f}
🛑 <b>Стоп-лосс:</b> ${signal['stop_loss']:,.8f}
🎯 <b>TP1:</b> ${signal['take_profit_1']:,.8f}
🎯 <b>TP2:</b> ${signal['take_profit_2']:,.8f}
🎯 <b>TP3:</b> ${signal['take_profit_3']:,.8f}
📊 <b>R:R</b> 1:{signal['risk_reward']}

📉 <b>RSI:</b> {signal['rsi']} | 24ч: {signal['change_24h']}%
💵 <b>Цена:</b> ${signal['price']:,.8f}
"""

def get_main_menu():
    keyboard = [
        ["📈 Сигнал", "📊 Обзор рынка"],
        ["🔍 Сканер", "🔝 Топ"],
        ["🔔 Алерты", "📈 Статистика"],
        ["📚 Помощь"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
def handle_start(bot, chat_id, user):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users 
                 (user_id, username, first_name, last_name, chat_id)
                 VALUES (?, ?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, user.last_name, chat_id))
    conn.commit()
    conn.close()
    
    welcome = f"""
👋 <b>Привет, {user.first_name}!</b>

🤖 <b>Crypto Signal Bot v7.3</b>
Автоматические сигналы на основе технического анализа.

📊 <b>Возможности:</b>
• Сигналы BUY/SELL с уровнями
• Автосканер каждые 10 минут
• Алерты на цену
• Статистика сигналов

Выбери действие в меню ниже 👇
"""
    bot.send_message(chat_id=chat_id, text=welcome, parse_mode="HTML", reply_markup=get_main_menu())

def handle_signal(bot, chat_id, args):
    if not args:
        bot.send_message(chat_id=chat_id, text="❌ Укажи монету: /signal BTC\nИли выбери из /top", reply_markup=get_main_menu())
        return
    
    coin = args[0].lower()
    coin_map = {
        "btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin",
        "sol": "solana", "xrp": "ripple", "doge": "dogecoin",
        "ada": "cardano", "avax": "avalanche-2", "dot": "polkadot",
        "matic": "matic-network", "link": "chainlink", "ltc": "litecoin"
    }
    coin_id = coin_map.get(coin, coin)
    
    bot.send_message(chat_id=chat_id, text=f"🔍 Анализирую {coin.upper()}...", reply_markup=get_main_menu())
    
    signal = analyze_coin(coin_id)
    if signal:
        msg = format_signal(signal)
        bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", reply_markup=get_main_menu())
        
        conn = get_db()
        c = conn.cursor()
        try:
            c.execute('''INSERT INTO signals 
                         (coin, signal_type, entry, stop_loss, take_profit_1, 
                          take_profit_2, take_profit_3, risk_reward, stars, rsi, change_24h)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (signal["coin"], signal["signal"], signal["entry"], 
                       signal["stop_loss"], signal["take_profit_1"], signal["take_profit_2"],
                       signal["take_profit_3"], signal["risk_reward"], signal["stars"],
                       signal["rsi"], signal["change_24h"]))
            conn.commit()
        except:
            pass
        conn.close()
    else:
        bot.send_message(chat_id=chat_id, text=f"⚠️ Нет сигнала для {coin.upper()}\nРынок нейтральный.", reply_markup=get_main_menu())

def handle_scanner(bot, chat_id):
    bot.send_message(chat_id=chat_id, text="🔍 Сканирую рынок...", reply_markup=get_main_menu())
    
    top_coins = get_top_coins(50)
    signals_found = []
    
    for coin in top_coins[:20]:
        coin_id = coin.get("id")
        if not coin_id:
            continue
        try:
            signal = analyze_coin(coin_id)
            if signal and signal["stars"] >= 2:
                signals_found.append(signal)
            time.sleep(2)  # Задержка между монетами
        except Exception as e:
            logger.error(f"Error analyzing {coin_id}: {e}")
            continue
    
    signals_found.sort(key=lambda x: x["stars"], reverse=True)
    
    if not signals_found:
        bot.send_message(chat_id=chat_id, text="📊 Нет активных сигналов.\nРынок в боковике.", reply_markup=get_main_menu())
        return
    
    message = f"🎯 <b>АКТИВНЫЕ СИГНАЛЫ</b> ({len(signals_found)})\n\n"
    for sig in signals_found[:5]:
        stars = "⭐" * sig["stars"]
        emoji = "🟢" if sig["signal"] == "BUY" else "🔴"
        message += f"{emoji} <b>{sig['coin']}</b> {stars} | R:R 1:{sig['risk_reward']}\n"
        message += f"   Вход: ${sig['entry']:,.6f} | SL: ${sig['stop_loss']:,.6f} | TP1: ${sig['take_profit_1']:,.6f}\n"
        message += f"   RSI: {sig['rsi']} | 24ч: {sig['change_24h']}%\n\n"
    
    bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML", reply_markup=get_main_menu())

def handle_top(bot, chat_id):
    bot.send_message(chat_id=chat_id, text="📊 Загружаю топ...", reply_markup=get_main_menu())
    
    coins = get_top_coins(10)
    message = "🔝 <b>ТОП-10 КРИПТО</b>\n\n"
    
    for i, coin in enumerate(coins, 1):
        symbol = coin.get("symbol", "?").upper()
        price = coin.get("current_price", 0)
        change = coin.get("price_change_percentage_24h", 0) or 0
        emoji = "🟢" if change >= 0 else "🔴"
        message += f"{i}. <b>{symbol}</b> — ${price:,.2f} {emoji} {change:+.2f}%\n"
    
    bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML", reply_markup=get_main_menu())

def handle_alert(bot, chat_id, user_id, args):
    if len(args) < 3:
        bot.send_message(chat_id=chat_id, text="❌ Формат: /alert BTC above 70000\nили: /alert ETH below 2000", reply_markup=get_main_menu())
        return
    
    coin = args[0].upper()
    condition = args[1].lower()
    try:
        target = float(args[2])
    except ValueError:
        bot.send_message(chat_id=chat_id, text="❌ Цена должна быть числом", reply_markup=get_main_menu())
        return
    
    if condition not in ["above", "below"]:
        bot.send_message(chat_id=chat_id, text="❌ Условие: above (выше) или below (ниже)", reply_markup=get_main_menu())
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO alerts (user_id, coin, condition, target_price)
                 VALUES (?, ?, ?, ?)''',
              (user_id, coin, condition, target))
    conn.commit()
    conn.close()
    
    emoji = "⬆️" if condition == "above" else "⬇️"
    bot.send_message(chat_id=chat_id, text=f"🔔 <b>Алерт установлен!</b>\n\n{coin} {emoji} {condition} ${target:,.2f}", parse_mode="HTML", reply_markup=get_main_menu())

def handle_alerts(bot, chat_id, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT coin, condition, target_price, triggered 
                 FROM alerts WHERE user_id = ? ORDER BY created_at DESC''',
              (user_id,))
    alerts = c.fetchall()
    conn.close()
    
    if not alerts:
        bot.send_message(chat_id=chat_id, text="🔕 У тебя нет активных алертов.\nУстанови: /alert BTC above 70000", reply_markup=get_main_menu())
        return
    
    message = "🔔 <b>ТВОИ АЛЕРТЫ</b>\n\n"
    for coin, condition, price, triggered in alerts:
        status = "✅" if triggered else "⏳"
        emoji = "⬆️" if condition == "above" else "⬇️"
        message += f"{status} {coin} {emoji} {condition} ${price:,.2f}\n"
    
    bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML", reply_markup=get_main_menu())

def handle_stats(bot, chat_id):
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
    
    bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML", reply_markup=get_main_menu())

def handle_help(bot, chat_id):
    help_text = """
📚 <b>ПОМОЩЬ</b>

<b>Команды:</b>
/start — Начать
/signal BTC — Сигнал на монету
/scanner — Сканер рынка
/top — Топ монет
/alert BTC above 70000 — Алерт на цену
/alerts — Мои алерты
/stats — Статистика

<b>Меню:</b>
📈 Сигнал — быстрый сигнал
📊 Обзор — обзор рынка
🔍 Сканер — поиск сигналов
🔝 Топ — топ-10 монет
🔔 Алерты — управление алертами
📈 Статистика — история

<b>Автосканер:</b>
Бот проверяет рынок каждые 10 минут и шлёт сигналы:
• 4-5⭐ — мощные, повторяются
• 1-3⭐ — отправляются один раз
"""
    bot.send_message(chat_id=chat_id, text=help_text, parse_mode="HTML", reply_markup=get_main_menu())

def handle_text_message(bot, chat_id, text, user_id):
    if "Сигнал" in text:
        bot.send_message(chat_id=chat_id, text="Введи: /signal BTC\nили выбери из /top", reply_markup=get_main_menu())
    elif "Обзор" in text or "Сканер" in text:
        handle_scanner(bot, chat_id)
    elif "Топ" in text:
        handle_top(bot, chat_id)
    elif "Алерты" in text:
        handle_alerts(bot, chat_id, user_id)
    elif "Статистика" in text:
        handle_stats(bot, chat_id)
    elif "Помощь" in text:
        handle_help(bot, chat_id)
    else:
        handle_signal(bot, chat_id, [text.strip()])

def process_update(bot, update_json):
    try:
        update = Update.de_json(update_json, bot)
        
        if not update.message:
            return
        
        chat_id = update.message.chat_id
        user = update.message.from_user
        text = update.message.text or ""
        user_id = user.id
        
        if text.startswith("/start"):
            handle_start(bot, chat_id, user)
        elif text.startswith("/signal"):
            args = text.split()[1:]
            handle_signal(bot, chat_id, args)
        elif text.startswith("/scanner"):
            handle_scanner(bot, chat_id)
        elif text.startswith("/top"):
            handle_top(bot, chat_id)
        elif text.startswith("/alert"):
            args = text.split()[1:]
            handle_alert(bot, chat_id, user_id, args)
        elif text.startswith("/alerts"):
            handle_alerts(bot, chat_id, user_id)
        elif text.startswith("/stats"):
            handle_stats(bot, chat_id)
        elif text.startswith("/help"):
            handle_help(bot, chat_id)
        else:
            handle_text_message(bot, chat_id, text, user_id)
            
    except Exception as e:
        logger.error(f"Error processing update: {e}")

# ==================== АВТОСКАНЕР ====================
def should_send_signal(coin, signal_type, stars):
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

def send_signal_to_users(signal, bot):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT chat_id, min_stars FROM users WHERE auto_signals = 1')
    users = c.fetchall()
    conn.close()
    
    msg = format_signal(signal)
    
    for chat_id, min_stars in users:
        if signal["stars"] >= min_stars:
            try:
                bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")

def check_alerts(bot):
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
                bot.send_message(
                    chat_id=user_id,
                    text=f"🔔 <b>АЛЕРТ СРАБОТАЛ!</b>\n\n{emoji} {coin} {condition} ${target:,.2f}\nТекущая цена: ${price:,.2f}",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Alert check error: {e}")
    
    conn.close()

def auto_scanner():
    logger.info("Auto-scanner started")
    bot = telegram.Bot(TOKEN)
    
    while True:
        try:
            logger.info("Running auto-scan...")
            check_alerts(bot)
            
            top_coins = get_top_coins(50)
            scanned = 0
            
            for coin in top_coins[:SIGNAL_CONFIG["scan_batch_size"]]:
                coin_id = coin.get("id")
                if not coin_id:
                    continue
                
                try:
                    signal = analyze_coin(coin_id)
                    if signal and signal["stars"] >= 2:
                        if should_send_signal(signal["coin"], signal["signal"], signal["stars"]):
                            conn = get_db()
                            c = conn.cursor()
                            try:
                                c.execute('''INSERT INTO signals 
                                             (coin, signal_type, entry, stop_loss, take_profit_1, 
                                              take_profit_2, take_profit_3, risk_reward, stars, rsi, change_24h)
                                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                          (signal["coin"], signal["signal"], signal["entry"], 
                                           signal["stop_loss"], signal["take_profit_1"], signal["take_profit_2"],
                                           signal["take_profit_3"], signal["risk_reward"], signal["stars"],
                                           signal["rsi"], signal["change_24h"]))
                                conn.commit()
                            except:
                                pass
                            conn.close()
                            
                            send_signal_to_users(signal, bot)
                            
                            if signal["stars"] == 5 and ADMIN_CHAT_ID:
                                try:
                                    bot.send_message(
                                        chat_id=ADMIN_CHAT_ID,
                                        text=f"⚡ <b>МОЩНЫЙ СИГНАЛ 5⭐</b>\n\n{format_signal(signal)}",
                                        parse_mode="HTML"
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
            
            logger.info(f"Auto-scan completed. Scanned: {scanned}")
        except Exception as e:
            logger.error(f"Auto-scanner error: {e}")
        
        time.sleep(SIGNAL_CONFIG["scan_interval"])

# ==================== FLASK ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Crypto Signal Bot v7.3 is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        json_data = request.get_json(force=True)
        process_update(bot, json_data)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "7.3"})

# ==================== MAIN ====================
def setup_webhook():
    """Настройка webhook через requests вместо async Bot methods"""
    try:
        # Удаляем старый webhook
        requests.get(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook", timeout=10)
        time.sleep(1)
        
        # Устанавливаем новый
        webhook_url = f"{WEBHOOK_URL}/webhook"
        resp = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/setWebhook",
            params={"url": webhook_url},
            timeout=10
        )
        
        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                logger.info(f"✅ Webhook установлен: {webhook_url}")
                return True
            else:
                logger.error(f"Webhook setup failed: {result}")
        else:
            logger.error(f"Webhook HTTP error: {resp.status_code}")
            
    except Exception as e:
        logger.error(f"Webhook setup error: {e}")
    
    return False

def main():
    global bot
    
    # Проверка токена
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    
    logger.info(f"✅ Токен получен: {TOKEN[:10]}...")
    
    # Инициализация
    init_db()
    logger.info("✅ База данных инициализирована")
    
    # Создаём бота
    bot = telegram.Bot(TOKEN)
    
    # Настраиваем webhook через HTTP API (не async)
    setup_webhook()
    
    # Запускаем фоновый сканер
    scanner_thread = threading.Thread(target=auto_scanner, daemon=True)
    scanner_thread.start()
    logger.info("✅ Автосканер запущен")
    
    # Запускаем Flask
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🚀 Запуск Flask на порту {port}")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
