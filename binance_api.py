"""
Binance API Integration Module
Публичные endpoints — не требуют API ключ
"""

import os
import json
import time
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com"


def _binance_get(endpoint, params=None):
    """Безопасный GET запрос к Binance API"""
    url = f"{BINANCE_BASE}{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            logger.warning("⚠️ Binance rate limit")
            time.sleep(1)
            return None
        else:
            logger.error(f"Binance HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Binance request error: {e}")
        return None


def binance_get_klines(symbol="BTCUSDT", interval="1h", limit=100):
    """
    Получить свечи (kline/candlestick)

    Args:
        symbol: Торговая пара, например "BTCUSDT"
        interval: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
        limit: Количество свечей (макс 1000)

    Returns:
        list: [[open_time, open, high, low, close, volume, close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]]
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    return _binance_get("/api/v3/klines", params)


def binance_get_ticker_24h(symbol="BTCUSDT"):
    """Получить статистику 24ч"""
    params = {"symbol": symbol}
    return _binance_get("/api/v3/ticker/24hr", params)


def binance_get_price(symbol="BTCUSDT"):
    """Получить текущую цену"""
    params = {"symbol": symbol}
    data = _binance_get("/api/v3/ticker/price", params)
    return float(data["price"]) if data else None


def binance_get_orderbook(symbol="BTCUSDT", limit=100):
    """Получить стакан ордеров"""
    params = {"symbol": symbol, "limit": limit}
    return _binance_get("/api/v3/depth", params)


# ==================== ТЕХНИЧЕСКИЙ АНАЛИЗ ====================
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


def analyze_binance_signal(symbol="BTCUSDT", interval="1h"):
    """
    Анализ сигнала на основе Binance данных
    Стратегия: EMA8/EMA50 crossover + RSI
    """
    klines = binance_get_klines(symbol, interval, limit=100)
    if not klines or len(klines) < 50:
        logger.warning(f"Недостаточно данных для {symbol}")
        return None

    # klines: [open_time, open, high, low, close, volume, close_time, ...]
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    ema8 = calculate_ema(closes, 8)
    ema50 = calculate_ema(closes, 50)
    rsi = calculate_rsi(closes)

    current_close = closes[-1]
    prev_close = closes[-2]

    # EMA Crossover сигналы
    ema_cross_up = (ema8[-2] <= ema50[-2] and ema8[-1] > ema50[-1])
    ema_cross_down = (ema8[-2] >= ema50[-2] and ema8[-1] < ema50[-1])

    # ATR
    recent_highs = highs[-14:]
    recent_lows = lows[-14:]
    atr = (max(recent_highs) - min(recent_lows)) / 14

    # Support/Resistance
    support = min(lows[-20:])
    resistance = max(highs[-20:])

    # Объём подтверждение (средний объём за 20 свечей)
    avg_volume = sum(volumes[-20:]) / 20
    current_volume = volumes[-1]
    volume_confirmed = current_volume > avg_volume * 0.8

    signal = None
    if ema_cross_up and volume_confirmed:
        signal = "BUY"
    elif ema_cross_down and volume_confirmed:
        signal = "SELL"

    if not signal:
        return None

    # Получаем изменение за 24ч
    ticker = binance_get_ticker_24h(symbol)
    change_24h = float(ticker.get("priceChangePercent", 0)) if ticker else 0

    min_risk = current_close * 0.005

    if signal == "BUY":
        sl = min(current_close - atr * 2.5, current_close * 0.975, support * 0.998)
        if current_close - sl < min_risk:
            sl = current_close - min_risk
        risk = current_close - sl
        if risk <= 0:
            return None
        return {
            "coin": symbol.replace("USDT", ""),
            "signal": signal,
            "entry": round(current_close, 2),
            "stop_loss": round(sl, 2),
            "take_profit_1": round(current_close + risk * 1.5, 2),
            "take_profit_2": round(current_close + risk * 3.0, 2),
            "take_profit_3": round(current_close + risk * 5.0, 2),
            "risk_reward": 1.5,
            "stars": 5 if rsi < 30 else 4 if rsi < 40 else 3,
            "rsi": round(rsi, 2),
            "change_24h": round(change_24h, 2),
            "price": current_close,
            "ema8": round(ema8[-1], 2),
            "ema50": round(ema50[-1], 2),
            "volume": round(current_volume, 4)
        }
    else:
        sl = max(current_close + atr * 2.5, current_close * 1.025, resistance * 1.002)
        if sl - current_close < min_risk:
            sl = current_close + min_risk
        risk = sl - current_close
        if risk <= 0:
            return None
        return {
            "coin": symbol.replace("USDT", ""),
            "signal": signal,
            "entry": round(current_close, 2),
            "stop_loss": round(sl, 2),
            "take_profit_1": round(current_close - risk * 1.5, 2),
            "take_profit_2": round(current_close - risk * 3.0, 2),
            "take_profit_3": round(current_close - risk * 5.0, 2),
            "risk_reward": 1.5,
            "stars": 5 if rsi > 70 else 4 if rsi > 60 else 3,
            "rsi": round(rsi, 2),
            "change_24h": round(change_24h, 2),
            "price": current_close,
            "ema8": round(ema8[-1], 2),
            "ema50": round(ema50[-1], 2),
            "volume": round(current_volume, 4)
        }


def test_binance_connection():
    """Тест подключения к Binance API"""
    logger.info("=" * 60)
    logger.info("🔌 ТЕСТ ПОДКЛЮЧЕНИЯ К BINANCE API")
    logger.info("=" * 60)

    # 1. Серверное время
    logger.info("1️⃣ Проверка серверного времени...")
    server_time = _binance_get("/api/v3/time")
    if server_time:
        logger.info(f"   ✅ Успех! Время: {datetime.fromtimestamp(server_time['serverTime']/1000)}")
    else:
        logger.error("   ❌ Ошибка")

    # 2. Свечи BTC
    logger.info("2️⃣ Получение свечей BTCUSDT (1ч)...")
    klines = binance_get_klines("BTCUSDT", "1h", 5)
    if klines:
        logger.info(f"   ✅ Получено {len(klines)} свечей")
        logger.info(f"   Последняя: Open=${float(klines[-1][1]):,.2f}, Close=${float(klines[-1][4]):,.2f}")
    else:
        logger.error("   ❌ Ошибка")

    # 3. Текущая цена
    logger.info("3️⃣ Получение текущей цены BTC...")
    price = binance_get_price("BTCUSDT")
    if price:
        logger.info(f"   ✅ BTC: ${price:,.2f}")
    else:
        logger.error("   ❌ Ошибка")

    # 4. Статистика 24ч
    logger.info("4️⃣ Получение статистики 24ч...")
    ticker = binance_get_ticker_24h("BTCUSDT")
    if ticker:
        logger.info(f"   ✅ Изменение 24ч: {float(ticker.get('priceChangePercent', 0)):+.2f}%")
    else:
        logger.error("   ❌ Ошибка")

    # 5. Анализ сигнала
    logger.info("5️⃣ Анализ сигнала BTC...")
    signal = analyze_binance_signal("BTCUSDT", "1h")
    if signal:
        logger.info(f"   ✅ Сигнал: {signal['signal']} {signal['coin']} @ ${signal['entry']}")
        logger.info(f"   RSI: {signal['rsi']}, EMA8: {signal['ema8']}, EMA50: {signal['ema50']}")
    else:
        logger.info("   ⚠️ Нет сигнала")

    logger.info("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_binance_connection()
