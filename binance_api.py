"""
CryptoCompare API Integration Module v3
Публичные endpoints -- не требуют API ключ для базовых запросов
v3: убран /pricemultifull (rate limit), 24ч-изменение считаем из OHLCV
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

CC_BASE = "https://min-api.cryptocompare.com/data"
CC_API_KEY = os.environ.get("CRYPTOCOMPARE_API_KEY", "")

# Rate limiting: минимальный интервал между запросами (сек)
MIN_REQUEST_INTERVAL = 1.0  # 60 запросов/мин -- запас для shared IP
_last_request_time = 0

# Кэш: {cache_key: (timestamp, data)}
_cache = {}
CACHE_TTL_SHORT = 60      # 1 мин для цен
CACHE_TTL_MEDIUM = 600    # 10 мин для OHLCV

# Маппинг символов: coin_id -> CryptoCompare символ
SYMBOL_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
    "binancecoin": "BNB", "ripple": "XRP", "dogecoin": "DOGE",
    "cardano": "ADA", "polkadot": "DOT", "chainlink": "LINK",
    "litecoin": "LTC", "avalanche": "AVAX", "polygon": "MATIC",
    "uniswap": "UNI", "aave": "AAVE", "cosmos": "ATOM",
    "near": "NEAR", "aptos": "APT", "sui": "SUI",
    "toncoin": "TON", "shiba-inu": "SHIB", "tron": "TRX",
    "monero": "XMR", "filecoin": "FIL", "algorand": "ALGO",
    "vechain": "VET", "tezos": "XTZ", "theta": "THETA",
    "fantom": "FTM", "stellar": "XLM", "eos": "EOS",
    "zcash": "ZEC", "dash": "DASH", "neo": "NEO",
    "iota": "IOTA", "maker": "MKR", "compound": "COMP",
    "synthetix": "SNX", "curve": "CRV", "1inch": "1INCH",
    "pancakeswap": "CAKE", "dydx": "DYDX", "lido-dao": "LDO",
    "render": "RNDR", "injective": "INJ", "optimism": "OP",
    "arbitrum": "ARB", "celestia": "TIA", "sei": "SEI",
    "pendle": "PENDLE", "ondo": "ONDO", "jupiter": "JUP",
    "wormhole": "W", "eigenlayer": "EIGEN", "layerzero": "ZRO",
    "bitcoin-cash": "BCH", "ethereum-classic": "ETC",
    "stacks": "STX", "immutable-x": "IMX", "flow": "FLOW",
    "hedera": "HBAR", "quant": "QNT", "fetch-ai": "FET",
    "singularitynet": "AGIX", "ocean-protocol": "OCEAN",
    "arweave": "AR", "livepeer": "LPT", "the-graph": "GRT",
    "basic-attention-token": "BAT", "enjincoin": "ENJ",
    "chiliz": "CHZ", "gala": "GALA", "sandbox": "SAND",
    "decentraland": "MANA", "axie-infinity": "AXS",
    "stepn": "GMT", "apecoin": "APE", "blur": "BLUR",
    "pepe": "PEPE", "bonk": "BONK", "floki": "FLOKI",
    "dogwifhat": "WIF", "book-of-meme": "BOME", "popcat": "POPCAT",
    "mew": "MEW", "cat-in-a-dogs-world": "MEW",
    "mog-coin": "MOG", "brett": "BRETT",
    "gigachad": "GIGA", "maga": "TRUMP", "pepe-unchained": "PEPU",
    "turbo": "TURBO", "pepecoin": "PEPECOIN",
}


def _rate_limit():
    """Ограничение скорости запросов"""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        sleep_time = MIN_REQUEST_INTERVAL - elapsed
        time.sleep(sleep_time)
    _last_request_time = time.time()


def _get_cache(key, ttl):
    """Получить данные из кэша если они ещё актуальны"""
    if key in _cache:
        timestamp, data = _cache[key]
        if time.time() - timestamp < ttl:
            return data
    return None


def _set_cache(key, data):
    """Сохранить данные в кэш"""
    _cache[key] = (time.time(), data)


def _cc_get(endpoint, params=None, use_cache=False, cache_ttl=60):
    """Безопасный GET запрос к CryptoCompare API с rate limiting и кэшированием"""
    cache_key = f"{endpoint}:{json.dumps(params, sort_keys=True) if params else ''}"

    if use_cache:
        cached = _get_cache(cache_key, cache_ttl)
        if cached is not None:
            return cached

    _rate_limit()

    url = f"{CC_BASE}{endpoint}"
    headers = {}
    if CC_API_KEY:
        headers["Authorization"] = f"Apikey {CC_API_KEY}"

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("Response") == "Error":
                msg = data.get('Message', 'Unknown error')
                if "rate limit" in msg.lower():
                    logger.warning(f"CryptoCompare rate limit: {msg}")
                    time.sleep(5)
                else:
                    logger.error(f"CryptoCompare API error: {msg}")
                return None
            if use_cache:
                _set_cache(cache_key, data)
            return data
        elif resp.status_code == 429:
            logger.warning("CryptoCompare HTTP 429, sleeping 10s...")
            time.sleep(10)
            return None
        else:
            logger.error(f"CryptoCompare HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except requests.exceptions.Timeout:
        logger.warning("CryptoCompare timeout")
        return None
    except Exception as e:
        logger.error(f"CryptoCompare request error: {e}")
        return None


def _resolve_symbol(coin_id):
    """Преобразует coin_id в CryptoCompare символ"""
    if coin_id.upper().endswith("USDT"):
        return coin_id.upper().replace("USDT", "")
    return SYMBOL_MAP.get(coin_id.lower(), coin_id.upper())


def cc_get_ohlcv(symbol="BTC", interval="hour", limit=100):
    """
    Получить OHLCV свечи

    Args:
        symbol: Крипто-символ, например "BTC"
        interval: hour, minute, day
        limit: Количество свечей

    Returns:
        list: [{time, open, high, low, close, volumefrom, volumeto}, ...]
    """
    fsym = _resolve_symbol(symbol)

    if interval == "minute":
        endpoint = "/v2/histominute"
    elif interval == "day":
        endpoint = "/v2/histoday"
    else:
        endpoint = "/v2/histohour"

    params = {
        "fsym": fsym,
        "tsym": "USDT",
        "limit": limit,
    }

    data = _cc_get(endpoint, params, use_cache=True, cache_ttl=CACHE_TTL_MEDIUM)
    if data and "Data" in data and "Data" in data["Data"]:
        return data["Data"]["Data"]
    return None


def cc_get_price(symbol="BTC"):
    """Получить текущую цену"""
    fsym = _resolve_symbol(symbol)
    params = {
        "fsym": fsym,
        "tsyms": "USDT",
    }
    data = _cc_get("/price", params, use_cache=True, cache_ttl=CACHE_TTL_SHORT)
    if data and "USDT" in data:
        return float(data["USDT"])
    return None


def cc_get_prices_multi(symbols):
    """Получить цены нескольких монет за 1 запрос"""
    if not symbols:
        return {}
    fsyms = ",".join([_resolve_symbol(s) for s in symbols])
    params = {
        "fsyms": fsyms,
        "tsyms": "USDT",
    }
    data = _cc_get("/pricemulti", params, use_cache=True, cache_ttl=CACHE_TTL_SHORT)
    if data:
        return {k: float(v.get("USDT", 0)) for k, v in data.items()}
    return {}


def cc_get_top_coins(limit=50):
    """Получить топ монет по капитализации"""
    params = {
        "tsym": "USDT",
        "limit": limit,
    }
    data = _cc_get("/top/mktcapfull", params, use_cache=True, cache_ttl=CACHE_TTL_MEDIUM)
    if data and "Data" in data:
        return data["Data"]
    return None


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


def _calculate_change_24h(ohlcv_data):
    """Рассчитать изменение за 24ч из OHLCV данных (24 свечи по 1ч = 24ч)"""
    if not ohlcv_data or len(ohlcv_data) < 25:
        return 0
    current = float(ohlcv_data[-1]["close"])
    past = float(ohlcv_data[-25]["close"])  # 24 часа назад
    if past == 0:
        return 0
    return ((current - past) / past) * 100


def analyze_binance_signal(symbol="BTCUSDT", interval="1h"):
    """
    Анализ сигнала на основе CryptoCompare данных
    Стратегия: EMA8/EMA50 crossover + RSI

    Args:
        symbol: "BTCUSDT" или coin_id типа "bitcoin"
        interval: "1h" (hour), "1d" (day), "1m" (minute)

    Returns:
        dict или None
    """
    cc_interval = "hour"
    if interval in ["1d", "1D", "day"]:
        cc_interval = "day"
    elif interval in ["1m", "1M", "minute"]:
        cc_interval = "minute"

    # Запрашиваем 100 свечей для EMA50 + запас для 24ч-изменения
    ohlcv = cc_get_ohlcv(symbol, cc_interval, limit=100)
    if not ohlcv or len(ohlcv) < 50:
        logger.warning(f"Недостаточно данных для {symbol}")
        return None

    closes = [float(k["close"]) for k in ohlcv]
    highs = [float(k["high"]) for k in ohlcv]
    lows = [float(k["low"]) for k in ohlcv]
    volumes_to = [float(k.get("volumeto", 0)) for k in ohlcv]

    ema8 = calculate_ema(closes, 8)
    ema50 = calculate_ema(closes, 50)
    rsi = calculate_rsi(closes)

    current_close = closes[-1]

    # EMA Crossover сигналы
    ema_cross_up = (ema8[-2] <= ema50[-2] and ema8[-1] > ema50[-1])
    ema_cross_down = (ema8[-2] >= ema50[-2] and ema8[-1] < ema50[-1])

    # ATR
    atr_values = []
    for i in range(-14, 0):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atr_values.append(tr)
    atr = sum(atr_values) / len(atr_values) if atr_values else current_close * 0.02

    # Support/Resistance
    support = min(lows[-20:])
    resistance = max(highs[-20:])

    # Объём подтверждение
    avg_volume = sum(volumes_to[-20:]) / 20
    current_volume = volumes_to[-1]
    volume_confirmed = current_volume > avg_volume * 0.8

    signal = None
    if ema_cross_up and volume_confirmed:
        signal = "BUY"
    elif ema_cross_down and volume_confirmed:
        signal = "SELL"

    if not signal:
        return None

    # Изменение за 24ч считаем из OHLCV (не делаем лишний запрос)
    change_24h = _calculate_change_24h(ohlcv)

    min_risk = current_close * 0.005

    coin_name = _resolve_symbol(symbol)

    if signal == "BUY":
        sl = min(current_close - atr * 2.5, current_close * 0.975, support * 0.998)
        if current_close - sl < min_risk:
            sl = current_close - min_risk
        risk = current_close - sl
        if risk <= 0:
            return None
        return {
            "coin": coin_name,
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
            "coin": coin_name,
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


# ==================== ОБРАТНАЯ СОВМЕСТИМОСТЬ ====================

def binance_get_klines(symbol="BTCUSDT", interval="1h", limit=100):
    """Алиас для cc_get_ohlcv -- совместимость с bot.py"""
    cc_interval = "hour"
    if interval in ["1d", "1D", "day"]:
        cc_interval = "day"
    elif interval in ["1m", "1M", "minute"]:
        cc_interval = "minute"
    data = cc_get_ohlcv(symbol, cc_interval, limit)
    if data:
        return [
            [
                k["time"] * 1000,
                str(k["open"]),
                str(k["high"]),
                str(k["low"]),
                str(k["close"]),
                str(k.get("volumefrom", 0)),
                (k["time"] + 3600) * 1000,
                str(k.get("volumeto", 0)),
                0,
                "0",
                "0",
                "0",
            ]
            for k in data
        ]
    return None


def binance_get_ticker_24h(symbol="BTCUSDT"):
    """Алиас для cc_get_ticker_24h -- совместимость с bot.py

    Убран /pricemultifull из-за rate limit.
    Возвращаем данные из OHLCV + текущей цены.
    """
    fsym = _resolve_symbol(symbol)

    # Получаем OHLCV для 24ч-изменения
    ohlcv = cc_get_ohlcv(symbol, "hour", 30)
    change_24h = _calculate_change_24h(ohlcv) if ohlcv else 0

    # Получаем текущую цену
    price = cc_get_price(symbol)

    if price:
        return {
            "lastPrice": str(price),
            "priceChangePercent": str(change_24h),
            "priceChange": str(price * change_24h / 100),
            "highPrice": str(max([float(k["high"]) for k in ohlcv])) if ohlcv else str(price),
            "lowPrice": str(min([float(k["low"]) for k in ohlcv])) if ohlcv else str(price),
            "volume": str(sum([float(k.get("volumeto", 0)) for k in ohlcv])) if ohlcv else "0",
            "openPrice": str(ohlcv[-25]["close"] if ohlcv and len(ohlcv) >= 25 else price),
            "weightedAvgPrice": str(price),
        }
    return None


def binance_get_price(symbol="BTCUSDT"):
    """Алиас для cc_get_price -- совместимость с bot.py"""
    return cc_get_price(symbol)


def test_binance_connection():
    """Тест подключения к CryptoCompare API (минимум запросов)"""
    logger.info("=" * 60)
    logger.info("ТЕСТ ПОДКЛЮЧЕНИЯ К CRYPTOCOMPARE API")
    logger.info("=" * 60)

    logger.info("1. Проверка API (OHLCV BTC)...")
    ohlcv = cc_get_ohlcv("BTC", "hour", 5)
    if ohlcv:
        last_time = datetime.fromtimestamp(ohlcv[-1]["time"])
        logger.info(f"   Успех! Последняя свеча: {last_time}")
        logger.info(f"   Open=${ohlcv[-1]['open']:,.2f}, Close=${ohlcv[-1]['close']:,.2f}")
    else:
        logger.error("   Ошибка подключения")
        return

    logger.info("2. Текущая цена BTC...")
    price = cc_get_price("BTC")
    if price:
        logger.info(f"   BTC: ${price:,.2f}")
    else:
        logger.error("   Ошибка")

    logger.info("3. 24ч изменение (из OHLCV)...")
    change = _calculate_change_24h(ohlcv)
    logger.info(f"   Изменение 24ч: {change:+.2f}%")

    logger.info("4. Анализ сигнала BTC...")
    signal = analyze_binance_signal("BTCUSDT", "1h")
    if signal:
        logger.info(f"   Сигнал: {signal['signal']} {signal['coin']} @ ${signal['entry']}")
        logger.info(f"   RSI: {signal['rsi']}, EMA8: {signal['ema8']}, EMA50: {signal['ema50']}")
    else:
        logger.info("   Нет сигнала")

    logger.info("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_binance_connection()
