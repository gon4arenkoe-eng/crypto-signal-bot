#!/usr/bin/env python3
"""
Crypto Signal Bot v5.0 — Flask + Webhook
Работает на Railway без прямого подключения к api.telegram.org
"""

import os
import logging
import threading
from datetime import datetime
from typing import Optional, List

import requests
from flask import Flask, request, jsonify

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # Railway домен
BINANCE_API = "https://api.binance.com/api/v3"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "MATICUSDT",
    "BNBUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT"
]

SIGNAL_CONFIG = {
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "volume_spike": 2.0,
    "price_change_24h": 5.0,
    "stop_loss_pct": 3.0,
    "take_profit_1": 5.0,
    "take_profit_2": 10.0,
    "take_profit_3": 20.0,
    "risk_reward_min": 2.0,
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
session = requests.Session()


# ============ TELEGRAM API ============

def tg_post(method: str, json_data: dict = None):
    try:
        resp = session.post(f"{TELEGRAM_API}/{method}", json=json_data, timeout=30)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return None


def send_message(chat_id: int, text: str, parse_mode: str = "HTML", reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_post("sendMessage", data)


def edit_message(chat_id: int, message_id: int, text: str, parse_mode: str = "HTML", reply_markup=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return tg_post("editMessageText", data)


def set_webhook(url: str):
    """Установить webhook для Telegram"""
    result = tg_post("setWebhook", {"url": url})
    logger.info(f"Webhook set: {result}")
    return result


def delete_webhook():
    """Удалить webhook"""
    result = tg_post("deleteWebhook")
    logger.info(f"Webhook deleted: {result}")
    return result


# ============ BINANCE API ============

def fetch_binance(endpoint: str, params: dict = None):
    try:
        resp = session.get(f"{BINANCE_API}/{endpoint}", params=params, timeout=15)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"Binance error: {e}")
        return None


def get_ticker_24h(symbol: str):
    return fetch_binance("ticker/24hr", {"symbol": symbol})


def get_klines(symbol: str, interval: str = "1h", limit: int = 200):
    data = fetch_binance("klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return data or []


def get_all_tickers():
    return fetch_binance("ticker/24hr") or []


# ============ ТЕХНИЧЕСКИЙ АНАЛИЗ ============

def calc_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def calc_ema(prices: List[float], period: int) -> List[float]:
    if len(prices) < period:
        return prices
    mult = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append((p - ema[-1]) * mult + ema[-1])
    return ema


def calc_macd(closes: List[float]) -> tuple:
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    min_len = min(len(ema12), len(ema26))
    macd_line = [e12 - e26 for e12, e26 in zip(ema12[-min_len:], ema26[-min_len:])]
    signal_line = calc_ema(macd_line, 9)
    min_len2 = min(len(macd_line), len(signal_line))
    hist = [macd_line[-min_len2 + i] - signal_line[i] for i in range(min_len2)]
    return macd_line[-1], signal_line[-1] if signal_line else 0, hist[-1] if hist else 0


def calc_atr(klines: List[List], period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 4)


def calc_bollinger(closes: List[float], period: int = 20, std_dev: int = 2):
    if len(closes) < period:
        return None, None, None
    recent = closes[-period:]
    sma = sum(recent) / period
    variance = sum((x - sma) ** 2 for x in recent) / period
    std = variance ** 0.5
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return round(upper, 2), round(sma, 2), round(lower, 2)


def calc_support_resistance(klines: List[List], lookback: int = 20):
    recent = klines[-lookback:]
    highs = [float(k[2]) for k in recent]
    lows = [float(k[3]) for k in recent]
    return round(max(highs), 2), round(min(lows), 2)


def calc_volume_spike(volumes: List[float]) -> float:
    if len(volumes) < 20:
        return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return round(volumes[-1] / avg, 2) if avg > 0 else 1.0


# ============ РАСЧЁТ УРОВНЕЙ ============

def calculate_levels(signal: str, entry: float, atr: float, support: float, resistance: float, bb_upper, bb_lower):
    cfg = SIGNAL_CONFIG

    if signal == "BUY":
        sl_atr = entry - (atr * 2.5)
        sl_pct = entry * (1 - cfg["stop_loss_pct"] / 100)
        stop_loss = round(min(sl_atr, sl_pct, support * 0.998), 2)
        risk = entry - stop_loss
        tp1 = round(entry + risk * 1.5, 2)
        tp2 = round(entry + risk * 3.0, 2)
        tp3 = round(entry + risk * 5.0, 2)
        tp1 = min(tp1, resistance * 0.995)
        tp2 = min(tp2, bb_upper if bb_upper else float('inf'))
    elif signal == "SELL":
        sl_atr = entry + (atr * 2.5)
        sl_pct = entry * (1 + cfg["stop_loss_pct"] / 100)
        stop_loss = round(max(sl_atr, sl_pct, resistance * 1.002), 2)
        risk = stop_loss - entry
        tp1 = round(entry - risk * 1.5, 2)
        tp2 = round(entry - risk * 3.0, 2)
        tp3 = round(entry - risk * 5.0, 2)
        tp1 = max(tp1, support * 1.005)
        tp2 = max(tp2, bb_lower if bb_lower else 0)
    else:
        return {}

    rr = round((tp1 - entry) / (entry - stop_loss), 2) if signal == "BUY" else round((entry - tp1) / (stop_loss - entry), 2)

    return {
        "entry": round(entry, 2),
        "stop_loss": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "take_profit_3": tp3,
        "risk_reward_1": rr,
        "risk_amount": round(abs(entry - stop_loss), 2)
    }


# ============ ОСНОВНОЙ АНАЛИЗ ============

def analyze_symbol(symbol: str) -> Optional[dict]:
    ticker = get_ticker_24h(symbol)
    k1h = get_klines(symbol, "1h", 200)
    k4h = get_klines(symbol, "4h", 100)

    if not ticker or not k1h or len(k1h) < 50:
        return None

    closes_1h = [float(k[4]) for k in k1h]
    volumes_1h = [float(k[5]) for k in k1h]
    closes_4h = [float(k[4]) for k in k4h] if k4h else closes_1h[::4]

    rsi_1h = calc_rsi(closes_1h)
    rsi_4h = calc_rsi(closes_4h) if len(closes_4h) > 14 else rsi_1h
    macd_val, signal_val, histogram = calc_macd(closes_1h)
    atr = calc_atr(k1h)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes_1h)
    resistance, support = calc_support_resistance(k1h)
    volume_spike = calc_volume_spike(volumes_1h)

    price = float(ticker["lastPrice"])
    price_change = float(ticker["priceChangePercent"])
    high_24h = float(ticker["highPrice"])
    low_24h = float(ticker["lowPrice"])
    quote_volume = float(ticker["quoteVolume"])

    signal = None
    strength = 0
    reasons = []

    if rsi_1h <= SIGNAL_CONFIG["rsi_oversold"] and rsi_4h <= 40:
        signal = "BUY"
        strength += 2
        reasons.append(f"RSI перепродан ({rsi_1h} 1H / {rsi_4h} 4H)")
    elif rsi_1h >= SIGNAL_CONFIG["rsi_overbought"] and rsi_4h >= 60:
        signal = "SELL"
        strength += 2
        reasons.append(f"RSI перекуплен ({rsi_1h} 1H / {rsi_4h} 4H)")

    if histogram > 0 and macd_val > signal_val:
        if signal != "SELL":
            signal = signal or "BUY"
            strength += 1
            reasons.append("MACD бычий перекрёсток")
    elif histogram < 0 and macd_val < signal_val:
        if signal != "BUY":
            signal = signal or "SELL"
            strength += 1
            reasons.append("MACD медвежий перекрёсток")

    if volume_spike >= SIGNAL_CONFIG["volume_spike"]:
        strength += 1
        reasons.append(f"Всплеск объёма (x{volume_spike})")

    if abs(price_change) >= SIGNAL_CONFIG["price_change_24h"]:
        strength += 1
        reasons.append(f"Сильное движение 24ч ({price_change:+.2f}%)")

    price_pos = (price - low_24h) / (high_24h - low_24h) if high_24h != low_24h else 0.5
    if price_pos < 0.1:
        signal = signal or "BUY"
        strength += 1
        reasons.append(f"Цена у поддержки ({price_pos:.1%} от диапазона)")
    elif price_pos > 0.9:
        signal = signal or "SELL"
        strength += 1
        reasons.append(f"Цена у сопротивления ({price_pos:.1%} от диапазона)")

    if bb_lower and price < bb_lower:
        signal = signal or "BUY"
        strength += 1
        reasons.append("Цена ниже нижней полосы Боллинджера")
    elif bb_upper and price > bb_upper:
        signal = signal or "SELL"
        strength += 1
        reasons.append("Цена выше верхней полосы Боллинджера")

    levels = {}
    if signal in ["BUY", "SELL"]:
        levels = calculate_levels(signal, price, atr, support, resistance, bb_upper, bb_lower)
        if levels.get("risk_reward_1", 0) < SIGNAL_CONFIG["risk_reward_min"]:
            signal = None
            strength = 0
            reasons.append("Отменено: R:R слишком низкое")
            levels = {}

    return {
        "symbol": symbol.replace("USDT", ""),
        "price": price,
        "price_change_24h": price_change,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "quote_volume_24h": quote_volume,
        "rsi_1h": rsi_1h,
        "rsi_4h": rsi_4h,
        "macd": round(macd_val, 4),
        "macd_signal": round(signal_val, 4),
        "macd_histogram": round(histogram, 4),
        "atr": atr,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "support": support,
        "resistance": resistance,
        "volume_spike": volume_spike,
        "signal": signal,
        "signal_strength": strength,
        "reasons": reasons,
        "levels": levels,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }


# ============ ФОРМАТИРОВАНИЕ ============

def format_signal(a: dict) -> str:
    symbol = a["symbol"]
    price = a["price"]
    change = a["price_change_24h"]
    signal = a["signal"]
    strength = a["signal_strength"]
    reasons = a["reasons"]
    levels = a.get("levels", {})

    if signal == "BUY":
        emoji = "🟢"
        signal_text = "📈 СИГНАЛ НА ПОКУПКУ"
    elif signal == "SELL":
        emoji = "🔴"
        signal_text = "📉 СИГНАЛ НА ПРОДАЖУ"
    else:
        emoji = "⚪"
        signal_text = "⏳ НЕЙТРАЛЬНО — НЕТ СИГНАЛА"

    stars = "⭐" * min(strength, 5) + "☆" * max(0, 5 - strength)
    change_emoji = "📈" if change >= 0 else "📉"

    lines = []
    lines.append(f"{emoji} <b>{symbol}/USDT</b> {emoji}")
    lines.append("")
    lines.append(f"<b>{signal_text}</b>")
    lines.append(f"Сила сигнала: {stars}")
    lines.append("")
    lines.append(f"💰 <b>Текущая цена:</b> <code>${price:,.2f}</code>")
    lines.append(f"{change_emoji} <b>Изменение 24ч:</b> <code>{change:+.2f}%</code>")
    lines.append(f"📊 <b>Объём 24ч:</b> <code>${a['quote_volume_24h']:,.0f}</code>")

    if levels:
        entry = levels["entry"]
        sl = levels["stop_loss"]
        tp1 = levels["take_profit_1"]
        tp2 = levels["take_profit_2"]
        tp3 = levels["take_profit_3"]
        rr = levels["risk_reward_1"]
        risk = levels["risk_amount"]

        sl_pct = abs((sl - entry) / entry * 100)
        tp1_pct = abs((tp1 - entry) / entry * 100)
        tp2_pct = abs((tp2 - entry) / entry * 100)
        tp3_pct = abs((tp3 - entry) / entry * 100)

        lines.append("")
        lines.append("╔════════════════════════════════════════╗")
        lines.append("║     📍 УРОВНИ ТОРГОВЛИ                ║")
        lines.append("╠════════════════════════════════════════╣")
        lines.append(f"║  🎯 <b>Вход:</b>          <code>${entry:,.2f}</code>           ║")
        lines.append(f"║  🛑 <b>Стоп-лосс:</b>    <code>${sl:,.2f}</code>  ({sl_pct:.1f}%)   ║")
        lines.append("╠════════════════════════════════════════╣")
        lines.append(f"║  💎 <b>Тейк 1:</b>       <code>${tp1:,.2f}</code>  (+{tp1_pct:.1f}%)  ║")
        lines.append(f"║  💎💎 <b>Тейк 2:</b>     <code>${tp2:,.2f}</code>  (+{tp2_pct:.1f}%)  ║")
        lines.append(f"║  💎💎💎 <b>Тейк 3:</b>   <code>${tp3:,.2f}</code>  (+{tp3_pct:.1f}%)  ║")
        lines.append("╠════════════════════════════════════════╣")
        lines.append(f"║  ⚖️ <b>Risk/Reward:</b>  <code>1:{rr}</code>              ║")
        lines.append(f"║  📏 <b>Риск:</b>         <code>${risk:,.2f}</code>           ║")
        lines.append("╚════════════════════════════════════════╝")
        lines.append("")
        lines.append("<b>📋 План сделки:</b>")
        lines.append(f"  1️⃣ Вход по рынку или лимиткой ~${entry:,.2f}")
        lines.append(f"  2️⃣ Стоп-лосс на ${sl:,.2f} ({sl_pct:.1f}%)")
        lines.append(f"  3️⃣ 50% позиции закрыть на TP1 (${tp1:,.2f})")
        lines.append(f"  4️⃣ 30% позиции закрыть на TP2 (${tp2:,.2f})")
        lines.append(f"  5️⃣ 20% позиции закрыть на TP3 (${tp3:,.2f})")
        lines.append("  6️⃣ Стоп в безубыток после достижения TP1")

    lines.append("")
    lines.append(f"📉 <b>RSI:</b> <code>{a['rsi_1h']} (1H)</code> | <code>{a['rsi_4h']} (4H)</code>")
    lines.append(f"📊 <b>MACD:</b> <code>{a['macd']}</code> | Signal: <code>{a['macd_signal']}</code>")
    lines.append(f"📦 <b>Всплеск объёма:</b> <code>x{a['volume_spike']}</code>")
    lines.append(f"📏 <b>ATR:</b> <code>${a['atr']}</code>")
    lines.append("")
    lines.append("<b>Уровни:</b>")
    lines.append(f"  🔺 Сопротивление: <code>${a['resistance']:,.2f}</code>")
    lines.append(f"  🔻 Поддержка: <code>${a['support']:,.2f}</code>")
    lines.append(f"  ⬆️ BB Upper: <code>${a['bb_upper']:,.2f}</code>")
    lines.append(f"  ⬇️ BB Lower: <code>${a['bb_lower']:,.2f}</code>")
    lines.append("")
    lines.append("🔍 <b>Причины сигнала:</b>")
    for r in reasons:
        lines.append(f"  • {r}")
    lines.append("")
    lines.append(f"⏰ <i>Обновлено: {a['timestamp']}</i>")
    lines.append("")
    lines.append("⚠️ <i>Не финансовый совет. Управляй рисками. DYOR!</i>")

    return "\n".join(lines)


def format_watchlist(analyses: List[dict]) -> str:
    lines = []
    lines.append("📊 <b>ОБЗОР РЫНКА</b>")
    lines.append("")
    lines.append("<code>АКТИВ    ЦЕНА        24Ч%   RSI   СИГНАЛ  R:R</code>")
    lines.append("<code>───────────────────────────────────────────</code>")

    for a in analyses:
        sym = a["symbol"].ljust(6)
        price = f"${a['price']:,.2f}".rjust(10)
        change = f"{a['price_change_24h']:+.1f}%".rjust(6)
        rsi = f"{a['rsi_1h']:.0f}".rjust(5)

        if a["signal"] == "BUY":
            sig = "🟢BUY"
            rr = a.get("levels", {}).get("risk_reward_1", 0)
            rr_str = f"1:{rr}" if rr else "—"
        elif a["signal"] == "SELL":
            sig = "🔴SELL"
            rr = a.get("levels", {}).get("risk_reward_1", 0)
            rr_str = f"1:{rr}" if rr else "—"
        else:
            sig = "⚪—"
            rr_str = "—"

        lines.append(f"<code>{sym} {price} {change} {rsi} {sig.ljust(7)} {rr_str}</code>")

    lines.append(f"⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>")
    return "\n".join(lines)


# ============ ОБРАБОТЧИКИ КОМАНД ============

def cmd_start(chat_id: int):
    welcome = ("🤖 <b>Crypto Signal Bot v5.0</b>\n\n"
        "Я анализирую рынок и даю готовые торговые сигналы с:\n"
        "• 🎯 <b>Точкой входа</b>\n"
        "• 🛑 <b>Стоп-лоссом</b>\n"
        "• 💎 <b>3 тейк-профитами</b>\n"
        "• ⚖️ <b>Соотношением Risk/Reward</b>\n\n"
        "<b>Команды:</b>\n"
        "/signal [монета] — Сигнал с уровнями\n"
        "/signals — Все активные сигналы\n"
        "/watchlist — Обзор рынка\n"
        "/top — Топ движений\n"
        "/scanner — Автосканер сигналов\n"
        "/help — Справка\n\n"
        "⚠️ <i>Не финансовый совет. Управляй рисками!</i>")

    kb = {
        "inline_keyboard": [
            [{"text": "📈 Сигнал BTC", "callback_data": "signal_BTC"}],
            [{"text": "📊 Обзор рынка", "callback_data": "watchlist"}],
            [{"text": "🔍 Сканер", "callback_data": "scanner"}]
        ]
    }
    send_message(chat_id, welcome, reply_markup=kb)


def cmd_help(chat_id: int):
    help_text = ("<b>📚 Справка</b>\n\n"
        "<b>/signal [монета]</b> — Детальный сигнал с уровнями\n"
        "  Пример: /signal BTC, /signal ETH\n\n"
        "<b>/signals</b> — Все активные BUY/SELL сигналы\n\n"
        "<b>/watchlist</b> — Таблица всех пар с R:R\n\n"
        "<b>/top</b> — Топ-10 движений за 24ч\n\n"
        "<b>/scanner</b> — Автоматический поиск сигналов\n\n"
        "<b>Уровни:</b>\n"
        "🎯 Вход — рекомендуемая цена входа\n"
        "🛑 Стоп-лосс — уровень фиксации убытка\n"
        "💎 TP1/TP2/TP3 — цели фиксации прибыли\n"
        "⚖️ R:R — соотношение риска к прибыли\n\n"
        "<b>Правила:</b>\n"
        "• Входить только при R:R >= 2.0\n"
        "• Стоп всегда ставить!\n"
        "• 50% закрывать на TP1, стоп в БУ\n"
        "• Не рисковать >2% депозита на сделку")
    send_message(chat_id, help_text)


def cmd_signal(chat_id: int, args: list):
    symbol = (args[0].upper() + "USDT") if args else "BTCUSDT"

    if symbol not in WATCHLIST and symbol != "BTCUSDT":
        send_message(chat_id, f"❌ {symbol} не в списке. Используй /watchlist")
        return

    msg = send_message(chat_id, f"🔍 Анализ {symbol.replace('USDT','')}/USDT...")

    a = analyze_symbol(symbol)
    if not a:
        edit_message(chat_id, msg["result"]["message_id"], "❌ Не удалось получить данные")
        return

    text = format_signal(a)

    kb = {
        "inline_keyboard": [
            [{"text": "🔄 Обновить", "callback_data": f"signal_{a['symbol']}"}],
            [{"text": "📊 Binance", "url": f"https://www.binance.com/ru/trade/{a['symbol']}_USDT"}]
        ]
    }
    edit_message(chat_id, msg["result"]["message_id"], text, reply_markup=kb)


def cmd_signals(chat_id: int):
    msg = send_message(chat_id, "🔍 Ищу сигналы...")

    analyses = []
    for s in WATCHLIST:
        a = analyze_symbol(s)
        if a and a["signal"] in ["BUY", "SELL"]:
            analyses.append(a)

    analyses.sort(key=lambda x: x["signal_strength"], reverse=True)

    if not analyses:
        edit_message(chat_id, msg["result"]["message_id"], "⚪ Нет сильных сигналов. Рынок в боковике.")
        return

    lines = [f"🎯 <b>АКТИВНЫЕ СИГНАЛЫ ({len(analyses)})</b>", ""]

    for a in analyses[:10]:
        emoji = "🟢" if a["signal"] == "BUY" else "🔴"
        stars = "⭐" * a["signal_strength"]
        levels = a.get("levels", {})
        rr = levels.get("risk_reward_1", 0)
        entry = levels.get("entry", a["price"])
        sl = levels.get("stop_loss", 0)
        tp1 = levels.get("take_profit_1", 0)

        lines.append(f"{emoji} <b>{a['symbol']}</b> {stars} | R:R 1:{rr}")
        lines.append(f"   Вход: ${entry:,.2f} | SL: ${sl:,.2f} | TP1: ${tp1:,.2f}")
        lines.append(f"   RSI: {a['rsi_1h']} | 24ч: {a['price_change_24h']:+.1f}%")
        lines.append("")

    kb = {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "signals_refresh"}]]}
    edit_message(chat_id, msg["result"]["message_id"], "\n".join(lines), reply_markup=kb)


def cmd_watchlist(chat_id: int):
    msg = send_message(chat_id, "📊 Загружаю рынок...")

    analyses = []
    for s in WATCHLIST:
        a = analyze_symbol(s)
        if a:
            analyses.append(a)

    analyses.sort(key=lambda x: x["quote_volume_24h"], reverse=True)

    text = format_watchlist(analyses)

    kb = {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "watchlist"}]]}
    edit_message(chat_id, msg["result"]["message_id"], text, reply_markup=kb)


def cmd_top(chat_id: int):
    msg = send_message(chat_id, "🔝 Ищу топ...")

    tickers = get_all_tickers()
    if not tickers:
        edit_message(chat_id, msg["result"]["message_id"], "❌ Ошибка")
        return

    usdt = [t for t in tickers if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 1e6]
    usdt.sort(key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)

    lines = ["🔝 <b>ТОП-10 ДВИЖЕНИЙ (24ч)</b>", ""]

    for i, t in enumerate(usdt[:10], 1):
        sym = t["symbol"].replace("USDT", "")
        change = float(t["priceChangePercent"])
        price = float(t["lastPrice"])
        vol = float(t["quoteVolume"])
        emoji = "🚀" if change > 10 else "📈" if change > 0 else "💥" if change < -10 else "📉"

        lines.append(f"{i}. {emoji} <b>{sym}</b>")
        lines.append(f"   ${price:,.4f} | {change:+.2f}% | Объём: ${vol/1e6:.1f}M")
        lines.append("")

    edit_message(chat_id, msg["result"]["message_id"], "\n".join(lines))


def cmd_scanner(chat_id: int):
    msg = send_message(chat_id, "🔍 Сканирую рынок на лучшие сигналы...\nЭто может занять 10-15 секунд...")

    results = []
    for s in WATCHLIST:
        a = analyze_symbol(s)
        if a:
            results.append(a)

    good_signals = []
    for r in results:
        if r["signal"] == "BUY":
            levels = r.get("levels", {})
            rr = levels.get("risk_reward_1", 0)
            if rr >= 2.0 and r["signal_strength"] >= 3:
                good_signals.append(r)

    good_signals.sort(key=lambda x: (x.get("levels", {}).get("risk_reward_1", 0), x["signal_strength"]), reverse=True)

    if not good_signals:
        edit_message(chat_id, msg["result"]["message_id"], "⚪ Нет качественных сигналов с R:R >= 2.0. Попробуй позже.")
        return

    lines = [f"🔍 <b>ЛУЧШИЕ СИГНАЛЫ (R:R >= 2.0)</b>", f"Найдено: {len(good_signals)} сигналов", ""]

    for a in good_signals[:5]:
        levels = a["levels"]
        stars = "⭐" * a["signal_strength"]
        lines.append(f"🟢 <b>{a['symbol']}</b> {stars} | R:R 1:{levels['risk_reward_1']}")
        lines.append(f"   🎯 Вход: ${levels['entry']:,.2f}")
        lines.append(f"   🛑 Стоп: ${levels['stop_loss']:,.2f} ({abs((levels['stop_loss']-levels['entry'])/levels['entry']*100):.1f}%)")
        lines.append(f"   💎 TP1: ${levels['take_profit_1']:,.2f} | TP2: ${levels['take_profit_2']:,.2f} | TP3: ${levels['take_profit_3']:,.2f}")
        lines.append(f"   RSI: {a['rsi_1h']} | Объём: x{a['volume_spike']}")
        lines.append("")

    kb = {
        "inline_keyboard": [
            [{"text": "🔄 Пересканировать", "callback_data": "scanner"}],
            [{"text": "📈 Сигнал BTC", "callback_data": "signal_BTC"}]
        ]
    }
    edit_message(chat_id, msg["result"]["message_id"], "\n".join(lines), reply_markup=kb)


def cmd_status(chat_id: int):
    status = (f"🤖 <b>Статус бота v5.0</b>\n\n"
        f"✅ Онлайн\n"
        f"📡 Binance Spot API\n"
        f"📊 Пар: {len(WATCHLIST)}\n\n"
        f"<b>Настройки:</b>\n"
        f"• RSI: {SIGNAL_CONFIG['rsi_oversold']}-{SIGNAL_CONFIG['rsi_overbought']}\n"
        f"• Стоп-лосс: {SIGNAL_CONFIG['stop_loss_pct']}%\n"
        f"• TP1: {SIGNAL_CONFIG['take_profit_1']}%\n"
        f"• TP2: {SIGNAL_CONFIG['take_profit_2']}%\n"
        f"• TP3: {SIGNAL_CONFIG['take_profit_3']}%\n"
        f"• Мин. R:R: 1:{SIGNAL_CONFIG['risk_reward_min']}\n\n"
        f"<i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>")
    send_message(chat_id, status)


def handle_callback(query: dict):
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    data = query["data"]

    tg_post("answerCallbackQuery", {"callback_query_id": query["id"], "text": "Обрабатываю..."})

    if data.startswith("signal_"):
        symbol = data.split("_")[1].upper() + "USDT"
        a = analyze_symbol(symbol)
        if a:
            text = format_signal(a)
            kb = {
                "inline_keyboard": [
                    [{"text": "🔄 Обновить", "callback_data": f"signal_{a['symbol']}"}],
                    [{"text": "📊 Binance", "url": f"https://www.binance.com/ru/trade/{a['symbol']}_USDT"}]
                ]
            }
            edit_message(chat_id, message_id, text, reply_markup=kb)

    elif data == "watchlist":
        analyses = [analyze_symbol(s) for s in WATCHLIST]
        analyses = [r for r in analyses if r]
        analyses.sort(key=lambda x: x["quote_volume_24h"], reverse=True)
        text = format_watchlist(analyses)
        kb = {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "watchlist"}]]}
        edit_message(chat_id, message_id, text, reply_markup=kb)

    elif data == "scanner":
        cmd_scanner(chat_id)

    elif data == "signals_refresh":
        cmd_signals(chat_id)


def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")

    if not text.startswith("/"):
        return

    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == "/start":
        cmd_start(chat_id)
    elif cmd == "/help":
        cmd_help(chat_id)
    elif cmd == "/signal":
        cmd_signal(chat_id, args)
    elif cmd == "/signals":
        cmd_signals(chat_id)
    elif cmd == "/watchlist":
        cmd_watchlist(chat_id)
    elif cmd == "/top":
        cmd_top(chat_id)
    elif cmd == "/scanner":
        cmd_scanner(chat_id)
    elif cmd == "/status":
        cmd_status(chat_id)
    else:
        send_message(chat_id, "❓ Неизвестная команда. Используй /help")


# ============ FLASK ROUTES ============

@app.route('/')
def index():
    return 'Crypto Signal Bot v5.0 is running!'


@app.route('/webhook', methods=['POST'])
def webhook():
    """Получать обновления от Telegram"""
    try:
        update = request.get_json()

        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot": "running"})


# ============ ЗАПУСК ============

def init_webhook():
    """Установить webhook при старте"""
    if WEBHOOK_URL:
        webhook_full_url = f"{WEBHOOK_URL}/webhook"
        # Удаляем старый webhook
        delete_webhook()
        # Устанавливаем новый
        result = set_webhook(webhook_full_url)
        logger.info(f"Webhook URL: {webhook_full_url}")
        logger.info(f"Webhook result: {result}")


if __name__ == "__main__":
    # Устанавливаем webhook в отдельном потоке
    threading.Thread(target=init_webhook, daemon=True).start()

    # Запускаем Flask
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Запуск Crypto Signal Bot v5.0 на порту {port}...")
    app.run(host="0.0.0.0", port=port)
