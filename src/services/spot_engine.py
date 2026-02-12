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

# Spot Volume Tracker
def spot_volume_tracker(user_keys, user_id) -> None:
    """
    Aggregates spot market data with accuracy and performance.
    Prioritizes CoinGecko data for volume accuracy.
    """
    def safe_float(val, default):
        try:
            if val is None or str(val).strip() == "":
                return default
            return float(val)
        except (ValueError, TypeError):
            return default

    print("    📊 Starting fresh spot analysis...")
    
    # Set thread name once at the start
    threading.current_thread().name = f"user_{user_id}"
    
    # Extract API keys
    CMC_API_KEY = user_keys.get("CMC_API_KEY", "CONFIG_REQUIRED_CMC")
    COINGECKO_API_KEY = user_keys.get("COINGECKO_API_KEY", "CONFIG_REQUIRED_CG")
    LIVECOINWATCH_API_KEY = user_keys.get("LIVECOINWATCH_API_KEY", "CONFIG_REQUIRED_LCW")

    # User Filters & Safety Helper
    settings = user_keys.get("engine_settings", {})
    MIN_VTMR    = safe_float(settings.get('min_vtmr'), 0.5)
    MAX_VTMR    = safe_float(settings.get('max_vtmr'), 199.0)
    MIN_LC_VTMR = safe_float(settings.get('min_largecap_vtmr'), 0.5)
    LC_THRESHOLD = 1_000_000_000
    FETCH_THRESHOLD = min(MIN_VTMR, MIN_LC_VTMR)
    
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
        """
        Generates the 'Ultimate' branded HTML report.
        Features: Fluid scaling for all screens, no-wrap data, and integrated navigation.
        """
        date_prefix = datetime.datetime.now().strftime("%b-%d-%y_%H-%M")
        user_dir = get_user_temp_dir(user_id) 
        html_file = user_dir / f"Spot_Analysis_Report_{date_prefix}.html"
        current_time = now_str("%d-%m-%Y %H:%M:%S")

        # Summary Metrics Calculation
        max_flip = max((t.get('flipping_multiple', 0) for t in hot_tokens), default=0)
        large_cap_count = len([t for t in hot_tokens if t.get('large_cap')])

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>Spot Analysis Report</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">
            <style>
                :root {{
                    --bg-dark: #151a1e;
                    --bg-card: #1e252a;
                    --accent-green: #10b981;
                    --text-main: #ffffff;
                    --text-dim: #848e9c;
                    --border: #2b3139;
                }}
                body {{ 
                    font-family: 'Inter', sans-serif; 
                    margin: 0; 
                    background-color: var(--bg-dark); 
                    color: var(--text-main); 
                    -webkit-font-smoothing: antialiased;
                }}
                .header {{ 
                    background: linear-gradient(180deg, rgba(16, 185, 129, 0.1) 0%, transparent 100%);
                    padding: 30px 15px; 
                    text-align: center; 
                    border-bottom: 1px solid var(--border);
                }}
                .header h1 {{ margin: 0; font-size: 1.3rem; color: var(--accent-green); font-weight: 800; text-transform: uppercase; }}
                .header p {{ margin: 8px 0 0; font-size: 0.8rem; color: var(--text-dim); font-family: 'JetBrains Mono', monospace; }}
                
                .summary {{ 
                    background: var(--bg-card); 
                    padding: 15px; 
                    margin: 15px; 
                    border-radius: 12px; 
                    border: 1px solid var(--border);
                    font-size: 0.85rem;
                    line-height: 1.5;
                    text-align: center;
                }}
                .summary b {{ color: var(--accent-green); }}

                .table-container {{ 
                    margin: 0 10px; 
                    border-radius: 12px; 
                    border: 1px solid var(--border); 
                    background: var(--bg-card);
                    overflow: hidden; /* Manage fit via scaling, not scrolling */
                }}
                table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
                
                th {{ 
                    background: rgba(0, 0, 0, 0.2); 
                    color: var(--text-dim); 
                    padding: 12px 5px; 
                    text-align: left; 
                    font-size: 0.65rem; 
                    text-transform: uppercase; 
                    letter-spacing: 1px;
                    border-bottom: 1px solid var(--border);
                    white-space: nowrap;
                }}
                td {{ 
                    padding: 0; 
                    border-bottom: 1px solid #2b3139; 
                    height: 52px; 
                    vertical-align: middle; 
                    font-size: 0.85rem; 
                    white-space: nowrap; /* Prevent data wrap */
                }}
                tr:last-child td {{ border-bottom: none; }}
                
                tr.large-cap {{ background: rgba(16, 185, 129, 0.03); }}
                tr.large-cap td:first-child {{ border-left: 3px solid var(--accent-green); }}

                /* Redirection Link Button */
                .ticker-btn {{
                    display: block; width: 100%; height: 100%; padding: 14px 8px;
                    color: var(--accent-green); text-decoration: none; font-weight: 800; 
                    box-sizing: border-box; transition: background 0.2s;
                }}
                .ticker-btn:active {{ background: rgba(16, 185, 129, 0.1); }}

                /* Fluid Scaling Logic for Mobile */
                @media (max-width: 480px) {{
                    td {{ font-size: 0.72rem; }}
                    th {{ font-size: 0.58rem; padding: 10px 4px; }}
                    .ticker-btn {{ padding: 10px 4px !important; }}
                    .mono {{ font-size: 0.68rem; }}
                    .header h1 {{ font-size: 1.1rem; }}
                    .summary {{ font-size: 0.75rem; margin: 10px; }}
                }}
                
                /* Dashboard Navigation Button */
                .nav-box {{ text-align: center; margin: 30px 0; }}
                .back-btn {{
                    display: inline-flex;
                    align-items: center;
                    padding: 12px 24px;
                    background: transparent;
                    border: 1px solid var(--text-dim);
                    color: var(--text-dim);
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: 800;
                    font-size: 0.85rem;
                    transition: all 0.2s;
                }}
                .back-btn:hover {{ border-color: #fff; color: #fff; background: rgba(255,255,255,0.05); }}

                .mono {{ font-family: 'JetBrains Mono', monospace; }}
                .vol-high {{ color: #ef4444; font-weight: bold; }}
                
                .footer {{ 
                    text-align: center; 
                    padding: 30px 20px; 
                    font-size: 0.75rem; 
                    color: var(--text-dim); 
                    border-top: 1px solid var(--border);
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Spot Volume Analysis Report</h1>
                <p>{current_time}</p>
            </div>
            
            <div class="summary">
                Found <b>{len(hot_tokens)}</b> tokens. | <b>{large_cap_count}</b> Largecaps tokens found | Highest VTMR <b>{max_flip:.1f}x</b>.
            </div>

            <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 12%; text-align:center;">#</th>
                        <th style="width: 25%;">Ticker</th>
                        <th style="width: 23%;">MarketCap</th>
                        <th style="width: 22%;">Volume</th>
                        <th style="width: 18%;">VTMR</th>
                    </tr>
                </thead>
                <tbody>
        """

        for i, token in enumerate(hot_tokens):
            is_lc = token.get('large_cap', False)
            row_class = "large-cap" if is_lc else ""
            vtmr = token.get('flipping_multiple', 0)
            vol_class = "vol-high" if vtmr >= 2 else ""
            sym = token.get('symbol', '???')
            
            # redirection to Deep Diver
            link = f'<a href="/deep-diver?ticker={sym}" class="ticker-btn">{sym}</a>'

            html_content += f"""
                <tr class="{row_class}">
                    <td style="text-align:center; color:var(--text-dim);" class="mono">#{i+1}</td>
                    <td>{link}</td>
                    <td style="padding-left:5px;" class="mono">${short_num(token.get('marketcap', 0))}</td>
                    <td style="padding-left:5px;" class="mono">${short_num(token.get('volume', 0))}</td>
                    <td class="mono {vol_class}" style="padding-left:5px;">{vtmr:.2f}x</td>
                </tr>
            """
        
        html_content += f"""
                </tbody>
            </table>
            </div>

            <div class="nav-box">
                <a href="/reports-list" class="back-btn">← BACK TO REPORTS LIST</a>
            </div>

            <div class="footer">
                Report by QuantVat using SpotVolTracker v2.6
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
        use_key = bool(COINGECKO_API_KEY and COINGECKO_API_KEY != "CONFIG_REQUIRED_CG")
        
        for page in range(1, 5):
            try:
                url = "https://api.coingecko.com/api/v3/coins/markets"
                params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page}
                headers = STEALTH_HEADERS.copy()
                
                if use_key:
                    if page == 1: print("    ⚡ Scanning CoinGecko...")
                    headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
                    delay = 0.05
                else:
                    if page == 1: print("    🐌 Scanning CoinGecko (Slow Mode)...")
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
                    if mc > 0 and (vol / mc) >= FETCH_THRESHOLD:
                        tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "CG"})
                time.sleep(delay)
            except Exception: continue
        print(f"    ✅ CoinGecko: {len(tokens)} tokens")
        return tokens

    def fetch_coinmarketcap(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
        tokens: List[Dict[str, Any]] = []
        if not CMC_API_KEY or CMC_API_KEY == "CONFIG_REQUIRED_CMC": return tokens

        print("    ⚡ Scanning CoinMarketCap...")
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
                    if mc > 0 and (vol / mc) >= FETCH_THRESHOLD:
                        tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "CMC"})
                time.sleep(0.2)
            except Exception: continue
        print(f"    ✅ CoinMarketCap: {len(tokens)} tokens")
        return tokens

    def fetch_livecoinwatch(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
        tokens: List[Dict[str, Any]] = []
        if not LIVECOINWATCH_API_KEY or LIVECOINWATCH_API_KEY == "CONFIG_REQUIRED_LCW": return tokens

        print("    ⚡ Scanning LiveCoinWatch...")
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
                if mc > 0 and (vol / mc) >= FETCH_THRESHOLD:
                    tokens.append({"symbol": symbol, "marketcap": mc, "volume": vol, "source": "LCW"})
        except Exception: pass
        print(f"    ✅ LiveCoinWatch: {len(tokens)} tokens")
        return tokens

    def fetch_all_sources() -> Tuple[List[Dict[str, Any]], int]:
        print("    🔍 Scanning for high-volumed tokens...")
        print("    ⬆️ CoinGecko data for accuracy... ")
        sources = [fetch_coingecko, fetch_coinmarketcap, fetch_livecoinwatch]
        results = []
        with ThreadPoolExecutor(max_workers=3) as exe:
            futures = [exe.submit(fn, requests.Session()) for fn in sources]
            for f in as_completed(futures):
                try:
                    res = f.result(timeout=60)
                    if res: results.extend(res)
                except Exception: continue
        print(f"    📊 Total raw results: {len(results)}")
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
            is_large = (marketcap > LC_THRESHOLD)
            
            # Flexible Thresholds
            if (is_large and ratio >= MIN_LC_VTMR and ratio <= MAX_VTMR) or \
               (not is_large and ratio >= MIN_VTMR and ratio <= MAX_VTMR):
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
                is_large = any(t['marketcap'] > LC_THRESHOLD for t in tokens)
                
                if (is_large and ratio >= MIN_LC_VTMR and ratio <= MAX_VTMR) or \
                   (not is_large and ratio >= MIN_VTMR and ratio <= MAX_VTMR):
                    verified_tokens.append({
                        "symbol": sym, "marketcap": marketcap, "volume": volume, 
                        "flipping_multiple": ratio, "source_count": len(tokens), "large_cap": is_large
                    })
    hot_tokens = sorted(verified_tokens, key=lambda x: x["flipping_multiple"], reverse=True)
    html_file = create_html_report(hot_tokens)
    #-- Print 
    report_filename = html_file.name
    now_h = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"    💎 Found {len(hot_tokens)} high-volume tokens at {now_h}")
    print(f"    📂 HTML report saved: /reports-list/{report_filename}")
    print("    🏁 Spot volume analysis completed!")