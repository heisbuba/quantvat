import sys
import threading
import datetime
import requests
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path
import config

# ==============================
# LOGGING SYSTEM
# ==============================
class LogCatcher:
    def __init__(self, original_stream):
        self.terminal = original_stream

    def write(self, msg):
        self.terminal.write(msg)
        if msg and msg.strip():
            with config.LOCK:
                config.LIVE_LOGS.append(msg)
                if len(config.LIVE_LOGS) > 500:
                    config.LIVE_LOGS.pop(0)
            
            # Progress Logic
            text = msg.lower()
            if "scanning coingecko" in text:
                self._update_progress(10, "Fetching CoinGecko Data...", "active")
            elif "scanning livecoinwatch" in text:
                self._update_progress(30, "Fetching LiveCoinWatch...", "active")
            elif "parsing spot file" in text:
                self._update_progress(50, "Analyzing Spot Volumes...", "active")
            elif "parsing futures pdf" in text:
                self._update_progress(70, "Parsing Futures PDF...", "active")
            elif "converting to pdf" in text:
                self._update_progress(90, "Compiling Report...", "active")
            elif "completed" in text or "pdf saved" in text:
                self._update_progress(100, "Task Completed Successfully", "success")
            elif "error" in text:
                self._update_progress(0, "Error Occurred", "error")

    def _update_progress(self, percent, text, status):
        with config.LOCK:
            config.PROGRESS = {"percent": percent, "text": text, "status": status}

    def flush(self):
        self.terminal.flush()

def setup_logging():
    sys.stdout = LogCatcher(sys.stdout)

# ==============================
# COMMON UTILS
# ==============================
def create_session(retries=3, backoff_factor=0.5):
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def short_num(n):
    try: n = float(n)
    except: return str(n)
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
    if n >= 1_000: return f"{n/1_000:.2f}K"
    return str(round(n))

def now_str(fmt="%d-%m-%Y %H:%M:%S"):
    return datetime.datetime.now().strftime(fmt)

def load_config_from_file():
    """Loads JSON config into config.py globals"""
    config_file = config.BASE_DIR / "crypto_vat_config.json"
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            config.HTML2PDF_API_KEY = data.get("HTML2PDF_API_KEY", config.HTML2PDF_API_KEY)
            config.CMC_API_KEY = data.get("CMC_API_KEY", config.CMC_API_KEY)
            config.LIVECOINWATCH_API_KEY = data.get("LIVECOINWATCH_API_KEY", config.LIVECOINWATCH_API_KEY)
            config.COINRANKINGS_API_KEY = data.get("COINRANKINGS_API_KEY", config.COINRANKINGS_API_KEY)
            config.COINALYZE_VTMR_URL = data.get("COINALYZE_VTMR_URL", config.COINALYZE_VTMR_URL)
        except: pass

def update_config_file(updates: dict):
    """Updates config.py globals and saves to JSON"""
    # Update globals
    if "HTML2PDF_API_KEY" in updates: config.HTML2PDF_API_KEY = updates["HTML2PDF_API_KEY"]
    if "CMC_API_KEY" in updates: config.CMC_API_KEY = updates["CMC_API_KEY"]
    if "LIVECOINWATCH_API_KEY" in updates: config.LIVECOINWATCH_API_KEY = updates["LIVECOINWATCH_API_KEY"]
    if "COINRANKINGS_API_KEY" in updates: config.COINRANKINGS_API_KEY = updates["COINRANKINGS_API_KEY"]
    if "COINALYZE_VTMR_URL" in updates: config.COINALYZE_VTMR_URL = updates["COINALYZE_VTMR_URL"]

    # Save to file
    file_data = {
        "HTML2PDF_API_KEY": config.HTML2PDF_API_KEY,
        "CMC_API_KEY": config.CMC_API_KEY,
        "LIVECOINWATCH_API_KEY": config.LIVECOINWATCH_API_KEY,
        "COINRANKINGS_API_KEY": config.COINRANKINGS_API_KEY,
        "COINALYZE_VTMR_URL": config.COINALYZE_VTMR_URL
    }
    try:
        with open(config.BASE_DIR / "crypto_vat_config.json", 'w', encoding='utf-8') as f:
            json.dump(file_data, f, indent=2)
    except: pass