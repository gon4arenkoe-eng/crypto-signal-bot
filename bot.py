#!/usr/bin/env python3
"""
Crypto Signal Bot v6.0 — CoinGecko API
Работает на Railway, Render, Heroku и т.д. без прокси!
Без API key — keyless доступ. С API key — до 100 req/min.
"""

import os
import logging
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict
from functools import lru_cache

import requests
from flask import Flask, request, jsonify

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
CG_API_KEY = os.environ.get("CG_API_KEY", "")  # Опционально, для Demo/Analyst плана
CG_BASE_URL = "https://api.coingecko.com/api/v3"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Маппинг: Binance символ -> CoinGecko ID
COIN_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "BNB": "binancecoin",
    "UNI": "uniswap",
    "LTC": "litecoin",
    "ATOM": "cosmos",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "SEI": "sei-network",
    "TIA": "celestia",
    "INJ": "injective-protocol",
    "RENDER": "render-token",
    "FET": "fetch-ai",
    "PEPE": "pepe",
    "SHIB": "shiba-inu",
    "WLD": "worldcoin-wld",
    "STRK": "starknet",
    "IMX": "immutable-x",
    "GRT": "the-graph",
    "AAVE": "aave",
    "MKR": "maker",
    "LDO": "lido-dao",
    "CRV": "curve-dao-token",
    "SNX": "havven",
    "COMP": "compound-governance-token",
    "YFI": "yearn-finance",
    "1INCH": "1inch",
    "DYDX": "dydx",
    "SUSHI": "sushi",
    "BAL": "balancer",
    "FXS": "frax-share",
    "CVX": "convex-finance",
    "GMX": "gmx",
    "RDNT": "radiant-capital",
    "MAGIC": "magic",
    "ARB": "arbitrum",
    "OP": "optimism",
    "BASE": "base",
    "ZRO": "layerzero",
    "ENA": "ethena",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "FLOKI": "floki",
    "MEME": "memecoin",
    "TURBO": "turbo",
    "PEOPLE": "constitutiondao",
    "TRB": "tellor",
    "PENDLE": "pendle",
    "EIGEN": "eigenlayer",
    "ETHFI": "ether-fi",
    "REZ": "renzo",
    "SAGA": "saga-2",
    "OMNI": "omni-network",
    "DYM": "dymension",
    "MANTA": "manta-network",
    "ALT": "altlayer",
    "PIXEL": "pixels",
    "PORTAL": "portal",
    "AEVO": "aevo",
    "ETHENA": "ethena",
    "W": "wormhole",
    "ZK": "zksync",
    "BLAST": "blast",
    "MODE": "mode",
    "MNT": "mantle",
    "STRK": "starknet",
    "SC": "siacoin",
    "BEAM": "beam-2",
    "KAS": "kaspa",
    "TAO": "bittensor",
    "RNDR": "render-token",
    "AGIX": "singularitynet",
    "OCEAN": "ocean-protocol",
    "NMR": "numeraire",
    "RLC": "iexec-rlc",
    "GNT": "golem",
    "STORJ": "storj",
    "FIL": "filecoin",
    "AR": "arweave",
    "HNT": "helium",
    "IOTA": "iota",
    "XMR": "monero",
    "ZEC": "zcash",
    "DASH": "dash",
    "ETC": "ethereum-classic",
    "BCH": "bitcoin-cash",
    "BSV": "bitcoin-cash-sv",
    "XLM": "stellar",
    "ALGO": "algorand",
    "XTZ": "tezos",
    "EOS": "eos",
    "TRX": "tron",
    "VET": "vechain",
    "ICP": "internet-computer",
    "THETA": "theta-token",
    "CHZ": "chiliz",
    "ENJ": "enjincoin",
    "SAND": "the-sandbox",
    "MANA": "decentraland",
    "AXS": "axie-infinity",
    "GALA": "gala",
    "ILV": "illuvium",
    "YGG": "yield-guild-games",
    "MC": "merit-circle",
    "BIGTIME": "big-time",
    "PYR": "vulcan-forged",
    "UOS": "ultra",
    "ALICE": "my-neighbor-alice",
    "TLM": "alien-worlds",
    "ATLAS": "star-atlas",
    "POLIS": "star-atlas-dao",
    "AURY": "aurory",
    "GENE": "genopets",
    "GST": "green-satoshi-token",
    "GMT": "stepn",
    "FITFI": "step-app",
    "MOVE": "move",
    "PRIME": "echelon-prime",
    "DMT": "dark-matter",
    "XAI": "xai",
    "RON": "ronin",
    "IMX": "immutable-x",
    "GODS": "gods-unchained",
    "SUPER": "superfarm",
    "BICO": "biconomy",
    "ZETA": "zetachain",
    "DEGEN": "degen-base",
    "AERO": "aerodrome-finance",
    "VELO": "velodrome-finance",
    "SYN": "synapse-2",
    "ACX": "across-protocol",
    "STG": "stargate-finance",
    "CAKE": "pancakeswap-token",
    "JOE": "joe",
    "GRAIL": "camelot-token",
    "RAM": "ramses-exchange",
    "THENX": "thena",
    "THE": "thena",
    "BANANA": "banana-gun",
    "MAV": "maverick-protocol",
    "IZI": "izumi-finance",
    "MERL": "merlin-chain",
    "BOME": "book-of-meme",
    "SLERF": "slerf",
    "MEW": "cat-in-a-dogs-world",
    "POPCAT": "popcat",
    "MOG": "mog-coin",
    "MICHI": "michicoin",
    "BRETT": "brett",
    "ANDY": "andy",
    "APU": "apu-apustaja",
    "BOBO": "bobo",
    "PEPEC": "pepe-2",
    "PEIPEI": "peipei",
    "PEPEW": "pepew",
    "PEPE2": "pepe-2",
    "PEPE3": "pepe-3",
    "PEPE4": "pepe-4",
    "PEPE5": "pepe-5",
    "PEPE6": "pepe-6",
    "PEPE7": "pepe-7",
    "PEPE8": "pepe-8",
    "PEPE9": "pepe-9",
    "PEPE10": "pepe-10",
}

# Обратный маппинг для отображения
ID_TO_SYMBOL = {v: k for k, v in COIN_MAP.items()}

WATCHLIST = [
    "BTC", "ETH", "SOL", "XRP", "DOGE",
    "ADA", "DOT", "LINK", "AVAX", "MATIC",
    "BNB", "UNI", "LTC", "ATOM", "NEAR",
    "APT", "ARB", "OP", "SUI", "SEI",
    "TIA", "INJ", "RENDER", "FET", "PEPE",
    "SHIB", "WLD", "STRK", "IMX", "GRT",
    "AAVE", "MKR", "LDO", "CRV", "SNX",
    "COMP", "YFI", "1INCH", "DYDX", "SUSHI",
    "BAL", "FXS", "CVX", "GMX", "RDNT",
    "MAGIC", "WIF", "BONK", "FLOKI", "MEME",
    "TURBO", "PEOPLE", "TRB", "PENDLE", "EIGEN",
    "ETHFI", "REZ", "SAGA", "OMNI", "DYM",
    "MANTA", "ALT", "PIXEL", "PORTAL", "AEVO",
    "W", "ZK", "MNT", "KAS", "TAO",
    "RNDR", "AGIX", "OCEAN", "FIL", "AR",
    "XMR", "BCH", "XLM", "ALGO", "XTZ",
    "TRX", "VET", "ICP", "THETA", "CHZ",
    "SAND", "MANA", "AXS", "GALA", "GMT",
    "PRIME", "RON", "SUPER", "ZETA", "DEGEN",
    "AERO", "VELO", "CAKE", "JOE", "BANANA",
    "MAV", "BOME", "SLERF", "MEW", "POPCAT",
    "MOG", "BRETT", "ANDY", "APU", "BOBO",
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

# ============ COINGECKO API ============

class CoinGeckoAPI:
    """Класс для работы с CoinGecko API с rate limiting и кэшированием"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.base_url = CG_BASE_URL
        self.session = requests.Session()
        self.last_request_time = 0
        self.min_delay = 0.6 if not api_key else 0.01  # 0.6s без ключа, 0.01s с ключом

        if api_key:
            self.session.headers.update({"x-cg-demo-api-key": api_key})

    def _rate_limit(self):
        """Простой rate limiter"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """GET запрос с rate limiting"""
        self._rate_limit()
        try:
            url = f"{self.base_url}/{endpoint}"
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                logger.warning("Rate limit hit! Waiting 60s...")
                time.sleep(60)
                return self._get(endpoint, params)  # Retry
            else:
                logger.error(f"CoinGecko error {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            logger.error(f"CoinGecko request error: {e}")
            return None

    def get_simple_price(self, ids: List[str], vs_currencies: str = "usd",
                         include_24hr_change: bool = True,
                         include_24hr_vol: bool = True,
                         include_market_cap: bool = True) -> Optional[dict]:
        """Простые цены (до 250 ID за запрос)"""
        ids_str = ",".join(ids)
        params = {
            "ids": ids_str,
            "vs_currencies": vs_currencies,
            "include_24hr_change": str(include_24hr_change).lower(),
            "include_24hr_vol": str(include_24hr_vol).lower(),
            "include_market_cap": str(include_market_cap).lower(),
            "include_last_updated_at": "true",
        }
        return self._get("simple/price", params)

    def get_markets(self, vs_currency: str = "usd", per_page: int = 250,
                    page: int = 1, sparkline: bool = False) -> Optional[List[dict]]:
        """Рыночные данные топ-монет"""
        params = {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": str(sparkline).lower(),
            "price_change_percentage": "24h,7d",
        }
        return self._get("coins/markets", params)

    def get_coin_data(self, coin_id: str) -> Optional[dict]:
        """Детальные данные по монете"""
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        }
        return self._get(f"coins/{coin_id}", params)

    def get_market_chart(self, coin_id: str, vs_currency: str = "usd",
                         days: int = 30) -> Optional[dict]:
        """Исторические данные: prices, market_caps, total_volumes"""
        params = {
            "vs_currency": vs_currency,
            "days": days,
            "interval": "daily" if days > 90 else "hourly",
        }
        return self._get(f"coins/{coin_id}/market_chart", params)

    def get_ohlc(self, coin_id: str, vs_currency: str = "usd",
                 days: int = 30) -> Optional[List[List]]:
        """OHLCV данные: [[timestamp, open, high, low, close], ...]"""
        params = {
            "vs_currency": vs_currency,
            "days": days,
        }
        return self._get(f"coins/{coin_id}/ohlc", params)

    def search(self, query: str) -> Optional[List[dict]]:
        """Поиск монет"""
        params = {"query": query}
        result = self._get("search", params)
        return result.get("coins", []) if result else None


# Глобальный инстанс API
cg = CoinGeckoAPI(api_key=CG_API_KEY)


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
    result = tg_post("setWebhook", {"url": url})
    logger.info(f"Webhook set: {result}")
    return result


def delete_webhook():
    result = tg_post("deleteWebhook")
    logger.info(f"Webhook deleted: {result}")
    return result


# ============ КЭШИРОВАНИЕ ============

class Cache:
    """Простой in-memory кэш с TTL"""
    def __init__(self, ttl: int = 60):
        self._cache = {}
        self.ttl = ttl

    def get(self, key: str):
        if key in self._cache:
            data, timestamp = self._cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            del self._cache[key]
        return None

    def set(self, key: str, data):
        self._cache[key] = (data, time.time())

    def clear(self):
        self._cache.clear()


cache = Cache(ttl=60)


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


def calc_atr(ohlcv: List[List], period: int = 14) -> float:
    """ATR из OHLCV: [[timestamp, open, high, low, close], ...]"""
    if len(ohlcv) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(ohlcv)):
        high = float(ohlcv[i][2])
        low = float(ohlcv[i][3])
        prev_close = float(ohlcv[i-1][4])
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


def calc_support_resistance(ohlcv: List[List], lookback: int = 20):
    recent = ohlcv[-lookback:]
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

def get_ohlcv_from_market_chart(coin_id: str, days: int = 30) -> Optional[List[List]]:
    """
    Конвертируем market_chart данные в OHLCV формат.
    CoinGecko market_chart возвращает prices, market_caps, total_volumes
    без OHLC, поэтому аппроксимируем через OHLC эндпоинт.
    """
    ohlc = cg.get_ohlc(coin_id, days=days)
    if ohlc and len(ohlc) > 0:
        # OHLC возвращает: [[timestamp, open, high, low, close], ...]
        return ohlc
    return None


def get_prices_and_volumes(coin_id: str, days: int = 30) -> tuple:
    """Получить цены и объёмы для анализа"""
    cache_key = f"chart_{coin_id}_{days}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    chart = cg.get_market_chart(coin_id, days=days)
    if not chart:
        return None, None

    prices = [p[1] for p in chart.get("prices", [])]
    volumes = [v[1] for v in chart.get("total_volumes", [])]

    result = (prices, volumes)
    cache.set(cache_key, result)
    return result


def analyze_symbol(symbol: str) -> Optional[dict]:
    """
    Анализ монеты через CoinGecko API.
    symbol: например "BTC", "ETH"
    """
    coin_id = COIN_MAP.get(symbol.upper())
    if not coin_id:
        logger.warning(f"Unknown symbol: {symbol}")
        return None

    # 1. Получаем текущие данные (кэшируем на 30 сек)
    cache_key = f"price_{coin_id}"
    price_data = cache.get(cache_key)

    if not price_data:
        price_data = cg.get_simple_price([coin_id], include_24hr_change=True, include_24hr_vol=True)
        if price_data:
            cache.set(cache_key, price_data)

    if not price_data or coin_id not in price_data:
        return None

    coin_info = price_data[coin_id]
    price = float(coin_info.get("usd", 0))
    price_change = float(coin_info.get("usd_24h_change", 0) or 0)
    volume_24h = float(coin_info.get("usd_24h_vol", 0) or 0)
    market_cap = float(coin_info.get("usd_market_cap", 0) or 0)

    if price == 0:
        return None

    # 2. Получаем исторические данные
    prices, volumes = get_prices_and_volumes(coin_id, days=30)
    if not prices or len(prices) < 50:
        # Пробуем 7 дней если 30 недоступно
        prices, volumes = get_prices_and_volumes(coin_id, days=7)
        if not prices or len(prices) < 50:
            return None

    # 3. Получаем OHLC для ATR и уровней
    ohlcv = get_ohlcv_from_market_chart(coin_id, days=30)
    if not ohlcv or len(ohlcv) < 20:
        ohlcv = get_ohlcv_from_market_chart(coin_id, days=7)

    # 4. Технический анализ
    closes = prices

    # Используем hourly данные, разбиваем на 1H и 4H
    # prices приходят почасовые (если days <= 90)
    closes_1h = closes
    closes_4h = closes[::4] if len(closes) >= 4 * 14 else closes

    rsi_1h = calc_rsi(closes_1h)
    rsi_4h = calc_rsi(closes_4h) if len(closes_4h) > 14 else rsi_1h
    macd_val, signal_val, histogram = calc_macd(closes_1h)

    # ATR и уровни из OHLCV
    if ohlcv and len(ohlcv) > 20:
        atr = calc_atr(ohlcv)
        bb_upper, bb_mid, bb_lower = calc_bollinger([float(k[4]) for k in ohlcv])
        resistance, support = calc_support_resistance(ohlcv)
        high_24h = max([float(k[2]) for k in ohlcv[-24:]]) if len(ohlcv) >= 24 else max([float(k[2]) for k in ohlcv])
        low_24h = min([float(k[3]) for k in ohlcv[-24:]]) if len(ohlcv) >= 24 else min([float(k[3]) for k in ohlcv])
    else:
        # Fallback: используем только цены
        atr = abs(price * 0.02)  # ~2% как fallback ATR
        bb_upper, bb_mid, bb_lower = calc_bollinger(closes_1h)
        recent = closes_1h[-20:]
        resistance = round(max(recent), 2)
        support = round(min(recent), 2)
        high_24h = max(closes_1h[-24:]) if len(closes_1h) >= 24 else max(closes_1h)
        low_24h = min(closes_1h[-24:]) if len(closes_1h) >= 24 else min(closes_1h)

    volume_spike = calc_volume_spike(volumes) if volumes else 1.0

    # 5. Генерация сигнала
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

    # 6. Расчёт уровней
    levels = {}
    if signal in ["BUY", "SELL"]:
        levels = calculate_levels(signal, price, atr, support, resistance, bb_upper, bb_lower)
        if levels.get("risk_reward_1", 0) < SIGNAL_CONFIG["risk_reward_min"]:
            signal = None
            strength = 0
            reasons.append("Отменено: R:R слишком низкое")
            levels = {}

    return {
        "symbol": symbol.upper(),
        "price": price,
        "price_change_24h": price_change,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "quote_volume_24h": volume_24h,
        "market_cap": market_cap,
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
    market_cap = a.get("market_cap", 0)

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

    # Форматируем market cap
    if market_cap >= 1e9:
        mc_str = f"${market_cap/1e9:.1f}B"
    elif market_cap >= 1e6:
        mc_str = f"${market_cap/1e6:.1f}M"
    else:
        mc_str = f"${market_cap:,.0f}"

    lines = []
    lines.append(f"{emoji} <b>{symbol}/USDT</b> {emoji}")
    lines.append("")
    lines.append(f"<b>{signal_text}</b>")
    lines.append(f"Сила сигнала: {stars}")
    lines.append("")
    lines.append(f"💰 <b>Текущая цена:</b> <code>${price:,.4f}</code>")
    lines.append(f"{change_emoji} <b>Изменение 24ч:</b> <code>{change:+.2f}%</code>")
    lines.append(f"📊 <b>Объём 24ч:</b> <code>${a['quote_volume_24h']:,.0f}</code>")
    lines.append(f"🏦 <b>Капитализация:</b> <code>{mc_str}</code>")

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
        lines.append(f"║  🎯 <b>Вход:</b>          <code>${entry:,.4f}</code>           ║")
        lines.append(f"║  🛑 <b>Стоп-лосс:</b>    <code>${sl:,.4f}</code>  ({sl_pct:.1f}%)   ║")
        lines.append("╠════════════════════════════════════════╣")
        lines.append(f"║  💎 <b>Тейк 1:</b>       <code>${tp1:,.4f}</code>  (+{tp1_pct:.1f}%)  ║")
        lines.append(f"║  💎💎 <b>Тейк 2:</b>     <code>${tp2:,.4f}</code>  (+{tp2_pct:.1f}%)  ║")
        lines.append(f"║  💎💎💎 <b>Тейк 3:</b>   <code>${tp3:,.4f}</code>  (+{tp3_pct:.1f}%)  ║")
        lines.append("╠════════════════════════════════════════╣")
        lines.append(f"║  ⚖️ <b>Risk/Reward:</b>  <code>1:{rr}</code>              ║")
        lines.append(f"║  📏 <b>Риск:</b>         <code>${risk:,.4f}</code>           ║")
        lines.append("╚════════════════════════════════════════╝")
        lines.append("")
        lines.append("<b>📋 План сделки:</b>")
        lines.append(f"  1️⃣ Вход по рынку или лимиткой ~${entry:,.4f}")
        lines.append(f"  2️⃣ Стоп-лосс на ${sl:,.4f} ({sl_pct:.1f}%)")
        lines.append(f"  3️⃣ 50% позиции закрыть на TP1 (${tp1:,.4f})")
        lines.append(f"  4️⃣ 30% позиции закрыть на TP2 (${tp2:,.4f})")
        lines.append(f"  5️⃣ 20% позиции закрыть на TP3 (${tp3:,.4f})")
        lines.append("  6️⃣ Стоп в безубыток после достижения TP1")

    lines.append("")
    lines.append(f"📉 <b>RSI:</b> <code>{a['rsi_1h']} (1H)</code> | <code>{a['rsi_4h']} (4H)</code>")
    lines.append(f"📊 <b>MACD:</b> <code>{a['macd']}</code> | Signal: <code>{a['macd_signal']}</code>")
    lines.append(f"📦 <b>Всплеск объёма:</b> <code>x{a['volume_spike']}</code>")
    lines.append(f"📏 <b>ATR:</b> <code>${a['atr']:,.4f}</code>")
    lines.append("")
    lines.append("<b>Уровни:</b>")
    lines.append(f"  🔺 Сопротивление: <code>${a['resistance']:,.4f}</code>")
    lines.append(f"  🔻 Поддержка: <code>${a['support']:,.4f}</code>")
    lines.append(f"  ⬆️ BB Upper: <code>${a['bb_upper']:,.4f}</code>")
    lines.append(f"  ⬇️ BB Lower: <code>${a['bb_lower']:,.4f}</code>")
    lines.append("")
    lines.append("🔍 <b>Причины сигнала:</b>")
    for r in reasons:
        lines.append(f"  • {r}")
    lines.append("")
    lines.append(f"⏰ <i>Обновлено: {a['timestamp']}</i>")
    lines.append("")
    lines.append("⚠️ <i>Не финансовый совет. Управляй рисками. DYOR!</i>")
    lines.append("")
    lines.append("<i>💡 Данные: CoinGecko API</i>")

    return "\n".join(lines)


def format_watchlist(analyses: List[dict]) -> str:
    lines = []
    lines.append("📊 <b>ОБЗОР РЫНКА</b>")
    lines.append("")
    lines.append("<code>АКТИВ    ЦЕНА          24Ч%   RSI   СИГНАЛ  R:R</code>")
    lines.append("<code>─────────────────────────────────────────────────</code>")

    for a in analyses:
        sym = a["symbol"].ljust(6)
        price = f"${a['price']:,.4f}".rjust(12)
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
    lines.append("")
    lines.append("<i>💡 Данные: CoinGecko API</i>")
    return "\n".join(lines)


# ============ ОБРАБОТЧИКИ КОМАНД ============

def cmd_start(chat_id: int):
    welcome = ("🤖 <b>Crypto Signal Bot v6.0</b>\n\n"
        "Я анализирую рынок через <b>CoinGecko API</b> и даю готовые торговые сигналы с:\n"
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
    symbol = args[0].upper() if args else "BTC"

    if symbol not in COIN_MAP:
        send_message(chat_id, f"❌ {symbol} не найден. Попробуй /watchlist")
        return

    msg = send_message(chat_id, f"🔍 Анализ {symbol}/USDT...")

    a = analyze_symbol(symbol)
    if not a:
        edit_message(chat_id, msg["result"]["message_id"], "❌ Не удалось получить данные. Попробуй позже.")
        return

    text = format_signal(a)

    kb = {
        "inline_keyboard": [
            [{"text": "🔄 Обновить", "callback_data": f"signal_{a['symbol']}"}],
            [{"text": "📊 CoinGecko", "url": f"https://www.coingecko.com/en/coins/{COIN_MAP[a['symbol']]}"}]
        ]
    }
    edit_message(chat_id, msg["result"]["message_id"], text, reply_markup=kb)


def cmd_signals(chat_id: int):
    msg = send_message(chat_id, "🔍 Ищу сигналы...")

    analyses = []
    for s in WATCHLIST[:15]:  # Ограничиваем для скорости
        a = analyze_symbol(s)
        if a and a["signal"] in ["BUY", "SELL"]:
            analyses.append(a)
        time.sleep(0.1)  # Rate limiting

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
        lines.append(f"   Вход: ${entry:,.4f} | SL: ${sl:,.4f} | TP1: ${tp1:,.4f}")
        lines.append(f"   RSI: {a['rsi_1h']} | 24ч: {a['price_change_24h']:+.1f}%")
        lines.append("")

    kb = {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "signals_refresh"}]]}
    edit_message(chat_id, msg["result"]["message_id"], "\n".join(lines), reply_markup=kb)


def cmd_watchlist(chat_id: int):
    msg = send_message(chat_id, "📊 Загружаю рынок...")

    analyses = []
    for s in WATCHLIST[:20]:  # Ограничиваем для скорости
        a = analyze_symbol(s)
        if a:
            analyses.append(a)
        time.sleep(0.1)

    analyses.sort(key=lambda x: x.get("quote_volume_24h", 0), reverse=True)

    text = format_watchlist(analyses)

    kb = {"inline_keyboard": [[{"text": "🔄 Обновить", "callback_data": "watchlist"}]]}
    edit_message(chat_id, msg["result"]["message_id"], text, reply_markup=kb)


def cmd_top(chat_id: int):
    msg = send_message(chat_id, "🔝 Ищу топ...")

    # Получаем топ-100 по market cap
    markets = cg.get_markets(per_page=100)
    if not markets:
        edit_message(chat_id, msg["result"]["message_id"], "❌ Ошибка получения данных")
        return

    # Сортируем по абсолютному изменению 24ч
    markets.sort(key=lambda x: abs(x.get("price_change_percentage_24h_in_currency", 0) or 0), reverse=True)

    lines = ["🔝 <b>ТОП-10 ДВИЖЕНИЙ (24ч)</b>", ""]

    for i, t in enumerate(markets[:10], 1):
        sym = t["symbol"].upper()
        change = t.get("price_change_percentage_24h_in_currency", 0) or 0
        price = t.get("current_price", 0)
        vol = t.get("total_volume", 0)
        mc = t.get("market_cap", 0)
        emoji = "🚀" if change > 10 else "📈" if change > 0 else "💥" if change < -10 else "📉"

        lines.append(f"{i}. {emoji} <b>{sym}</b>")
        lines.append(f"   ${price:,.4f} | {change:+.2f}% | Объём: ${vol/1e6:.1f}M | Кап: ${mc/1e9:.1f}B")
        lines.append("")

    edit_message(chat_id, msg["result"]["message_id"], "\n".join(lines))


def cmd_scanner(chat_id: int):
    msg = send_message(chat_id, "🔍 Сканирую рынок на лучшие сигналы...\nЭто может занять 20-30 секунд...")

    results = []
    for s in WATCHLIST[:15]:
        a = analyze_symbol(s)
        if a:
            results.append(a)
        time.sleep(0.1)

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
        lines.append(f"   🎯 Вход: ${levels['entry']:,.4f}")
        lines.append(f"   🛑 Стоп: ${levels['stop_loss']:,.4f} ({abs((levels['stop_loss']-levels['entry'])/levels['entry']*100):.1f}%)")
        lines.append(f"   💎 TP1: ${levels['take_profit_1']:,.4f} | TP2: ${levels['take_profit_2']:,.4f} | TP3: ${levels['take_profit_3']:,.4f}")
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
    status = (f"🤖 <b>Статус бота v6.0</b>\n\n"
        f"✅ Онлайн\n"
        f"📡 CoinGecko API\n"
        f"🔑 API Key: {'✅ Да' if CG_API_KEY else '❌ Нет (keyless)'}\n"
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
        symbol = data.split("_")[1].upper()
        a = analyze_symbol(symbol)
        if a:
            text = format_signal(a)
            kb = {
                "inline_keyboard": [
                    [{"text": "🔄 Обновить", "callback_data": f"signal_{a['symbol']}"}],
                    [{"text": "📊 CoinGecko", "url": f"https://www.coingecko.com/en/coins/{COIN_MAP.get(a['symbol'], '')}"}]
                ]
            }
            edit_message(chat_id, message_id, text, reply_markup=kb)

    elif data == "watchlist":
        analyses = []
        for s in WATCHLIST[:20]:
            a = analyze_symbol(s)
            if a:
                analyses.append(a)
            time.sleep(0.1)
        analyses = [r for r in analyses if r]
        analyses.sort(key=lambda x: x.get("quote_volume_24h", 0), reverse=True)
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
    return 'Crypto Signal Bot v6.0 (CoinGecko) is running!'


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
    return jsonify({"status": "ok", "bot": "running", "api": "coingecko"})


# ============ ЗАПУСК ============

def init_webhook():
    """Установить webhook при старте"""
    if WEBHOOK_URL:
        webhook_full_url = f"{WEBHOOK_URL}/webhook"
        delete_webhook()
        result = set_webhook(webhook_full_url)
        logger.info(f"Webhook URL: {webhook_full_url}")
        logger.info(f"Webhook result: {result}")


if __name__ == "__main__":
    threading.Thread(target=init_webhook, daemon=True).start()

    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Запуск Crypto Signal Bot v6.0 (CoinGecko) на порту {port}...")
    logger.info(f"📡 API Key: {'✅' if CG_API_KEY else '❌ Keyless (rate limit ~30 req/min)'}")
    app.run(host="0.0.0.0", port=port)
