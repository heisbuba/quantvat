import requests
import time
from decimal import Decimal, ROUND_HALF_UP

# --- Global Cache --- #
CACHE = {}
CACHE_DURATION = 120  # TTL set to 2 minutes

# Headers to mimic browser behavior and avoid basic scraping blocks
STEALTH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Connection": "keep-alive",
}

# --- Helper --- #
def format_compact(num):
    """Converts large numbers into human-readable strings (e.g., 1.5M, 2B)."""
    if num is None or num == 0: return "0"
    for unit in ['', 'K', 'M', 'B', 'T']:
        if abs(num) < 1000.0:
            return f"{num:,.2f}{unit}".replace(".00", "")
        num /= 1000.0
    return f"{num:,.2f}P"

def calculate_deep_dive(coin_id: str, user_keys: dict):
    """Fetches market data from CoinGecko, calculates ratios, and returns a structured payload."""
    coin_id = coin_id.strip().lower()
    
    # --- Cache Check --- #
    now = time.time()
    if coin_id in CACHE:
        cached_item = CACHE[coin_id]
        if now < cached_item['expires']:
            print(f"    ⚡ Serving {coin_id} from cache")
            return cached_item['data']

    # --- API Request Setup --- #
    cg_key = str(user_keys.get("COINGECKO_API_KEY", "")).strip()
    base_url = "https://api.coingecko.com/api/v3"
    headers = STEALTH_HEADERS.copy()
    
    # Handle both Demo and Pro API key formats
    if cg_key and cg_key != "CONFIG_REQUIRED_CG":
        headers["x-cg-demo-api-key" if cg_key.startswith("CG-") else "x-cg-pro-api-key"] = cg_key

    try:
        # Request only necessary fields to reduce payload size and latency
        url = (f"{base_url}/coins/{coin_id}?"
               "localization=false&"
               "tickers=false&"  
               "market_data=true&"
               "community_data=false&"
               "developer_data=false&"
               "sparkline=false&"
               "price_change_percentage=1h") 

        r = requests.get(url, headers=headers, timeout=15)
        
        if r.status_code == 429: return {"status": "error", "message": "Rate Limit Hit. Please wait."}
        if r.status_code != 200: return {"status": "error", "message": f"API {r.status_code}"}
            
        res = r.json()
        mkt = res.get('market_data', {})
        symbol = res.get('symbol', '').upper()
        
        # --- Logic Extraction ---
        mcap = mkt.get('market_cap', {}).get('usd', 0) or 0
        vol = mkt.get('total_volume', {}).get('usd', 0) or 0 
        
        p_ch_24h = mkt.get('price_change_percentage_24h', 0) or 0
        p_ch_1h = mkt.get('price_change_percentage_1h_in_currency', {}).get('usd', 0) or 0 
        
        d_vol = Decimal(str(vol))
        d_mcap = Decimal(str(mcap))
        
        # Volume-to-Market-Cap Ratio (VTMR)
        vtmr_val = (d_vol / d_mcap).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if d_mcap > 0 else 0
        
        # Volume-to-Price-Change (VTPC) - Proxy for liquidity absorption
        vtpc_val = vol / abs(p_ch_24h) if p_ch_24h != 0 else 0
        current_price = mkt.get('current_price', {}).get('usd', 0)

        # --- Link Construction --- #
        links = {
            "cg": f"https://www.coingecko.com/en/coins/{coin_id}",
            "tv": f"https://www.tradingview.com/chart/?symbol={symbol}USDT"
        }

        # --- Payload Construction --- #
        data_payload = {
            "status": "success",
            "vitals": {
                "name": res.get('name', 'Unknown'),
                "symbol": symbol,
                "price": f"${current_price:,.8f}" if current_price < 1 else f"${current_price:,.2f}",
                "mcap": f"${format_compact(mcap)}",
                "vol24h": f"${format_compact(vol)}"
            },
            "ratios": {
                "vtmr": f"{vtmr_val}x",
                "vtpc": f"${format_compact(vtpc_val)}"
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
            "links": links 
        }

        # --- Save to Cache --- #
        CACHE[coin_id] = {'data': data_payload, 'expires': now + CACHE_DURATION}
        
        return data_payload

    except Exception as e:
        return {"status": "error", "message": str(e)}