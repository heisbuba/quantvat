import time
import asyncio
import aiohttp
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

# --- Caches --- #
CACHE = {}                # snapshot data
CACHE_DURATION = 180       # 3 minutes (CoinGecko data latency is <4 min)

HIST_CACHE = {}            # 1-year historical aggregates
HIST_CACHE_DURATION = 3600 # 1 hour 

BASE_URL = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(connect=5, total=15)

STEALTH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Connection": "keep-alive",
}

# --- DexScreener (Liquidity) --- #
DEXSCREENER_ENDPOINT = "https://api.dexscreener.com/latest/dex/tokens/{address}"
DEXSCREENER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}
# Preference order when a token has contracts on multiple chains (CoinGecko's
# 'platforms' dict has no inherent ordering) - highest-liquidity chains first.
PLATFORM_PRIORITY = [
    'ethereum', 'binance-smart-chain', 'solana', 'arbitrum-one',
    'base', 'polygon-pos', 'avalanche', 'optimistic-ethereum'
]
MAX_LIQUIDITY_CHAINS = 8  # cap fan-out for tokens deployed on many chains (e.g. stablecoins)

# -------------------------------------------------------------------
# Helper Formatting Utilities
# -------------------------------------------------------------------

def format_compact(num):
    """Converts large numbers into human-readable strings (e.g., 1.5M, 2B)."""
    if num is None or num == 0: return "0"
    for unit in ['', 'K', 'M', 'B', 'T']:
        if abs(num) < 1000.0:
            return f"{num:,.2f}{unit}".replace(".00", "")
        num /= 1000.0
    return f"{num:,.2f}P"


def short_num(num: float) -> str:
    """Lightweight compact number formatter for technical indicators."""
    if not num: return "0"
    if abs(num) >= 1e9: return f"${num/1e9:.2f}B"
    if abs(num) >= 1e6: return f"${num/1e6:.2f}M"
    if abs(num) >= 1e3: return f"${num/1e3:.2f}K"
    return f"${num:.2f}"


def _build_headers(cg_key: str) -> dict:
    headers = STEALTH_HEADERS.copy()
    cg_key = (cg_key or "").strip()
    if cg_key and cg_key != "CONFIG_REQUIRED_CG":
        headers["x-cg-demo-api-key" if cg_key.startswith("CG-") else "x-cg-pro-api-key"] = cg_key
    return headers

# -------------------------------------------------------------------
# Quantitative Math & Technical Indicators
# -------------------------------------------------------------------

def compute_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI"""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_bollinger(prices: list[float], period: int = 20, mult: float = 2.0) -> Optional[dict]:
    if len(prices) < period:
        return None
    window = prices[-period:]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = sma + mult * std
    lower = sma - mult * std
    current = prices[-1]
    return {"sma": sma, "upper": upper, "lower": lower, "current": current}


def resample_to_hours(timestamped_prices: list[list], bucket_hours: int) -> list[float]:
    bucket_ms = bucket_hours * 60 * 60 * 1000
    buckets = {}
    for ts, price in timestamped_prices:
        key = ts // bucket_ms
        buckets[key] = price
    sorted_keys = sorted(buckets.keys())
    return [buckets[k] for k in sorted_keys]


def rsi_label(rsi: Optional[float]) -> str:
    if rsi is None: return "Unavailable"
    if rsi <= 30: return "Oversold"
    if rsi >= 70: return "Overbought"
    return "Neutral"


def format_mr_sentence(label: str, rsi: Optional[float], bb: Optional[dict]) -> str:
    if rsi is None or bb is None:
        return f"{label}: Insufficient data"
    price = bb["current"]
    lower = bb["lower"]
    upper = bb["upper"]
    sma = bb["sma"]

    pct_b = (price - lower) / (upper - lower) if upper != lower else 0.5

    if pct_b > 1.0: pos = "Above Upper (Overbought)"
    elif pct_b >= 0.8: pos = "Testing Upper"
    elif pct_b >= 0.6: pos = "Upper Half"
    elif pct_b >= 0.4: pos = "Middle Zone"
    elif pct_b >= 0.2: pos = "Lower Half"
    elif pct_b >= 0.0: pos = "Testing Lower"
    else: pos = "Below Lower (Oversold)"

    bandwidth = (upper - lower) / sma if sma != 0 else 0.0
    if bandwidth < 0.08: vol_state = "Squeeze"
    elif bandwidth < 0.20: vol_state = "Coiling"
    elif bandwidth < 0.40: vol_state = "Steady"
    else: vol_state = "Expansion"

    return (f"{label}: RSI {rsi:.1f} ({rsi_label(rsi)}) | "
            f"BB: {pos} (%b: {pct_b:.2f}) | "
            f"Vol: {vol_state} (BW: {bandwidth:.2f})")

# -------------------------------------------------------------------
# Async API Fetchers
# -------------------------------------------------------------------

def _extract_contract_addresses(platforms: dict) -> list[str]:
    """Pulls deduped, non-empty contract addresses from CoinGecko."""
    if not platforms:
        return []

    ordered, seen = [], set()
    for chain in PLATFORM_PRIORITY:
        addr = platforms.get(chain)
        if addr and addr.strip().lower() not in seen:
            seen.add(addr.strip().lower())
            ordered.append(addr.strip())
    for addr in platforms.values():
        if addr and addr.strip().lower() not in seen:
            seen.add(addr.strip().lower())
            ordered.append(addr.strip())

    return ordered[:MAX_LIQUIDITY_CHAINS]


async def _fetch_liquidity_async(session: aiohttp.ClientSession, contract_addresses: list[str]) -> Optional[float]:
    """Aggregate USD DEX liquidity across all chains a token is deployed on."""
    if not contract_addresses:
        return None

    target_addrs = {a.lower() for a in contract_addresses}

    async def fetch_one(addr: str) -> list:
        url = DEXSCREENER_ENDPOINT.format(address=addr)
        try:
            async with session.get(url, headers=DEXSCREENER_HEADERS) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        except Exception:
            return []
        return data.get("pairs") or []

    results = await asyncio.gather(*(fetch_one(a) for a in contract_addresses))

    # Keyed by pairAddress - a pool can surface from more than one queried
    # chain address (e.g. CREATE2 deployments sharing an address), so this
    # dedupes before summing instead of double-counting liquidity.
    seen_pairs = {}
    for pairs in results:
        for pair in pairs:
            base_addr = pair.get("baseToken", {}).get("address", "").lower()
            quote_addr = pair.get("quoteToken", {}).get("address", "").lower()
            if target_addrs & {base_addr, quote_addr}:
                seen_pairs[pair.get("pairAddress")] = pair.get("liquidity", {}).get("usd", 0.0) or 0.0

    return sum(seen_pairs.values())


async def _fetch_depth_async(session: aiohttp.ClientSession, coin_id: str, headers: dict) -> dict:
    url = f"{BASE_URL}/coins/{coin_id}/tickers"
    params = {"depth": "true", "order": "volume_desc", "limit": 1000}
    try:
        async with session.get(url, params=params, headers=headers) as r:
            if r.status != 200:
                return {"up": None, "down": None, "markets": 0}
            res = await r.json()
    except Exception:
        return {"up": None, "down": None, "markets": 0}

    tickers = res.get("tickers", []) or []
    total_up, total_down, count = 0.0, 0.0, 0
    
    for t in tickers:
        up = t.get("cost_to_move_up_usd")
        down = t.get("cost_to_move_down_usd")
        if up is not None and down is not None:
            total_up += float(up)
            total_down += float(down)
            count += 1

    if count == 0:
        return {"up": None, "down": None, "markets": 0}

    return {"up": total_up, "down": total_down, "markets": count}


async def _fetch_chart_async(session: aiohttp.ClientSession, coin_id: str, days: int, headers: dict) -> dict:
    url = f"{BASE_URL}/coins/{coin_id}/market_chart?vs_currency=usd&days={days}"
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            return await resp.json()
        raise Exception(f"Chart fetch failed for '{coin_id}' ({days}d): HTTP {resp.status}")


async def _fetch_snapshot_async(session: aiohttp.ClientSession, coin_id: str, headers: dict) -> dict:
    url = (f"{BASE_URL}/coins/{coin_id}?"
           "localization=false&tickers=false&market_data=true&"
           "community_data=false&developer_data=false&sparkline=false&"
           "price_change_percentage=1h")
    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 429:
                return {"status": "error", "message": "Rate Limit Hit. Please wait."}
            if r.status != 200:
                return {"status": "error", "message": f"API {r.status}"}
            res = await r.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    mkt = res.get('market_data', {})
    symbol = res.get('symbol', '').upper()

    mcap = mkt.get('market_cap', {}).get('usd', 0) or 0
    vol = mkt.get('total_volume', {}).get('usd', 0) or 0

    p_ch_24h = mkt.get('price_change_percentage_24h', 0) or 0
    p_ch_1h = mkt.get('price_change_percentage_1h_in_currency', {}).get('usd', 0) or 0

    d_vol = Decimal(str(vol))
    d_mcap = Decimal(str(mcap))

    vtmr_val = (d_vol / d_mcap).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if d_mcap > 0 else 0
    vtpc_val = vol / abs(p_ch_24h) if p_ch_24h != 0 else 0
    current_price = mkt.get('current_price', {}).get('usd', 0)

    links = {
        "cg": f"https://www.coingecko.com/en/coins/{coin_id}",
        "tv": f"https://www.tradingview.com/chart/?symbol={symbol}USDT"
    }

    contract_addresses = _extract_contract_addresses(res.get('platforms', {}))

    depth, liquidity_usd = await asyncio.gather(
        _fetch_depth_async(session, coin_id, headers),
        _fetch_liquidity_async(session, contract_addresses)
    )

    depth_payload = {
        "up": f"${format_compact(depth['up'])}" if depth["up"] is not None else "—",
        "down": f"${format_compact(depth['down'])}" if depth["down"] is not None else "—",
        "markets": depth["markets"]
    }

    liquidity_payload = "N/A" if liquidity_usd is None else f"${format_compact(liquidity_usd)}"

    return {
        "status": "success",
        "raw_price": current_price,
        "vitals": {
            "name": res.get('name', 'Unknown'),
            "symbol": symbol,
            "price": f"${current_price:,.8f}" if current_price < 1 else f"${current_price:,.2f}",
            "mcap": f"${format_compact(mcap)}",
            "vol24h": f"${format_compact(vol)}"
        },
        "ratios": {
            "vtmr": f"{vtmr_val}x",
            "vtpc": f"${format_compact(vtpc_val)}",
            "liquidity": liquidity_payload
        },
        "velocity": {
            "h1": f"{p_ch_1h:+.2f}%",
            "h24": f"{p_ch_24h:+.2f}%",
            "d7": f"{(mkt.get('price_change_percentage_7d') or 0):+.2f}%",
            "m1": f"{(mkt.get('price_change_percentage_30d') or 0):+.2f}%",
            "y1": f"{(mkt.get('price_change_percentage_1y') or 0):+.2f}%"
        },
        "supply": {
            "total": format_compact(mkt.get('total_supply', 0))
        },
        "depth": depth_payload,       
        "links": links
    }


def _compute_historical_metrics(prices: list, volumes: list) -> dict | None:
    n = min(len(prices), len(volumes))
    if n < 7:
        return None
    prices, volumes = prices[:n], volumes[:n]

    avg_7d_daily_vol = sum(volumes[-7:]) / min(7, n)
    avg_30d_vol = sum(volumes) / n

    total_dollar_vol = sum(p * v for p, v in zip(prices, volumes))
    total_vol = sum(volumes)
    vwap_30d = (total_dollar_vol / total_vol) if total_vol else 0.0

    return {"avg_7d_daily_vol": avg_7d_daily_vol, "avg_30d_vol": avg_30d_vol, "vwap_30d": vwap_30d}


async def _fetch_historical_async(session: aiohttp.ClientSession, coin_id: str, headers: dict) -> dict:
    placeholder = {"avg_7d_daily_vol": "—", "avg_30d_vol": "—", "vwap_30d": "—"}
    url = f"{BASE_URL}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": "30", "interval": "daily"}
    try:
        async with session.get(url, params=params, headers=headers) as r:
            if r.status != 200:
                return placeholder
            res = await r.json()
    except Exception:
        return placeholder

    prices = [p[1] for p in res.get("prices", []) if len(p) > 1]
    volumes = [v[1] for v in res.get("total_volumes", []) if len(v) > 1]

    metrics = _compute_historical_metrics(prices, volumes)
    if metrics is None:
        return placeholder

    return {
        "avg_7d_daily_vol": f"${format_compact(metrics['avg_7d_daily_vol'])}",
        "avg_30d_vol": f"${format_compact(metrics['avg_30d_vol'])}",
        "vwap_30d": f"${format_compact(metrics['vwap_30d'])}",
    }

# -------------------------------------------------------------------
# Primary Public Routines
# -------------------------------------------------------------------

async def _fetch_needed(coin_id: str, headers: dict, need_snapshot: bool, need_historical: bool):
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        coros = []
        if need_snapshot:
            coros.append(_fetch_snapshot_async(session, coin_id, headers))
        if need_historical:
            coros.append(_fetch_historical_async(session, coin_id, headers))
        results = await asyncio.gather(*coros)
    it = iter(results)
    snapshot = next(it) if need_snapshot else None
    historical = next(it) if need_historical else None
    return snapshot, historical


def calculate_deep_dive(coin_id: str, user_keys: dict) -> dict:
    """Fetches base snapshot + historical metrics for Deep Diver interface."""
    coin_id = coin_id.strip().lower()
    now = time.time()

    global CACHE, HIST_CACHE
    CACHE = {k: v for k, v in CACHE.items() if now < v['expires']}
    HIST_CACHE = {k: v for k, v in HIST_CACHE.items() if now < v['expires']}

    need_snapshot = coin_id not in CACHE
    need_historical = coin_id not in HIST_CACHE

    if need_snapshot or need_historical:
        headers = _build_headers(str(user_keys.get("COINGECKO_API_KEY", "")))
        snapshot, historical = asyncio.run(_fetch_needed(coin_id, headers, need_snapshot, need_historical))

        if need_snapshot:
            if snapshot.get("status") == "error":
                return snapshot
            CACHE[coin_id] = {"data": snapshot, "expires": time.time() + CACHE_DURATION}

        if need_historical:
            HIST_CACHE[coin_id] = {"data": historical, "expires": time.time() + HIST_CACHE_DURATION}

    payload = dict(CACHE[coin_id]["data"])
    payload["historical"] = HIST_CACHE[coin_id]["data"]
    return payload


async def get_mean_reversion_async(coin_id: str, user_keys: dict) -> dict:
    """Calculates live 1D, 4H, and 1H Mean Reversion metrics asynchronously.
    """
    coin_id = coin_id.strip().lower()

    api_key = str(user_keys.get("COINGECKO_API_KEY", "")).strip()
    if not api_key or api_key == "CONFIG_REQUIRED_CG":
        raise Exception("No CoinGecko API key configured. Add one in Settings to use Mean Reversion.")

    cached_entry = CACHE.get(coin_id)
    if not cached_entry or time.time() >= cached_entry["expires"]:
        raise Exception("Token data has expired. Please refresh the Deep Dive view before calculating Mean Reversion.")

    live_price = cached_entry["data"].get("raw_price")

    headers = _build_headers(api_key)

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        chart90, chart14, chart7 = await asyncio.gather(
            _fetch_chart_async(session, coin_id, 90, headers),
            _fetch_chart_async(session, coin_id, 14, headers),
            _fetch_chart_async(session, coin_id, 7, headers),
        )

    closes1d = resample_to_hours(chart90.get("prices", []), 24)
    closes4h = resample_to_hours(chart14.get("prices", []), 4)
    closes1h = [p[1] for p in chart7.get("prices", [])]

    if live_price:
        for closes in (closes1d, closes4h, closes1h):
            if closes:
                closes[-1] = live_price

    # Indicator Sentence Builds
    line1d = format_mr_sentence("1D", compute_rsi(closes1d), compute_bollinger(closes1d))
    line4h = format_mr_sentence("4H", compute_rsi(closes4h), compute_bollinger(closes4h))
    line1h = format_mr_sentence("1H", compute_rsi(closes1h), compute_bollinger(closes1h))

    return {
        "line1d": line1d,
        "line4h": line4h,
        "line1h": line1h,
    }