import os
import datetime
from flask import Blueprint, render_template, session, redirect, url_for, request, flash, send_from_directory

from ..config import get_user_keys, update_user_keys, is_user_setup_complete, db, get_global_stats, increment_global_stat
from ..state import USER_PROGRESS, get_user_temp_dir, TEMP_DIR
from .auth import login_required

main_bp = Blueprint('main', __name__)

# --- 1. NPublic Landing Page (Root URL) ---
@main_bp.route("/")
def index():
    # Renders the public homepage.
    # Logged-in users will see "Launch Dashboard" button in index.html logic.
    return render_template("index.html")

# Dashboard route
@main_bp.route("/dashboard")
@login_required
def home():
    uid = session['user_id']
    if not is_user_setup_complete(uid):
        return redirect(url_for('main.setup'))
    
    # Check if current user is Admin
    admin_id = os.environ.get('ADMIN_UID', '')
    is_admin = uid == admin_id or uid in admin_id.split(',')

    return render_template("dashboard/home.html", is_admin=is_admin)

@main_bp.route("/setup")
@login_required
def setup():
    uid = session['user_id']
    current_keys = get_user_keys(uid)
    return render_template("dashboard/setup.html",
        cmc=current_keys.get("CMC_API_KEY", ""),
        lcw=current_keys.get("LIVECOINWATCH_API_KEY", ""),
        cr=current_keys.get("COINRANKINGS_API_KEY", ""),
        vtmr=current_keys.get("COINALYZE_VTMR_URL", "")
    )

@main_bp.route("/settings")
@login_required
def settings():
    uid = session['user_id']
    current_keys = get_user_keys(uid)
    return render_template("dashboard/settings.html",
        cmc=current_keys.get("CMC_API_KEY", ""),
        lcw=current_keys.get("LIVECOINWATCH_API_KEY", ""),
        cr=current_keys.get("COINRANKINGS_API_KEY", ""),
        vtmr=current_keys.get("COINALYZE_VTMR_URL", "")
    )

@main_bp.route("/save-config", methods=["POST"])
@login_required
def save_config():
    uid = session['user_id']
    source = request.form.get("source", "setup")
    
    keys = {
        "CMC_API_KEY": request.form.get("cmc_key", "").strip(),
        "LIVECOINWATCH_API_KEY": request.form.get("lcw_key", "").strip(),
        "COINRANKINGS_API_KEY": request.form.get("cr_key", "").strip(),
        "COINALYZE_VTMR_URL": request.form.get("vtmr_url", "").strip()
    }
    
    if not update_user_keys(uid, keys):
        flash("System Error: Could not save configuration.", "error")
        return redirect(url_for('main.settings')) if source == 'settings' else redirect(url_for('main.setup'))

    if source == 'settings':
        flash("Configuration updated successfully!", "success")
        return redirect(url_for('main.settings'))

    if is_user_setup_complete(uid):
        flash("Setup Complete! Welcome to your Dashboard.", "success")
        return redirect(url_for('main.home'))
    else:
        flash("Progress saved! Please enter the remaining keys to continue.", "success")
        return redirect(url_for('main.setup'))

@main_bp.route("/factory-reset")
@login_required
def factory_reset():
    uid = session['user_id']
    update_user_keys(uid, {
        "CMC_API_KEY": "",
        "LIVECOINWATCH_API_KEY": "",
        "COINRANKINGS_API_KEY": "",
        "COINALYZE_VTMR_URL": ""
    })
    return redirect(url_for('main.setup'))

@main_bp.route("/help")
def help_page():
    setup_status = False
    if 'user_id' in session:
        setup_status = is_user_setup_complete(session['user_id'])
    return render_template("dashboard/help.html", is_setup_complete=setup_status)

# --- Admin Section ---

@main_bp.route("/admin")
@login_required
def admin_dashboard():
    # Fetch Firestore Stats
    try:
        if db:
            all_users = db.collection('users').stream()
            user_count = len(list(all_users))
        else:
            user_count = "DB Error"
    except Exception:
        user_count = "DB Error"

    # Fetch Lifetime Scans from Firebase
    stats = get_global_stats()
    lifetime_scans = stats.get('lifetime_scans', 0)
    report_views = stats.get('report_views', 0)

    # Storage Stats
    total_size = 0
    if TEMP_DIR.exists():
        for dirpath, dirnames, filenames in os.walk(TEMP_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    storage_mb = round(total_size / (1024 * 1024), 2)

    return render_template("admin/admin.html", 
        user_count=user_count,
        active_tasks=lifetime_scans, 
        report_views=report_views,  
        storage_usage=storage_mb,
        progress=USER_PROGRESS,
        server_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

# --- Reporting Section ---

@main_bp.route("/reports-list")
@login_required
def reports_list():
    uid = session['user_id']
    user_dir = get_user_temp_dir(uid)
    report_files = []
    if user_dir.exists():
        for pattern in ['*.html', '*.pdf']:
            for f in user_dir.glob(pattern):
                if f.is_file():
                    report_files.append(f.name)
    
    return render_template("reports/list.html", report_files=sorted(report_files, reverse=True))


@main_bp.route("/reports/<path:filename>")
@login_required
def serve_report(filename):
    uid = session['user_id']
    user_dir = get_user_temp_dir(uid) 
    # Check if user explicitly clicked "Download" (dl=1)
    is_download = request.args.get('dl') == '1'
    increment_global_stat("report_views")
    mimetype = None
    if filename.lower().endswith('.pdf'):
        mimetype = 'application/pdf'
    
    return send_from_directory(
        str(user_dir), 
        filename, 
        as_attachment=is_download,
        mimetype=mimetype 
    )