import time
import datetime
import threading
import requests
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import Shared Modules
from src.state import get_user_temp_dir
from src.config import STABLECOINS
from src.services.utils import short_num, now_str  

# --- HARDCODED CONFIGURATION ---
CG_DEMO_API_KEY = "CG-r5a3XQVMhkMC4fQGMuWCw1gp"

def spot_volume_tracker(user_keys, user_id) -> None:
    """
    Aggregates spot market data with accuracy and performance.
    Prioritizes CoinGecko data for volume accuracy.
    """
    print("   📊 Starting fresh spot analysis...")
    
    # Set thread name once at the start
    threading.current_thread().name = f"user_{user_id}"
    
    # Extract API keys
    CMC_API_KEY = user_keys.get("CMC_API_KEY", "CONFIG_REQUIRED_CMC")
    LIVECOINWATCH_API_KEY = user_keys.get("LIVECOINWATCH_API_KEY", "CONFIG_REQUIRED_LCW")
    COINRANKINGS_API_KEY = user_keys.get("COINRANKINGS_API_KEY", "CONFIG_REQUIRED_CR")

    # --- Stealth Headers Injection ---
    STEALTH_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
                                     
    def create_html_report(hot_tokens: List[Dict[str, Any]]) -> str:
        """Generates an HTML report table for the identified high-volume spot tokens."""
        date_prefix = datetime.datetime.now().strftime("%b-%d-%y_%H-%M")
        
        # Use user isolated directory
        user_dir = get_user_temp_dir(user_id) 
        html_file = user_dir / f"Volumed_Spot_Tokens_{date_prefix}.html"
        
        current_time = now_str("%d-%m-%Y %H:%M:%S")

        max_flip = max((t.get('flipping_multiple', 0) for t in hot_tokens), default=0)
        high_volume = len([t for t in hot_tokens if t.get('flipping_multiple', 0) >= 2])
        large_cap_count = len([t for t in hot_tokens if t.get('large_cap')])

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Crypto Volume Tracker v2.5</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                .header {{ text-align: center; background-color: #2c3e50; color: white; padding: 20px; border-radius: 10px; }}
                .summary {{ background-color: #34495e; color: white; padding: 15px; border-radius: 8px; margin: 10px 0; }}
                .table {{ width: 100%; border-collapse: collapse; background-color: white; }}
                .table th {{ background-color: #3498db; color: white; padding: 12px; text-align: left; }}
                .table td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
                .table tr:nth-child(even) {{ background-color: #f2f2f2; }}
                .table tr:hover {{ background-color: #e8f4f8; }}
                .footer {{ text-align: center; margin-top: 20px; color: #7f8c8d; }}
                .large-cap {{ background-color: #e8f6f3 !important; }}
                .high-volume {{ color: #e74c3c; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>SPOT VOLUME CRYPTO TRACKER v2.5</h1>
                <p>Volume-driven Spot Tokens Analysis</p>
                <p><small>Generated on: {current_time}</small></p>
            </div>
            <div class="summary">
                <h3>Summary</h3>
                <p>Total High-Volume Tokens: {len(hot_tokens)}</p>
                <p>Peak Flipping (VTMR) Multiple: {max_flip:.1f}x</p>
                <p>Large-Cap Tokens (>$1B): {large_cap_count}</p>
            </div>
        """

        if hot_tokens:
            html_content += """
            <table class="table">
                <tr>
                    <th>Rank</th>
                    <th>Ticker</th>
                    <th>Market Cap</th>
                    <th>Volume 24h</th>
                    <th>Spot VTMR</th>
                    <th>Sources</th>
                    <th>Large Cap</th>
                </tr>
            """
            for i, token in enumerate(hot_tokens):
                row_class = "large-cap" if token.get('large_cap') else ""
                volume_class = "high-volume" if token.get('flipping_multiple', 0) >= 2 else ""
                html_content += f"""
                <tr class="{row_class}">
                    <td>#{i+1}</td>
                    <td><b>{token.get('symbol')}</b></td>
                    <td>${short_num(token.get('marketcap', 0))}</td>
                    <td>${short_num(token.get('volume', 0))}</td>
                    <td class="{volume_class}">{token.get('flipping_multiple', 0):.1f}x</td>
                    <td>{token.get('source_count')}</td>
                    <td>{'Yes' if token.get('large_cap') else 'No'}</td>
                </tr>
                """
            html_content += "</table>"
        else:
            html_content += "<div style='text-align: center; padding: 40px;'><h3>No high-volume tokens found</h3></div>"

        html_content += f"""
            <div class="footer">
                <p>Generated by Spot Volume Crypto Tracker v2.5</p>
            </div>
        </body>
        </html>
        """
        
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        return html_file

    # --- Data Fetching Functions ---

    def fetch_coingecko(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
        tokens: List[Dict[str, Any]] = []
        use_key = bool(CG_DEMO_API_KEY and CG_DEMO_API_KEY not in ["YOUR_COINGECKO_DEMO_KEY_HERE", ""])
        
        for page in range(1, 5):
            try:
                url = "https://api.coingecko.com/api/v3/coins/markets"
                params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page}
                headers = STEALTH_HEADERS.copy()
                
                if use_key:
                    if page == 1: print("   Scanning CoinGecko (User API - Fast Mode)...")
                    headers["x-cg-demo-api-key"] = CG_DEMO_API_KEY
                    delay = 0.05
                else:
                    if page == 1: print("   Scanning CoinGecko (Public API)...")
                    delay = 0.2
                
                r = session.get(url, params=params, headers=headers, timeout=15)
                if use_key and r.status_code in [401, 403, 429]:
                    use_key = False
                    delay = 0.2
                    r = session.get(url, params=params, headers=STEALTH_HEADERS, timeout=15)
                
                r.raise_for_status()
                for t in r.json():
                    symbol = (t.get("symbol") or "").upper()
                    if symbol in STABLECOINS: continue
                    vol, mc = float(t.get("total_volume") or 0), float(t.get("market_cap") or 0)
                    
                    # Fetching pre-filter
                    if mc and vol >= 0.5 * mc:
                        tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "CG"})
                time.sleep(delay)
            except Exception: continue
        print(f"   CoinGecko: {len(tokens)} tokens")
        return tokens

    def fetch_coinmarketcap(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
        tokens: List[Dict[str, Any]] = []
        if not CMC_API_KEY or CMC_API_KEY == "CONFIG_REQUIRED_CMC": return tokens

        print("   Scanning CoinMarketCap...")
        headers = STEALTH_HEADERS.copy()
        headers["X-CMC_PRO_API_KEY"] = CMC_API_KEY
        for start in range(1, 1001, 100):
            try:
                r = session.get("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest", 
                                headers=headers, params={"start": start, "limit": 100, "convert": "USD"}, timeout=15)
                r.raise_for_status()
                for t in r.json().get("data", []):
                    symbol = (t.get("symbol") or "").upper()
                    if symbol in STABLECOINS: continue
                    q = t.get("quote", {}).get("USD", {})
                    vol, mc = float(q.get("volume_24h") or 0), float(q.get("market_cap") or 0)
                    if mc and vol >= 0.5 * mc:
                        tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "CMC"})
                time.sleep(0.2)
            except Exception: continue
        print(f"   CoinMarketCap: {len(tokens)} tokens")
        return tokens

    def fetch_livecoinwatch(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
        tokens: List[Dict[str, Any]] = []
        if not LIVECOINWATCH_API_KEY or LIVECOINWATCH_API_KEY == "CONFIG_REQUIRED_LCW": return tokens

        print("   Scanning LiveCoinWatch...")
        headers = STEALTH_HEADERS.copy()
        headers.update({"content-type": "application/json", "x-api-key": LIVECOINWATCH_API_KEY})
        payload = {"currency": "USD", "sort": "rank", "order": "ascending", "offset": 0, "limit": 1000, "meta": True}
        try:
            r = session.post("https://api.livecoinwatch.com/coins/list", json=payload, headers=headers, timeout=20)
            r.raise_for_status()
            for t in r.json():
                symbol = (t.get("code") or "").upper()
                if symbol in STABLECOINS: continue
                vol, mc = float(t.get("volume") or 0), float(t.get("cap") or 0)
                if mc and vol >= 0.5 * mc:
                    tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "LCW"})
        except Exception: pass
        print(f"   LiveCoinWatch: {len(tokens)} tokens")
        return tokens

    def fetch_coinrankings(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
        tokens: List[Dict[str, Any]] = []
        if not COINRANKINGS_API_KEY or COINRANKINGS_API_KEY == "CONFIG_REQUIRED_CR": return tokens

        print("   Scanning CoinRankings...")
        headers = STEALTH_HEADERS.copy()
        headers["x-access-token"] = COINRANKINGS_API_KEY
        for offset in range(0, 1000, 100):
            try:
                r = session.get("https://api.coinranking.com/v2/coins", headers=headers, 
                                params={"limit": 100, "offset": offset, "orderBy": "marketCap", "orderDirection": "desc"}, timeout=15)
                r.raise_for_status()
                for coin in r.json().get("data", {}).get("coins", []):
                    symbol = (coin.get("symbol") or "").upper()
                    if symbol in STABLECOINS: continue
                    vol, mc = float(coin.get("24hVolume") or 0), float(coin.get("marketCap") or 0)
                    if mc and vol >= 0.5 * mc:
                        tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "CR"})
                time.sleep(0.2)
            except Exception: pass
        print(f"   CoinRankings: {len(tokens)} tokens")
        return tokens

    def fetch_all_sources() -> Tuple[List[Dict[str, Any]], int]:
        print("   Scanning for high-volume tokens...")
        print("   Prioritize CoinGecko data for Accuracy... ")
        print("   " + "-" * 50)
        sources = [fetch_coingecko, fetch_coinmarketcap, fetch_livecoinwatch, fetch_coinrankings]
        results = []
        with ThreadPoolExecutor(max_workers=4) as exe:
            futures = [exe.submit(fn, requests.Session()) for fn in sources]
            for f in as_completed(futures):
                try:
                    res = f.result(timeout=60)
                    if res: results.extend(res)
                except Exception: continue
        print(f"   Total raw results: {len(results)}")
        return results, len(results)

    # --- Processing Logic ---
    raw_tokens, _ = fetch_all_sources()
    all_data = {}
    for t in raw_tokens:
        all_data.setdefault(t['symbol'], []).append(t)

    verified_tokens = []
    for sym, tokens in all_data.items():
        # Identify CoinGecko entry specifically
        cg_data = next((t for t in tokens if t['source'] == 'CG'), None)
        
        if cg_data:
            # --- GATEKEEPER RULE: COINGECKO IS SOVEREIGN ---
            # If CG has it, we ignore all other sources and use CG metrics alone
            volume, marketcap = cg_data['volume'], cg_data['marketcap']
            ratio = volume / marketcap
            is_large = (marketcap > 1_000_000_000)
            
            # Flexible Thresholds
            if (is_large and ratio >= 0.5) or (not is_large and ratio >= 0.5):
                verified_tokens.append({
                    "symbol": sym, "marketcap": marketcap, "volume": volume, 
                    "flipping_multiple": ratio, "source_count": len(tokens), "large_cap": is_large
                })
        else:
            # --- FALLBACK RULE: MULTI-SOURCE VERIFICATION ---
            # If CG is missing, the token MUST have at least 2 other sources to even be considered
            if len(tokens) >= 2:
                volume = sum(t['volume'] for t in tokens) / len(tokens)
                marketcap = sum(t['marketcap'] for t in tokens) / len(tokens)
                ratio = volume / marketcap
                is_large = any(t['marketcap'] > 1_000_000_000 for t in tokens)
                
                if (is_large and ratio >= 0.5) or (not is_large and ratio >= 0.5):
                    verified_tokens.append({
                        "symbol": sym, "marketcap": marketcap, "volume": volume, 
                        "flipping_multiple": ratio, "source_count": len(tokens), "large_cap": is_large
                    })
    hot_tokens = sorted(verified_tokens, key=lambda x: x["flipping_multiple"], reverse=True)
    html_file = create_html_report(hot_tokens)

    now_h = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"   Found {len(hot_tokens)} high-volume tokens at {now_h}")
    if hot_tokens:
        print("\n   HIGH-VOLUME TOKENS:")
        print("   " + "-" * 60)
        for i, token in enumerate(hot_tokens):
            lc = " [LARGE]" if token.get('large_cap') else ""
            print(f"   #{i+1:2d}. {token.get('symbol'):8} {token.get('flipping_multiple'):.1f}x "
                  f"| MC: ${short_num(token.get('marketcap')):>8} | Sources: {token.get('source_count')}{lc}")
        print("   " + "-" * 60)
    
    print(f"   HTML report saved: {html_file}")
    print("   Spot Volume Tracker Analysis completed!")