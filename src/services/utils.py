import os
import datetime
import requests
from pathlib import Path
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Import Global State
from ..state import get_user_temp_dir

# --- Shared Utilities ---

def create_session(retries: int = 3, backoff_factor: float = 0.5, status_forcelist=(429, 500, 502, 503, 504)) -> requests.Session:
    # Initialize requests session with exponential backoff and retry logic
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
    # Scale large integers into abbreviated strings (K, M, B)
    try:
        n = float(n)
    except Exception:
        return str(n)
    if n >= 1_000_000_000_000:
        return f"{n/1_000_000_000_000:.2f}T"
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return str(round(n))

def now_str(fmt: str = "%d-%m-%Y %H:%M:%S") -> str:
    # Return current local system time in specified format
    return datetime.datetime.now().strftime(fmt)

# --- Numeric Parsing / Formatting Helpers ---

def safe_float(val, default: float) -> float:
    """Coerce val to float; return default for None/blank/unparseable input."""
    try:
        if val is None or str(val).strip() == "":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def safe_int_clamped(val, default: int, min_val: int, max_val: int) -> int:
    """Coerce val to int; return default for None/blank/unparseable input,
    then clamp the result (default included) into [min_val, max_val]."""
    try:
        if val is None or str(val).strip() == "":
            parsed = default
        else:
            parsed = int(float(val))  # tolerate "6.0"-style strings too
    except (ValueError, TypeError):
        parsed = default
    return max(min_val, min(max_val, parsed))

_MC_SUFFIX_MULT = {'k': 1e3, 'm': 1e6, 'b': 1e9, 't': 1e12}

def parse_mc(val, default: float = 0.0) -> float:
    """Parse market-cap shorthand like '5m', '1.2b', '500k', '1t' into a raw float."""
    if val is None:
        return default
    s = str(val).strip().replace('$', '').replace(',', '')
    if s == "":
        return default
    suffix = s[-1].lower() if s[-1].lower() in _MC_SUFFIX_MULT else None
    try:
        if suffix:
            return float(s[:-1]) * _MC_SUFFIX_MULT[suffix]
        return float(s)
    except (ValueError, TypeError):
        return default

# --- File Cleanup ---

def cleanup_after_analysis(spot_file: Optional[Path], futures_file: Optional[Path], keep_spot: bool = False) -> int:
    files_cleaned = 0

    for file_path, file_type in [(spot_file, "spot"), (futures_file, "futures CSV")]:
        if keep_spot and file_type == "spot":
            continue 
        if file_path and file_path.exists():
            try:
                file_path.unlink()
                print(f"   🗑️  Cleaned up {file_type} file: {file_path.name}")
                files_cleaned += 1
            except Exception as e:
                print(f"   ⚠️  Could not remove {file_type} file: {e}")

    return files_cleaned