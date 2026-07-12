"""
Crypto Data Module v5 -- CCXT + Kraken + Yahoo Finance Fallback
Multi-timeframe strategy: 4h EMA50 (trend) + 1h EMA8/EMA50 (signal)
"""

import os
import json
import time
import logging
import requests
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
DB_PATH = "market_data.db"
CACHE_TTL_SHORT = 60
CACHE_TTL_MEDIUM = 600

# Rate limits
MIN_REQUEST_INTERVAL = 3.0  # Kraken: ~1 req per 3 sec for public
_last_request_time = 0

# In-memory cache
_cache = {}

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

# Kraken symbol mapping (Kraken uses XBT instead of BTC, etc.)
KRAKEN_SYMBOL_MAP = {
    "BTC": "XBT", "ETH": "ETH", "SOL": "SOL", "BNB": "BNB",
    "XRP": "XRP", "DOGE": "DOGE", "ADA": "ADA", "DOT": "DOT",
    "LINK": "LINK", "LTC": "LTC", "AVAX": "AVAX", "MATIC": "MATIC",
    "UNI": "UNI", "AAVE": "AAVE", "ATOM": "ATOM", "NEAR": "NEAR",
    "APT": "APT", "SUI": "SUI", "TON": "TON", "SHIB": "SHIB",
    "TRX": "TRX", "XMR": "XMR", "FIL": "FIL", "ALGO": "ALGO",
    "VET": "VET", "XTZ": "XTZ", "THETA": "THETA", "FTM": "FTM",
    "XLM": "XLM", "EOS": "EOS", "ZEC": "ZEC", "DASH": "DASH",
    "NEO": "NEO", "IOTA": "IOTA", "MKR": "MKR", "COMP": "COMP",
    "SNX": "SNX", "CRV": "CRV", "1INCH": "1INCH", "CAKE": "CAKE",
    "DYDX": "DYDX", "LDO": "LDO", "RNDR": "RNDR", "INJ": "INJ",
    "OP": "OP", "ARB": "ARB", "TIA": "TIA", "SEI": "SEI",
    "PENDLE": "PENDLE", "ONDO": "ONDO", "JUP": "JUP", "W": "W",
    "EIGEN": "EIGEN", "ZRO": "ZRO", "BCH": "BCH", "ETC": "ETC",
    "STX": "STX", "IMX": "IMX", "FLOW": "FLOW", "HBAR": "HBAR",
    "QNT": "QNT", "FET": "FET", "AGIX": "AGIX", "OCEAN": "OCEAN",
    "AR": "AR", "LPT": "LPT", "GRT": "GRT", "BAT": "BAT",
    "ENJ": "ENJ", "CHZ": "CHZ", "GALA": "GALA", "SAND": "SAND",
    "MANA": "MANA", "AXS": "AXS", "GMT": "GMT", "APE": "APE",
    "BLUR": "BLUR", "PEPE": "PEPE", "BONK": "BONK", "FLOKI": "FLOKI",
    "WIF": "WIF", "BOME": "BOME", "POPCAT": "POPCAT", "MEW": "MEW",
    "MOG": "MOG", "BRETT": "BRETT", "GIGA": "GIGA", "TRUMP": "TRUMP",
    "PEPU": "PEPU", "TURBO": "TURBO", "PEPECOIN": "PEPECOIN",
}


def init_market_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT, interval TEXT, time INTEGER,
            open REAL, high REAL, low REAL, close REAL,
            volumefrom REAL, volumeto REAL,
            PRIMARY KEY (symbol, interval, time)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, signal_type TEXT, entry_price REAL,
            stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
            risk_reward REAL, stars INTEGER, status TEXT DEFAULT 'OPEN',
            exit_price REAL, pnl_pct REAL, exit_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_balance (
            id INTEGER PRIMARY KEY,
            balance_usdt REAL DEFAULT 10000.0,
            total_pnl REAL DEFAULT 0.0,
            total_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("SELECT COUNT(*) FROM paper_balance")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO paper_balance (id, balance_usdt) VALUES (1, 10000.0)")
    conn.commit()
    conn.close()


def _get_db():
    return sqlite3.connect(DB_PATH)


def _rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get_cache(key, ttl):
    if key in _cache:
        timestamp, data = _cache[key]
        if time.time() - timestamp < ttl:
            return data
    return None


def _set_cache(key, data):
    _cache[key] = (time.time(), data)


def _resolve_symbol(coin_id):
    if coin_id.upper().endswith("USDT"):
        return coin_id.upper().replace("USDT", "")
    return SYMBOL_MAP.get(coin_id.lower(), coin_id.upper())


def _kraken_pair(symbol):
    """Convert symbol to Kraken pair format (e.g., BTC -> XBTUSD)"""
    base = KRAKEN_SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    return f"{base}USD"


def _save_candles_to_db(symbol, interval, candles):
    if not candles:
        return
    conn = _get_db()
    c = conn.cursor()
    for k in candles:
        c.execute("""
            INSERT OR REPLACE INTO candles
            (symbol, interval, time, open, high, low, close, volumefrom, volumeto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, interval, k["time"], k["open"], k["high"], k["low"],
              k["close"], k.get("volumefrom", 0), k.get("volumeto", 0)))
    conn.commit()
    conn.close()


def _get_candles_from_db(symbol, interval, limit=100):
    conn = _get_db()
    c = conn.cursor()
    c.execute("""
        SELECT time, open, high, low, close, volumefrom, volumeto
        FROM candles WHERE symbol = ? AND interval = ?
        ORDER BY time DESC LIMIT ?
    """, (symbol, interval, limit))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return None
    return [{
        "time": r[0], "open": r[1], "high": r[2], "low": r[3],
        "close": r[4], "volumefrom": r[5], "volumeto": r[6]
    } for r in reversed(rows)]


# ==================== KRAKEN API ====================
def kraken_get_ohlcv(symbol="BTC", interval="hour", limit=100):
    """
    Fetch OHLCV from Kraken public API.
    interval: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600 (minutes)
    """
    pair = _kraken_pair(symbol)

    # Map interval names to Kraken minutes
    interval_map = {
        "minute": 1, "5min": 5, "15min": 15, "30min": 30,
        "hour": 60, "1h": 60, "4h": 240, "day": 1440, "1d": 1440,
        "week": 10080, "month": 21600
    }
    kraken_interval = interval_map.get(interval, 60)

    cache_key = f"kraken:ohlcv:{pair}:{kraken_interval}:{limit}"
    cached = _get_cache(cache_key, CACHE_TTL_MEDIUM)
    if cached is not None:
        return cached

    _rate_limit()

    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": kraken_interval}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Kraken HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        if data.get("error"):
            logger.error(f"Kraken API error: {data['error']}")
            return None

        result = data.get("result", {})
        # Kraken returns data with pair name as key (e.g., "XXBTZUSD" or "XBTUSD")
        pair_key = None
        for k in result.keys():
            if k != "last":
                pair_key = k
                break

        if not pair_key:
            logger.error("Kraken: no pair data found")
            return None

        raw_candles = result[pair_key]
        candles = []
        for c in raw_candles[-limit:]:
            candles.append({
                "time": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volumefrom": float(c[6]),  # volume in base currency
                "volumeto": float(c[6]) * float(c[4])  # approximate
            })

        _set_cache(cache_key, candles)
        _save_candles_to_db(symbol, interval, candles)
        return candles

    except Exception as e:
        logger.error(f"Kraken request error: {e}")
        return None


def kraken_get_price(symbol="BTC"):
    """Get current price from Kraken"""
    pair = _kraken_pair(symbol)
    cache_key = f"kraken:price:{pair}"
    cached = _get_cache(cache_key, CACHE_TTL_SHORT)
    if cached is not None:
        return cached

    _rate_limit()

    url = "https://api.kraken.com/0/public/Ticker"
    params = {"pair": pair}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get("error"):
            return None

        result = data.get("result", {})
        for k, v in result.items():
            if k != "last":
                price = float(v["c"][0])  # "c" = last trade closed
                _set_cache(cache_key, price)
                return price
        return None

    except Exception as e:
        logger.error(f"Kraken price error: {e}")
        return None


# ==================== YAHOO FINANCE FALLBACK ====================
def yahoo_get_ohlcv(symbol="BTC", interval="1h", limit=100):
    """
    Fetch crypto OHLCV from Yahoo Finance.
    Yahoo uses symbols like BTC-USD, ETH-USD
    """
    yahoo_symbol = f"{symbol.upper()}-USD"

    # Map intervals
    interval_map = {
        "minute": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
        "hour": "1h", "1h": "1h", "4h": "1h",  # Yahoo doesn't have 4h, use 1h
        "day": "1d", "1d": "1d", "week": "1wk", "month": "1mo"
    }
    yahoo_interval = interval_map.get(interval, "1h")

    # Yahoo range
    range_map = {
        "minute": "1d", "5min": "5d", "15min": "5d", "30min": "1mo",
        "hour": "1mo", "1h": "1mo", "4h": "3mo",
        "day": "1y", "1d": "1y", "week": "5y", "month": "max"
    }
    yahoo_range = range_map.get(interval, "1mo")

    cache_key = f"yahoo:ohlcv:{yahoo_symbol}:{yahoo_interval}:{limit}"
    cached = _get_cache(cache_key, CACHE_TTL_MEDIUM)
    if cached is not None:
        return cached

    _rate_limit()

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
    params = {
        "interval": yahoo_interval,
        "range": yahoo_range,
        "includeAdjustedClose": "false"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.error(f"Yahoo HTTP {resp.status_code}")
            return None

        data = resp.json()
        chart = data.get("chart", {})
        if chart.get("error"):
            logger.error(f"Yahoo error: {chart['error']}")
            return None

        result = chart.get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        ohlcv = result.get("indicators", {}).get("quote", [{}])[0]

        opens = ohlcv.get("open", [])
        highs = ohlcv.get("high", [])
        lows = ohlcv.get("low", [])
        closes = ohlcv.get("close", [])
        volumes = ohlcv.get("volume", [])

        candles = []
        for i in range(len(timestamps)):
            if opens[i] is None:
                continue
            candles.append({
                "time": timestamps[i],
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volumefrom": float(volumes[i]) if volumes[i] else 0,
                "volumeto": float(volumes[i]) * float(closes[i]) if volumes[i] else 0
            })

        candles = candles[-limit:]
        _set_cache(cache_key, candles)
        _save_candles_to_db(symbol, interval, candles)
        return candles

    except Exception as e:
        logger.error(f"Yahoo request error: {e}")
        return None


def yahoo_get_price(symbol="BTC"):
    """Get current price from Yahoo Finance"""
    yahoo_symbol = f"{symbol.upper()}-USD"
    cache_key = f"yahoo:price:{yahoo_symbol}"
    cached = _get_cache(cache_key, CACHE_TTL_SHORT)
    if cached is not None:
        return cached

    _rate_limit()

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
    params = {"interval": "1m", "range": "1d"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        result = data.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        if price:
            _set_cache(cache_key, float(price))
            return float(price)

        # Fallback to last close
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        if closes and closes[-1]:
            _set_cache(cache_key, float(closes[-1]))
            return float(closes[-1])
        return None

    except Exception as e:
        logger.error(f"Yahoo price error: {e}")
        return None


# ==================== UNIFIED DATA API ====================
def get_ohlcv(symbol="BTC", interval="hour", limit=100, use_db_cache=True):
    """
    Unified OHLCV fetcher with fallback chain:
    1. Database cache
    2. Kraken API
    3. Yahoo Finance
    """
    fsym = _resolve_symbol(symbol)

    # 1. Check DB cache
    if use_db_cache:
        db_candles = _get_candles_from_db(fsym, interval, limit)
        if db_candles and len(db_candles) >= limit * 0.8:
            logger.info(f"Using DB cache for {fsym} {interval}")
            return db_candles

    # 2. Try Kraken
    logger.info(f"Fetching {fsym} {interval} from Kraken...")
    candles = kraken_get_ohlcv(fsym, interval, limit)
    if candles and len(candles) >= limit * 0.5:
        logger.info(f"Kraken OK: {len(candles)} candles")
        return candles

    # 3. Fallback to Yahoo
    logger.info(f"Kraken failed, trying Yahoo Finance for {fsym}...")
    candles = yahoo_get_ohlcv(fsym, interval, limit)
    if candles and len(candles) >= limit * 0.5:
        logger.info(f"Yahoo OK: {len(candles)} candles")
        return candles

    logger.error(f"All data sources failed for {fsym} {interval}")
    return None


def get_price(symbol="BTC"):
    """Unified price fetcher with fallback"""
    fsym = _resolve_symbol(symbol)

    # Try Kraken first
    price = kraken_get_price(fsym)
    if price:
        return price

    # Fallback to Yahoo
    price = yahoo_get_price(fsym)
    if price:
        return price

    logger.error(f"All price sources failed for {fsym}")
    return None


def get_prices_multi(symbols):
    """Get prices for multiple symbols"""
    result = {}
    for sym in symbols:
        price = get_price(sym)
        if price:
            result[sym] = price
        time.sleep(MIN_REQUEST_INTERVAL)
    return result


# ==================== TECHNICAL ANALYSIS ====================
def calculate_ema(prices, period):
    multiplier = 2 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append(price * multiplier + ema[-1] * (1 - multiplier))
    return ema


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


def calculate_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return (max(highs) - min(lows)) / period if highs else 0
    atr_values = []
    for i in range(-period, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        atr_values.append(tr)
    return sum(atr_values) / len(atr_values)


def _calculate_change_24h(ohlcv_data):
    if not ohlcv_data or len(ohlcv_data) < 25:
        return 0
    current = float(ohlcv_data[-1]["close"])
    past = float(ohlcv_data[-25]["close"])
    if past == 0:
        return 0
    return ((current - past) / past) * 100


def analyze_binance_signal(symbol="BTCUSDT", interval="1h"):
    fsym = _resolve_symbol(symbol)

    # Fetch 1h data
    ohlcv_1h = get_ohlcv(fsym, "hour", limit=100)
    if not ohlcv_1h or len(ohlcv_1h) < 50:
        logger.warning(f"Insufficient 1h data for {symbol}")
        return None

    # Fetch 4h data (or build from 1h)
    ohlcv_4h = get_ohlcv(fsym, "4h", limit=100)
    if not ohlcv_4h or len(ohlcv_4h) < 25:
        # Build 4h from 1h if not enough
        logger.info(f"Building 4h from 1h for {symbol}")
        ohlcv_4h = []
        for i in range(0, len(ohlcv_1h) - 3, 4):
            chunk = ohlcv_1h[i:i+4]
            ohlcv_4h.append({
                "time": chunk[0]["time"],
                "open": chunk[0]["open"],
                "high": max(k["high"] for k in chunk),
                "low": min(k["low"] for k in chunk),
                "close": chunk[-1]["close"],
                "volumefrom": sum(k["volumefrom"] for k in chunk),
                "volumeto": sum(k["volumeto"] for k in chunk)
            })

    closes_1h = [float(k["close"]) for k in ohlcv_1h]
    highs_1h = [float(k["high"]) for k in ohlcv_1h]
    lows_1h = [float(k["low"]) for k in ohlcv_1h]
    volumes_1h = [float(k.get("volumeto", 0)) for k in ohlcv_1h]

    ema8_1h = calculate_ema(closes_1h, 8)
    ema50_1h = calculate_ema(closes_1h, 50)
    rsi_1h = calculate_rsi(closes_1h)

    closes_4h = [float(k["close"]) for k in ohlcv_4h]
    if len(closes_4h) < 25:
        logger.warning(f"Insufficient 4h data for EMA50 {symbol}")
        return None

    ema50_4h = calculate_ema(closes_4h, 50)

    current_close = closes_1h[-1]
    trend_bullish = current_close > ema50_4h[-1]
    trend_bearish = current_close < ema50_4h[-1]

    ema_cross_up = (ema8_1h[-2] <= ema50_1h[-2] and ema8_1h[-1] > ema50_1h[-1])
    ema_cross_down = (ema8_1h[-2] >= ema50_1h[-2] and ema8_1h[-1] < ema50_1h[-1])

    atr = calculate_atr(highs_1h, lows_1h, closes_1h, period=14)
    support = min(lows_1h[-20:])
    resistance = max(highs_1h[-20:])

    avg_volume = sum(volumes_1h[-20:]) / 20
    current_volume = volumes_1h[-1]
    volume_confirmed = current_volume > avg_volume * 0.8

    signal = None
    signal_strength = 0

    if ema_cross_up and volume_confirmed and trend_bullish:
        signal = "BUY"
        signal_strength = 3
        if rsi_1h < 40:
            signal_strength += 1
        if current_volume > avg_volume * 1.5:
            signal_strength += 1
        if current_close > ema50_4h[-1] * 1.02:
            signal_strength += 1
    elif ema_cross_down and volume_confirmed and trend_bearish:
        signal = "SELL"
        signal_strength = 3
        if rsi_1h > 60:
            signal_strength += 1
        if current_volume > avg_volume * 1.5:
            signal_strength += 1
        if current_close < ema50_4h[-1] * 0.98:
            signal_strength += 1

    if not signal:
        return None

    stars = min(5, max(1, signal_strength))
    change_24h = _calculate_change_24h(ohlcv_1h)
    min_risk = current_close * 0.005

    if signal == "BUY":
        sl = min(current_close - atr * 2.5, current_close * 0.975, support * 0.998)
        if current_close - sl < min_risk:
            sl = current_close - min_risk
        risk = current_close - sl
        if risk <= 0:
            return None
        return {
            "coin": fsym, "signal": signal, "entry": round(current_close, 2),
            "stop_loss": round(sl, 2),
            "take_profit_1": round(current_close + risk * 1.5, 2),
            "take_profit_2": round(current_close + risk * 3.0, 2),
            "take_profit_3": round(current_close + risk * 5.0, 2),
            "risk_reward": 1.5, "stars": stars, "rsi": round(rsi_1h, 2),
            "change_24h": round(change_24h, 2), "price": current_close,
            "ema8": round(ema8_1h[-1], 2), "ema50": round(ema50_1h[-1], 2),
            "ema50_4h": round(ema50_4h[-1], 2), "volume": round(current_volume, 4),
            "trend": "BULLISH" if trend_bullish else "BEARISH",
        }
    else:
        sl = max(current_close + atr * 2.5, current_close * 1.025, resistance * 1.002)
        if sl - current_close < min_risk:
            sl = current_close + min_risk
        risk = sl - current_close
        if risk <= 0:
            return None
        return {
            "coin": fsym, "signal": signal, "entry": round(current_close, 2),
            "stop_loss": round(sl, 2),
            "take_profit_1": round(current_close - risk * 1.5, 2),
            "take_profit_2": round(current_close - risk * 3.0, 2),
            "take_profit_3": round(current_close - risk * 5.0, 2),
            "risk_reward": 1.5, "stars": stars, "rsi": round(rsi_1h, 2),
            "change_24h": round(change_24h, 2), "price": current_close,
            "ema8": round(ema8_1h[-1], 2), "ema50": round(ema50_1h[-1], 2),
            "ema50_4h": round(ema50_4h[-1], 2), "volume": round(current_volume, 4),
            "trend": "BULLISH" if trend_bullish else "BEARISH",
        }


# ==================== PAPER TRADING ====================
def open_paper_trade(signal):
    conn = _get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO paper_trades
        (symbol, signal_type, entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3, risk_reward, stars, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
    """, (signal["coin"], signal["signal"], signal["entry"], signal["stop_loss"],
          signal["take_profit_1"], signal["take_profit_2"], signal["take_profit_3"],
          signal["risk_reward"], signal["stars"]))
    conn.commit()
    trade_id = c.lastrowid
    conn.close()
    logger.info(f"Paper trade opened: #{trade_id} {signal['coin']} {signal['signal']} @ ${signal['entry']}")
    return trade_id


def check_paper_trades():
    conn = _get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, symbol, signal_type, entry_price, stop_loss,
               take_profit_1, take_profit_2, take_profit_3, stars
        FROM paper_trades WHERE status = 'OPEN'
    """)
    trades = c.fetchall()
    for trade in trades:
        trade_id, symbol, signal_type, entry, sl, tp1, tp2, tp3, stars = trade
        current_price = get_price(symbol)
        if current_price is None:
            continue
        pnl_pct = 0
        exit_reason = None
        exit_price = current_price
        if signal_type == "BUY":
            if current_price <= sl:
                pnl_pct = ((sl - entry) / entry) * 100
                exit_reason = "STOP_LOSS"
                exit_price = sl
            elif current_price >= tp3:
                pnl_pct = ((tp3 - entry) / entry) * 100
                exit_reason = "TAKE_PROFIT_3"
                exit_price = tp3
            elif current_price >= tp2:
                pnl_pct = ((tp2 - entry) / entry) * 100
                exit_reason = "TAKE_PROFIT_2"
                exit_price = tp2
            elif current_price >= tp1:
                pnl_pct = ((tp1 - entry) / entry) * 100
                exit_reason = "TAKE_PROFIT_1"
                exit_price = tp1
        else:
            if current_price >= sl:
                pnl_pct = ((entry - sl) / entry) * 100
                exit_reason = "STOP_LOSS"
                exit_price = sl
            elif current_price <= tp3:
                pnl_pct = ((entry - tp3) / entry) * 100
                exit_reason = "TAKE_PROFIT_3"
                exit_price = tp3
            elif current_price <= tp2:
                pnl_pct = ((entry - tp2) / entry) * 100
                exit_reason = "TAKE_PROFIT_2"
                exit_price = tp2
            elif current_price <= tp1:
                pnl_pct = ((entry - tp1) / entry) * 100
                exit_reason = "TAKE_PROFIT_1"
                exit_price = tp1
        if exit_reason:
            c.execute("""
                UPDATE paper_trades SET status = 'CLOSED', exit_price = ?, pnl_pct = ?,
                exit_reason = ?, closed_at = ? WHERE id = ?
            """, (exit_price, round(pnl_pct, 2), exit_reason, datetime.now(), trade_id))
            c.execute("""
                UPDATE paper_balance SET
                total_pnl = total_pnl + ?,
                total_trades = total_trades + 1,
                winning_trades = winning_trades + ?,
                updated_at = ?
                WHERE id = 1
            """, (pnl_pct, 1 if pnl_pct > 0 else 0, datetime.now()))
            conn.commit()
            logger.info(f"Paper trade closed: #{trade_id} {symbol} {exit_reason} P&L: {pnl_pct:+.2f}%")
    conn.close()


def get_paper_stats():
    conn = _get_db()
    c = conn.cursor()
    c.execute("SELECT balance_usdt, total_pnl, total_trades, winning_trades FROM paper_balance WHERE id = 1")
    balance = c.fetchone()
    c.execute("""
        SELECT symbol, signal_type, entry_price, exit_price, pnl_pct, exit_reason, stars, created_at, closed_at
        FROM paper_trades WHERE status = 'CLOSED' ORDER BY closed_at DESC LIMIT 10
    """)
    closed_trades = c.fetchall()
    c.execute("""
        SELECT symbol, signal_type, entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3, stars, created_at
        FROM paper_trades WHERE status = 'OPEN' ORDER BY created_at DESC
    """)
    open_trades = c.fetchall()
    conn.close()
    if not balance:
        return None
    total_trades = balance[2]
    win_rate = (balance[3] / total_trades * 100) if total_trades > 0 else 0
    return {
        "balance": balance[0], "total_pnl": balance[1],
        "total_trades": total_trades, "winning_trades": balance[3],
        "win_rate": round(win_rate, 1),
        "open_trades": open_trades, "closed_trades": closed_trades,
    }


# ==================== BACKWARD COMPATIBILITY ====================
def binance_get_klines(symbol="BTCUSDT", interval="1h", limit=100):
    cc_interval = "hour"
    if interval in ["1d", "1D", "day"]:
        cc_interval = "day"
    elif interval in ["1m", "1M", "minute"]:
        cc_interval = "minute"
    data = get_ohlcv(symbol, cc_interval, limit)
    if data:
        return [
            [k["time"] * 1000, str(k["open"]), str(k["high"]), str(k["low"]),
             str(k["close"]), str(k.get("volumefrom", 0)),
             (k["time"] + 3600) * 1000, str(k.get("volumeto", 0)),
             0, "0", "0", "0"]
            for k in data
        ]
    return None


def binance_get_ticker_24h(symbol="BTCUSDT"):
    fsym = _resolve_symbol(symbol)
    ohlcv = get_ohlcv(fsym, "hour", 30)
    change_24h = _calculate_change_24h(ohlcv) if ohlcv else 0
    price = get_price(symbol)
    if price:
        return {
            "lastPrice": str(price), "priceChangePercent": str(change_24h),
            "priceChange": str(price * change_24h / 100),
            "highPrice": str(max([float(k["high"]) for k in ohlcv])) if ohlcv else str(price),
            "lowPrice": str(min([float(k["low"]) for k in ohlcv])) if ohlcv else str(price),
            "volume": str(sum([float(k.get("volumeto", 0)) for k in ohlcv])) if ohlcv else "0",
            "openPrice": str(ohlcv[-25]["close"] if ohlcv and len(ohlcv) >= 25 else price),
            "weightedAvgPrice": str(price),
        }
    return None


def binance_get_price(symbol="BTCUSDT"):
    return get_price(symbol)


# Legacy aliases for bot.py
def cc_get_price(symbol="BTC"):
    return get_price(symbol)


def cc_get_prices_multi(symbols):
    return get_prices_multi(symbols)


def test_binance_connection():
    logger.info("=" * 60)
    logger.info("CRYPTO DATA API v5 TEST (Kraken + Yahoo Fallback)")
    logger.info("=" * 60)
    init_market_db()

    logger.info("1. OHLCV BTC (1h) from Kraken...")
    ohlcv = get_ohlcv("BTC", "hour", 5)
    if ohlcv:
        logger.info(f"   OK: {len(ohlcv)} candles, Close=${ohlcv[-1]['close']:,.2f}")
    else:
        logger.error("   FAIL")
        return

    logger.info("2. Current BTC price...")
    price = get_price("BTC")
    if price:
        logger.info(f"   OK: ${price:,.2f}")
    else:
        logger.error("   FAIL")

    logger.info("3. Multi-timeframe analysis BTC...")
    signal = analyze_binance_signal("BTCUSDT", "1h")
    if signal:
        logger.info(f"   SIGNAL: {signal['signal']} {signal['coin']} @ ${signal['entry']}")
        logger.info(f"   Stars: {signal['stars']}, Trend: {signal['trend']}")
        logger.info(f"   EMA8(1h): {signal['ema8']}, EMA50(1h): {signal['ema50']}, EMA50(4h): {signal['ema50_4h']}")
    else:
        logger.info("   No signal")

    logger.info("4. Paper trading init...")
    stats = get_paper_stats()
    if stats:
        logger.info(f"   Balance: ${stats['balance']:,.2f}, P&L: {stats['total_pnl']:+.2f}%, Trades: {stats['total_trades']}")

    logger.info("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_binance_connection()
