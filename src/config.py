import os
import json
import sys
import time
import threading
from typing import Dict

# --- Firebase Imports ---
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
    

FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_API_KEY")

# --- Database Initialization ---
db = None # Global DB object

def _patch_database_string(client, database_id):
    resolved_id = database_id or "(default)"
    try:
        client._database_string_internal = f"projects/{client.project}/databases/{resolved_id}"
    except AttributeError as e:
        raise RuntimeError(
        ) from e

def init_firebase():
    """Initialize Firebase connection using environment variables."""
    global db
    if not FIREBASE_AVAILABLE:
        raise ImportError("Firebase libraries not installed.")

    firebase_config_str = os.environ.get("FIREBASE_CONFIG")
    if not firebase_config_str:
        raise RuntimeError("FIREBASE_CONFIG environment variable is not set.")

    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(json.loads(firebase_config_str))
            firebase_admin.initialize_app(cred)

        # Optional override: set FIRESTORE_DATABASE_ID 
        database_id = os.environ.get("FIRESTORE_DATABASE_ID") or None
        db = firestore.client(database_id=database_id)
        _patch_database_string(db, database_id)
        print("✅ Firebase Connected Successfully")
        return db
    except Exception as e:
        raise RuntimeError(f"Firebase initialization failed: {e}") from e

# --- User Management Helpers ---

def _log_firestore_exception(label: str, e: Exception):
    """Log a Firestore exception in one line"""
    code = getattr(e, "code", None)
    print(f"Firestore Error ({label}): {type(e).__name__} code={code} - {e}")

# --- Per-User Settings Cache --- #
USER_SETTINGS_CACHE = {}          # fresh, TTL-bound (used for fast-path reads)
USER_SETTINGS_LAST_GOOD = {}      # sticky last-known-good snapshot: never TTL-expired
USER_SETTINGS_CACHE_TTL = 60
_USER_SETTINGS_LOCK = threading.Lock()
_GET_KEYS_RETRY_DELAY = 0.35      # one short retry absorbs single-shot blips

def _user_settings_prune():
    now = time.time()
    for cached_uid, entry in list(USER_SETTINGS_CACHE.items()):
        if now - entry["ts"] >= USER_SETTINGS_CACHE_TTL:
            USER_SETTINGS_CACHE.pop(cached_uid, None)

def invalidate_user_cache(uid):
    # Only delete_account calls this — the uid is gone for good, drop both.
    with _USER_SETTINGS_LOCK:
        USER_SETTINGS_CACHE.pop(uid, None)
        USER_SETTINGS_LAST_GOOD.pop(uid, None)

def _fetch_user_doc(uid):
    """Single Firestore round-trip. Returns (exists, data_or_None)."""
    doc = db.collection('users').document(uid).get()
    if doc.exists:
        return True, doc.to_dict()
    return False, None

def get_user_keys(uid) -> Dict:
    if not db: return {}
    with _USER_SETTINGS_LOCK:
        entry = USER_SETTINGS_CACHE.get(uid)
        if entry is not None and (time.time() - entry["ts"]) < USER_SETTINGS_CACHE_TTL:
            return dict(entry["data"])

    last_error = None
    for attempt in range(2):
        try:
            exists, data = _fetch_user_doc(uid)
            if exists:
                with _USER_SETTINGS_LOCK:
                    _user_settings_prune()
                    USER_SETTINGS_CACHE[uid] = {"ts": time.time(), "data": data}
                    USER_SETTINGS_LAST_GOOD[uid] = dict(data)
                return dict(data)
            # Doc genuinely absent (new user) — a real "unset", safe to cache
            with _USER_SETTINGS_LOCK:
                USER_SETTINGS_CACHE[uid] = {"ts": time.time(), "data": {}}
            return {}
        except Exception as e:
            last_error = e
            if attempt == 0:
                time.sleep(_GET_KEYS_RETRY_DELAY)
                continue

    _log_firestore_exception("get_user_keys", last_error)
    # Transient failure, not a reset — serve last-good rather than {} (which reads "unconfigured").
    with _USER_SETTINGS_LOCK:
        fallback = USER_SETTINGS_LAST_GOOD.get(uid)
    return dict(fallback) if fallback is not None else {}

def update_user_keys(uid, data):
    if not db: return False
    try:
        db.collection('users').document(uid).set(data, merge=True)
        with _USER_SETTINGS_LOCK:
            _user_settings_prune()
            entry = USER_SETTINGS_CACHE.get(uid)
            base = entry["data"] if entry is not None else USER_SETTINGS_LAST_GOOD.get(uid, {})
            merged = dict(base)
            for k, v in data.items():
                if v is firestore.DELETE_FIELD:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            USER_SETTINGS_CACHE[uid] = {"ts": time.time(), "data": merged}
            USER_SETTINGS_LAST_GOOD[uid] = dict(merged)
        return True
    except Exception as e:
        _log_firestore_exception("update_user_keys", e)
    return False

def is_user_setup_complete(uid, user_data=None):
    keys = user_data if user_data is not None else get_user_keys(uid)
    required = ["COINGECKO_API_KEY", "COINALYZE_VTMR_URL"]
    for k in required:
        if k not in keys or not keys[k] or "CONFIG_" in str(keys[k]):
            return False
    return True

# Admin dashboard stats
def increment_global_stat(field: str):
    """Atomically increments a global statistic in Firestore."""
    increment_global_stat_count(field, 1)


def increment_global_stat_count(field: str, n: int):
    """Atomically increment a stat by n"""
    if not db or not n: return
    try:
        ref = db.collection('stats').document('global')
        ref.set({field: firestore.Increment(n)}, merge=True)
    except Exception as e:
        print(f"⚠️ Stats Increment Error: {e}")

def get_global_stats() -> Dict:
    """Fetches global statistics from Firestore."""
    if not db: return {}
    try:
        doc = db.collection('stats').document('global').get()
        return doc.to_dict() if doc.exists else {}
    except Exception as e:
        print(f"⚠️ Stats Fetch Error: {e}")
        return {}

# --- System Health / Error Tracking ---
_ERROR_DEDUP_WINDOW = 10  # seconds
_ERROR_FINGERPRINTS = {}
_ERROR_FINGERPRINTS_LOCK = threading.Lock()
_ERROR_FINGERPRINTS_MAX = 500


import re as _re

_ERROR_WRAPPER_RE = _re.compile(
    r"^(?:\[[A-Z ]*ERROR\]|CRITICAL ERROR|Traceback \(most recent call last\):"
    r"|[A-Za-z_][\w.]*(?:Error|Exception))\s*:?\s*",
    _re.IGNORECASE
)


def _error_fingerprint(uid, message):
    text = str(message).strip()
    lines = text.splitlines()
    line = " ".join(lines[-1].split()) if lines else ""
    prev = None
    while prev != line: 
        prev = line
        line = _ERROR_WRAPPER_RE.sub("", line)
    return (uid or "", line[-100:])


def _error_seen_recently(fingerprint):
    now = time.time()
    with _ERROR_FINGERPRINTS_LOCK:
        last = _ERROR_FINGERPRINTS.get(fingerprint)
        _ERROR_FINGERPRINTS[fingerprint] = now
        if len(_ERROR_FINGERPRINTS) > _ERROR_FINGERPRINTS_MAX:
            # drop the oldest third when the table fills
            cutoff = sorted(_ERROR_FINGERPRINTS.values())[_ERROR_FINGERPRINTS_MAX // 3]
            for fp, ts in list(_ERROR_FINGERPRINTS.items()):
                if ts <= cutoff:
                    del _ERROR_FINGERPRINTS[fp]
        return last is not None and (now - last) < _ERROR_DEDUP_WINDOW


def log_error(source: str, message: str, uid: str = None):
    """Persist an exception for the admin dashboard; stdout fallback if down."""
    if not db:
        print(f"[unlogged error] {source}: {message}")
        return
    if _error_seen_recently(_error_fingerprint(uid, message)):
        return
    try:
        db.collection('error_logs').add({
            "source": source,
            "message": str(message)[:2000],
            "uid": uid,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"⚠️ Error Log Write Failed: {e}")

def get_recent_errors(limit: int = 50) -> list:
    """Fetches the most recent error log entries, newest first."""
    if not db: return []
    try:
        docs = (
            db.collection('error_logs')
            .order_by('timestamp', direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        results = []
        for d in docs:
            item = d.to_dict()
            item['id'] = d.id
            ts = item.get('timestamp')
            item['timestamp'] = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
            results.append(item)
        return results
    except Exception as e:
        print(f"⚠️ Error Log Fetch Error: {e}")
        return []

def clear_error_logs() -> bool:
    """Deletes all stored error log entries."""
    if not db: return False
    try:
        while True:
            docs = list(db.collection('error_logs').limit(500).stream())
            if not docs:
                break
            for d in docs:
                d.reference.delete()
        return True
    except Exception as e:
        print(f"⚠️ Error Log Clear Error: {e}")
        return False