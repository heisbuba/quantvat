#!/usr/bin/env python3

#---------------------------------
# Libraries  Setup Guide
#---------------------------------
"""
Install these 4 libraries at Pip section of your app before integrating API keys.

  1. Requests
  2. Pandas
  3. Beautifulsoup4
  4. pypdf
  
----------------------------------------------------

#-------------------------------
# CoinAlyze VTRM Setup
#-------------------------------

You can find the detail in the github repo 
or at traderabba's paragraph blog.

Github: https://github.com/traderabbaa/crypto-volume-analysis-toolkit

Paragraph: https://paragraph.com/@traderabba/crypto-volume-analysis-toolkit

--------------------------------------

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

# -------------------------
# Configuration / API KEYS
# -------------------------

# Get api key from https:// html2pdf.app and replace it with YOUR_API_KEY_HERE

HTML2PDF_API_KEY = os.getenv("HTML2PDF_API_KEY", "YOUR_API_KEY_HERE")

# Get api key from https://pro.coinmarketcap.com/signup and replace it with YOUR_CMC_API_KEY_HERE

CMC_API_KEY = os.getenv("CMC_API_KEY", "YOUR_CMC_API_KEY_HERE")

# Get api key from https://www.livecoinwatch.com/tools/api and replace it with YOUR_LWC_API_KEY_HERE

LIVECOINWATCH_API_KEY = os.getenv("LIVECOINWATCH_API_KEY", "YOUR_LWC_API_KEY_HERE")

# Get api key from https://coinranking.com/api and replace it with YOUR_CR_API_KEY_HERE

COINRANKINGS_API_KEY = os.getenv("COINRANKINGS_API_KEY", "YOUR_CR_API_KEY_HERE")

# -------------------------
# Global constants
# -------------------------
STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'BSC-USD', 'USD1', 'CBBTC', 'WETH',
    'UST', 'TUSD', 'USDP', 'USDD', 'FRAX', 'GUSD', 'LUSD', 'FDUSD'
}

# Where to save reports (Android & fallback)
DEFAULT_SAVE_PATHS = [
    Path("/sdcard/Download"),
    Path("/storage/emulated/0/Download"),
    Path("/storage/emulated/0/Downloads"),
    Path.cwd()
]

# requests Session with retry
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

# -------------------------
# Utilities
# -------------------------
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

def safe_get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

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

# -------------------------
# Terminal functions
# -------------------------
def display_welcome() -> None:
    print("\n" + "="*50)
    print("   24HR CRYPTO VOLUME ANALYSIS TOOLKIT v1.0")
    print("   " + "*"*12 + " By @TraderAbba " + "*"*12)
    print("="*50)

def learn_more() -> None:
    print("\n" + "="*50)
    print("   LEARN MORE")
    print("="*50)
    print("   📚 Documentation & Source Code:")
    print("   🔗 GitHub: github.com/traderabba/crypto-volume-analysis/toolkit")
    print("   🔗 Paragraph Guide: https://paragraph.com/@traderabba/crypto-volume-analysis-toolkit")
    print()
    print("   🛠️ Features:")
    print("   • Spot Volume Tracker - Find high-volume tokens in the last 24 hours")
    print("   • Volume Analysis Toolkit v1.0 - Cross-market analysis")
    print("   • PDF Report Generation - Professional reports")
    print()
    print("   📊 Metrics:")
    print("   • Volume-to-Market-Cap Ratio (VTMR)")
    print("   • Cross-market token analysis")
    print("   • Large-cap token filtering")
    print("   • Multi-source data verification")
    print()
    print("   💡 Tips:")
    print("   • Tokens with VTMR > 2x are considered high-volume")
    print("   • Large-cap tokens (>$1B) have different thresholds")
    print("   • Multiple API sources ensure data accuracy")
    print("="*50)
    input("   Press Enter to return to menu...")

def spot_next_tool_menu() -> str:
    while True:
        print("\n" + "="*50)
        print("   DO YOU WANT TO RUN NEXT TOOL?")
        print("="*50)
        print("   (Y) For Advanced Crypto Volume Analysis v1.0")
        print("   (M) Return to Main Menu")
        print("   (L) Learn More")
        print("   (X) Exit")
        print("-" * 50)
        choice = input("   Enter Your Choice (Y / M / L / X): ").strip().upper()
        if choice == 'Y':
            return 'B'
        if choice == 'M':
            return 'M'
        if choice == 'L':
            learn_more()
            continue
        if choice == 'X':
            return 'X'
        print("   ❌ Invalid choice! Please enter Y, M, L, or X.")

# -------------------------
# Spot Volume Crypto Tracker v2.0
# -------------------------
def spot_volume_tracker() -> None:
   
    print("   📊 Starting fresh spot analysis...")

    def create_html_report(hot_tokens: List[Dict[str, Any]], save_path: Path) -> Path:
        date_prefix = datetime.datetime.now().strftime("%b-%d-%y")
        html_file = save_path / f"Volumed_Spot_Tokens_{date_prefix}.html"
        current_time = now_str("%d-%m-%Y %H:%M:%S")

        max_flip = max((t.get('flipping_multiple', 0) for t in hot_tokens), default=0)
        high_volume = len([t for t in hot_tokens if t.get('flipping_multiple', 0) >= 2])
        large_cap_count = len([t for t in hot_tokens if t.get('large_cap')])

        # HTML Code
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
                <p>Generated by Spot Volume Crypto Tracker v2.0 | By (@traderabba)</p>
            </div>
        </body>
        </html>
        """
        try:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)
        except Exception as e:
            # fallback to cwd
            html_file = Path.cwd() / html_file.name
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)
        return html_file

    # ---- API fetcher implementations----
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
        if not CMC_API_KEY:
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
        if not LIVECOINWATCH_API_KEY:
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
        if not COINRANKINGS_API_KEY:
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

    # Parallel fetch orchestration
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
        # given list with at least one dict, return volume, marketcap, ratio
        if not token_list:
            return 0.0, 0.0, 0.0
        try:
            v = float(token_list[0].get('volume', 0))
            m = float(token_list[0].get('marketcap', 0))
            r = v / m if m else 0.0
            return v, m, r
        except Exception:
            return 0.0, 0.0, 0.0

    # Run fetchers
    raw_tokens, _ = fetch_all_sources()

    # Combine by symbol
    all_data: Dict[str, List[Dict[str, Any]]] = {}
    for t in raw_tokens:
        sym = (t.get('symbol') or '').upper()
        if not sym: continue
        all_data.setdefault(sym, []).append(t)

    verified_tokens: List[Dict[str, Any]] = []
    for sym, tokens in all_data.items():
        # allow single large-cap verification
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

    print("   💡 Spot data saved. Run Advanced Analysis (Option B) to use this data.")
    print("   Spot Volume Tracker completed!")

# -------------------------
# HTML to PDF conversion
# -------------------------
def convert_html_to_pdf(html_content: str, output_dir: Path) -> Optional[Path]:
    """
    Convert HTML content to PDF via html2pdf.app API.
    """
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
        elif response.status_code == 401:
            print("   Invalid API Key")
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

# -------------------------
# Advanced Crypto Analysis Toolkit v1.0
# -------------------------
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
        """
    ORIGINAL_MATCHED_HEADERS = ["Ticker", "Spot Market Cap", "Spot Volume", "Spot Flip", "Futures Volume", "Futures VTMR"]
    ORIGINAL_FUTURES_HEADERS = ["Token", "Market Cap", "Volume", "VTMR"]
    ORIGINAL_SPOT_HEADERS = ["Ticker", "Market Cap", "Volume", "Spot VTMR"]

    DOWNLOAD_DIRS = [
        Path("/storage/emulated/0/Download"),
        Path("/storage/emulated/0/Downloads"),
        Path.cwd()
    ]

    @dataclass
    class TokenData:
        ticker: str
        name: str
        market_cap: str
        volume: str
        vtmr: float

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
        FINANCIAL_PATTERN = re.compile(r'(\$?[\d,\.]+[kKmMbB]?)\s+(\$?[\d,\.]+[kKmMbB]?)\s+(\d+\.\d+)')
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
                    # Normalize numbers
                    mc = groups[0].replace('$', '').replace(',', '')
                    vol = groups[1].replace('$', '').replace(',', '')
                    vtmr = groups[2]
                    financials.append((mc, vol, vtmr))
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
                mc, vol, vtmr = financials[k]
                # Strings for mc/vol
                tokens.append(TokenData(
                    ticker=ticker,
                    name=name,
                    market_cap=mc,
                    volume=vol,
                    vtmr=float(vtmr)
                ))
            return tokens

        @staticmethod
        def _clean_ticker_strict(text: str) -> Optional[str]:
            if len(text) > 15:
                return None
            cleaned = re.sub(r'[^A-Z0-9]', '', text.upper())
            if 2 <= len(cleaned) <= 10:
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
                col_map = {'coin': 'ticker', 'token': 'ticker', 'symbol': 'ticker',
                           'spot_vtmr': 'spot_flip', 'market_cap': 'spot_mc', 'volume_24h': 'spot_vol'}
                df = df.rename(columns=col_map, errors='ignore')
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
            # Defensive: ensure columns exist
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
            # Merge & filtering
            merged = pd.merge(spot_df, futures_df, on='ticker', how='inner', suffixes=('_spot', '_fut'))
            try:
                if 'vtmr' in merged.columns:
                    merged = merged[merged['vtmr'] >= 0.50]
            except Exception as e:
                print(f"   Cross-market filtering error: {e}")
            if 'vtmr' in merged.columns:
                merged = merged.sort_values('vtmr', ascending=False)
            futures_only = futures_df[~futures_df['ticker'].isin(spot_df['ticker'])].copy()
            try:
                if 'vtmr' in futures_only.columns:
                    futures_only = futures_only[futures_only['vtmr'] >= 0.50]
            except Exception as e:
                print(f"   Futures filtering error: {e}")
            if 'vtmr' in futures_only.columns:
                futures_only = futures_only.sort_values('vtmr', ascending=False)
            spot_only = spot_df[~spot_df['ticker'].isin(futures_df['ticker'])].copy()
            if 'spot_flip' in spot_only.columns:
                try:
                    spot_only = spot_only.copy()
                    spot_only.loc[:, 'flip_numeric'] = spot_only['spot_flip'].astype(str).str.replace('x', '', case=False).astype(float)
                    spot_only = spot_only[spot_only['flip_numeric'] >= 0.75]
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
            merged_cols = ['ticker', 'spot_mc', 'spot_vol', 'spot_flip', 'volume', 'vtmr']
            html_content = ""
            html_content += DataProcessor._generate_table_html("Tokens in Both Futures & Spot Markets", merged, ORIGINAL_MATCHED_HEADERS, merged_cols)
            html_content += DataProcessor._generate_table_html("Remaining Futures-Only Tokens", futures_only, ORIGINAL_FUTURES_HEADERS, ['ticker', 'market_cap', 'volume', 'vtmr'])
            html_content += DataProcessor._generate_table_html("Remaining Spot-Only Tokens", spot_only, ORIGINAL_SPOT_HEADERS, ['ticker', 'spot_mc', 'spot_vol', 'spot_flip'])
            current_time = now_str("%d-%m-%Y %H:%M:%S")
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Data Analysis - Using Cryto Volume Analysis Toolkit v1.0</title>
                <meta charset="UTF-8">
                <style>{ORIGINAL_HTML_STYLE}</style>
            </head>
            <body>
                <div class="header"><h1>Crypto Futures & Spot Analysis</h1><p>Cross-market token analysis report</p><p><small>Generated on: {current_time}</small></p></div>
                {html_content}
                <div class="footer">
                    <p>Generated by Crypto Volume Analysis Toolkit v1.0 | By (@traderabba)</p>
                </div>
            </body>
            </html>
            """
            return html

    def main_v4() -> None:
        print("   ADVANCED CRYPTO VOLUME ANALYSIS v1.0")
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
            # Calculate filtered counts for summary
            merged = pd.merge(spot_df, futures_df, on='ticker', how='inner', suffixes=('_spot', '_fut'))
            futures_only = futures_df[~futures_df['ticker'].isin(spot_df['ticker'])].copy()
            spot_only = spot_df[~spot_df['ticker'].isin(futures_df['ticker'])].copy()
            if 'vtmr' in merged.columns:
                merged = merged[merged['vtmr'] >= 0.50]
            if 'vtmr' in futures_only.columns:
                futures_only = futures_only[futures_only['vtmr'] >= 0.50]
            if 'spot_flip' in spot_only.columns:
                try:
                    spot_only = spot_only.copy()
                    spot_only.loc[:, 'flip_numeric'] = spot_only['spot_flip'].astype(str).str.replace('x', '', case=False).astype(float)
                    spot_only = spot_only[spot_only['flip_numeric'] >= 0.75]
                except Exception:
                    pass
            cross_market = len(merged)
            futures_only_count = len(futures_only)
            spot_only_count = len(spot_only)
            print(f"\n   Analysis Summary:")
            print(f"   Cross-market tokens: {cross_market} (Volume ≥ 50% MC - Futures Standard)")
            print(f"   Futures-only tokens: {futures_only_count} (Volume ≥ 50% MC)")
            print(f"   Spot-only tokens: {spot_only_count} (Volume ≥ 75% MC)")
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

# -------------------------
# Cleanup helper
# -------------------------
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

# -------------------------
# Main menu
# -------------------------
def main() -> None:
    display_welcome()
    while True:
        print("   A) Spot Volume Tracker")
        print("   B) Advanced Crypto Volume Analysis v1.0")
        print("   Q) Quit")
        print("-" * 50)
        choice = input("   Enter Your Choice (A / B / Q): ").strip().upper()
        if choice == 'A':
            spot_volume_tracker()
            next_action = spot_next_tool_menu()
            if next_action == 'B':
                crypto_analysis_v4()
            elif next_action == 'X':
                print("\n   Thank you for using Crypto Volume Analysis Toolkit v1.0. Goodbye!")
                break
            display_welcome()
        elif choice == 'B':
            crypto_analysis_v4()
            display_welcome()
        elif choice == 'Q':
            print("\n   Thank you for using Crypto Volume Analysis Toolkit v1.0. Goodbye!")
            break
        else:
            print("\n   ❌ Invalid choice! Please enter A, B, or Q.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n   Toolkit interrupted by user. Goodbye!")
    except Exception as e:
        print(f"\n   An error occurred: {e}")