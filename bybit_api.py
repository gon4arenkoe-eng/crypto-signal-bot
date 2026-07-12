"""
Bybit API v5 Integration Module
Поддерживает Testnet, Mainnet и Demo Trading
"""

import os
import json
import time
import hmac
import hashlib
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
BYBIT_MODE = os.environ.get("BYBIT_MODE", "testnet")  # testnet | mainnet | demo

# URL-ы
BASE_URLS = {
    "testnet": "https://api-testnet.bybit.com",
    "mainnet": "https://api.bybit.com",
    "demo": "https://api-demo.bybit.com"
}

BASE_URL = BASE_URLS.get(BYBIT_MODE, BASE_URLS["testnet"])

logger.info(f"🔌 Bybit API Mode: {BYBIT_MODE}")
logger.info(f"🔌 Bybit API URL: {BASE_URL}")
logger.info(f"🔌 API Key: {BYBIT_API_KEY[:10]}..." if BYBIT_API_KEY else "❌ API Key не задан")


# ==================== УТИЛИТЫ ПОДПИСИ ====================
def generate_signature(timestamp, api_key, recv_window, params_str):
    """Генерация HMAC-SHA256 подписи для Bybit API v5"""
    payload = timestamp + api_key + recv_window + params_str
    signature = hmac.new(
        BYBIT_API_SECRET.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def get_headers(method, endpoint, params=None, body=None):
    """Формирование заголовков с подписью"""
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"

    if method == "GET" and params:
        params_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    elif method == "POST" and body:
        params_str = json.dumps(body)
    else:
        params_str = ""

    signature = generate_signature(timestamp, BYBIT_API_KEY, recv_window, params_str)

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    return headers


# ==================== ПУБЛИЧНЫЕ МЕТОДЫ ====================
def get_server_time():
    """Получить серверное время Bybit"""
    url = f"{BASE_URL}/v5/market/time"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return int(data["result"]["timeSecond"])
        else:
            logger.error(f"Bybit time error: {data}")
            return None
    except Exception as e:
        logger.error(f"get_server_time error: {e}")
        return None


def get_klines(symbol="BTCUSDT", interval="60", category="linear", limit=200):
    """
    Получить свечи (kline/candlestick)

    Args:
        symbol: Торговая пара, например "BTCUSDT"
        interval: Таймфрейм — 1, 3, 5, 15, 30, 60, 120, 240, D, W, M
        category: "spot", "linear", "inverse"
        limit: Количество свечей (макс 1000)

    Returns:
        list: [[timestamp, open, high, low, close, volume, turnover]]
    """
    url = f"{BASE_URL}/v5/market/kline"
    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"]
        else:
            logger.error(f"get_klines error: {data}")
            return []
    except Exception as e:
        logger.error(f"get_klines error: {e}")
        return []


def get_tickers(symbol="BTCUSDT", category="linear"):
    """Получить текущие цены и статистику 24ч"""
    url = f"{BASE_URL}/v5/market/tickers"
    params = {
        "category": category,
        "symbol": symbol
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"][0]
        else:
            logger.error(f"get_tickers error: {data}")
            return None
    except Exception as e:
        logger.error(f"get_tickers error: {e}")
        return None


def get_orderbook(symbol="BTCUSDT", category="linear", limit=50):
    """Получить стакан ордеров"""
    url = f"{BASE_URL}/v5/market/orderbook"
    params = {
        "category": category,
        "symbol": symbol,
        "limit": limit
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]
        else:
            logger.error(f"get_orderbook error: {data}")
            return None
    except Exception as e:
        logger.error(f"get_orderbook error: {e}")
        return None


# ==================== ПРИВАТНЫЕ МЕТОДЫ ====================
def get_wallet_balance(accountType="UNIFIED"):
    """Получить баланс кошелька"""
    url = f"{BASE_URL}/v5/account/wallet-balance"
    params = {"accountType": accountType}

    headers = get_headers("GET", "/v5/account/wallet-balance", params=params)

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"]
        else:
            logger.error(f"get_wallet_balance error: {data}")
            return None
    except Exception as e:
        logger.error(f"get_wallet_balance error: {e}")
        return None


def get_positions(category="linear", symbol=None):
    """Получить открытые позиции"""
    url = f"{BASE_URL}/v5/position/list"
    params = {"category": category}
    if symbol:
        params["symbol"] = symbol

    headers = get_headers("GET", "/v5/position/list", params=params)

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            return data["result"]["list"]
        else:
            logger.error(f"get_positions error: {data}")
            return []
    except Exception as e:
        logger.error(f"get_positions error: {e}")
        return []


# ==================== ТОРГОВЫЕ ОПЕРАЦИИ ====================
def place_order(category, symbol, side, orderType, qty, price=None, 
                stopLoss=None, takeProfit=None, timeInForce="GTC"):
    """
    Разместить ордер

    Args:
        category: "spot", "linear", "inverse"
        symbol: "BTCUSDT"
        side: "Buy" или "Sell"
        orderType: "Market" или "Limit"
        qty: Количество
        price: Цена (для Limit)
        stopLoss: Стоп-лосс цена
        takeProfit: Тейк-профит цена
        timeInForce: "GTC", "IOC", "FOK"
    """
    url = f"{BASE_URL}/v5/order/create"

    body = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": orderType,
        "qty": str(qty),
        "timeInForce": timeInForce
    }

    if price and orderType == "Limit":
        body["price"] = str(price)
    if stopLoss:
        body["stopLoss"] = str(stopLoss)
    if takeProfit:
        body["takeProfit"] = str(takeProfit)

    headers = get_headers("POST", "/v5/order/create", body=body)

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            logger.info(f"✅ Ордер размещён: {data['result']}")
            return data["result"]
        else:
            logger.error(f"place_order error: {data}")
            return None
    except Exception as e:
        logger.error(f"place_order error: {e}")
        return None


def cancel_order(category, symbol, orderId=None):
    """Отменить ордер"""
    url = f"{BASE_URL}/v5/order/cancel"
    body = {
        "category": category,
        "symbol": symbol
    }
    if orderId:
        body["orderId"] = orderId

    headers = get_headers("POST", "/v5/order/cancel", body=body)

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=10)
        data = resp.json()
        if data.get("retCode") == 0:
            logger.info(f"✅ Ордер отменён")
            return True
        else:
            logger.error(f"cancel_order error: {data}")
            return False
    except Exception as e:
        logger.error(f"cancel_order error: {e}")
        return False


# ==================== ТЕХНИЧЕСКИЙ АНАЛИЗ ====================
def calculate_rsi(prices, period=14):
    """Расчёт RSI"""
    if len(prices) < period + 1:
        return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_ema(prices, period):
    """Расчёт EMA"""
    multiplier = 2 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append(price * multiplier + ema[-1] * (1 - multiplier))
    return ema


def analyze_bybit_signal(symbol="BTCUSDT", category="linear"):
    """
    Анализ сигнала на основе Bybit данных
    Стратегия: EMA8/EMA50 crossover + RSI на 1-часовых свечах
    """
    # Получаем 1-часовые свечи (нужно минимум 50 для EMA50)
    klines = get_klines(symbol, interval="60", category=category, limit=100)
    if not klines or len(klines) < 50:
        logger.warning(f"Недостаточно данных для {symbol}")
        return None

    # klines формат: [timestamp, open, high, low, close, volume, turnover]
    # Сортируем по времени (старые → новые)
    klines_sorted = sorted(klines, key=lambda x: int(x[0]))

    closes = [float(k[4]) for k in klines_sorted]
    highs = [float(k[2]) for k in klines_sorted]
    lows = [float(k[3]) for k in klines_sorted]

    # EMA
    ema8 = calculate_ema(closes, 8)
    ema50 = calculate_ema(closes, 50)

    # RSI
    rsi = calculate_rsi(closes)

    current_close = closes[-1]
    prev_close = closes[-2]

    # Сигналы crossover
    ema_cross_up = (ema8[-2] <= ema50[-2] and ema8[-1] > ema50[-1])
    ema_cross_down = (ema8[-2] >= ema50[-2] and ema8[-1] < ema50[-1])

    # ATR
    recent_highs = highs[-14:]
    recent_lows = lows[-14:]
    atr = (max(recent_highs) - min(recent_lows)) / 14

    # Support/Resistance
    support = min(lows[-20:])
    resistance = max(highs[-20:])

    # Определяем сигнал
    signal = None
    if ema_cross_up:
        signal = "BUY"
    elif ema_cross_down:
        signal = "SELL"

    if not signal:
        return None

    # Расчёт уровней
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
            "rsi": round(rsi, 2),
            "ema8": round(ema8[-1], 2),
            "ema50": round(ema50[-1], 2),
            "price": current_close
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
            "rsi": round(rsi, 2),
            "ema8": round(ema8[-1], 2),
            "ema50": round(ema50[-1], 2),
            "price": current_close
        }


# ==================== ПРОВЕРКА ПОДКЛЮЧЕНИЯ ====================
def test_connection():
    """Тест подключения к Bybit API"""
    print("=" * 60)
    print("🔌 ТЕСТ ПОДКЛЮЧЕНИЯ К BYBIT API")
    print("=" * 60)
    print(f"Режим: {BYBIT_MODE}")
    print(f"URL: {BASE_URL}")
    print(f"API Key: {BYBIT_API_KEY[:15]}..." if BYBIT_API_KEY else "❌ Не задан")
    print()

    # 1. Серверное время
    print("1️⃣ Проверка серверного времени...")
    server_time = get_server_time()
    if server_time:
        print(f"   ✅ Успех! Время: {datetime.fromtimestamp(server_time)}")
    else:
        print("   ❌ Ошибка")

    # 2. Свечи BTC
    print("
2️⃣ Получение свечей BTCUSDT (1ч)...")
    klines = get_klines("BTCUSDT", "60", "linear", 5)
    if klines:
        print(f"   ✅ Получено {len(klines)} свечей")
        print(f"   Последняя: {klines[0]}")
    else:
        print("   ❌ Ошибка")

    # 3. Текущая цена
    print("
3️⃣ Получение текущей цены BTC...")
    ticker = get_tickers("BTCUSDT", "linear")
    if ticker:
        print(f"   ✅ BTC: ${ticker.get('lastPrice', 'N/A')}")
        print(f"   24ч изменение: {ticker.get('price24hPcnt', 'N/A')}%")
    else:
        print("   ❌ Ошибка")

    # 4. Баланс (только если есть ключи)
    if BYBIT_API_KEY and BYBIT_API_SECRET:
        print("
4️⃣ Получение баланса...")
        balance = get_wallet_balance()
        if balance:
            print(f"   ✅ Баланс получен")
            for acc in balance:
                print(f"   Account: {acc.get('accountType')}")
                for coin in acc.get('coin', []):
                    print(f"   {coin.get('coin')}: {coin.get('walletBalance', '0')}")
        else:
            print("   ❌ Ошибка (возможно, нет прав или неверная подпись)")
    else:
        print("
4️⃣ Баланс: пропущено (нет API ключей)")

    # 5. Анализ сигнала
    print("
5️⃣ Анализ сигнала BTC...")
    signal = analyze_bybit_signal("BTCUSDT", "linear")
    if signal:
        print(f"   ✅ Сигнал: {signal['signal']} {signal['coin']}")
        print(f"   Entry: ${signal['entry']}, SL: ${signal['stop_loss']}")
        print(f"   RSI: {signal['rsi']}, EMA8: {signal['ema8']}, EMA50: {signal['ema50']}")
    else:
        print("   ⚠️ Нет сигнала")

    print("
" + "=" * 60)


if __name__ == "__main__":
    test_connection()
