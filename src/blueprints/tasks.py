import threading
from flask import Blueprint, jsonify, session, request, redirect, url_for, render_template
from markupsafe import escape
from werkzeug.utils import secure_filename

# Import Logic Services
from ..services.spot_engine import spot_volume_tracker
from ..services.analysis import crypto_analysis_v4
from ..state import LOCK, USER_LOGS, USER_PROGRESS, update_progress, get_user_temp_dir, get_progress
from ..config import get_user_keys, increment_global_stat
from .auth import login_required

tasks_bp = Blueprint('tasks', __name__)

# --- Background Worker Helper ---
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
            
            # Increment Lifetime Counter
            increment_global_stat("lifetime_scans")

            update_progress(user_id, 100, "Analysis Complete", "success")
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {str(e)}\n")
            update_progress(user_id, 0, "Error Occurred", "error")
            
    thread = threading.Thread(target=worker, name=f"user_{user_id}")
    thread.daemon = True
    thread.start()

# --- Job Triggers ---

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

# --- Progress & Logs API ---

@tasks_bp.route("/progress")
@login_required
def progress():
    uid = session['user_id']
    return jsonify(get_progress(uid))

@tasks_bp.route("/logs-chunk")
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

# --- Futures Data Handling ---

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
    if 'futures_pdf' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['futures_pdf']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    if file:
        uid = session['user_id']
        filename = secure_filename(file.filename)
        save_path = get_user_temp_dir(uid) / filename
        file.save(save_path)
        print(f"✅ User uploaded futures file: {save_path}")
        return jsonify({"status": "success"}), 200
        
    return jsonify({"error": "Upload failed"}), 400