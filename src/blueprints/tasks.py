import threading
import requests
import datetime
import time
from flask import Blueprint, jsonify, session, request, redirect, url_for, render_template, flash
from markupsafe import escape
from werkzeug.utils import secure_filename

# --- Import Logic Services --- #
from ..services.spot_engine import spot_volume_tracker
from ..services.analysis import crypto_analysis_v4
from ..services.deep_diver_engine import calculate_deep_dive
from ..services.futures_engine import PDFParser
from ..services.journal_engine import JournalEngine
from ..state import LOCK, USER_LOGS, USER_PROGRESS, update_progress, get_user_temp_dir, get_progress
from ..config import get_user_keys, update_user_keys, db, firestore, increment_global_stat
from .auth import login_required

# --- Simple DD Search Cache --- #
SEARCH_CACHE = {} 
SEARCH_TTL = 3600  # 1-hour TTL for search results

tasks_bp = Blueprint('tasks', __name__)

# -- Deep Diver -- #
@tasks_bp.route('/quant-diver')
@login_required
def quant_diver_page():
    return render_template('deep_diver.html')
    
# --- Background Worker Helper --- #
def run_background_task(target_func, user_id):
    # Initialize session logs and progress before spawning thread
    with LOCK:
        USER_LOGS[user_id] = []
        USER_PROGRESS[user_id] = {"percent": 5, "text": "Initializing Engine...", "status": "active"}

    def worker():
        try:
            threading.current_thread().name = f"user_{user_id}"
            user_keys = get_user_keys(user_id)
            target_func(user_keys, user_id)
            increment_global_stat("lifetime_scans")
            update_progress(user_id, 100, "Analysis Complete", "success")
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {str(e)}\n")
            update_progress(user_id, 0, "Error Occurred", "error")
            
    # Run task in daemon thread to prevent blocking main process
    thread = threading.Thread(target=worker, name=f"user_{user_id}")
    thread.daemon = True
    thread.start()

# --- Job Triggers --- #

@tasks_bp.route("/run-spot")
@login_required
def run_spot():
    uid = session['user_id']
    run_background_task(spot_volume_tracker, uid)
    return jsonify({"status": "started"})

@tasks_bp.route("/run-advanced")
@login_required
def run_advanced():
    uid = session['user_id']
    run_background_task(crypto_analysis_v4, uid)
    return jsonify({"status": "started"})

# --- Progress & Logs API --- #

@tasks_bp.route("/progress")
@login_required
def progress():
    uid = session['user_id']
    return jsonify(get_progress(uid))

@tasks_bp.route("/logs-chunk")
@login_required
def logs_chunk():
    # Returns incremental log updates to the frontend based on last index
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


# --- Filters Save & Retrieve --- #
@tasks_bp.route("/save-filters", methods=["POST"])
@login_required
def save_filters():
    uid = session['user_id']
    filter_data = request.get_json()
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
    try:
        db.collection('users').document(uid).update({
            "engine_settings": firestore.DELETE_FIELD
        })
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Reset Error: {e}")
        return jsonify({"status": "error"}), 500

# --- Deep Diver Data Handling --- #
@tasks_bp.route("/api/search-tickers")
@login_required
def search_tickers():
    query = request.args.get('q', '').strip().lower()
    if not query: return jsonify([])

    now = time.time()
    if query in SEARCH_CACHE:
        data, timestamp = SEARCH_CACHE[query]
        if now < timestamp + SEARCH_TTL:
            return jsonify(data)

    uid = session['user_id']
    user_keys = get_user_keys(uid)
    cg_key = user_keys.get("COINGECKO_API_KEY", "")
    
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if cg_key and "pro-api" not in cg_key and cg_key != "CONFIG_REQUIRED_CG":
        headers["x-cg-demo-api-key"] = cg_key

    try:
        # Proxy search to CoinGecko and cache results to save API credits
        r = requests.get(f"https://api.coingecko.com/api/v3/search?query={query}", headers=headers, timeout=5)
        r.raise_for_status()
        results = r.json().get('coins', [])[:8]
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
    if 'futures_pdf' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['futures_pdf']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    uid = session['user_id']
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"futures_data_{timestamp}.pdf"
        user_dir = get_user_temp_dir(uid)
        save_path = user_dir / filename
        file.save(save_path)
        
        def parse_worker(path_to_process):
            try:
                update_progress(uid, 0, "File received. Extracting data tables...", "active")
                df = PDFParser.extract(path_to_process)
                if not df.empty:
                    update_progress(uid, 100, "Futures Data Parsed & Ready.", "success")
                else:
                    update_progress(uid, 0, "PDF recognized but no table data found.", "error")
            except Exception as e:
                update_progress(uid, 0, f"Parse Error: {str(e)}", "error")

        thread = threading.Thread(target=parse_worker, args=(save_path,))
        thread.start()

        return jsonify({"status": "success", "message": "Upload successful, parsing started."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Trading Journal Routes --- #
@tasks_bp.route("/journal/save", methods=["POST"])
@login_required
def save_journal_trade():
    # Persistent storage of trade logs to Google Drive AppData
    uid = session['user_id']
    trade_entry = request.get_json()
    
    creds = JournalEngine.get_creds(uid)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive not linked"}), 401
    
    try:
        service = JournalEngine.get_drive_service(creds)
        file_id = JournalEngine.initialize_journal(service) 
        JournalEngine.save_trade(service, file_id, trade_entry)
        
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
    # Remove specific trade entry from Drive file by ID
    uid = session['user_id']
    creds = JournalEngine.get_creds(uid)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive session expired. Please reconnect."}), 401
    
    try:
        service = JournalEngine.get_drive_service(creds)
        file_id = JournalEngine.initialize_journal(service)
        success = JournalEngine.delete_trade(service, file_id, str(trade_id))
        
        if success:
            return jsonify({"status": "success", "message": "Trade Log deleted successfully"})
        else:
            return jsonify({"status": "error", "message": "Trade not found in your journal file"}), 404
            
    except Exception as e:
        print(f"❌ Deletion Error: {str(e)}")
        return jsonify({"status": "error", "message": "Internal Server Error during deletion"}), 500
        
@tasks_bp.route("/journal/stats")
@login_required
def get_journal_stats():
    # Return winrate, best ticker, and dominant bias metrics
    uid = session['user_id']
    creds = JournalEngine.get_creds(uid)
    if not creds: return jsonify({})
    try:
        service = JournalEngine.get_drive_service(creds)
        file_id = JournalEngine.initialize_journal(service)
        journal = JournalEngine.load_journal(service, file_id)
        return jsonify(JournalEngine.calculate_stats(journal))
    except:
        return jsonify({})

# ---------------------------------------------------------
# GOOGLE AUTH FLOW
# ---------------------------------------------------------

@tasks_bp.route("/auth/google/login")
@login_required
def google_login():
    # Redirect to Google's consent screen for Drive access
    flow = JournalEngine.get_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline', 
        include_granted_scopes='true',
        prompt='consent'
    )
    session['oauth_state'] = state
    return redirect(authorization_url)

@tasks_bp.route("/auth/google/callback")
@login_required
def google_callback():
    # Finalize OAuth exchange and store persistent refresh token
    flow = JournalEngine.get_flow()
    authorization_response = request.url.replace('http:', 'https:')
    
    try:
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        uid = session['user_id']
        update_user_keys(uid, {
            "google_refresh_token": creds.refresh_token,
            "google_token_json": creds.to_json()
        })
        flash("Google Drive connected successfully!", "success")
        return redirect(url_for('main.settings'))
    except Exception as e:
        print(f"❌ OAuth Callback Error: {e}")
        flash(f"Login Failed: {str(e)}", "error")
        return redirect(url_for('main.settings'))

@tasks_bp.route("/auth/google/disconnect", methods=["POST"])
@login_required
def google_disconnect():
    # Remove Google Drive credentials from database
    uid = session['user_id']
    try:
        db.collection('users').document(uid).update({
            "google_refresh_token": firestore.DELETE_FIELD,
            "google_token_json": firestore.DELETE_FIELD
        })
        flash("Google Drive has been disconnected.", "success")
        return redirect(url_for('main.settings'))
    except Exception as e:
        print(f"Disconnect Error: {e}")
        flash(f"Disconnect Failed: {str(e)}", "error")
        return redirect(url_for('main.settings'))