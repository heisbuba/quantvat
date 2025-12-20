#!/usr/bin/env python3
"""
#----Crypto Volume Analysis Toolkit  v4.0 (Ultimate Edition)
#---- Web-Based Version By @heisbuba

_____Install these 5 REQUIRED libraries from PIP section before running:

  1. requests
  2. pandas
  3. pypdf
  4. flask
  5. lxml

Then run your toolkit.

It will open browser interface automatically at http://127.0.0.1:5000

"""

from __future__ import annotations

import os
import sys
import re
import time
import json
import math
import datetime
import threading
import webbrowser
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Keep pypdf usage, but be resilient if import fails
try:
    import pypdf
except Exception:
    pypdf = None

import pandas as pd

# FLASK IMPORT FOR WEB UI
from flask import Flask, render_template_string, jsonify, request, redirect, url_for, send_from_directory
import signal

# ==============================
# API KEYS SETUP ONLY IN WEB UI #================================

# Html2PDF Api Key Location
HTML2PDF_API_KEY = 'CONFIG_REQUIRED_HTML2PDF'

# CoinMarketCap Api Key Location
CMC_API_KEY = 'CONFIG_REQUIRED_CMC'

# LiveCoinWatch Api Location
LIVECOINWATCH_API_KEY = 'CONFIG_REQUIRED_LCW'

# CoinRaking Api Key Location
COINRANKINGS_API_KEY = 'CONFIG_REQUIRED_CR'

# Coinalyze Custom VTMR URL for Futures Data
COINALYZE_VTMR_URL = 'CONFIG_VTMR_URL'

#================================
# GLOBAL CONSTANTS
#================================
STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'BSC-USD', 'USD1', 'CBBTC', 'WBNB', 'WETH',
    'UST', 'TUSD', 'USDP', 'USDD', 'FRAX', 'GUSD', 'LUSD', 'FDUSD'
}

# [FIX]: Added Path.home() / "Downloads" for Windows/Mac support
DEFAULT_SAVE_PATHS = [
    Path.home() / "Downloads",
    Path("/sdcard/Download"),
    Path("/storage/emulated/0/Download"),
    Path("/storage/emulated/0/Downloads"),
    Path.cwd()
]

BASE_URL = "http://127.0.0.1:5000"
BASE_DIR = Path(__file__).resolve().parent

# ============================================
# FLASK WEB APPLICATION SETUP
# ============================================
app = Flask(__name__)

# Global State for Web UI
LIVE_LOGS = []
PROGRESS = {"percent": 0, "text": "System Idle", "status": "idle"}
LOCK = threading.Lock()

# ============================================
# CONFIGURATION MANAGEMENT
# ============================================
def detect_download_folder() -> Path:
    for p in DEFAULT_SAVE_PATHS:
        try:
            if p.exists() and p.is_dir():
                return p
        except Exception:
            continue
    return Path.cwd()

REPORT_SAVE_PATH = detect_download_folder()

def is_system_configured():
    """Checks if keys are actual values or placeholders."""
    checks = [
        (HTML2PDF_API_KEY, "CONFIG_REQUIRED_HTML2PDF"),
        (CMC_API_KEY, "CONFIG_REQUIRED_CMC"),
        (LIVECOINWATCH_API_KEY, "CONFIG_REQUIRED_LCW"),
        (COINRANKINGS_API_KEY, "CONFIG_REQUIRED_CR"),
        (COINALYZE_VTMR_URL, "CONFIG_VTMR_URL")
    ]
    
    for val, placeholder in checks:
        if str(val).strip() == placeholder or "YOUR_" in str(val) or "CONFIG_REQUIRED" in str(val):
            return False
    return True

def update_config(variable_name, new_value):
    """Updates configuration variable in memory and writes to disk config."""
    global HTML2PDF_API_KEY, CMC_API_KEY, LIVECOINWATCH_API_KEY, COINRANKINGS_API_KEY, COINALYZE_VTMR_URL
    
    # Update in memory
    if variable_name == "HTML2PDF_API_KEY":
        HTML2PDF_API_KEY = new_value
    elif variable_name == "CMC_API_KEY":
        CMC_API_KEY = new_value
    elif variable_name == "LIVECOINWATCH_API_KEY":
        LIVECOINWATCH_API_KEY = new_value
    elif variable_name == "COINRANKINGS_API_KEY":
        COINRANKINGS_API_KEY = new_value
    elif variable_name == "COINALYZE_VTMR_URL":
        COINALYZE_VTMR_URL = new_value
    
    # Save to config file
    config_file = BASE_DIR / "crypto_vat_config.json"
    config = {
        "HTML2PDF_API_KEY": HTML2PDF_API_KEY,
        "CMC_API_KEY": CMC_API_KEY,
        "LIVECOINWATCH_API_KEY": LIVECOINWATCH_API_KEY,
        "COINRANKINGS_API_KEY": COINRANKINGS_API_KEY,
        "COINALYZE_VTMR_URL": COINALYZE_VTMR_URL
    }
    
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass  # Silent fail if can't write config
    
    return True

def load_config():
    """Load configuration from file if exists."""
    config_file = BASE_DIR / "crypto_vat_config.json"
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            global HTML2PDF_API_KEY, CMC_API_KEY, LIVECOINWATCH_API_KEY, COINRANKINGS_API_KEY, COINALYZE_VTMR_URL
            HTML2PDF_API_KEY = config.get("HTML2PDF_API_KEY", HTML2PDF_API_KEY)
            CMC_API_KEY = config.get("CMC_API_KEY", CMC_API_KEY)
            LIVECOINWATCH_API_KEY = config.get("LIVECOINWATCH_API_KEY", LIVECOINWATCH_API_KEY)
            COINRANKINGS_API_KEY = config.get("COINRANKINGS_API_KEY", COINRANKINGS_API_KEY)
            COINALYZE_VTMR_URL = config.get("COINALYZE_VTMR_URL", COINALYZE_VTMR_URL)
        except Exception:
            pass  # Keep defaults if config file is corrupted

# Load config on startup
load_config()

# ============================================
# WEB UI BACKGROUND TASKS
# ============================================
def run_background_task(target_func):
    with LOCK:
        LIVE_LOGS.clear()
        global PROGRESS
        PROGRESS = {"percent": 5, "text": "Initializing Engine...", "status": "active"}

    def worker():
        global PROGRESS
        try:
            target_func()
            with LOCK: PROGRESS = {"percent": 100, "text": "Analysis Complete", "status": "success"}
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {str(e)}\n")
            with LOCK: PROGRESS = {"percent": 0, "text": "Error Occurred", "status": "error"}
            
    thread = threading.Thread(target=worker)
    thread.daemon = True
    thread.start()

# ============================================
# CORE ENGINE FUNCTIONS
# ============================================
def create_session(retries: int = 3, backoff_factor: float = 0.5, status_forcelist=(429, 500, 502, 503, 504)) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

SESSION = create_session()

def short_num(n: float | int) -> str:
    try:
        n = float(n)
    except Exception:
        return str(n)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return str(round(n))

def now_str(fmt: str = "%d-%m-%Y %H:%M:%S") -> str:
    return datetime.datetime.now().strftime(fmt)

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path

def detect_save_path() -> Path:
    for p in DEFAULT_SAVE_PATHS:
        try:
            if p.exists() and p.is_dir():
                return p
        except Exception:
            continue
    return Path.cwd()

# ============================================
# SPOT VOLUME TRACKER v2.0
# ============================================
def spot_volume_tracker() -> None:
    print("   📊 Starting fresh spot analysis...")

    def create_html_report(hot_tokens: List[Dict[str, Any]], save_path: Path) -> Path:
        date_prefix = datetime.datetime.now().strftime("%b-%d-%y")
        html_file = save_path / f"Volumed_Spot_Tokens_{date_prefix}.html"
        current_time = now_str("%d-%m-%Y %H:%M:%S")

        max_flip = max((t.get('flipping_multiple', 0) for t in hot_tokens), default=0)
        high_volume = len([t for t in hot_tokens if t.get('flipping_multiple', 0) >= 2])
        large_cap_count = len([t for t in hot_tokens if t.get('large_cap')])

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Crypto Volume Tracker v2.0</title>
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
                <h1>SPOT VOLUME CRYPTO TRACKER v2.0</h1>
                <p>High Volume Spot Tokens Analysis</p>
                <p><small>Generated on: {current_time}</small></p>
            </div>
            <div class="summary">
                <h3>Summary</h3>
                <p>Total High-Volume Tokens: {len(hot_tokens)}</p>
                <p>Peak Flipping (VTMR) Multiple: {max_flip:.1f}x</p>
                <p>High-Volume Tokens (2x+): {high_volume}</p>
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
                    <th>Verifications</th>
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
                <p>Generated by Spot Volume Crypto Tracker v2.0 | By (@heisbuba)</p>
            </div>
        </body>
        </html>
        """
        try:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)
        except Exception as e:
            html_file = Path.cwd() / html_file.name
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)
        return html_file

    def fetch_coingecko(session: requests.Session) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        print("   Scanning CoinGecko...")
        for page in range(1, 5):
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page}
            try:
                r = session.get(url, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                for t in data:
                    symbol = (t.get("symbol") or "").upper()
                    if symbol in STABLECOINS: continue
                    volume = float(t.get("total_volume") or 0)
                    marketcap = float(t.get("market_cap") or 0)
                    if marketcap and volume > 0.75 * marketcap:
                        tokens.append({
                            "symbol": symbol,
                            "marketcap": marketcap,
                            "volume": volume,
                            "volume_ratio": volume / marketcap if marketcap else 0,
                            "source": "CG"
                        })
                time.sleep(0.2)
            except Exception:
                continue
        print(f"   CoinGecko: {len(tokens)} tokens")
        return tokens

    def fetch_coinmarketcap(session: requests.Session) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        print("   Scanning CoinMarketCap...")
        if not CMC_API_KEY or CMC_API_KEY == "CONFIG_REQUIRED_CMC":
            print("   ⚠️  No CMC API key provided")
            return tokens
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        for start in range(1, 1001, 100):
            url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
            params = {"start": start, "limit": 100, "convert": "USD"}
            try:
                r = session.get(url, headers=headers, params=params, timeout=15)
                r.raise_for_status()
                data = r.json().get("data", [])
                for t in data:
                    symbol = (t.get("symbol") or "").upper()
                    if symbol in STABLECOINS: continue
                    quote = t.get("quote", {}).get("USD", {})
                    volume = float(quote.get("volume_24h") or 0)
                    marketcap = float(quote.get("market_cap") or 0)
                    if marketcap and volume > 0.75 * marketcap:
                        tokens.append({
                            "symbol": symbol,
                            "marketcap": marketcap,
                            "volume": volume,
                            "volume_ratio": volume / marketcap if marketcap else 0,
                            "source": "CMC"
                        })
                time.sleep(0.2)
            except Exception:
                continue
        print(f"   CoinMarketCap: {len(tokens)} tokens")
        return tokens

    def fetch_livecoinwatch(session: requests.Session) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        if not LIVECOINWATCH_API_KEY or LIVECOINWATCH_API_KEY == "CONFIG_REQUIRED_LCW":
            print("   ⚠️  No LiveCoinWatch API key provided")
            return tokens
        print("   Scanning LiveCoinWatch...")
        url = "https://api.livecoinwatch.com/coins/list"
        headers = {"content-type": "application/json", "x-api-key": LIVECOINWATCH_API_KEY}
        payload = {"currency": "USD", "sort": "rank", "order": "ascending", "offset": 0, "limit": 1000, "meta": True}
        try:
            r = session.post(url, json=payload, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
            for t in data:
                symbol = (t.get("code") or "").upper()
                if symbol in STABLECOINS: continue
                volume = float(t.get("volume") or 0)
                marketcap = float(t.get("cap") or 0)
                if marketcap and volume > 0.75 * marketcap:
                    tokens.append({
                        "symbol": symbol,
                        "marketcap": marketcap,
                        "volume": volume,
                        "volume_ratio": volume / marketcap if marketcap else 0,
                        "source": "LCW"
                    })
        except Exception:
            pass
        print(f"   LiveCoinWatch: {len(tokens)} tokens")
        return tokens

    def fetch_coinrankings(session: requests.Session) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        print("   Scanning CoinRankings...")
        if not COINRANKINGS_API_KEY or COINRANKINGS_API_KEY == "CONFIG_REQUIRED_CR":
            print("   ⚠️  No CoinRankings API key provided")
            return tokens
        headers = {"x-access-token": COINRANKINGS_API_KEY}
        url = "https://api.coinranking.com/v2/coins"
        for offset in range(0, 1000, 100):
            params = {"limit": 100, "offset": offset, "orderBy": "marketCap", "orderDirection": "desc"}
            try:
                r = SESSION.get(url, headers=headers, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                coins = data.get("data", {}).get("coins", [])
                for coin in coins:
                    symbol = (coin.get("symbol") or "").upper()
                    if symbol in STABLECOINS: continue
                    volume = float(coin.get("24hVolume") or 0)
                    marketcap = float(coin.get("marketCap") or 0)
                    if marketcap and volume > 0.75 * marketcap:
                        tokens.append({
                            "symbol": symbol,
                            "marketcap": marketcap,
                            "volume": volume,
                            "volume_ratio": volume / marketcap if marketcap else 0,
                            "source": "CR"
                        })
                time.sleep(0.2)
            except Exception:
                pass
        print(f"   CoinRankings: {len(tokens)} tokens")
        return tokens

    def fetch_all_sources() -> Tuple[List[Dict[str, Any]], int]:
        print("   Scanning for high-volume tokens...")
        print("   Criteria: Volume > 75% of Market Cap")
        print("   Large-cap: Volume >= 50% of Market Cap")
        print("   " + "-" * 50)
        sources = [fetch_coingecko, fetch_coinmarketcap, fetch_livecoinwatch, fetch_coinrankings]
        results: List[Dict[str, Any]] = []
        futures = []
        with ThreadPoolExecutor(max_workers=4) as exe:
            for fn in sources:
                futures.append(exe.submit(fn, SESSION))
            for f in as_completed(futures):
                try:
                    res = f.result(timeout=60)
                    if res:
                        results.extend(res)
                except Exception:
                    continue
        print(f"   Total raw results: {len(results)}")
        return results, len(results)

    def is_large_cap_token_from_list(tokens: List[Dict[str, Any]]) -> bool:
        for token in tokens:
            try:
                if float(token.get('marketcap', 0)) > 1_000_000_000:
                    return True
            except Exception:
                continue
        return False

    def calculate_simple_metrics(token_list: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        if not token_list:
            return 0.0, 0.0, 0.0
        try:
            v = float(token_list[0].get('volume', 0))
            m = float(token_list[0].get('marketcap', 0))
            r = v / m if m else 0.0
            return v, m, r
        except Exception:
            return 0.0, 0.0, 0.0

    raw_tokens, _ = fetch_all_sources()
    all_data: Dict[str, List[Dict[str, Any]]] = {}
    for t in raw_tokens:
        sym = (t.get('symbol') or '').upper()
        if not sym: continue
        all_data.setdefault(sym, []).append(t)

    verified_tokens: List[Dict[str, Any]] = []
    for sym, tokens in all_data.items():
        if len(tokens) == 1 and is_large_cap_token_from_list(tokens):
            volume, marketcap, volume_ratio = calculate_simple_metrics(tokens)
            if volume_ratio >= 0.50:
                verified_tokens.append({
                    "symbol": sym,
                    "marketcap": marketcap,
                    "volume": volume,
                    "volume_ratio": volume_ratio,
                    "flipping_multiple": volume_ratio,
                    "source_count": 1,
                    "large_cap": True
                })
            continue

        if len(tokens) >= 2:
            volumes = []
            marketcaps = []
            for t in tokens:
                try:
                    volumes.append(float(t.get('volume', 0)))
                    marketcaps.append(float(t.get('marketcap', 0)))
                except Exception:
                    continue
            if not volumes or not marketcaps:
                continue
            avg_volume = sum(volumes) / len(volumes)
            avg_marketcap = sum(marketcaps) / len(marketcaps)
            volume_ratio = (avg_volume / avg_marketcap) if avg_marketcap else 0.0
            if volume_ratio > 0.75:
                verified_tokens.append({
                    "symbol": sym,
                    "marketcap": avg_marketcap,
                    "volume": avg_volume,
                    "volume_ratio": volume_ratio,
                    "flipping_multiple": volume_ratio,
                    "source_count": len(tokens),
                    "large_cap": any(m > 1_000_000_000 for m in marketcaps)
                })

    hot_tokens = sorted(verified_tokens, key=lambda x: x.get("flipping_multiple", 0), reverse=True)
    save_path = detect_save_path()
    html_file = create_html_report(hot_tokens, save_path)

    now_h = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"   Found {len(hot_tokens)} high-volume tokens at {now_h}")
    print(f"   HTML report: {html_file}")

    if hot_tokens:
        print("\n   HIGH-VOLUME TOKENS:")
        print("   " + "-" * 60)
        for i, token in enumerate(hot_tokens):
            large_cap_indicator = " [LARGE]" if token.get('large_cap') else ""
            print(f"   #{i+1:2d}. {token.get('symbol', ''):8} {token.get('flipping_multiple', 0):.1f}x "
                  f"| MC: ${short_num(token.get('marketcap', 0)):>8} | Sources: {token.get('source_count')}{large_cap_indicator}")
        print("   " + "-" * 60)
        max_flip = max((t.get('flipping_multiple', 0) for t in hot_tokens), default=0)
        high_volume = len([t for t in hot_tokens if t.get('flipping_multiple', 0) >= 2])
        large_cap_count = len([t for t in hot_tokens if t.get('large_cap')])
        print(f"   Peak: {max_flip:.1f}x | High-volume: {high_volume} tokens")
        print(f"   Large-cap: {large_cap_count} tokens")
    else:
        print("   No high-volume tokens found")

    print("   💡 Spot data saved. Run Advanced Analysis to use this data.")
    print("   Spot Volume Tracker completed!")

# ============================================
# HTML TO PDF CONVERSION
# ============================================
def convert_html_to_pdf(html_content: str, output_dir: Path) -> Optional[Path]:
    print("\n   Converting to PDF...")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    pdf_name = f"{today}-crypto-analysis.pdf"
    pdf_path = output_dir / pdf_name
    try:
        response = SESSION.post(
            "https://api.html2pdf.app/v1/generate",
            json={'html': html_content, 'apiKey': HTML2PDF_API_KEY},
            headers={'Content-Type': 'application/json'},
            timeout=30
        )
        if response.status_code == 200:
            with open(pdf_path, "wb") as f:
                f.write(response.content)
            file_size_str = f"{len(response.content):,} bytes"
            print(f"   PDF created: {pdf_name}")
            print(f"   Size: {file_size_str}")
            print(f"   Location: {output_dir}")
            return pdf_path
        # [FIX]: Added specific error handling for 429 and 500+
        elif response.status_code == 401:
            print("   Invalid API Key")
            return None
        elif response.status_code == 429:
            print("   API Rate Limit Exceeded (Try again later)")
            return None
        elif response.status_code >= 500:
            print(f"   Server Error: {response.status_code}")
            return None
        else:
            print(f"   API Error: {response.status_code}")
            return None
    except requests.exceptions.ConnectionError:
        print("   No internet connection")
        return None
    except requests.exceptions.Timeout:
        print("   Request timed out")
        return None
    except Exception as e:
        print(f"   Error: {e}")
        return None

# ============================================
# ADVANCED CRYPTO ANALYSIS TOOLKIT v2.0
# ============================================
def crypto_analysis_v4() -> None:
    print("   🔍 Looking for analysis files...")

    ORIGINAL_HTML_STYLE = """
            body { margin: 20px; background: #f5f5f5; font-family: Arial, sans-serif; }
            .table-container { margin: 20px 0; background: white; padding: 15px; border-radius: 10px; }
            table { width: 100%; border-collapse: collapse; margin: 10px 0; }
            th, td { padding: 10px; border: 1px solid #ddd; text-align: left; }
            th { background: #2c3e50; color: white; }
            tr:nth-child(even) { background: #f9f9f9; }
            .header { background: #2c3e50; color: white; padding: 20px; border-radius: 10px; text-align: center; }
            h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
            .footer { text-align: center; margin-top: 20px; color: #7f8c8d; }
            .oi-strong { color: #27ae60; font-weight: bold; }
            .oi-weak { color: #c0392b; }
        """
    
    # --- UPDATED HEADERS ---
    # Table 1: OISS comes first, then Funding
    ORIGINAL_MATCHED_HEADERS = ["Ticker", "Spot Market Cap", "Spot Volume", "Spot VTMR", "Futures Volume", "Futures VTMR", "OISS", "Funding"]
    
    # Table 2: Added Funding at the end
    ORIGINAL_FUTURES_HEADERS = ["Token", "Market Cap", "Volume", "VTMR", "OISS", "Funding"]
    
    ORIGINAL_SPOT_HEADERS = ["Ticker", "Market Cap", "Volume", "Spot VTMR"]

    DOWNLOAD_DIRS = [
        Path.home() / "Downloads",
        Path("/sdcard/Download"),
        Path("/storage/emulated/0/Download"),
        Path("/storage/emulated/0/Downloads"),
        Path.cwd()
    ]

    # --- HELPER FUNCTIONS ---
    def oi_score_and_signal(oi_change: float) -> Tuple[int, str]:
        if oi_change > 0.20: return 5, "Strong"
        if oi_change > 0.10: return 4, "Bullish"
        if oi_change > 0.00: return 3, "Build-Up"
        if oi_change > -0.10: return 2, "Weakening"
        if oi_change > -0.20: return 1, "Exiting"
        return 0, "Exiting"

    def funding_score_and_signal(funding_val: float) -> Tuple[str, str]:
        # Positive Side (Longs paying Shorts)
        if funding_val >= 0.05: return "Greed", "oi-strong"
        if funding_val > 0.00: return "Bullish", "oi-strong"
        # Negative Side (Shorts paying Longs)
        if funding_val <= -0.05: return "Extreme Fear", "oi-weak"
        if funding_val < 0.00: return "Bearish", "oi-weak"
        return "Neutral", ""

    def make_oiss(oi_percent_str: str) -> str:
        if not oi_percent_str: return "-"
        val = oi_percent_str.replace("%", "").strip()
        try:
            oi_change = float(val) / 100
            score, signal = oi_score_and_signal(oi_change)
            
            if oi_change > 0: css_class = "oi-strong"
            elif oi_change < 0: css_class = "oi-weak"
            else: css_class = ""

            sign = "+" if oi_change > 0 else ""
            
            if css_class:
                percent = f'<span class="{css_class}">{sign}{oi_change*100:.0f}%</span>'
            else:
                percent = f"{sign}{oi_change*100:.0f}%"

            return f"{percent} {signal}"
        except Exception:
            return "-"

    def make_funding_signal(funding_str: str) -> str:
        if not funding_str or funding_str in ['-', 'N/A']: return "-"
        try:
            val = float(funding_str.replace('%', '').strip())
            signal_word, css_class = funding_score_and_signal(val)
            
            if css_class:
                html = f'<span class="{css_class}">{val}%</span> <span style="font-size:0.8em; color:#7f8c8d;">{signal_word}</span>'
            else:
                html = f'{val}% {signal_word}'
            return html
        except Exception:
            return funding_str

    @dataclass
    class TokenData:
        ticker: str
        name: str
        market_cap: str
        volume: str
        vtmr: float
        funding: str = "-"
        oiss: str = "-"

    class FileScanner:
        @staticmethod
        def find_files() -> Tuple[Optional[Path], Optional[Path]]:
            spot_file: Optional[Path] = None
            futures_file: Optional[Path] = None
            for d in DOWNLOAD_DIRS:
                try:
                    if not d.exists():
                        continue
                    for f in d.iterdir():
                        if not f.is_file():
                            continue
                        name = f.name.lower()
                        if f.suffix == ".pdf" and "futures" in name:
                            futures_file = f
                        elif f.suffix in [".csv", ".html"] and "spot" in name:
                            spot_file = f
                        if spot_file and futures_file:
                            break
                except Exception:
                    continue
                if spot_file and futures_file:
                    break
            return spot_file, futures_file

    class PDFParser:
        # [FIX]: UPDATED REGEX: Captures 5 columns. Fixed VTMR to allow .5 or 0.5 or 16
        FINANCIAL_PATTERN = re.compile(
            r'(\$?[+-]?[\d,\.]+[kKmMbB]?)\s+'             # 1. Market Cap
            r'(\$?[+-]?[\d,\.]+[kKmMbB]?)\s+'             # 2. Volume
            r'(?:([+\-]?[\d\.\,]+\%?|[\-\–\—]|N\/A)\s+)?' # 3. OI Change
            r'(?:([+\-]?[\d\.\,]+\%?|[\-\–\—]|N\/A)\s+)?' # 4. Funding Rate (NEW)
            r'(\d*\.?\d+)'                                # 5. VTMR
        )

        IGNORE_KEYWORDS = {
            'page', 'coinalyze', 'contract', 'filter', 'column',
            'mkt cap', 'vol 24h', 'vtmr', 'coins', 'all contracts', 'custom metrics', 'watchlists'
        }

        @classmethod
        def extract(cls, path: Path) -> pd.DataFrame:
            print(f"   Parsing Futures PDF: {path.name}")
            if pypdf is None:
                print("   pypdf not available - PDF parsing disabled.")
                return pd.DataFrame()
            data: List[TokenData] = []
            try:
                reader = pypdf.PdfReader(path)
                for page in reader.pages:
                    raw = page.extract_text() or ""
                    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
                    page_data = cls._parse_page_smart(lines)
                    data.extend(page_data)
                print(f"   Extracted {len(data)} futures tokens")
                if not data:
                    return pd.DataFrame()
                df = pd.DataFrame([vars(t) for t in data])
                df['ticker'] = df['ticker'].apply(lambda x: re.sub(r'[^A-Z0-9]', '', str(x).upper()))
                df = df[df['ticker'].str.len() > 1]
                print(f"   Valid futures tokens: {len(df)}")
                return df
            except Exception as e:
                print(f"   PDF Error: {e}")
                return pd.DataFrame()

        @classmethod
        def _parse_page_smart(cls, lines: List[str]) -> List[TokenData]:
            financials = []
            raw_text_lines = []
            
            for line in lines:
                if any(k in line.lower() for k in cls.IGNORE_KEYWORDS):
                    continue
                
                fin_match = cls.FINANCIAL_PATTERN.search(line)
                if fin_match:
                    groups = fin_match.groups()
                    mc = groups[0].replace('$', '').replace(',', '')
                    vol = groups[1].replace('$', '').replace(',', '')
                    oi_str = groups[2]       # Group 3: OI
                    fund_str = groups[3]     # Group 4: Funding
                    vtmr = groups[4]         # Group 5: VTMR
                    
                    try:
                        float(vtmr)
                        financials.append((mc, vol, vtmr, oi_str, fund_str))
                    except:
                        raw_text_lines.append(line)
                else:
                    if not line.isdigit() and len(line) > 1:
                        raw_text_lines.append(line)
            
            token_pairs = []
            i = 0
            while i < len(raw_text_lines):
                line = raw_text_lines[i]
                clean_current = cls._clean_ticker_strict(line)
                
                if clean_current:
                    if i + 1 < len(raw_text_lines):
                        next_line = raw_text_lines[i + 1]
                        clean_next = cls._clean_ticker_strict(next_line)
                        if clean_next:
                            token_pairs.append((line, clean_next))
                            i += 2
                            continue
                
                if i + 1 < len(raw_text_lines):
                    name_candidate = raw_text_lines[i]
                    ticker_candidate_raw = raw_text_lines[i + 1]
                    ticker = cls._clean_ticker_strict(ticker_candidate_raw)
                    if ticker:
                        token_pairs.append((name_candidate, ticker))
                        i += 2
                    else:
                        i += 1
                else:
                    i += 1
            
            tokens: List[TokenData] = []
            limit = min(len(token_pairs), len(financials))
            
            for k in range(limit):
                name, ticker = token_pairs[k]
                mc, vol, vtmr, oi_pct, fund_pct = financials[k]

                oiss_val = make_oiss(oi_pct) if oi_pct and oi_pct not in ['-', 'N/A'] else "-"
                
                # --- NEW FUNDING LOGIC APPLIED HERE ---
                funding_val = make_funding_signal(fund_pct)

                tokens.append(TokenData(
                    ticker=ticker,
                    name=name,
                    market_cap=mc,
                    volume=vol,
                    vtmr=float(vtmr),
                    funding=funding_val,
                    oiss=oiss_val
                ))
            return tokens

        @staticmethod
        def _clean_ticker_strict(text: str) -> Optional[str]:
            if len(text) > 15:
                return None
            cleaned = re.sub(r'[^A-Z0-9]', '', text.upper())
            if 2 <= len(cleaned) <= 12: 
                return cleaned
            return None
            
    class DataProcessor:
        @staticmethod
        def load_spot(path: Path) -> pd.DataFrame:
            print(f"   Parsing Spot File: {path.name}")
            try:
                if path.suffix == '.html':
                    df = pd.read_html(path)[0]
                else:
                    df = pd.read_csv(path)
                df.columns = [c.lower().replace(' ', '_') for c in df.columns]
                
                # [FIX]: Updated column map to match actual HTML report headers
                col_map = {
                    'ticker': 'ticker',
                    'symbol': 'ticker', 
                    'spot_vtmr': 'spot_flip', 
                    'flipping_multiple': 'spot_flip',
                    'market_cap': 'spot_mc',
                    'marketcap': 'spot_mc',
                    'volume_24h': 'spot_vol',
                    'volume': 'spot_vol'
                }
                
                df = df.rename(columns=col_map, errors='ignore')
                
                # Fallback to identify ticker column if rename didn't work perfectly
                if 'ticker' not in df.columns:
                     for col in df.columns:
                        if 'sym' in col or 'tick' in col or 'tok' in col:
                            df = df.rename(columns={col: 'ticker'})
                            break

                if 'ticker' in df.columns:
                    df['ticker'] = df['ticker'].apply(lambda x: re.sub(r'[^A-Z0-9]', '', str(x).upper()))
                print(f"   Extracted {len(df)} spot tokens")
                return df
            except Exception as e:
                print(f"   Spot File Error: {e}")
                return pd.DataFrame()

        @staticmethod
        def _generate_table_html(title: str, df: pd.DataFrame, headers: List[str], df_cols: List[str]) -> str:
            if df.empty:
                return f'<div class="table-container"><h2>{title}</h2><p>No data found</p></div>'
            missing = [c for c in df_cols if c not in df.columns]
            df_display = df.copy()
            for m in missing:
                df_display[m] = ""
            df_display = df_display[df_cols]
            df_display.columns = headers
            table_html = df_display.to_html(index=False, classes='table', escape=False)
            return f'<div class="table-container"><h2>{title}</h2>{table_html}</div>'

        @staticmethod
        def generate_html_report(futures_df: pd.DataFrame, spot_df: pd.DataFrame) -> Optional[str]:
            if futures_df.empty or spot_df.empty:
                return None
            
            if 'oiss' not in futures_df.columns:
                futures_df['oiss'] = "-"

            valid_futures = futures_df.copy()
            try:
                if 'vtmr' in valid_futures.columns:
                    valid_futures = valid_futures[valid_futures['vtmr'] >= 0.50]
                    valid_futures['vtmr_display'] = valid_futures['vtmr'].apply(lambda x: f"{x:.1f}x")
            except Exception as e:
                print(f"   Futures high-quality filtering error: {e}")
                valid_futures['vtmr_display'] = valid_futures['vtmr']

            merged = pd.merge(spot_df, valid_futures, on='ticker', how='inner', suffixes=('_spot', '_fut'))
            if 'vtmr' in merged.columns:
                merged = merged.sort_values('vtmr', ascending=False)
            
            futures_only = valid_futures[~valid_futures['ticker'].isin(spot_df['ticker'])].copy()
            if 'vtmr' in futures_only.columns:
                futures_only = futures_only.sort_values('vtmr', ascending=False)
            
            spot_only = spot_df[~spot_df['ticker'].isin(merged['ticker'])].copy()
            
            if 'spot_flip' in spot_only.columns:
                try:
                    spot_only = spot_only.copy()
                    spot_only.loc[:, 'flip_numeric'] = spot_only['spot_flip'].astype(str).str.replace('x', '', case=False).astype(float)
                    spot_only = spot_only[spot_only['flip_numeric'] >= 0.50]
                    spot_only = spot_only.drop(columns=['flip_numeric'])
                except Exception as e:
                    print(f"   Spot filtering error: {e}")
            
            if 'spot_flip' in spot_only.columns:
                try:
                    spot_only = spot_only.copy()
                    spot_only.loc[:, 'sort_val'] = spot_only['spot_flip'].astype(str).str.replace('x', '', case=False).astype(float)
                    spot_only = spot_only.sort_values('sort_val', ascending=False).drop(columns=['sort_val'])
                except Exception:
                    pass
            
            # --- UPDATED COLUMN ORDERING ---
            
            # Table 1 Data: OISS before Funding
            merged_cols = ['ticker', 'spot_mc', 'spot_vol', 'spot_flip', 'volume', 'vtmr_display', 'oiss', 'funding']
            
            # Table 2 Data: Added 'funding' at the end
            futures_cols = ['ticker', 'market_cap', 'volume', 'vtmr_display', 'oiss', 'funding']
            
            html_content = ""
            html_content += DataProcessor._generate_table_html("Tokens in Both Futures & Spot Markets", merged, ORIGINAL_MATCHED_HEADERS, merged_cols)
            html_content += DataProcessor._generate_table_html("Remaining Futures-Only Tokens", futures_only, ORIGINAL_FUTURES_HEADERS, futures_cols)
            html_content += DataProcessor._generate_table_html("Remaining Spot-Only Tokens", spot_only, ORIGINAL_SPOT_HEADERS, ['ticker', 'spot_mc', 'spot_vol', 'spot_flip'])
            current_time = now_str("%d-%m-%Y %H:%M:%S")
            
            cheat_sheet_pdf_footer = """
                <div style="margin-top: 30px; padding: 15px; background: #ecf0f1; border-radius: 8px;">
                    <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 0;">OISS & Funding Cheat Sheet:</h2>
                    <ul style="list-style-type: none; padding-left: 0; line-height: 1.6;">                
<li><strong>(1) Bullish Squeeze:</strong> Open Interest is positive and Funding Rate is negative, meaning new capital is flowing in while most are short. Shorts rush to buy back, triggering a sharp price surge. Bro, we are bullish.</li>
<li><strong>(2) Uptrend:</strong> Open Interest is positive and Funding Rate is positive, meaning the market is rising with broad participation. Capital keeps flowing, but the trend is costly (high fees). Solid uptrend, but watch for pauses.</li>
<li><strong>(3) Short Covering / Recovery:</strong> Open Interest is negative and Funding Rate is negative, meaning shorts are closing positions, causing a temporary rebound. No real buying pressure—trend may not last.</li>
<li><strong>(4) Flatline:</strong> Open Interest is unchanged and Funding Rate is positive, meaning the market is dead with no new capital. Minimal movement, trend paused, fees still accumulate.</li>
<li><strong>(5) Bearish Dump:</strong> Open Interest is negative and Funding Rate is positive, meaning longs are exiting aggressively or facing liquidation. Price drops sharply—strong selling pressure dominates.</li><br/>
<li><strong style="color:red;">NOTE:</strong> OISS stands for <strong>Open Interest Signal Score</strong> and FUNDING stands for <strong>Funding Rate</strong>.</li>
</ul>
<h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 0;">Why VTMR of All Sides Matter</h2>
                    <ul style="list-style-type: none; padding-left: 0; line-height: 1.6;">  
  <li>  
    <strong>(1) The Divergence Signal (Spot vs. Futures):</strong>
    <ul style="list-style-type: disc; padding-left: 20px; line-height: 1.4;">
      <li><strong.Futures VTMR &gt; Spot VTMR (The Casino):</strong> If Futures volume is huge (e.g., 8x) but Spot is low, the price is being driven by leverage and speculation. This is fragile—expect violent "wicks" and liquidation hunts.</li>
      <li><strong>Spot VTMR &gt; Futures VTMR (The Bank):</strong> If Spot volume is leading, real money is buying to own the asset, not just gamble on it. This signals genuine accumulation and a healthier, more sustainable trend.</li>
    </ul>
  </li>  
  <li>  
    <strong>(2) The Heat Check:</strong>
    <ul style="list-style-type: disc; padding-left: 20px; line-height: 1.4;">
      <li>If VTMR is Over 1.0x: The token is trading its entire Market Cap in volume. It is hyper-active and volatile. But still that doesn't guarantee pump.</li>
    </ul>
  </li>  
</ul>
</ul>
                    <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 20px;">Remaining Spot Only Tokens</h2>
                    <p>Remember those remaining spot only tokens because there is plenty opportunity there too. So, check them out. Don't fade on them.</p>
           <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 20px;">Disclaimer</h2>
                    <small>This analysis was generated by you using the <strong>Crypto Volume Analysis Toolkit</strong> by <strong>@heisbuba</strong>. It empowers your market research but does not replace your due diligence. Verify the data, back your own instincts, and trade entirely at your own risk.</small>
                </div>
            """
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Crypto Volume-driven Data Analysis Report</title>
                <meta charset="UTF-8">
                <style>{ORIGINAL_HTML_STYLE}</style>
            </head>
            <body>
                <div class="header">
                    <h1>Cross-Market Crypto Analysis Report</h1>
                    <p>Using Both Spot & Futures Market Data</p>
                    <p><small>Generated on: {current_time}</small></p>
                </div>
                {html_content}
                  {cheat_sheet_pdf_footer}
                <div class="footer">
                    <p>Generated by Crypto Volume Analysis Toolkit 4.0 | By (@heisbuba)</p>
                </div>
            </body>
            </html>
            """
            return html

    def main_v4() -> None:
        print("   ADVANCED CRYPTO VOLUME ANALYSIS v2.0")
        print("   Scanning for Futures PDF and Spot CSV/HTML files")
        print("   " + "=" * 50)
        spot_file, futures_file = FileScanner.find_files()
        if not spot_file or not futures_file:
            print("   Required files not found.")
            print("   Need: Futures PDF and Spot CSV/HTML in Download folder")
            print("   💡 Tip: Run Spot Volume Tracker first to generate fresh spot data")
            return
        futures_df = PDFParser.extract(futures_file)
        spot_df = DataProcessor.load_spot(spot_file)
        html_content = DataProcessor.generate_html_report(futures_df, spot_df)
        
        if html_content:
            valid_futures = futures_df.copy()
            if 'vtmr' in valid_futures.columns:
                valid_futures = valid_futures[valid_futures['vtmr'] >= 0.50]
                
            merged = pd.merge(spot_df, valid_futures, on='ticker', how='inner', suffixes=('_spot', '_fut'))
            futures_only = valid_futures[~valid_futures['ticker'].isin(spot_df['ticker'])]
            spot_only = spot_df[~spot_df['ticker'].isin(merged['ticker'])].copy()
            
            if 'spot_flip' in spot_only.columns:
                try:
                    spot_only = spot_only.copy()
                    spot_only.loc[:, 'flip_numeric'] = spot_only['spot_flip'].astype(str).str.replace('x', '', case=False).astype(float)
                    spot_only = spot_only[spot_only['flip_numeric'] >= 0.50]
                except Exception:
                    pass

            cross_market = len(merged)
            futures_only_count = len(futures_only)
            spot_only_count = len(spot_only)
            print(f"\n   Analysis Summary:")
            print(f"   Cross-market tokens: {cross_market} (Volume ≥ 50% MC - Futures Standard)")
            print(f"   Futures-only tokens: {futures_only_count} (Volume ≥ 50% MC)")
            print(f"   Spot-only tokens: {spot_only_count} (Volume ≥ 50% MC - Adjusted)")
            
            pdf_path = None
            try:
                pdf_path = convert_html_to_pdf(html_content, spot_file.parent)
            except Exception:
                pdf_path = None
            print("   🧹 Cleaning up source files after analysis...")
            cleanup_after_analysis(spot_file, futures_file)
            if pdf_path:
                print(f"   PDF saved: {pdf_path}")
                print("   📊 Analysis completed! Source files cleaned up.")
            else:
                print("   PDF conversion failed")
        else:
            print("   No data to generate report")
        print("   Advanced Analysis completed!")

    main_v4()

# ============================================
# DOWNLOD FOLDER  CLEANUP HELPER
# ============================================
def cleanup_after_analysis(spot_file: Optional[Path], futures_file: Optional[Path]) -> int:
    files_cleaned = 0
    now_date = datetime.datetime.now().date()
    if spot_file and spot_file.exists():
        try:
            file_time = datetime.datetime.fromtimestamp(spot_file.stat().st_mtime)
            if file_time.date() == now_date:
                spot_file.unlink()
                print(f"   🗑️  Cleaned up spot file: {spot_file.name}")
                files_cleaned += 1
        except Exception as e:
            print(f"   ⚠️  Could not remove spot file: {e}")
    if futures_file and futures_file.exists():
        try:
            file_time = datetime.datetime.fromtimestamp(futures_file.stat().st_mtime)
            if file_time.date() == now_date:
                futures_file.unlink()
                print(f"   🗑️  Cleaned up futures PDF: {futures_file.name}")
                files_cleaned += 1
        except Exception as e:
            print(f"   ⚠️  Could not remove futures PDF: {e}")
    if files_cleaned > 0:
        print(f"   ✅ Cleaned up {files_cleaned} source files")
    return files_cleaned

# ============================================
# LOGGING CAPTURE FOR WEB UI
# ============================================
class LogCatcher:
    def __init__(self, original_stream):
        self.terminal = original_stream

    def write(self, msg):
        global PROGRESS
        self.terminal.write(msg)
        if msg and msg.strip():
            # [FIX]: Lock applied to list modification (append/pop)
            with LOCK:
                LIVE_LOGS.append(msg)
                if len(LIVE_LOGS) > 500:
                    LIVE_LOGS.pop(0)
            
            # [FIX]: Synced progress triggers with actual print output strings
            text = msg.lower()
            if "scanning coingecko" in text:
                with LOCK: PROGRESS = {"percent": 10, "text": "Fetching CoinGecko Data...", "status": "active"}
            elif "scanning livecoinwatch" in text:
                with LOCK: PROGRESS = {"percent": 30, "text": "Fetching LiveCoinWatch...", "status": "active"}
            elif "parsing spot file" in text: # [FIXED] from 'processing spot data'
                with LOCK: PROGRESS = {"percent": 50, "text": "Analyzing Spot Volumes...", "status": "active"}
            elif "parsing futures pdf" in text:
                with LOCK: PROGRESS = {"percent": 70, "text": "Parsing Futures PDF...", "status": "active"}
            elif "converting to pdf" in text: # [FIXED] from 'generating html'
                with LOCK: PROGRESS = {"percent": 90, "text": "Compiling Report...", "status": "active"}
            elif "completed" in text or "pdf saved" in text:
                with LOCK: PROGRESS = {"percent": 100, "text": "Task Completed Successfully", "status": "success"}
            elif "error" in text:
                with LOCK: PROGRESS = {"percent": 0, "text": "Error Occurred", "status": "error"}

    def flush(self):
        self.terminal.flush()

sys.stdout = LogCatcher(sys.stdout)

# ============================================
# WEB UI TEMPLATES
# ============================================
COMMON_HEAD = """
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Crypto Volume Analysis Toolkit</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark:#0b0e11; --bg-card:#151a1e; --text-main:#eaecef; --text-dim:#848e9c; --accent-green:#0ecb81; --accent-blue:#3b82f6; --accent-orange:#f59e0b; --accent-red:#f6465d; --border:#2b3139; --input-bg:#1e252a; }
        body { margin:0; background:var(--bg-dark); color:var(--text-main); font-family:'Inter',sans-serif; }
        .container { max-width:600px; margin:0 auto; padding:20px; }
        .card { background:var(--bg-card); padding:25px; border-radius:12px; border:1px solid var(--border); margin-bottom:20px; }
        h1 { color:var(--accent-green); text-align:center; font-size:1.4rem; margin-bottom:10px; }
        h2 { color:var(--accent-blue); font-size:1.1rem; margin-top:20px; }
        input[type="text"] { width:100%; padding:12px; background:var(--input-bg); border:1px solid var(--border); color:#fff; border-radius:8px; font-family:monospace; margin-top:5px; box-sizing:border-box; }
         .btn-all{padding:15px; border:none; border-radius:8px; font-weight:800; cursor:pointer; text-align:center; text-decoration:none; margin-top:10px; font-size:0.95rem; }
        .btn, .btn-reset{display:block; width:90%; padding:15px; border:none; border-radius:8px; font-weight:800; cursor:pointer; text-align:center; text-decoration:none; margin-top:10px; font-size:0.95rem; }
        .btn-share {display:block; width: 80%;  padding:15px; border:none; border-radius:8px; font-weight:800; cursor:pointer; text-align:center; text-decoration:none; margin-top:10px; font-size:0.95rem; }
        .btn-update{display:block; width:100%; padding:15px; border:none; border-radius:8px; font-weight:800; cursor:pointer; text-align:center; text-decoration:none; margin-top:10px; font-size:0.95rem; }
        .btn-green { background:var(--accent-green); color:#000; }
        .btn-blue { background:var(--bg-card); border:1px solid var(--accent-blue); color:var(--accent-blue); }
        .btn-red { background:rgba(246,70,93,0.1); border:1px solid var(--accent-red); color:var(--accent-red); }
        .link { color:var(--accent-blue); text-decoration:none; font-size:0.85rem; float:right; }
        .back-link { display:block; text-align:center; margin-top:20px; color:var(--text-dim); text-decoration:none; }
        .grid-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    </style>
</head>
"""

SETUP_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<body>
    <div class="container">
        <h1>⚙️ Setup Wizard</h1>
        <p style="text-align:center; color:#848e9c; font-size:0.9rem;">
            Crypto Volume Analysis Toolkit v4.0<br>By @heisbuba
        </p> <div class="card">
            <p><strong>Welcome!</strong> This one-time setup configures your API keys for the backend analysis engine.</p>
            <p><small>Note: Values are saved securely to your local script files. You can save partially and return later.</small></p>
            <a href="https://github.com/heisbuba/crypto-volume-analysis-toolkit" class="link" target="_blank">GitHub Documentation ↗</a>
        </div>

        <form action="/save-config" method="POST">
            <div class="card">
                <h2>1. API Keys Setup</h2>
                
                <label>CoinMarketCap Key <a href="https://pro.coinmarketcap.com/signup/" target="_blank" class="link">Get Key ↗</a></label>
                <input type="text" name="cmc_key" value="{{{{ cmc }}}}" placeholder="Paste CMC Key here...">

                <label style="margin-top:15px; display:block;">LiveCoinWatch Key <a href="https://www.livecoinwatch.com/tools/api" target="_blank" class="link">Get Key ↗</a></label>
                <input type="text" name="lcw_key" value="{{{{ lcw }}}}" placeholder="Paste LCW Key here...">

                <label style="margin-top:15px; display:block;">CoinRanking Key <a href="https://coinranking.com/api" target="_blank" class="link">Get Key ↗</a></label>
                <input type="text" name="cr_key" value="{{{{ cr }}}}" placeholder="Paste CR Key here...">

                <label style="margin-top:15px; display:block;">HTML2PDF Key <a href="https://html2pdf.app" target="_blank" class="link">Get Key ↗</a></label>
                <input type="text" name="html2pdf_key" value="{{{{ html2pdf }}}}" placeholder="Paste HTML2PDF Key here...">
            </div>

            <div class="card">
                <h2>2. CoinAlyze Setup</h2>
                <label>VTMR URL <a href="https://coinalyze.net" target="_blank" class="link">Go to CoinAlyze ↗</a></label>
                <input type="text" name="vtmr_url" value="{{{{ vtmr }}}}" placeholder="https://coinalyze.net/?columns=...">
                <p style="font-size:0.9rem; color:#848e9c; margin-top:9px; margin-bottom:3px;">
                    <strong>Instructions:</strong><br>
                    1. Visit Github Documentation page and copy VTRM code.<br>
                    2. Go to CoinAlyze.net and Signup.<br>
                    3. Create <strong>Custom Metrics</strong> with name <strong>VTMR</strong>, paste your code and save.<br>
                    4. Select these Columns: MC, Vol 24H, Open Interest Change % 24H, Predicted Funding Rate Average, OI Weighted, VTMR (Custom) and tap on Apply<br>
                    5. Sort the tokens by VTMR<br>
                    6. Copy the <strong>entire</strong> browser URL and paste above.
                </p>
            </div>

            <div class="grid-buttons">
                <button type="submit" name="action" value="save" class="btn-update btn-green">LAUNCH APP</button>
                <button type="submit" name="action" value="quit" class="btn-update btn-red">SAVE & QUIT</button>
            </div>
        </form>
    </div>
</body></html>"""

SETTINGS_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<body>
    <div class="container">
        <h1>🛠️ Settings</h1>
        
        <form action="/save-config" method="POST">
            <div class="card">
                <h2>Update Configuration</h2>
                <label>CoinMarketCap Key</label>
                <input type="text" name="cmc_key" value="{{{{ cmc }}}}" required>
                
                <label style="margin-top:10px; display:block;">LiveCoinWatch Key</label>
                <input type="text" name="lcw_key" value="{{{{ lcw }}}}" required>
                
                <label style="margin-top:10px; display:block;">CoinRanking Key</label>
                <input type="text" name="cr_key" value="{{{{ cr }}}}" required>
                
                <label style="margin-top:10px; display:block;">HTML2PDF Key</label>
                <input type="text" name="html2pdf_key" value="{{{{ html2pdf }}}}" required>
                
                <label style="margin-top:10px; display:block;">VTMR URL</label>
                <input type="text" name="vtmr_url" value="{{{{ vtmr }}}}" required>
                
                <button type="submit" class="btn-update btn-green">UPDATE SETTINGS</button>
            </div>
        </form>

        <div class="card" style="border-color: var(--accent-red);">
            <h2 style="color:var(--accent-red); margin-top:0;">Danger Zone</h2>
            <p style="font-size:0.85rem;">Reset all keys and return to the Setup Wizard.</p>
            <a href="/factory-reset" class="btn-reset btn-red" onclick="return confirm('Are you sure? This will delete all saved API keys and VTMR url.');">FACTORY RESET</a>
        </div>

        <a href="/" style="color: #3b82f6;" class="back-link">← Back to Dashboard</a>
    </div>
</body></html>"""

HELP_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<body>
    <div class="container">
        <h1>📚 Help & Info</h1>
        
        <div class="card">
            <h2>About Crypto VAT v4.0</h2>
            <p style="font-size:0.9rem; line-height:1.6;">
                <strong>Crypto Volume Analysis Toolkit v4.0</strong><br>
                Is a lightweight and simple standalone toolkit that helps you track high-volume crypto tokens in the last 24 hours and run cross-market analysis using spot + futures data and generate reports in a pdf. The generated report is a valuable asset to cryto analysts and trades alike.
            </p>
        </div>

        <div class="card">
            <h2>Links & Socials</h2>
            <a href="https://github.com/heisbuba/crypto-volume-analysis-toolkit" class="btn btn-blue" target="_blank">View on GitHub</a>
            <a href="https://medium.com/@bubanotes" class="btn btn-blue" target="_blank">Medium Blog</a>
            <div style="margin-top:15px; display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <a href="https://x.com/heisbuba" class="btn-share btn-blue" style="margin:0;" target="_blank">X (Twitter)</a>
                <a href="https://facebook.com/heisbuba" class="btn-share btn-blue" style="margin:0;" target="_blank">Facebook</a>
                 <a href="https://youtube.com/@heisbuba" class="btn-share btn-blue" style="margin:0;" target="_blank">YouTube</a>
                  <a href="https://t.me/heisbuba" class="btn-share btn-blue" style="margin:0;" target="_blank">Telegram</a>
            </div>
        </div>

        <a href="/" style="color: #3b82f6;" class="back-link">← Back to Dashboard</a>
    </div>
</body></html>"""

HOME_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Crypto VAT Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #0b0e11; --bg-card: #151a1e; --text-main: #eaecef; --text-dim: #848e9c; --accent-green: #0ecb81; --accent-blue: #3b82f6; --accent-purple: #9333ea; --accent-orange: #f59e0b; --border: #2b3139; --accent-red: #f6465d; }
        * { box-sizing: border-box; }
        body { margin:0; background:var(--bg-dark); color:var(--text-main); font-family:'Inter', sans-serif; display:flex; flex-direction:column; min-height:100vh; }
        .header, .footer { padding:20px; background:var(--bg-card); border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
        .header h1 { margin:0; font-size:1.1rem; color:var(--accent-green); }
        .icon-btn { color:var(--text-dim); text-decoration:none; font-size:1.2rem; padding:0 10px; }
        .footer p { margin:0; text-align:center; font-size:0.9rem; color:var(--accent-green); }
        .icon-btn { color:var(--text-dim); text-decoration:none; font-size:1.2rem; padding:0 10px; }
        .quit-btn { color:var(--accent-red); text-decoration:none; font-size:1.2rem; padding:0 10px; font-weight:bold; }
        .container { flex:1; padding:20px; max-width:600px; margin:0 auto; width:100%; }
        .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:20px; }
        .btn { background:var(--bg-card); border:1px solid var(--border); color:var(--text-main); padding:20px; border-radius:12px; font-weight:600; text-align:center; cursor:pointer; text-decoration:none; display:flex; flex-direction:column; align-items:center; gap:8px; }
        .btn svg { width:24px; height:24px; margin-bottom:5px; }
        .btn-spot { border-color:var(--accent-blue); color:var(--accent-blue); }
        .btn-futures { border-color:var(--accent-orange); color:var(--accent-orange); }
        .btn-adv { border-color:var(--accent-green); color:var(--accent-green); }
        .btn-report { border-color:var(--accent-purple); color:var(--accent-purple); }
        .terminal { background:#000; padding:15px; border-radius:12px; border:1px solid var(--border); font-family:'JetBrains Mono', monospace; font-size:0.75rem; color:var(--text-dim); height:200px; overflow-y:auto; margin-top:20px; }
        .log-line { margin-bottom:4px; }
        .log-line.highlight { color:#fff; border-left:2px solid var(--accent-green); padding-left:5px; }
        .log-line.error { color:#f6465d; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Crypto VAT v4.0</h1>
        <div>
            <a href="/help" class="icon-btn">?</a>
            <a href="/settings" class="icon-btn">⚙️</a>
            <a href="javascript:void(0)" onclick="quitApp()" class="quit-btn" title="Quit">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px;">
                    <path d="M18.36 6.64a9 9 0 11-12.72 0"></path>
                    <path d="M12 2v6"></path>
                </svg>
            </a>
        </div>
    </div>
    
    <div class="container">
        <div class="grid">
            <button class="btn btn-spot" onclick="trigger('/run-spot')">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path></svg>
                SPOT SCAN
            </button>
            <a class="btn btn-futures" href="/get-futures-data">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path></svg>
                GET FUTURES
            </a>
            <button class="btn btn-adv" onclick="trigger('/run-advanced')">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
                ADVANCED
            </button>
            <a class="btn btn-report" href="/reports-list">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                REPORTS
            </a>
        </div>

        <div style="margin-top:20px; display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:0.8rem; color:var(--text-dim);">STATUS</span>
            <span id="percent" style="font-size:0.8rem; font-weight:bold; color:var(--accent-green);">0%</span>
        </div>
        <div style="height:4px; background:#2b3139; border-radius:2px; margin-top:5px; overflow:hidden;">
            <div id="bar" style="height:100%; width:0%; background:var(--accent-green); transition:width 0.3s;"></div>
        </div>

        <div class="terminal" id="term">
            <div class="log-line">> System Initialized...</div>
        </div>
        <br>

   <div class="footer">
        <p style="color:#fff;">© 2025 | Made with 💚 4rm Nigeria.</p>
    </div>
    <script>
        let busy = false; let lastIdx = 0;
        
        function quitApp() {
            if(confirm('Quit and Save?')) {
                fetch('/shutdown');
                document.body.innerHTML = "<h2 style='color:#fff;text-align:center;margin-top:50px;'>App Terminated.<br>You can close this window or app.</h2>";
            }
        }

        function trigger(url) {
            if (busy) return;
            busy = true; 
            
            // AUTOMATION: Immediate UI Cleanup
            document.getElementById('term').innerHTML = ''; // Clear terminal visual
            lastIdx = 0; // Reset log index to 0
            document.getElementById('bar').style.width='5%';
            document.getElementById('percent').innerText='5%';
            
            fetch(url).then(r=>r.json()).then(()=>{ poll(); logs(); }).catch(()=>{ busy=false; });
        }
        function poll() {
            fetch('/progress').then(r=>r.json()).then(d=>{
                document.getElementById('bar').style.width=d.percent+'%';
                document.getElementById('percent').innerText=d.percent+'%';
                if(d.status==='active') setTimeout(poll,800); else busy=false;
            });
        }
        function logs() {
            fetch('/logs-chunk?last='+lastIdx).then(r=>r.json()).then(d=>{
                if(d.logs.length){
                    lastIdx=d.last_index;
                    d.logs.forEach(l=>{
                        let div=document.createElement('div');
                        div.className='log-line '+(l.includes('Error')?'error':l.includes('Found')?'highlight':'');
                        div.innerText='> '+l;
                        document.getElementById('term').appendChild(div);
                    });
                    document.getElementById('term').scrollTop=9999;
                }
                if(busy) setTimeout(logs,1000);
            });
        }
    </script>
</body></html>"""

FUTURES_INSTRUCTIONS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Get Futures Data</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body { background-color: #0b0e11; color: #eaecef; font-family: 'Inter', sans-serif; padding: 20px; text-align: center; }
        .container { max-width: 640px; margin: 0 auto; background: #151a1e; padding: 30px; border-radius: 12px; border: 1px solid #2b3139; }
        h1 { color: #f59e0b; }
        .btn { background: #f59e0b; color: #000; padding: 15px 25px; border-radius: 8px; text-decoration: none; font-weight: bold; display: inline-block; margin-top: 20px; }
        .instruction-list { text-align: left; margin-top: 20px; line-height: 1.6; color: #848e9c; }
        li { margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Get Futures Data</h1>
        <p>Your VTMR view is configured and ready.</p>
        <div class="instruction-list">
            <ol>
                <li>Click the button below to open CoinAlyze in Chrome.</li>
                <li>Wait for the data table to load completely.</li>
                <li> Tap on share button to open it directly on Chrome without terminating this toolkit</li>
                <li><strong> In Chrome, go to Menu (⋮) → Share → Print → Save as PDF</strong>.</li>
                <li>Save strictly to: <code>{{ REPORT_SAVE_PATH }}</code></li>
                <li>Do NOT rename the file.</li>
                <li>Return here and run "Advanced Analysis".</li>
            </ol>
        </div>
        <a href="{{ FUTURES_URL }}" target="_blank" class="btn">OPEN VTMR VIEW</a>
        <br><br>
        <a href="/" style="color: #3b82f6;">← Back to Dashboard</a>
    </div>
</body>
</html>
"""

REPORT_LIST_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reports</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body { background-color: #0b0e11; color: #eaecef; font-family: 'Inter', sans-serif; padding: 20px; }
        .container { max-width: 600px; margin: 0 auto; }
        h1 { text-align: center; color: #9333ea; }
        .file-item { background: #151a1e; padding: 15px; border-radius: 8px; border: 1px solid #2b3139; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
        .btn { background: #9333ea; color: #fff; padding: 8px 12px; border-radius: 4px; text-decoration: none; font-size: 0.8rem; }
        .back { display: block; text-align: center; margin-top: 20px; color: #3b82f6; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>All Analysis Reports</h1>
        {% for file in files %}
        <div class="file-item">
            <span>{{ file }}</span>
            <a href="{{ BASE_URL }}/reports/{{ file }}" target="_blank" class="btn">OPEN</a>
        </div>
        {% else %}
        <p style="text-align:center; color:#848e9c;">No reports found.</p>
        {% endfor %}
        <a href="/" class="back">← Back to Dashboard</a>
    </div>
</body>
</html>
"""

# ============================================
# FLASK ROUTES
# ============================================

@app.route("/")
def home():
    if not is_system_configured():
        return redirect(url_for('setup'))
    return render_template_string(HOME_TEMPLATE, BASE_URL=BASE_URL, REPORT_SAVE_PATH=str(REPORT_SAVE_PATH))

@app.route("/setup")
def setup():
    def clean(val):
        s = str(val)
        if "CONFIG_REQUIRED" in s or "YOUR_" in s or "CONFIG_VTMR_URL" in s: return ""
        return s

    return render_template_string(SETUP_TEMPLATE, 
        cmc=clean(CMC_API_KEY),
        lcw=clean(LIVECOINWATCH_API_KEY),
        cr=clean(COINRANKINGS_API_KEY),
        html2pdf=clean(HTML2PDF_API_KEY),
        vtmr=clean(COINALYZE_VTMR_URL)
    )

@app.route("/settings")
def settings():
    return render_template_string(SETTINGS_TEMPLATE, 
        cmc=CMC_API_KEY, 
        lcw=LIVECOINWATCH_API_KEY,
        cr=COINRANKINGS_API_KEY,
        html2pdf=HTML2PDF_API_KEY,
        vtmr=COINALYZE_VTMR_URL
    )

@app.route("/help")
def help_page():
    return render_template_string(HELP_TEMPLATE, BASE_URL=BASE_URL)

@app.route("/save-config", methods=["POST"])
def save_config():
    action = request.form.get("action")
    
    cmc = request.form.get("cmc_key", "").strip() or "CONFIG_REQUIRED_CMC"
    lcw = request.form.get("lcw_key", "").strip() or "CONFIG_REQUIRED_LCW"
    cr = request.form.get("cr_key", "").strip() or "CONFIG_REQUIRED_CR"
    html2pdf = request.form.get("html2pdf_key", "").strip() or "CONFIG_REQUIRED_HTML2PDF"
    vtmr_url = request.form.get("vtmr_url", "").strip() or "CONFIG_VTMR_URL"

    update_config("CMC_API_KEY", cmc)
    update_config("LIVECOINWATCH_API_KEY", lcw)
    update_config("COINRANKINGS_API_KEY", cr)
    update_config("HTML2PDF_API_KEY", html2pdf)
    update_config("COINALYZE_VTMR_URL", vtmr_url)
    
    if action == "quit":
        return redirect(url_for('shutdown_page_visual'))
    
    return redirect(url_for('home'))

@app.route("/factory-reset")
def factory_reset():
    update_config("CMC_API_KEY", "CONFIG_REQUIRED_CMC")
    update_config("LIVECOINWATCH_API_KEY", "CONFIG_REQUIRED_LCW")
    update_config("COINRANKINGS_API_KEY", "CONFIG_REQUIRED_CR")
    update_config("HTML2PDF_API_KEY", "CONFIG_REQUIRED_HTML2PDF")
    update_config("COINALYZE_VTMR_URL", "CONFIG_VTMR_URL")
    return redirect(url_for('setup'))

@app.route("/shutdown", methods=['GET', 'POST'])
def shutdown():
    os.kill(os.getpid(), signal.SIGINT)
    return "Server shutting down..."

@app.route("/shutdown-page")
def shutdown_page_visual():
    def kill_me():
        time.sleep(1)
        os.kill(os.getpid(), signal.SIGINT)
    threading.Thread(target=kill_me).start()
    
    return f"""<!DOCTYPE html><html>
    <head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{{background:#0b0e11;color:#eaecef;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;flex-direction:column;text-align:center;}}</style></head>
    <body><h2 style='color:#0ecb81;'>Configuration Saved</h2><p>Application Terminated.<br>You can close this window now.</p></body></html>"""

@app.route("/reports-list")
def reports_list():
    def get_report_files() -> list[str]:
        if not REPORT_SAVE_PATH.exists(): REPORT_SAVE_PATH.mkdir(parents=True, exist_ok=True)
        report_files = [f.name for f in REPORT_SAVE_PATH.glob('*') if f.suffix in ['.html', '.pdf'] and f.is_file()]
        toolkit_patterns = ['Volumed_Spot_Tokens_*.html', '*crypto-analysis.pdf', '*Crypto_Report_*.html']
        for pattern in toolkit_patterns:
            for f in REPORT_SAVE_PATH.glob(pattern):
                if f.name not in report_files: report_files.append(f.name)
        return sorted(report_files, reverse=True)
    
    files = get_report_files()
    return render_template_string(REPORT_LIST_TEMPLATE, files=files, REPORT_SAVE_PATH=str(REPORT_SAVE_PATH), BASE_URL=BASE_URL)

@app.route("/latest-report")
def latest_report():
    def get_report_files() -> list[str]:
        if not REPORT_SAVE_PATH.exists(): REPORT_SAVE_PATH.mkdir(parents=True, exist_ok=True)
        report_files = [f.name for f in REPORT_SAVE_PATH.glob('*') if f.suffix in ['.html', '.pdf'] and f.is_file()]
        toolkit_patterns = ['Volumed_Spot_Tokens_*.html', '*crypto-analysis.pdf', '*Crypto_Report_*.html']
        for pattern in toolkit_patterns:
            for f in REPORT_SAVE_PATH.glob(pattern):
                if f.name not in report_files: report_files.append(f.name)
        return sorted(report_files, reverse=True)
    
    files = get_report_files()
    if files:
        return redirect(url_for('serve_report', filename=files[0]))
    return "No reports found."

@app.route("/reports/<path:filename>")
def serve_report(filename):
    return send_from_directory(str(REPORT_SAVE_PATH), filename)

@app.route("/get-futures-data")
def get_futures_data():
    return render_template_string(FUTURES_INSTRUCTIONS_TEMPLATE,
                                  FUTURES_URL=COINALYZE_VTMR_URL,
                                  BASE_URL=BASE_URL,
                                  REPORT_SAVE_PATH=str(REPORT_SAVE_PATH))

@app.route("/run-spot")
def run_spot():
    run_background_task(spot_volume_tracker)
    return jsonify({"status": "started"})

@app.route("/run-advanced")
def run_advanced():
    run_background_task(crypto_analysis_v4)
    return jsonify({"status": "started"})

@app.route("/progress")
def progress():
    return jsonify(PROGRESS)

@app.route("/logs-chunk")
def logs_chunk():
    try: last_idx = int(request.args.get('last', 0))
    except: last_idx = 0
    with LOCK:
        current_len = len(LIVE_LOGS)
        if last_idx > current_len:
            new_logs = LIVE_LOGS
            current_len = len(LIVE_LOGS)
        else:
            new_logs = [] if last_idx >= current_len else LIVE_LOGS[last_idx:]
    return jsonify({"logs": new_logs, "last_index": current_len})

# ============================================
# LAUNCHER
# ============================================
def open_browser():
    time.sleep(1.5)
    webbrowser.open(BASE_URL)

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("CRYPTO VOLUME ANALYSIS TOOLKIT v4.0 - Standalone Web Version")
    print(f"{'='*60}")
    print(f"Dashboard URL: {BASE_URL}")
    print(f"Reports saved to: {REPORT_SAVE_PATH}")
    print(f"{'='*60}")
    print("Press Ctrl+C to stop the server\n")
    
    # Daemon thread ensures browser opening doesn't block server
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

#--------------------- END OF TE CODE --------------
#---------------------           BY               ----------------
#--------------------- @heisbuba --------------------