import threading
import datetime
import csv
from typing import Optional
from flask import Blueprint, jsonify, session, request, render_template, Response
from markupsafe import escape
from werkzeug.utils import secure_filename

# --- Import Logic Services --- #
from ..services.spot_engine import spot_volume_tracker
from ..services.adv_analysis import adv_analysis_v5
from ..services.futures_engine import PDFParser
from ..state import LOCK, USER_LOGS, USER_PROGRESS, update_progress, get_user_temp_dir, get_progress, set_pending_file, try_start_task, end_task, start_new_run, CURRENT_LOG_USER
from ..config import get_user_keys, update_user_keys, db, firestore, increment_global_stat, is_user_setup_complete, log_error
from .auth_route import login_required
from ..services.utils import short_num

tasks_bp = Blueprint('tasks', __name__)

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
    # Returns incremental log updates. `last` is the highest log sequence id the
    # client has seen (not a list index), so buffer trimming can't freeze it.
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
        new_logs = [e for e in logs if e.get("seq", 0) > last_idx]
        new_last = new_logs[-1]["seq"] if new_logs else last_idx

    return jsonify({"logs": new_logs, "last_index": new_last, "run_id": current_run, "stale": False})


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