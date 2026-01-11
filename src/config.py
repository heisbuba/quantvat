import os
import json
import sys
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

# --- Constants ---
STABLECOINS = {
    'USDT', 'USDC', 'BUSD', 'DAI', 'BSC-USD', 'USD1', 'CBBTC', 'WBNB', 'WETH',
    'UST','SBUSDT', 'TUSD', 'USDP', 'USDD', 'FRAX', 'GUSD', 'LUSD', 'FDUSD'
}

FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_API_KEY")

# --- Database Initialization ---
db = None # Global DB object

def init_firebase():
    """Initialize Firebase connection using environment variables."""
    global db
    if not FIREBASE_AVAILABLE:
        raise ImportError("Firebase libraries not installed.")
    
    firebase_config_str = os.environ.get("FIREBASE_CONFIG")
    if not firebase_config_str:
        # In development, you might want to skip this or warn
        print("⚠️ FIREBASE_CONFIG not set")
        return None
    
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(json.loads(firebase_config_str))
            firebase_admin.initialize_app(cred)
        
        db = firestore.client()
        print("✅ Firebase Connected Successfully")
        return db
    except Exception as e:
        raise Exception(f"Firebase initialization failed: {e}")

# --- User Management Helpers ---

def get_user_keys(uid) -> Dict:
    if not db: return {}
    try:
        doc = db.collection('users').document(uid).get()
        if doc.exists:
            return doc.to_dict()
    except Exception as e:
        print(f"Firestore Error: {e}")
    return {}

def update_user_keys(uid, data):
    if not db: return False
    try:
        db.collection('users').document(uid).set(data, merge=True)
        return True
    except Exception:
        return False

def is_user_setup_complete(uid):
    keys = get_user_keys(uid)
    required = ["CMC_API_KEY", "LIVECOINWATCH_API_KEY", "COINRANKINGS_API_KEY", "COINALYZE_VTMR_URL"]
    for k in required:
        if k not in keys or not keys[k] or "CONFIG_" in str(keys[k]):
            return False
    return True

# Admin dashboard stats
def increment_global_stat(field: str):
    """Atomically increments a global statistic in Firestore."""
    if not db: return
    try:
        # 'stats' collection, 'global' document
        ref = db.collection('stats').document('global')
        # Use merge=True to create the document if it doesn't exist
        ref.set({field: firestore.Increment(1)}, merge=True)
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