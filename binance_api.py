"""
CryptoCompare API Integration Module v4
Multi-timeframe strategy: 4h EMA50 (trend) + 1h EMA8/EMA50 (signal)
"""

import os
import json
import time
import logging
import requests
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

CC_BASE = "https://min-api.cryptocompare.com/data"
CC_API_KEY = os.environ.get("CRYPTOCOMPARE_API_KEY", "")

MIN_REQUEST_INTERVAL = 1.0
_last_request_time = 0

_cache = {}
CACHE_TTL_SHORT = 60
CACHE_TTL_MEDIUM = 600

DB_PATH = "market_data.db"

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


def _cc_get(endpoint, params=None, use_cache=False, cache_ttl=60):
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
                    logger.warning(f"CC rate limit: {msg}")
                    time.sleep(5)
                else:
                    logger.error(f"CC API error: {msg}")
                return None
            if use_cache:
                _set_cache(cache_key, data)
            return data
        elif resp.status_code == 429:
            logger.warning("CC HTTP 429, sleeping 10s...")
            time.sleep(10)
            return None
        else:
            logger.error(f"CC HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"CC request error: {e}")
        return None


def _resolve_symbol(coin_id):
    if coin_id.upper().endswith("USDT"):
        return coin_id.upper().replace("USDT", "")
    return SYMBOL_MAP.get(coin_id.lower(), coin_id.upper())


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


def cc_get_ohlcv(symbol="BTC", interval="hour", limit=100, use_db_cache=True):
    fsym = _resolve_symbol(symbol)
    if use_db_cache:
        db_candles = _get_candles_from_db(fsym, interval, limit)
        if db_candles and len(db_candles) >= limit * 0.8:
            return db_candles
    if interval == "minute":
        endpoint = "/v2/histominute"
    elif interval == "day":
        endpoint = "/v2/histoday"
    else:
        endpoint = "/v2/histohour"
    params = {"fsym": fsym, "tsym": "USDT", "limit": limit}
    data = _cc_get(endpoint, params, use_cache=True, cache_ttl=CACHE_TTL_MEDIUM)
    if data and "Data" in data and "Data" in data["Data"]:
        candles = data["Data"]["Data"]
        if use_db_cache:
            _save_candles_to_db(fsym, interval, candles)
        return candles
    return None


def cc_get_price(symbol="BTC"):
    fsym = _resolve_symbol(symbol)
    params = {"fsym": fsym, "tsyms": "USDT"}
    data = _cc_get("/price", params, use_cache=True, cache_ttl=CACHE_TTL_SHORT)
    if data and "USDT" in data:
        return float(data["USDT"])
    return None


def cc_get_prices_multi(symbols):
    if not symbols:
        return {}
    fsyms = ",".join([_resolve_symbol(s) for s in symbols])
    params = {"fsyms": fsyms, "tsyms": "USDT"}
    data = _cc_get("/pricemulti", params, use_cache=True, cache_ttl=CACHE_TTL_SHORT)
    if data:
        return {k: float(v.get("USDT", 0)) for k, v in data.items()}
    return {}


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
    ohlcv_1h = cc_get_ohlcv(fsym, "hour", limit=100)
    if not ohlcv_1h or len(ohlcv_1h) < 50:
        logger.warning(f"Insufficient 1h data for {symbol}")
        return None
    ohlcv_4h = cc_get_ohlcv(fsym, "hour", limit=200)
    if not ohlcv_4h or len(ohlcv_4h) < 200:
        logger.warning(f"Insufficient 4h data for {symbol}")
        return None
    closes_1h = [float(k["close"]) for k in ohlcv_1h]
    highs_1h = [float(k["high"]) for k in ohlcv_1h]
    lows_1h = [float(k["low"]) for k in ohlcv_1h]
    volumes_1h = [float(k.get("volumeto", 0)) for k in ohlcv_1h]
    ema8_1h = calculate_ema(closes_1h, 8)
    ema50_1h = calculate_ema(closes_1h, 50)
    rsi_1h = calculate_rsi(closes_1h)
    closes_4h = closes_1h[::4]
    if len(closes_4h) < 50:
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
        current_price = cc_get_price(symbol)
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
    data = cc_get_ohlcv(symbol, cc_interval, limit)
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
    ohlcv = cc_get_ohlcv(fsym, "hour", 30)
    change_24h = _calculate_change_24h(ohlcv) if ohlcv else 0
    price = cc_get_price(symbol)
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
    return cc_get_price(symbol)


def test_binance_connection():
    logger.info("=" * 60)
    logger.info("CRYPTOCOMPARE API v4 TEST")
    logger.info("=" * 60)
    init_market_db()
    logger.info("1. OHLCV BTC (1h)...")
    ohlcv = cc_get_ohlcv("BTC", "hour", 5)
    if ohlcv:
        logger.info(f"   OK: {len(ohlcv)} candles, Close=${ohlcv[-1]['close']:,.2f}")
    else:
        logger.error("   FAIL")
        return
    logger.info("2. Current BTC price...")
    price = cc_get_price("BTC")
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
