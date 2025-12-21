from flask import Blueprint, render_template_string, jsonify, request, redirect, session, url_for
from werkzeug.utils import secure_filename
import threading
import config
from engines.spot_tracker import run_spot_analysis
from engines.futures_engine import run_futures_analysis
from templates import HOME_TEMPLATE, FUTURES_INSTRUCTIONS_TEMPLATE, REPORT_LIST_TEMPLATE, HELP_TEMPLATE

core_bp = Blueprint('core', __name__)

# Helper to run backgrounds
def run_bg(func, *args):
    with config.LOCK:
        config.LIVE_LOGS.clear()
        config.PROGRESS = {"percent": 5, "text": "Starting...", "status": "active"}
    threading.Thread(target=func, args=args).start()

# Decorator
def login_required(f):
    def wrap(*args, **kwargs):
        if 'user_id' not in session: return redirect('/login')
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

@core_bp.route("/")
@login_required
def home():
    if "CONFIG_REQUIRED" in config.CMC_API_KEY: return redirect('/setup')
    return render_template_string(HOME_TEMPLATE, BASE_URL="")

@core_bp.route("/run-spot")
@login_required
def run_spot():
    uid = session['user_id']
    config.FirebaseHelper.log_activity(uid, "Run Spot")
    run_bg(run_spot_analysis, uid)
    return jsonify({"status": "started"})

@core_bp.route("/run-advanced")
@login_required
def run_advanced():
    uid = session['user_id']
    config.FirebaseHelper.log_activity(uid, "Run Advanced")
    run_bg(run_futures_analysis, uid)
    return jsonify({"status": "started"})

@core_bp.route("/get-futures-data")
@login_required
def get_futures():
    return render_template_string(FUTURES_INSTRUCTIONS_TEMPLATE, FUTURES_URL=config.COINALYZE_VTMR_URL)

@core_bp.route("/upload-futures", methods=["POST"])
@login_required
def upload():
    if 'futures_pdf' in request.files:
        f = request.files['futures_pdf']
        if f.filename.endswith('.pdf'):
            uid = session['user_id']
            filename = secure_filename(f"{uid}_futures.pdf")
            f.save(config.UPLOAD_FOLDER / filename)
            print(f"   ✅ Received PDF: {f.filename}")
            return redirect('/')
    return "Invalid File", 400

@core_bp.route("/reports-list")
@login_required
def reports():
    uid = session['user_id']
    _, bucket = config.FirebaseHelper.initialize()
    files = []
    
    # 1. Try Cloud Files
    if bucket:
        try:
            blobs = bucket.list_blobs(prefix=f"reports/{uid}/")
            files = [{"name": b.name.split('/')[-1], "url": b.public_url} for b in blobs]
        except: pass
    
    # 2. [CRITICAL] Add Local Files (The Backup Plan)
    # This ensures you see reports even without Cloud Storage
    local_reports = config.UPLOAD_FOLDER.glob(f"*{uid}*analysis*.pdf")
    for f in local_reports:
        # Avoid duplicates if cloud is active
        if not any(d['name'] == f.name for d in files):
            # Create a local link route
            files.append({"name": f.name, "url": f"/download-local/{f.name}"})
            
    return render_template_string(REPORT_LIST_TEMPLATE, files=files)

# 3. Add this NEW Route to download local files
@core_bp.route("/download-local/<path:filename>")
@login_required
def download_local(filename):
    return send_from_directory(config.UPLOAD_FOLDER, filename)
@core_bp.route("/help")
def help_page(): return render_template_string(HELP_TEMPLATE)

@core_bp.route("/progress")
def progress(): return jsonify(config.PROGRESS)

@core_bp.route("/logs-chunk")
def logs():
    idx = int(request.args.get('last', 0))
    with config.LOCK:
        logs = config.LIVE_LOGS[idx:] if idx < len(config.LIVE_LOGS) else []
        return jsonify({"logs": logs, "last_index": len(config.LIVE_LOGS)})