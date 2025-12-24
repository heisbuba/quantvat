#!/usr/bin/env python3
"""
Crypto Volume Analysis Toolkit v4.0 - Cloud Edition
Web-Based Version adapted for Hugging Face Spaces.

This application performs cross-market analysis by comparing Spot volume data 
(from multiple APIs) against Futures data (parsed from Coinalyze PDFs).
"""

from __future__ import annotations

import os
import sys
import re
import time
import json
import datetime
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, jsonify, request, redirect, url_for, send_from_directory, session, flash, get_flashed_messages
from functools import wraps
from datetime import timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Optional dependency: pypdf is required for futures analysis but optional for spot-only runs.
try:
    import pypdf
except Exception:
    pypdf = None

import pandas as pd
from werkzeug.utils import secure_filename
from markupsafe import escape 
from playwright.sync_api import sync_playwright
import signal

# Firebase imports (Required for Hugging Face persistence)
try:
    import firebase_admin
    from firebase_admin import credentials, firestore, auth
    FIREBASE_AVAILABLE = True
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None
    auth = None
    FIREBASE_AVAILABLE = False
    print("❌ Firebase libraries not available - This version requires Firebase for Hugging Face")

# -----------------------------------------------------------------------------
# Global Constants & Configuration
# -----------------------------------------------------------------------------

# Tokens to exclude to prevent skewed volume data from stablecoin pairs
STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'BSC-USD', 'USD1', 'CBBTC', 'WBNB', 'WETH',
    'UST', 'TUSD', 'USDP', 'USDD', 'FRAX', 'GUSD', 'LUSD', 'FDUSD'
}

TEMP_DIR = Path("/tmp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get('SPACE_URL', 'http://127.0.0.1:7860')
BASE_DIR = Path(__file__).resolve().parent

# -----------------------------------------------------------------------------
# Flask Application Setup
# -----------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())

# Cookie settings required for running within Hugging Face iframe & caching
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True

# -----------------------------------------------------------------------------
# Global State for Web UI
# -----------------------------------------------------------------------------

# Dictionaries to hold state per user: { "user_id": data }
USER_LOGS = {} 
USER_PROGRESS = {}
LOCK = threading.Lock()

def get_progress(uid):
    with LOCK:
        return USER_PROGRESS.get(uid, {"percent": 0, "text": "System Idle", "status": "idle"})

def update_progress(uid, percent, text, status):
    with LOCK:
        USER_PROGRESS[uid] = {"percent": percent, "text": text, "status": status}

def get_user_temp_dir(uid) -> Path:
    """Creates and returns a specific directory for the logged-in user."""
    user_dir = TEMP_DIR / uid
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir

# -----------------------------------------------------------------------------
# Database Connection (Firebase)
# -----------------------------------------------------------------------------

def init_firebase():
    """
    Initialize Firebase connection using environment variables.
    This is critical for persistent user storage on ephemeral cloud containers.
    """
    if not FIREBASE_AVAILABLE:
        raise ImportError("Firebase libraries not installed. Install with: pip install firebase-admin")
    
    firebase_config_str = os.environ.get("FIREBASE_CONFIG")
    if not firebase_config_str:
        raise ValueError("FIREBASE_CONFIG environment variable not set")
    
    try:
        cred = credentials.Certificate(json.loads(firebase_config_str))
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase Connected Successfully")
        return db
    except Exception as e:
        raise Exception(f"Firebase initialization failed: {e}")

# Attempt to initialize DB immediately on startup
try:
    db = init_firebase()
    FIREBASE_INITIALIZED = True
except Exception as e:
    print(f"❌ FATAL: {e}")
    print("This application requires Firebase configuration to run on Hugging Face.")
    print("Please set the FIREBASE_CONFIG environment variable in your Hugging Face Space secrets.")
    sys.exit(1)

# API Key for Client-Side Auth (Firebase REST)
FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_API_KEY")
if not FIREBASE_WEB_API_KEY:
    print("⚠️ FIREBASE_API_KEY not set. Login will use test mode.")

# -----------------------------------------------------------------------------
# Cloud Configuration Helpers
# -----------------------------------------------------------------------------

def get_user_keys(uid):
    """Retrieve API keys for a specific user from Firestore."""
    try:
        doc = db.collection('users').document(uid).get()
        if doc.exists:
            return doc.to_dict()
    except Exception as e:
        print(f"Firestore Error: {e}")
    return {}

def update_user_keys(uid, data):
    """Upsert API keys to the user's Firestore document."""
    try:
        db.collection('users').document(uid).set(data, merge=True)
        return True
    except Exception:
        return False

def is_user_setup_complete(uid):
    """Validate if the user has configured all required external API keys."""
    keys = get_user_keys(uid)
    required = ["CMC_API_KEY", "LIVECOINWATCH_API_KEY", "COINRANKINGS_API_KEY", "COINALYZE_VTMR_URL"]
    for k in required:
        if k not in keys or not keys[k] or "CONFIG_" in str(keys[k]):
            return False
    return True

REPORT_SAVE_PATH = TEMP_DIR

# -----------------------------------------------------------------------------
# Background Task Manager
# -----------------------------------------------------------------------------

def run_background_task(target_func, user_id):
    """
    Executes analysis in a thread named after the user_id.
    """
    with LOCK:
        USER_LOGS[user_id] = []
        USER_PROGRESS[user_id] = {"percent": 5, "text": "Initializing Engine...", "status": "active"}

    def worker():
        try:
            # Re-confirm thread name inside for LogCatcher safety
            threading.current_thread().name = f"user_{user_id}"
            user_keys = get_user_keys(user_id)
            target_func(user_keys, user_id) 
            update_progress(user_id, 100, "Analysis Complete", "success")
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {str(e)}\n")
            update_progress(user_id, 0, "Error Occurred", "error")
            
    thread = threading.Thread(target=worker, name=f"user_{user_id}")
    thread.daemon = True
    thread.start()
# -----------------------------------------------------------------------------
# Core Utilities
# -----------------------------------------------------------------------------

def create_session(retries: int = 3, backoff_factor: float = 0.5, status_forcelist=(429, 500, 502, 503, 504)) -> requests.Session:
    """Configures a requests session with automatic retry logic for resilience."""
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
    """Formats large numbers into readable strings (e.g., 1.5B, 200M)."""
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

# -----------------------------------------------------------------------------
# Spot Volume Analysis Engine
# -----------------------------------------------------------------------------

def spot_volume_tracker(user_keys, user_id) -> None:
    """
    Aggregates spot market data from CoinGecko, CoinMarketCap, LiveCoinWatch, and CoinRankings.
    Identifies tokens where 24h Volume > 75% of Market Cap.
    """
    print("   📊 Starting fresh spot analysis...")
    
    # Extract API keys
    CMC_API_KEY = user_keys.get("CMC_API_KEY", "CONFIG_REQUIRED_CMC")
    LIVECOINWATCH_API_KEY = user_keys.get("LIVECOINWATCH_API_KEY", "CONFIG_REQUIRED_LCW")
    COINRANKINGS_API_KEY = user_keys.get("COINRANKINGS_API_KEY", "CONFIG_REQUIRED_CR")
                                     
    def create_html_report(hot_tokens: List[Dict[str, Any]]) -> Path:
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
        
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        return html_file

    # --- Data Fetching Functions ---

    def fetch_coingecko(session: requests.Session) -> List[Dict[str, Any]]:
        threading.current_thread().name = f"user_{user_id}"
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
        threading.current_thread().name = f"user_{user_id}"
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
        threading.current_thread().name = f"user_{user_id}"
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
        threading.current_thread().name = f"user_{user_id}"
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
        """Concurrent execution of all data fetchers."""
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

    # --- Processing Logic ---
    raw_tokens, _ = fetch_all_sources()
    all_data: Dict[str, List[Dict[str, Any]]] = {}
    
    # Aggregate by Symbol
    for t in raw_tokens:
        sym = (t.get('symbol') or '').upper()
        if not sym: continue
        all_data.setdefault(sym, []).append(t)

    verified_tokens: List[Dict[str, Any]] = []
    
    # Verification & Averaging Logic
    for sym, tokens in all_data.items():
        # Case 1: Large Cap (Allow single source verification if Market Cap > 1B)
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

        # Case 2: Multi-source verification for standard caps
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
    html_file = create_html_report(hot_tokens)

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

# -----------------------------------------------------------------------------
# PDF Generation Service
# -----------------------------------------------------------------------------

def convert_html_to_pdf(html_content: str, user_id: str) -> Optional[Path]:
    """
    Uses Playwright (Local Chromium) to convert HTML to PDF.
    Renders exactly as defined in CSS (Scale 1.0) on US Letter paper.
    """
    print("\n   Converting to PDF (Playwright Engine)...")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    pdf_name = f"{timestamp}-crypto-analysis.pdf"
    
    # Use user isolated directory
    user_dir = get_user_temp_dir(user_id)
    pdf_path = user_dir / pdf_name
        
    try:
        with sync_playwright() as p:
            # 1. Launch Browser
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # 2. Load HTML
            # wait_until="networkidle" is safer to ensure styles load
            page.set_content(html_content, wait_until="networkidle")
            
            # 3. Print to PDF (US Letter, No Auto-Scaling)
            page.pdf(
                path=pdf_path,
                format="Letter",       # <--- UPDATED TO LETTER
                landscape=False,
                scale=1.0,             # <--- FORCED TO 1.0 (Exact Size)
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}
            )
            browser.close()

        file_size = pdf_path.stat().st_size
        print(f"   PDF created: {pdf_name}")
        print(f"   Size: {file_size:,} bytes")
        print(f"   Location: {user_dir}")
        return pdf_path

    except Exception as e:
        print(f"   ❌ Playwright Error: {e}")
        return None
# -----------------------------------------------------------------------------
# Advanced Analysis Toolkit (Spot + Futures)
# -----------------------------------------------------------------------------

def crypto_analysis_v4(user_keys, user_id) -> None:
    """
    Orchestrates the cross-market analysis by matching Spot data (CSV/HTML) 
    with Futures data (PDF extracted).
    """
    print("   🔍 Looking for analysis files...")
    

    ORIGINAL_HTML_STYLE = """
            body { margin: 20px; background: #f5f5f5; font-family: Arial, sans-serif; }
            .table-container { margin: 20px 0; background: white; padding: 15px; border-radius: 10px; }
            table { width: 100%; border-collapse: collapse; margin: 10px 0; }
            thead { display: table-row-group; }
            th, td { padding: 10px; border: 1px solid #ddd; text-align: left; }
            th { background: #2c3e50; color: white; }
            tr:nth-child(even) { background: #f9f9f9; }
            .header { background: #2c3e50; color: white; padding: 20px; border-radius: 10px; text-align: center; }
            h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
            .footer { text-align: center; margin-top: 20px; color: #7f8c8d; }
            .oi-strong { color: #27ae60; font-weight: bold; }
            .oi-weak { color: #c0392b; }
        """
    
    ORIGINAL_MATCHED_HEADERS = ["Ticker", "Spot MrktCap", "Spot Volume", "Spot VTMR", "Futures Volume", "Futures VTMR", "OISS", "Funding Rate"]
    ORIGINAL_FUTURES_HEADERS = ["Ticker", "Market Cap", "Volume", "VTMR", "OISS", "Funding Rate"]
    ORIGINAL_SPOT_HEADERS = ["Ticker", "Market Cap", "Volume", "Spot VTMR"]

    # --- Signal Analysis Helpers ---

    def oi_score_and_signal(oi_change: float) -> Tuple[int, str]:
        if oi_change > 0.20: return 5, "Strong"
        if oi_change > 0.10: return 4, "Bullish"
        if oi_change > 0.00: return 3, "Build-Up"
        if oi_change > -0.10: return 2, "Weakening"
        if oi_change > -0.20: return 1, "Exiting"
        return 0, "Exiting"

    def funding_score_and_signal(funding_val: float) -> Tuple[str, str]:
        if funding_val >= 0.05: return "Greed", "oi-strong"
        if funding_val > 0.00: return "Bullish", "oi-strong"
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
        """Locates the latest Spot and Futures data files in the USER directory."""
        @staticmethod
        def find_files() -> Tuple[Optional[Path], Optional[Path]]:
            spot_file: Optional[Path] = None
            futures_file: Optional[Path] = None
            
            user_dir = get_user_temp_dir(user_id)
            if not user_dir.exists():
                return None, None

            # Get today's date for filtering
            today = datetime.datetime.now().date()
            
            # Filter for today's files only
            today_files = []
            for f in user_dir.iterdir():
                if f.is_file():
                    try:
                        file_time = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                        if file_time.date() == today:  # Only use today's files
                            today_files.append(f)
                    except Exception:
                        continue
            
            if not today_files:
                return None, None
                
            # Sort by modification time (newest first)
            files = sorted(today_files, key=lambda x: x.stat().st_mtime, reverse=True)

            for f in files:
                name = f.name.lower()
                if not futures_file and f.suffix == ".pdf" and "futures" in name:
                    futures_file = f
                elif not spot_file and f.suffix in [".csv", ".html"] and "spot" in name:
                    spot_file = f
                
                if spot_file and futures_file:
                    break
                    
            return spot_file, futures_file

    class PDFParser:
        """Handles extraction of tabular data from Coinalyze PDFs using regex."""
        
        # Regex explanation:
        # 1. Market Cap, 2. Volume (both allow K/M/B suffixes)
        # 3. OI Change (optional, %, or N/A)
        # 4. Funding Rate (optional, %, or N/A)
        # 5. VTMR (floating point number)
        FINANCIAL_PATTERN = re.compile(
            r'(\$?[+-]?[\d,\.]+[kKmMbB]?)\s+'             
            r'(\$?[+-]?[\d,\.]+[kKmMbB]?)\s+'             
            r'(?:([+\-]?[\d\.\,]+\%?|[\-\–\—]|N\/A)\s+)?' 
            r'(?:([+\-]?[\d\.\,]+\%?|[\-\–\—]|N\/A)\s+)?' 
            r'(\d*\.?\d+)'                                
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
            """
            Separates text lines into Ticker/Name and Financial Data, then reconciles them.
            This handles PDF layouts where columns might not align perfectly in raw text extraction.
            """
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
                    oi_str = groups[2]
                    fund_str = groups[3]
                    vtmr = groups[4]
                    
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
        """Handles Dataframe loading, merging, and HTML generation."""
        
        @staticmethod
        def load_spot(path: Path) -> pd.DataFrame:
            print(f"   Parsing Spot File: {path.name}")
            try:
                if path.suffix == '.html':
                    df = pd.read_html(path)[0]
                else:
                    df = pd.read_csv(path)
                df.columns = [c.lower().replace(' ', '_') for c in df.columns]
                
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
                
                # Normalize ticker column
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
            """Merges Spot and Futures dataframes and creates the final HTML report."""
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

            # Create the 3 main datasets: Overlap, Futures-Only, Spot-Only
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
            
            merged_cols = ['ticker', 'spot_mc', 'spot_vol', 'spot_flip', 'volume', 'vtmr_display', 'oiss', 'funding']
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
      <li><strong> Futures VTMR &gt; Spot VTMR (The Casino):</strong> If Futures volume is huge (e.g., 8x) but Spot is low, the price is being driven by leverage and speculation. This is fragile—expect violent "wicks" and liquidation hunts.</li>
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
        """Main execution flow for Advanced Analysis."""
        print("   ADVANCED CRYPTO VOLUME ANALYSIS v4.0")
        print("   Scanning for Futures PDF and Spot CSV/HTML files")
        print("   " + "=" * 50)
        spot_file, futures_file = FileScanner.find_files()
        if not spot_file or not futures_file:
            print("   Required files not found.")
            raise FileNotFoundError("   You Need CoinAlyze Futures PDF and Spot Market Data. Kindly Generate Spot Data And Upload Futures PDF First.")
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
            
            #  Pass user_id to PDF converter
            pdf_path = convert_html_to_pdf(html_content, user_id)
            
            print("   🧹 Cleaning up source files after analysis...")
            cleanup_after_analysis(spot_file, futures_file)
            
            if pdf_path:
                print(f"   PDF saved: {pdf_path}")
                print("   📊 Analysis completed! Source files cleaned up.")
            else:
                print("   PDF conversion failed! Check API Key")
        else:
            print("   No data to generate report")
        print("   Advanced Analysis completed!")

    main_v4()

# -----------------------------------------------------------------------------
# File Management
# -----------------------------------------------------------------------------

def cleanup_after_analysis(spot_file: Optional[Path], futures_file: Optional[Path]) -> int:
    """Removes source files (CSV/PDF) after successful analysis to keep the temp dir clean."""
    files_cleaned = 0
    now_date = datetime.datetime.now().date()
    
    for file_path, file_type in [(spot_file, "spot"), (futures_file, "futures PDF")]:
        if file_path and file_path.exists():
            try:
                file_time = datetime.datetime.fromtimestamp(file_path.stat().st_mtime)
                if file_time.date() == now_date:
                    file_path.unlink()
                    print(f"   🗑️  Cleaned up {file_type} file: {file_path.name}")
                    files_cleaned += 1
            except Exception as e:
                print(f"   ⚠️  Could not remove {file_type} file: {e}")
    
    if files_cleaned > 0:
        print(f"   ✅ Cleaned up {files_cleaned} source files")
    return files_cleaned

# -----------------------------------------------------------------------------
# Web UI Logging Adapter
# -----------------------------------------------------------------------------

class LogCatcher:
    """
    Redirects stdout. Detects which user triggered the log based on 
    the current thread name (which we will set to the User ID).
    """
    def __init__(self, original_stream):
        self.terminal = original_stream

    def write(self, msg):
        self.terminal.write(msg) # Keep server logs visible
        if msg and msg.strip():
            # Identify user by thread name (set in run_background_task)
            thread_name = threading.current_thread().name
            
            # Only capture logs for worker threads named "user_..."
            if thread_name.startswith("user_"):
                uid = thread_name.replace("user_", "")
                
                with LOCK:
                    if uid not in USER_LOGS:
                        USER_LOGS[uid] = []
                    
                    USER_LOGS[uid].append(msg)
                    if len(USER_LOGS[uid]) > 500:
                        USER_LOGS[uid].pop(0)
                
                # Update progress bars based on keywords
                text = msg.lower()
                if "scanning coingecko" in text:
                    update_progress(uid, 10, "Fetching CoinGecko Data...", "active")
                elif "scanning livecoinwatch" in text:
                    update_progress(uid, 30, "Fetching LiveCoinWatch...", "active")
                elif "parsing spot file" in text:
                    update_progress(uid, 50, "Analyzing Spot Volumes...", "active")
                elif "parsing futures pdf" in text:
                    update_progress(uid, 70, "Parsing Futures PDF...", "active")
                elif "converting to pdf" in text:
                    update_progress(uid, 90, "Compiling Report...", "active")
                elif "completed" in text or "pdf saved" in text:
                    update_progress(uid, 100, "Task Completed Successfully", "success")
                elif "error" in text:
                    update_progress(uid, 0, "Error Occurred", "error")

    def flush(self):
        self.terminal.flush()

sys.stdout = LogCatcher(sys.stdout)

# -----------------------------------------------------------------------------
# HTML Templates
# -----------------------------------------------------------------------------

COMMON_HEAD = """
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Crypto VAT v4.0 - Cloud Edition</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root { 
            --bg-dark: #0b0e11; 
            --bg-card: #151a1e; 
            --text-main: #eaecef; 
            --text-dim: #848e9c; 
            --accent-green: #0ecb81; 
            --accent-blue: #3b82f6; 
            --accent-orange: #f59e0b; 
            --accent-red: #f6465d; 
            --accent-purple: #9333ea;
            --border: #2b3139; 
            --input-bg: #1e252a; 
        }
        body { margin:0; background:var(--bg-dark); color:var(--text-main); font-family:'Inter',sans-serif; }
        .container { max-width:600px; margin:0 auto; padding:20px; }
        .card { background:var(--bg-card); padding:25px; border-radius:12px; border:1px solid var(--border); margin-bottom:20px; }
        h1 { color:var(--accent-green); text-align:center; font-size:1.4rem; margin-bottom:10px; }
        h2 { color:var(--accent-blue); font-size:1.1rem; margin-top:20px; }
        input[type="text"], input[type="email"], input[type="password"] { 
            width:100%; padding:12px; background:var(--input-bg); 
            border:1px solid var(--border); color:#fff; border-radius:8px; 
            font-family:monospace; margin-top:5px; box-sizing:border-box; 
        }
        .btn { display:block; width:100%; padding:15px; border:none; border-radius:8px; font-weight:800; cursor:pointer; text-align:center; text-decoration:none; margin-top:10px; font-size:0.95rem; }
        .btn:hover { transform: translateY(-2px); }
        .btn-green { background:var(--accent-green); color:#000; }
        .btn-green:hover { box-shadow: 0 4px 12px rgba(14,203,129,0.3); opacity: 0.9; }
        .btn-blue { background:var(--bg-card); width: 90%; border:1px solid var(--accent-blue); color:var(--accent-blue); }
        .btn-blue:hover { background: rgba(59,130,246,0.1); box-shadow: 0 4px 12px rgba(59,130,246,0.2); }
        .btn-links{ background:var(--bg-card); width: 80%; border:1px solid var(--accent-blue); color:var(--accent-blue); }
        .btn-links:hover { background: rgba(59,130,246,0.1); }
        .btn-red { background:rgba(246,70,93,0.1); width:95%; border:1px solid var(--accent-red); color:var(--accent-red); }
        .btn-red:hover { background:rgba(246,70,93,0.2); box-shadow: 0 4px 12px rgba(246,70,93,0.2); }
        .link { color:var(--accent-blue); text-decoration:none; font-size:0.85rem; float:right; }
        .link:hover { text-decoration: underline; }
        .back-link { display:block; text-align:center; margin-top:20px; color:var(--text-dim); text-decoration:none; }
        .back-link:hover { color: var(--accent-blue); }
        .grid-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .error-message { color:var(--accent-red); padding:10px; background:rgba(246,70,93,0.1); border-radius:8px; margin:10px 0; }
        .success-message { color:var(--accent-green); padding:10px; background:rgba(14,203,129,0.1); border-radius:8px; margin:10px 0; }
    </style>
</head>
"""

AUTH_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<body>
    <div class="container">
        <h1>{{% if mode == 'login' %}}🔐 Login to Your Account{{% elif mode == 'register' %}}📝 Create New Account{{% else %}}🔄 Reset Password{{% endif %}}</h1>
       <div class="card">
       <p> {{% if mode == 'login' %}}Hey bro, login to your account to perform crypto institutional-grade cross-market analysis daily for free.{{% elif mode == 'register' %}} Create account to be part of this crypto journey where we use cross-market data to our advantage. It is fast, easy, and free.{{% else %}}You forgot your password right? Enter your email address below. Password reset link will be sent. Kindly check your spam box.{{% endif %}}
       </div>
        <div class="card">
            
            {{% if error %}}
            <div class="error-message">⚠️ {{{{ error }}}}</div>
            {{% endif %}}
            
            {{% if success %}}
            <div class="success-message">✅ {{{{ success }}}}</div>
            {{% endif %}}
            
            <form method="POST">
                <label>Email Address</label>
                <input type="email" name="email" required placeholder="email@example.com" autocomplete="email">
                
                {{% if mode != 'reset' %}}
                <label style="margin-top:15px; display:block;">Password</label>
                <input type="password" name="password" required minlength="6" autocomplete="{{% if mode == 'login' %}}current-password{{% else %}}new-password{{% endif %}}">
                {{% endif %}}
                
                <button type="submit" class="btn btn-green">
                    {{% if mode == 'login' %}}LOGIN TO DASHBOARD{{% elif mode == 'register' %}}CREATE ACCOUNT{{% else %}}SEND RESET LINK{{% endif %}}
                </button>
            </form>
        </div>
        
        <div style="text-align:center; margin-top:20px; display:flex; justify-content:center; gap:15px;">
            {{% if mode == 'login' %}}
            <a href="/register" class="link" style="float:none;">Create Account</a>
            <a href="/reset-password" class="link" style="float:none;">Forgot Password?</a>
            {{% elif mode == 'register' %}}
            <a href="/login" class="link" style="float:none;">Already have an account?</a>
            {{% else %}}
            <a href="/login" class="link" style="float:none;">Back to Login</a>
            {{% endif %}}
        </div>
    </div>
</body></html>"""

SETUP_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<style>
 input::placeholder {{ color: #64748b; opacity: 1; font-style: italic; }}
 </style>
<body>
    <div class="container">
        {{% with messages = get_flashed_messages(with_categories=true) %}}
          {{% if messages %}}
            {{% for category, message in messages %}}
              <div class="{{{{ category }}}}-message">✅ {{{{ message }}}}</div>
            {{% endfor %}}
          {{% endif %}}
        {{% endwith %}}

        {{% if success %}}
          <div class="success-message">✅ {{{{ success }}}}</div>
        {{% endif %}}

        <h1>⚙️ Setup Wizard</h1>
        <p style="text-align:center; color:#848e9c; font-size:0.9rem;">
            Crypto Volume Analysis Toolkit v4.0<br>Cloud Edition By @heisbuba
        </p>
        
        <div class="card">
            <p><strong>Welcome to Crypto VAT v4.0</strong> Configure your API keys to unlock the full potential of the toolkit.</p>
            <p><small>Note: All values are encrypted and stored securely in your cloud account. You can update them anytime.</small></p>
            <a href="https://github.com/heisbuba/crypto-volume-analysis-toolkit" class="link" target="_blank">GitHub Documentation ↗</a>
        </div>

        <form action="/save-config" method="POST">
            <div class="card">
                <h2>1. API Keys Setup</h2>
                
                <label>CoinMarketCap Key <a href="https://pro.coinmarketcap.com/signup/" target="_blank" class="link">Get Key ↗</a></label>
                <input type="text" name="cmc_key" value="{{{{ cmc }}}}" placeholder="Paste CMC Key here...">

                <label style="margin-top:15px; display:block;">LiveCoinWatch Key <a href="https://www.livecoinwatch.com/tools/api" target=\"_blank\" class=\"link\">Get Key ↗</a></label>
                <input type="text" name="lcw_key" value="{{{{ lcw }}}}" placeholder="Paste LCW Key here...">

                <label style="margin-top:15px; display:block;">CoinRanking Key <a href="https://coinranking.com/api" target="_blank" class="link">Get Key ↗</a></label>
                <input type="text" name="cr_key" value="{{{{ cr }}}}" placeholder="Paste CR Key here...">
            </div>

            <div class="card">
                <h2>2. CoinAlyze Setup</h2>
                <label>VTMR URL <a href="https://coinalyze.net" target="_blank" class="link">Go to CoinAlyze ↗</a></label>
                <input type="text" name="vtmr_url" value="{{{{ vtmr }}}}" placeholder="https://coinalyze.net/?columns=...">
                <div style="font-size:0.9rem; color:#848e9c; margin-top:9px; margin-bottom:3px; background:rgba(59,130,246,0.1); padding:10px; border-radius:8px;">
                    <strong>📋 Instructions:</strong><br>
                    1. Visit <a href="https://github.com/heisbuba/crypto-volume-analysis-toolkit" target="_blank" style="color:#3b82f6;">GitHub</a> and copy VTMR code<br>
                    2. Go to <a href="https://coinalyze.net" target="_blank" style="color:#3b82f6;">CoinAlyze.net</a> and Signup/Login<br>
                    3. Create <strong>Custom Metrics</strong> → Name: <strong>VTMR</strong>, paste the code<br>
                    4. Select these Columns: Mrkt Cap, Vol 24H, Open Interest Change % 24H, Predicted Funding Rate Average OI Weighted, VTMR<br>
                    5. Sort by VTMR (highest first)<br>
                    6. Copy the <strong>entire browser URL</strong> and paste above
                </div>
            </div>

            <div class="grid-buttons" style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-top: 20px;">
                <button type="submit" class="btn btn-green" style="margin: 0; width: 100%; height: 50px; padding: 0; display: flex; align-items: center; justify-content: center; font-weight: 800;">
                    SAVE & LAUNCH
                </button>

                <a href="/logout" class="btn btn-red" style="margin: 0; width: 100%; height: 50px; padding: 0; box-sizing: border-box; text-decoration: none; display: flex; align-items: center; justify-content: center; font-weight: 800;">
                    QUIT
                </a>
            </div>
            </div>
        </form> </div>
</body></html>"""

SETTINGS_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<style>
    .modal-overlay {{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.85); backdrop-filter:blur(5px); z-index:1000; justify-content:center; align-items:center; }}
    .modal-box {{ background:#151a1e; border:1px solid #f6465d; border-radius:16px; padding:25px 20px; max-width:300px; width:85%; text-align:center; box-shadow:0 20px 40px rgba(0,0,0,0.6); animation:popIn 0.3s cubic-bezier(0.18,0.89,0.32,1.28); }}
    @keyframes popIn {{ from {{ transform:scale(0.8); opacity:0; }} to {{ transform:scale(1); opacity:1; }} }}
    .modal-icon {{ font-size:3rem; margin-bottom:10px; display:block; filter:drop-shadow(0 0 10px rgba(245,158,11,0.3)); }}
    .modal-title {{ color:#f6465d; font-size:1.1rem; font-weight:800; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.5px; }}
    .modal-text {{ color:#9ca3af; font-size:0.9rem; line-height:1.5; margin-bottom:20px; }}
    .modal-actions {{ display:grid; grid-template-columns:1fr; gap:10px; }}
    .btn-cancel {{ background:transparent; border:1px solid #2b3139; color:#6b7280; cursor:pointer; padding:12px; border-radius:8px; font-weight:600; font-size:0.9rem; transition:all 0.2s; }}
    .btn-cancel:hover {{ background:#2b3139; color:#fff; }}
    .btn-delete {{ background:rgba(246,70,93,0.1); border:1px solid #f6465d; color:#f6465d; display:flex; align-items:center; justify-content:center; text-decoration:none; padding:12px; border-radius:8px; font-weight:700; font-size:0.9rem; transition:all 0.2s; }}
    .btn-delete:hover {{ background:#f6465d; color:#fff; box-shadow:0 4px 15px rgba(246,70,93,0.4); }}
    </style>
<body>
    <div class="container">
     <!-- Success Message -->
        {{% with messages = get_flashed_messages(with_categories=true) %}}
          {{% if messages %}}
            {{% for category, message in messages %}}
              <div class="{{{{ category }}}}-message">✅ {{{{ message }}}}</div>
            {{% endfor %}}
          {{% endif %}}
        {{% endwith %}}

        {{% if success %}}
          <div class="success-message">✅ {{{{ success }}}}</div>
        {{% endif %}}
        <h1>🛠️ Settings</h1>
        
        <div class="card">
            <p style="text-align:center; color:#848e9c; font-size:0.9rem;">
                Manage your API configuration and account settings
            </p>
        </div>

        <form action="/save-config" method="POST">
            <input type="hidden" name="source" value="settings">
            <div class="card">
                <h2>API Configuration</h2>
                <p style="color:#848e9c; font-size:0.9rem; margin-bottom:15px;">Update your API keys anytime</p>
                
                <label>CoinMarketCap Key</label>
                <input type="text" name="cmc_key" value="{{{{ cmc }}}}" required>
                
                <label style="margin-top:10px; display:block;">LiveCoinWatch Key</label>
                <input type="text" name="lcw_key" value="{{{{ lcw }}}}" required>
                
                <label style="margin-top:10px; display:block;">CoinRanking Key</label>
                <input type="text" name="cr_key" value="{{{{ cr }}}}" required>
                
                <label style="margin-top:10px; display:block;">VTMR URL</label>
                <input type="text" name="vtmr_url" value="{{{{ vtmr }}}}" required>
                
                <button type="submit" class="btn btn-green">UPDATE SETTINGS</button>
            </div>
        </form>

        <div class="card" style="border-color: var(--accent-red); background: rgba(246,70,93,0.05);">
            <h2 style="color:var(--accent-red); margin-top:0;">⚠️ Danger Zone</h2>
            <p style="font-size:0.85rem; color:#848e9c;">This will delete all your saved API keys and VTMR URL, returning you to the Setup Wizard.</p>
            <button type="button" class="btn btn-red" onclick="openModal()">FACTORY RESET</button>
        </div>

        <a href="/" style="color: #3b82f6; text-decoration:none;" class="back-link">← Back to Dashboard</a>
    </div>

    <div id="resetModal" class="modal-overlay">
        <div class="modal-box">
            <span class="modal-icon">⚠️</span>
            <div class="modal-title">Are you absolutely sure?</div>
            <div class="modal-text">
                This action cannot be undone. All your saved API keys and configurations will be permanently deleted.
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" onclick="closeModal()">CANCEL</button>
                <a href="/factory-reset" class="btn-delete" style="margin-top:0; display:flex; align-items:center; justify-content:center; text-decoration:none;">YES, DELETE IT</a>
            </div>
        </div>
    </div>

    <script>
        // Modal helper
        function openModal() {{
            document.getElementById('resetModal').style.display = 'flex';   
        }}
        
        function closeModal() {{
            document.getElementById('resetModal').style.display = 'none';
        }}  
        
        // Close if clicked outside the box
        document.getElementById('resetModal').addEventListener('click', function(e) {{
            if (e.target === this) closeModal();
        }});
    </script>
</body></html>"""

HELP_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<body>
    <div class="container">
        <h1>📚 Help & Information</h1>
        
        <div class="card">
            <h2>About Crypto VAT v4.0</h2>
            <div style="font-size:0.9rem; line-height:1.6; color:#848e9c;">
                <p><strong>Crypto Volume Analysis Toolkit v4.0</strong> is a powerful web-based toolkit that helps you track high-volume crypto tokens in the last 24 hours and run cross-market analysis using spot + futures data. Generate professional PDF reports directly in your browser.</p>
                <p>This cloud edition runs on Hugging Face Spaces with secure Firebase authentication, ensuring your API keys are protected and accessible from anywhere.</p>
            </div>
        </div>

        <div class="card">
            <h2>Getting Started Guide</h2>
            <div style="font-size:0.9rem; line-height:1.6; color:#848e9c;">
                <ol style="padding-left:20px;">
                    <li><strong>Register/Login:</strong> Create your secure account</li>
                    <li><strong>Setup Wizard:</strong> Configure your API keys (one-time setup)</li>
                    <li><strong>Spot Scan:</strong> Find high-volume tokens across multiple exchanges</li>
                    <li><strong>Get Futures Data:</strong> Export data from CoinAlyze and upload PDF</li>
                    <li><strong>Advanced Analysis:</strong> Combine spot & futures data for insights</li>
                    <li><strong>Reports:</strong> View and download your analysis reports</li>
                </ol>
            </div>
        </div>

        <div class="card">
            <h2>Links & Resources</h2>
            <a href="https://github.com/heisbuba/crypto-volume-analysis-toolkit" class="btn btn-blue" target="_blank">GitHub Repository</a>
            <a href="https://medium.com/@bubanotes" class="btn btn-blue" target="_blank">Medium Blog</a>
            <div style="margin-top:15px; display:grid; grid-template-columns:1fr 1fr; gap:10px;">
                <a href="https://x.com/heisbuba" class="btn btn-links" style="margin:0; padding:12px; font-size:0.85rem;" target="_blank">𝕏 Twitter</a>
                <a href="https://facebook.com/heisbuba" class="btn btn-links" style="margin:0; padding:12px; font-size:0.85rem;" target="_blank">Facebook</a>
                <a href="https://youtube.com/@heisbuba25" class="btn btn-links" style="margin:0; padding:12px; font-size:0.85rem;" target="_blank">YouTube</a>
                <a href="https://t.me/heisbuba" class="btn btn-links" style="margin:0; padding:12px; font-size:0.85rem;" target="_blank">Telegram</a>
            </div>
        </div>
       
        <a href="/" style="color: #3b82f6; text-decoration:none;" class="back-link">← Back to Dashboard</a>
    </div>
</body></html>"""

ADMIN_TEMPLATE = f"""<!DOCTYPE html><html>
{COMMON_HEAD}
<style>
    body {{ background: var(--bg-dark); }}
    .admin-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; }}
    .admin-title {{ color: var(--accent-green); margin: 0; font-size: 1.3rem; }}
    .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 25px; }}
    .stat-card {{ background: var(--bg-card); padding: 20px; border-radius: 12px; border: 1px solid var(--border); transition: transform 0.2s; }}
    .stat-card:hover {{ border-color: var(--accent-blue); transform: translateY(-2px); }}
    .stat-label {{ color: var(--text-dim); font-size: 0.75rem; text-transform: uppercase; font-weight: 700; letter-spacing: 1px; }}
    .stat-value {{ font-size: 1.8rem; font-weight: 800; color: var(--text-main); margin-top: 8px; font-family: 'JetBrains Mono', monospace; }}
    .table-card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
    .table-header {{ padding: 15px 20px; background: rgba(59, 130, 246, 0.05); border-bottom: 1px solid var(--border); color: var(--accent-blue); font-weight: 700; font-size: 0.9rem; display: flex; align-items: center; gap: 8px; }}
    .table-wrapper {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 600px; }}
    th {{ background: rgba(0, 0, 0, 0.2); color: var(--text-dim); font-size: 0.75rem; text-transform: uppercase; padding: 12px 20px; text-align: left; border-bottom: 1px solid var(--border); }}
    td {{ padding: 14px 20px; border-bottom: 1px solid var(--border); color: var(--text-main); font-size: 0.85rem; }}
    .status-badge {{ padding: 4px 10px; border-radius: 6px; font-size: 0.7rem; font-weight: 800; text-transform: uppercase; }}
    .status-active {{ background: rgba(14, 203, 129, 0.1); color: var(--accent-green); border: 1px solid rgba(14, 203, 129, 0.2); }}
    .status-idle {{ background: rgba(132, 142, 156, 0.1); color: var(--text-dim); border: 1px solid var(--border); }}
    .progress-track {{ width: 80px; height: 6px; background: var(--input-bg); border-radius: 3px; overflow: hidden; }}
    .progress-fill {{ height: 100%; background: var(--accent-blue); transition: width 0.3s; }}
</style>
<body>
    <div class="container" style="max-width: 900px;">
        <div class="admin-header">
            <h1 class="admin-title">👮 Admin Panel</h1>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Users</div>
                <div class="stat-value">{{{{ user_count }}}}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Scans</div>
                <div class="stat-value" style="color: var(--accent-green);">{{{{ active_tasks }}}}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">System Storage</div>
                <div class="stat-value" style="color: var(--accent-purple);">{{{{ storage_usage }}}}<small style="font-size: 0.9rem;">MB</small></div>
            </div>
        </div>

        <div class="table-card">
            <div class="table-header">⚡ LIVE ACTIVITY</div>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>User ID</th>
                            <th>Status</th>
                            <th>Progress</th>
                            <th>Current Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {{% for uid, data in progress.items() %}}
                        <tr>
                            <td style="font-family: 'JetBrains Mono'; color: var(--accent-blue);">{{{{ uid[:8] }}}}</td>
                            <td><span class="status-badge {{'status-active' if data.status == 'active' else 'status-idle'}}">{{{{ data.status }}}}</span></td>
                            <td><div class="progress-track"><div class="progress-fill" style="width: {{{{ data.percent }}}}%"></div></div></td>
                            <td style="color: var(--text-dim);">{{{{ data.text }}}}</td>
                        </tr>
                        {{% else %}}
                        <tr><td colspan="4" style="text-align:center; padding: 40px; color: var(--text-dim);">No sessions found in memory</td></tr>
                        {{% endfor %}}
                    </tbody>
                </table>
            </div>
        </div>

        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border); text-align: center;">
            <a href="/" class="btn btn-links" style="width: auto; padding: 10px 25px; display: inline-block; margin-bottom: 15px;">
                ← BACK TO DASHBOARD
            </a>
            <p style="color: var(--text-dim); font-size: 0.75rem; letter-spacing: 0.5px;">SERVER TIMESTAMP: {{{{ server_time }}}}</p>
        </div>
    </div>
</body></html>"""

HOME_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Crypto Volume Analysis Toolkit v4.0</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #0b0e11; --bg-card: #151a1e; --text-main: #eaecef; --text-dim: #848e9c; --accent-green: #0ecb81; --accent-blue: #3b82f6; --accent-purple: #9333ea; --accent-orange: #f59e0b; --border: #2b3139; --accent-red: #f6465d; }
        * { box-sizing: border-box; }
        body { margin:0; background:var(--bg-dark); color:var(--text-main); font-family:'Inter', sans-serif; display:flex; flex-direction:column; min-height:100vh; }
        .header { padding:20px; background:var(--bg-card); border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
        .header h1 { margin:0; font-size:1.1rem; color:var(--accent-green); }
        .icon-btn { color:var(--text-dim); text-decoration:none; font-size:1.2rem; padding:0 10px; }
        .logout-btn { color:var(--accent-red); text-decoration:none; font-size:1.2rem; padding:0 10px; font-weight:bold; }
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
        .progress-container { margin-top:20px; display:flex; justify-content:space-between; align-items:center; }
        .progress-bar { height:4px; background:#2b3139; border-radius:2px; margin-top:5px; overflow:hidden; }
        .progress-fill { height:100%; width:0%; background:var(--accent-green); transition:width 0.3s; }
        .footer { text-align:center; padding:20px; color:var(--text-dim); font-size:0.9rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Crypto VAT v4.0</h1>
        <div>
            <a href="/help" class="icon-btn">?</a>
            <a href="/settings" class="icon-btn">⚙️</a>
            <a href="/logout" class="logout-btn" title="Logout">➜]</a>
        </div>
    </div>
    
    <div class="container">
    {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div style="padding:15px; margin-bottom:20px; border-radius:8px; font-weight:bold; background:{{ 'rgba(14,203,129,0.15)' if category == 'success' else 'rgba(246,70,93,0.15)' }}; color:{{ '#0ecb81' if category == 'success' else '#f6465d' }}; border:1px solid {{ '#0ecb81' if category == 'success' else '#f6465d' }};">
                  {{ message }}
              </div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        <div class="grid">
            <button class="btn btn-spot" onclick="trigger('/run-spot')">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path></svg>
                SPOT SCAN
            </button>
            <a class="btn btn-futures" href="/get-futures-data">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path></svg>
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

        <div class="progress-container">
            <span style="font-size:0.8rem; color:var(--text-dim);">STATUS</span>
            <span id="percent" style="font-size:0.8rem; font-weight:bold; color:var(--accent-green);">0%</span>
        </div>
        <div class="progress-bar">
            <div id="bar" class="progress-fill"></div>
        </div>

        <div class="terminal" id="term">
            <div class="log-line">> System Ready...</div>
        </div>
    </div>

    <div class="footer">
        <p>© 2025 | Made with 💚 4rom Nigeria.</p>
        
        {% if is_admin %}
        <div style="margin-top: 15px; display: flex; justify-content: center;">
            <a href="/admin" class="btn btn-links" style="width: auto; padding: 10px 20px; font-size: 0.8rem; margin: 0;">
                ADMIN DASHBOARD
            </a>
        </div>
        {% endif %}
    </div>
    
    <script>
        let busy = false;
        let lastIdx = 0;
        
        function trigger(url) {
            if (busy) return;
            busy = true;
            
            document.getElementById('term').innerHTML = '<div class="log-line">> Starting task...</div>';
            lastIdx = 0;
            document.getElementById('bar').style.width = '5%';
            document.getElementById('percent').innerText = '5%';
            
            fetch(url).then(r => r.json()).then(() => {
                poll();
                logs();
            }).catch(() => {
                busy = false;
            });
        }
        
        function poll() {
            fetch('/progress').then(r => r.json()).then(data => {
                document.getElementById('bar').style.width = data.percent + '%';
                document.getElementById('percent').innerText = data.percent + '%';
                if (data.status === 'active') {
                    setTimeout(poll, 800);
                } else {
                    busy = false;
                }
            });
        }
        
        function logs() {
            fetch('/logs-chunk?last=' + lastIdx).then(r => r.json()).then(data => {
                if (data.logs.length) {
                    lastIdx = data.last_index;
                    data.logs.forEach(log => {
                        let div = document.createElement('div');
                        div.className = 'log-line ' + (log.includes('Error') ? 'error' : log.includes('Found') ? 'highlight' : '');
                        div.innerText = '> ' + log;
                        document.getElementById('term').appendChild(div);
                    });
                    document.getElementById('term').scrollTop = 9999;
                }
                if (busy) {
                    setTimeout(logs, 1000);
                }
            });
        }
    </script>
</body>
</html>"""

# -----------------------------------------------------------------------------
# Flask Routes & Logic
# -----------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Authentication ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        
        if FIREBASE_WEB_API_KEY:
            # Exchange password for auth token via Google Identity Toolkit
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
            resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
            if resp.status_code == 200:
                session.permanent = True
                session['user_id'] = resp.json()['localId']
                return redirect(url_for('home'))
            else:
                return render_template_string(AUTH_TEMPLATE, mode="login", error="Invalid email or password")
        else:
            return render_template_string(AUTH_TEMPLATE, mode="login", error="Authentication system not configured")
    
    return render_template_string(AUTH_TEMPLATE, mode="login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        
        if FIREBASE_WEB_API_KEY:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
            resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
            if resp.status_code == 200:
                session['user_id'] = resp.json()['localId']
                flash("Registration Successful! Welcome to the Toolkit.", "success")
                return redirect(url_for('setup'))
            else:
                return render_template_string(AUTH_TEMPLATE, mode="register", error="Registration failed")
        else:
            return render_template_string(AUTH_TEMPLATE, mode="register", error="Registration system not configured")
    
    return render_template_string(AUTH_TEMPLATE, mode="register")

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email")
        if FIREBASE_WEB_API_KEY:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}"
            resp = requests.post(url, json={"requestType": "PASSWORD_RESET", "email": email})
            if resp.status_code == 200:
                return render_template_string(AUTH_TEMPLATE, mode="reset", success="Password reset email sent!")
            else:
                return render_template_string(AUTH_TEMPLATE, mode="reset", error="Error sending reset email")
        else:
            return render_template_string(AUTH_TEMPLATE, mode="reset", error="Password reset not configured")
    
    return render_template_string(AUTH_TEMPLATE, mode="reset")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- Application Views ---

@app.route("/")
@login_required
def home():
    uid = session['user_id']
    if not is_user_setup_complete(uid):
        return redirect(url_for('setup'))
    
    # Check if current user is the Admin defined in Secrets
    admin_id = os.environ.get('ADMIN_UID', '')
    is_admin = uid == admin_id or uid in admin_id.split(',')

    return render_template_string(HOME_TEMPLATE, is_admin=is_admin)

@app.route("/admin")
@login_required
def admin_dashboard():
    # Fetch Firestore Stats (Total Users)
    try:
        # Note: In a massive app, .stream() is slow, but for <10k users it's fine.
        all_users = db.collection('users').stream()
        user_count = len(list(all_users))
    except Exception:
        user_count = "DB Error"

    # Calculate Real-time Stats from Memory
    active_tasks = sum(1 for p in USER_PROGRESS.values() if p.get('status') == 'success')

    # Calculate Storage Usage (Disk space used by TEMP_DIR)
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(TEMP_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
    storage_mb = round(total_size / (1024 * 1024), 2)

    return render_template_string(ADMIN_TEMPLATE, 
        user_count=user_count,
        active_tasks=active_tasks,
        storage_usage=storage_mb,
        progress=USER_PROGRESS,
        server_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

@app.route("/setup") # Remove POST here
@login_required
def setup():
    uid = session['user_id']
    current_keys = get_user_keys(uid)
    return render_template_string(SETUP_TEMPLATE,
        cmc=current_keys.get("CMC_API_KEY", ""),
        lcw=current_keys.get("LIVECOINWATCH_API_KEY", ""),
        cr=current_keys.get("COINRANKINGS_API_KEY", ""),
        vtmr=current_keys.get("COINALYZE_VTMR_URL", "")
    )

@app.route("/settings")
@login_required
def settings(success=None):
    uid = session['user_id']
    current_keys = get_user_keys(uid)
    
    return render_template_string(SETTINGS_TEMPLATE,
        cmc=current_keys.get("CMC_API_KEY", ""),
        lcw=current_keys.get("LIVECOINWATCH_API_KEY", ""),
        cr=current_keys.get("COINRANKINGS_API_KEY", ""),
        vtmr=current_keys.get("COINALYZE_VTMR_URL", ""),
        success=success
    )

@app.route("/help")
@login_required
def help_page():
    return render_template_string(HELP_TEMPLATE)

@app.route("/save-config", methods=["POST"])
@login_required
def save_config():
    uid = session['user_id']
    source = request.form.get("source", "setup")
    
    keys = {
        "CMC_API_KEY": request.form.get("cmc_key", "").strip(),
        "LIVECOINWATCH_API_KEY": request.form.get("lcw_key", "").strip(),
        "COINRANKINGS_API_KEY": request.form.get("cr_key", "").strip(),
        "COINALYZE_VTMR_URL": request.form.get("vtmr_url", "").strip()
    }
    
    if not update_user_keys(uid, keys):
        flash("System Error: Could not save configuration.", "error")
        return redirect(url_for('settings')) if source == 'settings' else redirect(url_for('setup'))

    if source == 'settings':
        flash("Configuration updated successfully!", "success")
        return redirect(url_for('settings'))

    if is_user_setup_complete(uid):
        flash("Setup Complete! Welcome to your Dashboard.", "success")
        return redirect(url_for('home'))
    else:
        flash("Progress saved! Please enter the remaining keys to continue.", "success")
        return redirect(url_for('setup'))

@app.route("/factory-reset")
@login_required
def factory_reset():
    """Resets user configuration to use default placeholders."""
    uid = session['user_id']
    update_user_keys(uid, {
        "CMC_API_KEY": "",
        "LIVECOINWATCH_API_KEY": "",
        "COINRANKINGS_API_KEY": "",
        "COINALYZE_VTMR_URL": ""
    })
    return redirect(url_for('setup'))

@app.route("/get-futures-data")
@login_required
def get_futures_data():
    uid = session['user_id']
    user_keys = get_user_keys(uid)
    # Escape the URL to prevent injection
    futures_url = escape(user_keys.get("COINALYZE_VTMR_URL", ""))
    
    return f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Get Futures Data</title>
        <style>
            body {{ background: #0b0e11; color: #eaecef; font-family: sans-serif; padding: 20px; text-align: center; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #151a1e; padding: 30px; border-radius: 12px; border: 1px solid #2b3139; }}
            h1 {{ color: #f59e0b; }}
            .btn {{ background: #f59e0b; color: #000; padding: 15px 25px; border-radius: 8px; text-decoration: none; font-weight: bold; display: inline-block; margin-top: 20px; }}
            .btn:hover {{ background: #d97706; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3); }}
            .btn-upload {{ background: transparent; border: 1px solid #f59e0b; color: #f59e0b; width: 100%; margin-top: 10px; }}
            .btn-upload:hover {{ background: rgba(245, 158, 11, 0.1); box-shadow: 0 0 10px rgba(245, 158, 11, 0.1); }}
            .instruction {{ text-align: left; margin-top: 20px; line-height: 1.6; color: #848e9c; }}
            input[type="file"] {{ background: #1e252a; color: #848e9c; padding: 10px; border-radius: 8px; border: 1px dashed #2b3139; width: 100%; margin-top: 15px; box-sizing: border-box; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Get Futures Data</h1>
            <div class="instruction">
                <p><strong>Instructions:</strong></p>
                <ol>
                    <li>Click the button below to open Coinalyze with your VTMR setup</li>
                    <li>Export the data as PDF (Print → Save as PDF)</li>
                    <li>Upload the PDF file using the form below</li>
                    <li>Return to dashboard and run Advanced Analysis</li>
                </ol>
            </div>
            <a href="{futures_url}" target="_blank" class="btn">OPEN COINALYZE</a>
            
            <div style="height: 1px; background: #2b3139; margin: 30px 0; position: relative;">
                <span style="position: absolute; top: -12px; left: 50%; transform: translateX(-50%); background: #151a1e; padding: 0 15px; color: #2b3139; font-weight: bold;">THEN</span>
            </div>
            
            <form action="/upload-futures" method="POST" enctype="multipart/form-data">
                <p style="text-align:left; font-size:0.9rem; color:#848e9c; margin-bottom:5px;">Upload the saved PDF:</p>
                <input type="file" name="futures_pdf" accept=".pdf" required>
                <button type="submit" class="btn btn-upload">UPLOAD & CONTINUE</button>
            </form>
            
            <a href="/" style="color: #3b82f6; text-decoration:none; display:block; margin-top: 30px;">← Back to Dashboard</a>
        </div>
    </body>
    </html>"""

@app.route("/upload-futures", methods=["POST"])
@login_required
def upload_futures():
    if 'futures_pdf' not in request.files:
        return redirect(url_for('get_futures_data'))
        
    file = request.files['futures_pdf']
    if file.filename == '':
        return redirect(url_for('get_futures_data'))
        
    if file:
        uid = session['user_id']
        # Sanitize filename to prevent path traversal and XSS
        filename = secure_filename(file.filename)
        
        # Save to user isolated directory
        save_path = get_user_temp_dir(uid) / filename
        file.save(save_path)
        
        print(f"✅ User uploaded futures file: {save_path}")
        return redirect(url_for('home'))
        
    return redirect(url_for('get_futures_data'))

# --- Job Triggers ---

@app.route("/run-spot")
@login_required
def run_spot():
    uid = session['user_id']
    # Pass uid to background task
    run_background_task(spot_volume_tracker, uid)
    return jsonify({"status": "started"})

@app.route("/run-advanced")
@login_required
def run_advanced():
    uid = session['user_id']
    # Pass uid to background task
    run_background_task(crypto_analysis_v4, uid)
    return jsonify({"status": "started"})

@app.route("/progress")
@login_required
def progress():
    uid = session['user_id']
    return jsonify(get_progress(uid))

@app.route("/logs-chunk")
@login_required
def logs_chunk():
    """Returns a slice of logs for the frontend terminal based on last received index."""
    uid = session['user_id']
    try:
        last_idx = int(request.args.get('last', 0))
    except:
        last_idx = 0
    
    with LOCK:
        logs = USER_LOGS.get(uid, [])
        current_len = len(logs)
        if last_idx > current_len:
            new_logs = logs
            current_len = len(logs)
        else:
            new_logs = [] if last_idx >= current_len else logs[last_idx:]
            
    return jsonify({"logs": new_logs, "last_index": current_len})

# --- Reporting ---

@app.route("/reports-list")
@login_required
def reports_list():
    uid = session['user_id']
    # List only files in user directory
    user_dir = get_user_temp_dir(uid)
    report_files = []
    if user_dir.exists():
        for pattern in ['*.html', '*.pdf']:
            for f in user_dir.glob(pattern):
                if f.is_file():
                    report_files.append(f.name)
    
    return f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Reports</title>
        <style>
    body {{ background: #0b0e11; color: #eaecef; font-family: 'Inter', sans-serif; padding: 20px; }}
    .container {{ max-width: 600px; margin: 0 auto; }}
    h1 {{ text-align: center; color: #9333ea; margin-bottom: 30px; }}
    .file-item {{ background: #151a1e; padding: 15px 20px; border-radius: 12px; border: 1px solid #2b3139; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; transition: all 0.2s ease; }}
    .file-item:hover {{ transform: translateX(5px); border-color: #9333ea; background: #1a2026; }}
    .filename {{ font-family: monospace; font-size: 0.9rem; color: #eaecef; }}
    .btn {{ background: #9333ea; color: #fff; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 600; transition: all 0.2s ease; }}
    .btn:hover {{ background: #7e22ce; box-shadow: 0 4px 12px rgba(147, 51, 234, 0.3); transform: translateY(-1px); }}
    .back-link {{ color: #3b82f6; text-decoration: none; display: block; text-align: center; margin-top: 30px; font-size: 0.9rem; transition: color 0.2s; }}
    .back-link:hover {{ color: #60a5fa; text-decoration: underline; }}
    .empty-state {{ text-align: center; color: #848e9c; padding: 40px; border: 1px dashed #2b3139; border-radius: 12px; }}
</style>
    </head>
    <body>
        <div class="container">
            <h1>📄 Analysis Reports</h1>
            {'<br>'.join(f'<div class="file-item"><span>{f}</span><a href="/reports/{f}" target="_blank" class="btn">OPEN</a></div>' for f in sorted(report_files, reverse=True)) if report_files else '<p style="text-align:center; color:#848e9c;">No reports found yet</p>'}
            <a href="/" style="color: #3b82f6; text-decoration:none; display:block; text-align:center; margin-top: 30px;">← Back to Dashboard</a>
        </div>
    </body>
    </html>"""

@app.route("/reports/<path:filename>")
@login_required
def serve_report(filename):
    uid = session['user_id']
    user_dir = get_user_temp_dir(uid)
    return send_from_directory(str(user_dir), filename)

# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("CRYPTO VOLUME ANALYSIS TOOLKIT - CLOUD EDITION")
    print(f"{'='*60}")
    print(f"Running on Hugging Face Spaces")
    print(f"App URL: {BASE_URL}")
    print(f"Storage: {TEMP_DIR}")
    print(f"{'='*60}")
    print("Application started successfully!\n")
    
    app.run(host="0.0.0.0", port=7860, debug=False)
