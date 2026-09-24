import asyncio
import threading
import requests
import datetime
import time
import csv
import io
from typing import Optional
from flask import Blueprint, jsonify, session, request, redirect, url_for, render_template, flash, Response, make_response
from markupsafe import escape
from werkzeug.utils import secure_filename
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# --- Import Logic Services --- #
from ..services.spot_engine import spot_volume_tracker
from ..services.adv_analysis import adv_analysis_v5
from ..services.deepdiver_engine import calculate_deep_dive, get_mean_reversion_async
from ..services.futures_engine import PDFParser
from ..services.journal_engine import JournalEngine
from ..state import LOCK, USER_LOGS, USER_PROGRESS, update_progress, get_user_temp_dir, get_progress, set_pending_file, try_start_task, end_task, start_new_run, CURRENT_LOG_USER
from ..config import get_user_keys, update_user_keys, db, firestore, increment_global_stat, is_user_setup_complete, log_error
from .auth import login_required
from ..services.utils import short_num

# --- Simple DD Search Cache --- #
SEARCH_CACHE = {}
SEARCH_TTL = 3600  # 1-hour TTL for search results

# --- Journal Trades Cache --- 
JOURNAL_CACHE = {}
JOURNAL_CACHE_TTL = 300
JOURNAL_CACHE_LOCK = threading.Lock()

# --- Chart Snapshot Upload Limits --- #
ALLOWED_IMAGE_MIMETYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB max image upload size

def _journal_cache_key(uid, mode_pref):
    return f"{uid}:{mode_pref}"

def _journal_cache_prune():
    # Lazy eviction on write
    now = time.time()
    dead = []
    for key, entry in list(JOURNAL_CACHE.items()):
        if now - entry["ts"] >= JOURNAL_CACHE_TTL:
            JOURNAL_CACHE.pop(key, None)
            dead.append(key)
    if dead:
        with _JOURNAL_FILL_LOCKS_GUARD:
            for key in dead:
                _JOURNAL_FILL_LOCKS.pop(key, None)

def _get_cached_journal(uid, mode_pref):
    with JOURNAL_CACHE_LOCK:
        entry = JOURNAL_CACHE.get(_journal_cache_key(uid, mode_pref))
        if entry is None or (time.time() - entry["ts"]) >= JOURNAL_CACHE_TTL:
            return None
        return entry["payload"]

def _set_cached_journal(uid, mode_pref, payload):
    with JOURNAL_CACHE_LOCK:
        _journal_cache_prune()
        JOURNAL_CACHE[_journal_cache_key(uid, mode_pref)] = {"ts": time.time(), "payload": payload}

# Per-uid generation
_JOURNAL_GENERATION = {}
_JOURNAL_GENERATION_LOCK = threading.Lock()


def _journal_gen(uid):
    with _JOURNAL_GENERATION_LOCK:
        return _JOURNAL_GENERATION.get(uid, 0)


def _invalidate_journal_cache(uid):
    with _JOURNAL_GENERATION_LOCK:
        _JOURNAL_GENERATION[uid] = _JOURNAL_GENERATION.get(uid, 0) + 1
    with JOURNAL_CACHE_LOCK:
        for key in list(JOURNAL_CACHE.keys()):
            if key.startswith(f"{uid}:"):
                JOURNAL_CACHE.pop(key, None)

# --- Chart Image In-Memory Cache --- #
IMAGE_CACHE = {}
IMAGE_CACHE_MAX = 200
IMAGE_CACHE_LOCK = threading.Lock()

def _image_cache_get(file_id):
    with IMAGE_CACHE_LOCK:
        hit = IMAGE_CACHE.get(file_id)
        if hit is not None:
            # refresh recency
            IMAGE_CACHE.pop(file_id)
            IMAGE_CACHE[file_id] = hit
        return hit

def _image_cache_put(file_id, content, mime):
    with IMAGE_CACHE_LOCK:
        IMAGE_CACHE.pop(file_id, None)
        IMAGE_CACHE[file_id] = (content, mime)
        while len(IMAGE_CACHE) > IMAGE_CACHE_MAX:
            IMAGE_CACHE.pop(next(iter(IMAGE_CACHE)))

def _image_cache_invalidate(file_id):
    with IMAGE_CACHE_LOCK:
        IMAGE_CACHE.pop(file_id, None)

tasks_bp = Blueprint('tasks', __name__)

# -- Deep Diver -- #
@tasks_bp.route('/quant-diver')
@login_required
def quant_diver_page():
    return render_template('dashboard/deep_diver.html')

# --- Background Worker Helper --- #
def run_background_task(target_func, user_id) -> Optional[str]:
    # Prevents two concurrent same-user actions from racing each other's progress/manifest writes.
    if not try_start_task(user_id, target_func.__name__):
        return None

    run_id = start_new_run(user_id)

    def worker():
        try:
            threading.current_thread().name = f"user_{user_id}"
            CURRENT_LOG_USER.set(user_id)  
            user_keys = get_user_keys(user_id)
            target_func(user_keys, user_id)
            increment_global_stat("lifetime_scans")
            update_progress(user_id, 100, "Analysis Complete", "success")
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {str(e)}\n")
            log_error(source=f"engine:{target_func.__name__}", message=f"{type(e).__name__}: {e}", uid=user_id)
            update_progress(user_id, 0, str(e) or "Error Occurred", "error")
        finally:
            end_task(user_id)

    # Run task in daemon thread to prevent blocking main process
    thread = threading.Thread(target=worker, name=f"user_{user_id}")
    thread.daemon = True
    thread.start()
    return run_id

# --- Job Triggers --- #

@tasks_bp.route("/run-spot")
@login_required
def run_spot():
    uid = session['user_id']
    run_id = run_background_task(spot_volume_tracker, uid)
    if not run_id:
        return jsonify({"status": "busy", "message": "A task is already running for your account. Please wait for it to finish."})
    return jsonify({"status": "started", "run_id": run_id})


@tasks_bp.route("/run-advanced")
@login_required
def run_advanced():
    uid = session['user_id']
    run_id = run_background_task(adv_analysis_v5, uid)
    if not run_id:
        return jsonify({"status": "busy", "message": "A task is already running for your account. Please wait for it to finish."})
    return jsonify({"status": "started", "run_id": run_id})


# --- Progress & Logs API --- #

@tasks_bp.route("/progress")
@login_required
def progress():
    uid = session['user_id']
    data = get_progress(uid)
    requested_run = request.args.get('run')
    data['stale'] = bool(requested_run) and requested_run != data.get('run_id')
    return jsonify(data)


@tasks_bp.route("/logs-chunk")
@login_required
def logs_chunk():
    # Returns incremental log updates to the frontend based on last index
    uid = session['user_id']
    try:
        last_idx = int(request.args.get('last', 0))
    except (ValueError, TypeError):
        last_idx = 0
    requested_run = request.args.get('run')

    with LOCK:
        current_run = USER_PROGRESS.get(uid, {}).get('run_id')
        if requested_run and requested_run != current_run:
            return jsonify({"logs": [], "last_index": last_idx, "run_id": current_run, "stale": True})

        logs = USER_LOGS.get(uid, [])
        current_len = len(logs)
        if last_idx >= current_len:
            new_logs = []
        else:
            new_logs = logs[last_idx:]

    return jsonify({"logs": new_logs, "last_index": current_len, "run_id": current_run, "stale": False})


# --- Filters Save & Retrieve --- #
@tasks_bp.route("/save-filters", methods=["POST"])
@login_required
def save_filters():
    uid = session['user_id']
    filter_data = request.get_json(silent=True) or {}
    success = update_user_keys(uid, {"engine_settings": filter_data})
    if success:
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500


@tasks_bp.route("/reset-filters", methods=["POST"])
@login_required
def reset_filters():
    # Remove custom engine settings from user's Firestore document
    uid = session['user_id']
    if not db:
        return jsonify({"status": "error"}), 500
    if update_user_keys(uid, {"engine_settings": firestore.DELETE_FIELD}):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500

# --- Deep Diver's Mean Reversion -- #
@tasks_bp.route('/api/mean-reversion', methods=['POST'])
@login_required
def calculate_mean_reversion():
    uid = session['user_id']
    data = request.get_json() or {}
    coin_id = data.get('coin_id', '').strip().lower()

    if not coin_id:
        return jsonify({"status": "error", "message": "Coin ID is required."}), 400

    user_keys = get_user_keys(uid) or {}

    try:
        analysis = asyncio.run(get_mean_reversion_async(coin_id, user_keys))

        return jsonify({
            "status": "success",
            "line1d": analysis.get("line1d"),
            "line4h": analysis.get("line4h"),
            "line1h": analysis.get("line1h")
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
        
# --- Deep Diver Data Handling --- #
@tasks_bp.route("/api/search-tickers")
@login_required
def search_tickers():
    global SEARCH_CACHE
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify([])

    now = time.time()
    SEARCH_CACHE = {k: v for k, v in SEARCH_CACHE.items() if now < v[1] + SEARCH_TTL}
    if query in SEARCH_CACHE:
        data, timestamp = SEARCH_CACHE[query]
        return jsonify(data)

    uid = session['user_id']
    user_keys = get_user_keys(uid)
    cg_key = user_keys.get("COINGECKO_API_KEY", "")

    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if cg_key and "pro-api" not in cg_key and cg_key != "CONFIG_REQUIRED_CG":
        headers["x-cg-demo-api-key"] = cg_key

    try:
        r = requests.get(f"https://api.coingecko.com/api/v3/search?query={query}", headers=headers, timeout=5)
        r.raise_for_status()
        all_coins = r.json().get('coins', [])
        exact = [c for c in all_coins if c.get('symbol', '').lower() == query]
        rest = [c for c in all_coins if c.get('symbol', '').lower() != query]

        found_ids = {c.get('id') for c in exact}
        try:
            full_list = _get_full_coin_list(headers)
            missing_exact = [
                {"id": c["id"], "symbol": c["symbol"], "name": c["name"], "thumb": ""}
                for c in full_list
                if c.get('symbol', '').lower() == query and c.get('id') not in found_ids
            ]
            exact = exact + missing_exact
        except Exception as e:
            print(f"[COIN LIST ERROR] {e}")

        results = (exact + rest)[:8]
        SEARCH_CACHE[query] = (results, now)
        return jsonify(results)
    except Exception as e:
        print(f"[SEARCH ERROR] {e}")
        return jsonify([])

@tasks_bp.route("/api/dive/<coin_id>")
@login_required
def get_dive_data(coin_id):
    # Fetch granular coin statistics and ratios
    uid = session['user_id']
    user_keys = get_user_keys(uid)
    data = calculate_deep_dive(coin_id, user_keys)
    if data.get("status") == "error":
        return jsonify(data), 500
    # Watchlist save & retrieve
    watchlist = user_keys.get('watchlist', [])
    data['is_watched'] = any(item.get('coin_id') == coin_id for item in watchlist)
    return jsonify(data)


# --- Futures Data Handling --- #
@tasks_bp.route("/get-futures-data")
@login_required
def get_futures_data():
    uid = session['user_id']
    user_keys = get_user_keys(uid)
    futures_url = user_keys.get("COINALYZE_VTMR_URL", "")
    return render_template("dashboard/upload_futures.html", futures_url=futures_url)


@tasks_bp.route("/upload-futures", methods=["POST"])
@login_required
def upload_futures():
    # Handle PDF upload and trigger background parsing
    if not request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = next(iter(request.files.values()))

    if not file or file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Only PDF files are accepted"}), 400

    uid = session['user_id']
    if not try_start_task(uid, "upload_futures"):
        return jsonify({"error": "A task is already running for your account. Please wait for it to finish."}), 409
        
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"futures_data_{timestamp}.pdf"
        user_dir = get_user_temp_dir(uid)
        save_path = user_dir / filename
        file.save(save_path)
        run_id = start_new_run(uid)

        def parse_worker(path_to_process):
            try:
                threading.current_thread().name = f"user_{uid}"
                CURRENT_LOG_USER.set(uid) 
                update_progress(uid, 0, "File received. Extracting data tables...", "active")
                update_progress(uid, 50, "Parsing PDF tables...", "active")
                csv_path = PDFParser.extract_and_persist(path_to_process)
                if csv_path:
                    set_pending_file(uid, "futures", csv_path)
                    # Raw PDF is no longer needed once extraction succeeds
                    try:
                        path_to_process.unlink()
                    except Exception as e:
                        print(f"   Could not remove source futures PDF: {e}")
                    update_progress(uid, 100, "Futures Data Parsed & Ready.", "success")
                else:
                    update_progress(uid, 0, "PDF recognized but no table data found.", "error")
            except Exception as e:
                update_progress(uid, 0, f"Parse Error: {str(e)}", "error")
            finally:
                end_task(uid)

        thread = threading.Thread(target=parse_worker, args=(save_path,), name=f"user_{uid}")
        thread.start()

        return jsonify({"status": "success", "message": "Upload successful, parsing started.", "run_id": run_id}), 200
    except Exception as e:
        end_task(uid)
        return jsonify({"error": str(e)}), 500

# --- Trading Journal Routes --- #
def _load_journal_payload(uid, user_data, mode_pref):
    # The single load path shared by /journal/api/trades and /journal/warm.
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return {"status": "success", "drive_linked": True, "trades": [], "needs_drive_reconnect": False}
    if not JournalEngine.has_drive_file_scope(creds):
        return {"status": "success", "drive_linked": True, "trades": [], "needs_drive_reconnect": True}

    # Modes are independent Drive trees — fetch concurrently
    modes = [m for m in ("normal", "meme") if mode_pref in (m, "both")]

    def _load_one(mode):
        thread_service = JournalEngine.get_drive_service(creds)
        return JournalEngine.load_mode_journal(
            thread_service, uid, mode, user_data=user_data, creds=creds
        )

    if len(modes) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="journal_modes") as pool:
            parts = list(pool.map(_load_one, modes))
    else:
        parts = [_load_one(modes[0])]

    journal_history = []
    for part in parts:
        journal_history += part

    journal_history.sort(key=lambda t: t.get('trade_date', ''))
    journal_history.reverse()
    return {
        "status": "success",
        "drive_linked": True,
        "needs_drive_reconnect": False,
        "trades": journal_history
    }


def warm_journal_for_user(uid):
    def _worker():
        try:
            user_data = get_user_keys(uid)
            if "google_refresh_token" not in user_data:
                return  # drive not linked — nothing to warm
            mode_pref = user_data.get("journal_mode_pref", "both")
            if mode_pref not in ("normal", "meme", "both"):
                mode_pref = "both"
            _journal_cache_fill(uid, user_data, mode_pref)
        except Exception:
            pass 
    threading.Thread(target=_worker, name=f"journal_warm_{uid}", daemon=True).start()


_JOURNAL_FILL_LOCKS = {}
_JOURNAL_FILL_LOCKS_GUARD = threading.Lock()

def _journal_fill_lock(key):
    with _JOURNAL_FILL_LOCKS_GUARD:
        lock = _JOURNAL_FILL_LOCKS.get(key)
        if lock is None:
            lock = _JOURNAL_FILL_LOCKS[key] = threading.Lock()
        return lock

def _journal_cache_fill(uid, user_data, mode_pref):
    key = _journal_cache_key(uid, mode_pref)
    gen_at_start = _journal_gen(uid)
    with _journal_fill_lock(key):
        if _get_cached_journal(uid, mode_pref) is not None:
            return
        payload = _load_journal_payload(uid, user_data, mode_pref)
        if _journal_gen(uid) != gen_at_start:
            return  # invalidated mid-flight — drop the stale payload
        _set_cached_journal(uid, mode_pref, payload)


@tasks_bp.route("/journal/api/trades")
@login_required
def journal_get_trades():
    # Async endpoint called after the page shell paints
    uid = session['user_id']
    user_data = get_user_keys(uid)  
    drive_linked = "google_refresh_token" in user_data

    if not drive_linked:
        return jsonify({"status": "success", "drive_linked": False, "trades": [], "needs_drive_reconnect": False})

    mode_pref = user_data.get("journal_mode_pref", "both")
    if mode_pref not in ("normal", "meme", "both"):
        mode_pref = "both"

    cached = _get_cached_journal(uid, mode_pref)
    if cached is not None:
        return jsonify(cached)

    try:
        _journal_cache_fill(uid, user_data, mode_pref)
        return jsonify(_get_cached_journal(uid, mode_pref))
    except Exception as e:
        print(f"⚠️ Journal Trades Fetch Error: {e}")
        return jsonify({"status": "error", "message": "Could not load journal from Drive.", "trades": []}), 500


@tasks_bp.route("/journal/save", methods=["POST"])
@login_required
def save_journal_trade():
    # Persistent storage of trade logs to Google Drive
    uid = session['user_id']
    trade_entry = request.get_json(silent=True)
    if not trade_entry:
        return jsonify({"status": "error", "message": "Invalid or empty trade payload."}), 400
    mode = trade_entry.get('mode') or 'normal'
    if mode not in JournalEngine.VALID_MODES:
        mode = 'normal'

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive not linked"}), 401
    if not JournalEngine.has_drive_file_scope(creds):
        return jsonify({"status": "error", "message": "Reconnect Google Drive to sync trades.", "needs_drive_reconnect": True}), 403

    try:
        service = JournalEngine.get_drive_service(creds)

        if trade_entry.get('id'):
            existing = JournalEngine.get_trade_by_id(service, uid, mode, trade_entry['id'], user_data=user_data)
            if existing:
                for slot in JournalEngine.IMAGE_SLOTS:
                    key = f"{slot}_image_id"
                    if key not in trade_entry:
                        trade_entry[key] = existing.get(key)

        JournalEngine.save_trade_v2(service, uid, mode, trade_entry, user_data=user_data)
        _invalidate_journal_cache(uid)

        return jsonify({
            "status": "success",
            "message": "Trade synced",
            "trade": trade_entry
        })
    except Exception as e:
        print(f"❌ Journal Save Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@tasks_bp.route("/journal/delete/<trade_id>", methods=["POST"])
@login_required
def delete_journal_trade(trade_id):
    uid = session['user_id']
    mode = request.args.get('mode') or 'normal'
    if mode not in JournalEngine.VALID_MODES:
        mode = 'normal'

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive session expired. Please reconnect."}), 401
    if not JournalEngine.has_drive_file_scope(creds):
        return jsonify({"status": "error", "message": "Reconnect Google Drive to manage trades.", "needs_drive_reconnect": True}), 403

    try:
        service = JournalEngine.get_drive_service(creds)
        success = JournalEngine.delete_trade_v2(service, uid, mode, str(trade_id), user_data=user_data)

        if success:
            _invalidate_journal_cache(uid)
            return jsonify({"status": "success", "message": "Trade Log deleted successfully"})
        else:
            return jsonify({"status": "error", "message": "Trade not found in your journal file"}), 404

    except Exception as e:
        print(f"❌ Deletion Error: {str(e)}")
        return jsonify({"status": "error", "message": "Internal Server Error during deletion"}), 500


@tasks_bp.route("/journal/stats")
@login_required
def get_journal_stats():
    uid = session['user_id']
    user_keys = get_user_keys(uid)  
    creds = JournalEngine.get_creds(uid, user_data=user_keys)
    if not creds:
        return jsonify({})
    try:
        mode_pref = user_keys.get("journal_mode_pref", "both")
        if mode_pref not in ("normal", "meme", "both"):
            mode_pref = "both"

        if not JournalEngine.has_drive_file_scope(creds):
            return jsonify({})

        # no duplicate Drive fetch
        _journal_cache_fill(uid, user_keys, mode_pref)
        cached = _get_cached_journal(uid, mode_pref)
        if cached is None:
            return jsonify({})
        return jsonify(JournalEngine.calculate_stats(cached["trades"]))
    except Exception as e:
        print(f"⚠️ Journal Stats Error: {e}")
        return jsonify({})


# --- Chart Snapshots (before/after trade images) ---

@tasks_bp.route("/journal/upload-image", methods=["POST"])
@login_required
def upload_journal_image():
    uid = session['user_id']
    trade_id = request.form.get('id')
    mode = request.form.get('mode') or 'normal'
    slot = request.form.get('slot')

    if mode not in JournalEngine.VALID_MODES:
        mode = 'normal'
    if slot not in JournalEngine.IMAGE_SLOTS:
        return jsonify({"status": "error", "message": "Invalid image slot."}), 400
    if not trade_id:
        return jsonify({"status": "error", "message": "Save the trade before attaching snapshots."}), 400

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400
    if file.mimetype not in ALLOWED_IMAGE_MIMETYPES:
        return jsonify({"status": "error", "message": "Only PNG, JPEG, or WEBP images are supported."}), 400

    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > MAX_IMAGE_UPLOAD_BYTES:
        return jsonify({"status": "error", "message": "Image too large (10MB max)."}), 400

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive not linked"}), 401
    if not JournalEngine.has_drive_file_scope(creds):
        return jsonify({"status": "error", "message": "Reconnect Google Drive to enable chart snapshots."}), 403

    try:
        service = JournalEngine.get_drive_service(creds)

        trade = JournalEngine.get_trade_by_id(service, uid, mode, trade_id, user_data=user_data)
        if not trade:
            return jsonify({"status": "error", "message": "Trade not found."}), 404

        old_image_id = trade.get(f"{slot}_image_id")
        image_id = JournalEngine.upload_chart_image(
            service, uid, mode, trade, slot, file.stream, user_data=user_data
        )
        if old_image_id and old_image_id != image_id:
            _image_cache_invalidate(old_image_id)
        trade[f"{slot}_image_id"] = image_id
        JournalEngine.save_trade_v2(service, uid, mode, trade, user_data=user_data)
        _invalidate_journal_cache(uid)

        return jsonify({
            "status": "success",
            "image_id": image_id,
            "url": url_for('tasks.get_journal_image', file_id=image_id),
            "trade": trade
        })
    except Exception as e:
        print(f"❌ Chart Image Upload Error: {e}")
        return jsonify({"status": "error", "message": "Upload failed. Please try again."}), 500


@tasks_bp.route("/journal/delete-image", methods=["POST"])
@login_required
def delete_journal_image():
    uid = session['user_id']
    data = request.get_json() or {}
    trade_id = data.get('id')
    mode = data.get('mode') or 'normal'
    slot = data.get('slot')

    if mode not in JournalEngine.VALID_MODES:
        mode = 'normal'
    if slot not in JournalEngine.IMAGE_SLOTS or not trade_id:
        return jsonify({"status": "error", "message": "Invalid request."}), 400

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive not linked"}), 401

    try:
        service = JournalEngine.get_drive_service(creds)

        trade = JournalEngine.get_trade_by_id(service, uid, mode, trade_id, user_data=user_data)
        if not trade:
            return jsonify({"status": "error", "message": "Trade not found."}), 404

        image_id = trade.get(f"{slot}_image_id")
        if image_id:
            JournalEngine.delete_chart_image(service, image_id)
            _image_cache_invalidate(image_id)
            trade[f"{slot}_image_id"] = None
            JournalEngine.save_trade_v2(service, uid, mode, trade, user_data=user_data)
            _invalidate_journal_cache(uid)

        return jsonify({"status": "success", "trade": trade})
    except Exception as e:
        print(f"❌ Chart Image Delete Error: {e}")
        return jsonify({"status": "error", "message": "Delete failed."}), 500


@tasks_bp.route("/journal/image/<file_id>")
@login_required
def get_journal_image(file_id):
    uid = session['user_id']
    user_data = get_user_keys(uid)
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return "Not authorized", 401

    cached = _image_cache_get(file_id)
    if cached is not None:
        content, mime = cached
        response = make_response(content)
        response.headers['Content-Type'] = mime
        response.headers['Cache-Control'] = 'private, max-age=3600'
        return response

    try:
        service = JournalEngine.get_drive_service(creds)
        meta = service.files().get(fileId=file_id, fields='mimeType').execute()

        media_request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, media_request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        content = fh.read()
        mime = meta.get('mimeType', 'image/webp')

        _image_cache_put(file_id, content, mime)

        response = make_response(content)
        response.headers['Content-Type'] = mime
        response.headers['Cache-Control'] = 'private, max-age=3600'
        return response
    except HttpError as e:
        if e.resp.status == 404:
            return "Not found", 404
        print(f"⚠️ Chart Image Fetch Error: {e}")
        return "Error", 500
    except Exception as e:
        print(f"⚠️ Chart Image Fetch Error: {e}")
        return "Error", 500


# --- Google Auth Flow ---

@tasks_bp.route("/auth/google/login")
@login_required
def google_login():
    flow = JournalEngine.get_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )
    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    return redirect(authorization_url)


@tasks_bp.route("/auth/google/callback")
@login_required
def google_callback():
    flow = JournalEngine.get_flow()
    flow.code_verifier = session.get('code_verifier')
    authorization_response = request.url.replace('http:', 'https:')

    try:
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        uid = session['user_id']
        
        if not JournalEngine.has_drive_file_scope(creds):
            flash("Google Drive connection failed: required permission was not granted. Please try again and approve Drive access.", "error")
            return redirect(url_for('main.settings'))

        update_user_keys(uid, {
            "google_refresh_token": creds.refresh_token,
            "google_token_json": creds.to_json()
        })
        session.pop('code_verifier', None)   # cleanup

        flash("Google Drive connected successfully!", "success")
        return redirect(url_for('main.settings'))
    except Exception as e:
        print(f"❌ OAuth Callback Error: {e}")
        log_error(source="google_callback", message=f"{type(e).__name__}: {e}", uid=session.get('user_id'))
        flash(f"Login Failed: {str(e)}", "error")
        return redirect(url_for('main.settings'))


@tasks_bp.route("/auth/google/disconnect", methods=["POST"])
@login_required
def google_disconnect():
    # Remove Google Drive credentials from database
    uid = session['user_id']
    try:
        # update_user_keys 
        update_user_keys(uid, {
            "google_refresh_token": firestore.DELETE_FIELD,
            "google_token_json": firestore.DELETE_FIELD,
            "journal_drive_file_id": firestore.DELETE_FIELD
        })
        flash("Google Drive has been disconnected.", "success")
        return redirect(url_for('main.settings'))
    except Exception as e:
        print(f"Disconnect Error: {e}")
        flash(f"Disconnect Failed: {str(e)}", "error")
        return redirect(url_for('main.settings'))


# -- Watchlist Routes -- #

@tasks_bp.route('/api/watchlist/toggle', methods=['POST'])
@login_required
def toggle_watchlist():
    # Atomic toggle for the watchlist with metadata snapshots
    uid = session['user_id']
    data = request.get_json(silent=True) or {}
    coin_id = data.get('coin_id')
    symbol = data.get('symbol', '').upper()
    action = data.get('action')

    if not coin_id:
        return jsonify({"status": "error", "message": "Missing Token ID"}), 400

    try:
        user_ref = db.collection('users').document(uid)
        user_doc = user_ref.get()
        user_data = user_doc.to_dict() if user_doc.exists else {}
        watchlist = user_data.get('watchlist', [])

        if action == 'add':
            # Capture metadata snapshot to display metrics instantly on the watchlist
            entry = {
                "coin_id": coin_id,
                "symbol": symbol or "??",
                "name": data.get('name', 'Unknown'),
                "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "price": data.get('price', '--'),
                "vtmr": data.get('vtmr', '--'),
                "mcap": data.get('mcap', '--'),
                "chg_24h": data.get('chg_24h', '--'),
                "chg_7d": data.get('chg_7d', '--'),
                "chg_30d": data.get('chg_30d', '--'),
                "chg_1y": data.get('chg_1y', '--')
            }

            # metrics
            watchlist = [item for item in watchlist if item.get('coin_id') != coin_id]
            watchlist.append(entry)
            # update_user_keys 
            update_user_keys(uid, {"watchlist": watchlist})
            return jsonify({"status": "success", "is_watched": True, "message": f"{symbol} added"})

        elif action == 'remove':
            watchlist = [item for item in watchlist if item.get('coin_id') != coin_id]
            update_user_keys(uid, {"watchlist": watchlist})
            return jsonify({"status": "success", "is_watched": False, "message": f"{symbol} removed"})

    except Exception as e:
        print(f"Watchlist Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500