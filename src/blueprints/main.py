import os
import re
from datetime import datetime
from flask import Blueprint, render_template, session, redirect, url_for, request, flash, send_from_directory, make_response, jsonify
from googleapiclient.errors import HttpError
from werkzeug.utils import safe_join
from firebase_admin import auth as firebase_auth

from ..config import get_user_keys, update_user_keys, is_user_setup_complete, db, get_global_stats, increment_global_stat, increment_global_stat_count, firestore, get_recent_errors, clear_error_logs, invalidate_user_cache
from ..state import USER_PROGRESS, get_user_temp_dir, TEMP_DIR
from .auth import login_required
from ..services.journal_engine import JournalEngine
from ..services.auditor_engine import AuditorEngine

main_bp = Blueprint('main', __name__)

# --- Report View Counter --- #
import threading as _vt
_report_view_buffer = [0]
_report_view_timer = [None]
_REPORT_VIEW_LOCK = _vt.Lock()


def _flush_report_views():
    with _REPORT_VIEW_LOCK:
        n = _report_view_buffer[0]
        _report_view_buffer[0] = 0
        _report_view_timer[0] = None
    if n:
        increment_global_stat_count("report_views", n)


def record_report_view() -> None:
    with _REPORT_VIEW_LOCK:
        _report_view_buffer[0] += 1
        if _report_view_timer[0] is None:
            timer = _vt.Timer(60.0, _flush_report_views)
            timer.daemon = True
            _report_view_timer[0] = timer
            timer.start()

# --- Navigation & Dashboard --- #

@main_bp.route("/")
def index():
    if session.get('user_id') and not request.args.get('no_redirect'):
        response = redirect(url_for('main.home'))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    
    response = make_response(render_template("index.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@main_bp.route("/dashboard")
@login_required
def home():
    uid = session['user_id']
    user_data = get_user_keys(uid) or {}
    if not is_user_setup_complete(uid, user_data=user_data):
        return redirect(url_for('main.setup'))
    filters = user_data.get("engine_settings", {}) 

    admin_id = os.environ.get('ADMIN_UID', '')
    is_admin = uid == admin_id or uid in admin_id.split(',')

    return render_template("dashboard/home.html", is_admin=is_admin, filters=filters)
    

# --- Configuration & Setup --- #

@main_bp.route("/setup")
@login_required
def setup():
    uid = session['user_id']
    current_keys = get_user_keys(uid)
    return render_template("auth/setup.html",
        cg=current_keys.get("COINGECKO_API_KEY", ""),
        vtmr=current_keys.get("COINALYZE_VTMR_URL", "")
    )

@main_bp.route("/settings")
@login_required
def settings():
    uid = session['user_id']
    current_keys = get_user_keys(uid)
    drive_linked = "google_refresh_token" in current_keys
    return render_template("dashboard/settings.html",
        cg=current_keys.get("COINGECKO_API_KEY", ""),
        vtmr=current_keys.get("COINALYZE_VTMR_URL", ""),
        drive_linked=drive_linked,
        journal_mode_pref=current_keys.get("journal_mode_pref", "both"),
        user_settings=current_keys
    )

@main_bp.route("/settings/save_journal_mode", methods=["POST"])
@login_required
def save_journal_mode():
    uid = session['user_id']
    mode_pref = request.form.get("journal_mode_pref", "both")
    if mode_pref not in ("normal", "meme", "both"):
        mode_pref = "both"

    if update_user_keys(uid, {"journal_mode_pref": mode_pref}):
        flash("Journal mode preference updated!", "success")
    else:
        flash("Could not save journal mode preference.", "error")

    return redirect(url_for('main.settings'))

@main_bp.route("/save-config", methods=["POST"])
@login_required
def save_config():
    uid = session['user_id']
    source = request.form.get("source", "setup")
    is_autosave = request.form.get("autosave") == "1"
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    keys = {
        "COINGECKO_API_KEY": request.form.get("cg_key", "").strip(),
        "COINALYZE_VTMR_URL": request.form.get("vtmr_url", "").strip()
    }

    # autosave per field
    if is_autosave:
        keys = {k: v for k, v in keys.items() if v}
        if not keys:
            return jsonify({"status": "success", "message": "Nothing to save"})
    
    if not update_user_keys(uid, keys):
        if is_ajax:
            return jsonify({"status": "error", "message": "Could not save configuration."}), 500
        flash("System Error: Could not save configuration.", "error")
        return redirect(url_for('main.settings' if source == 'settings' else 'main.setup'))

    # For AJAX auto-save, just return JSON
    if is_ajax:
        return jsonify({"status": "success", "message": "Saved"})

    if source == 'settings':
        flash("Configuration updated successfully!", "success")
        return redirect(url_for('main.settings'))

    # Redirect based on setup completion status
    if is_user_setup_complete(uid):
        flash("Setup Complete! Welcome to your Dashboard.", "success")
        return redirect(url_for('main.home'))
    
    flash("Progress saved! Please enter the remaining keys to continue.", "success")
    return redirect(url_for('main.setup'))

@main_bp.route("/factory-reset", methods=["POST"])
@login_required
def factory_reset():
    uid = session['user_id']
    update_user_keys(uid, {
        "COINGECKO_API_KEY": "",
        "COINALYZE_VTMR_URL": "",
        "gemini_key": "",
        "ai_history": []
    })
    return redirect(url_for('main.setup'))

@main_bp.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    uid = session['user_id']
    try:
        firebase_auth.delete_user(uid) 
    except Exception as e:
        print(f"Delete Account Auth Error: {e}")
        flash(f"Account deletion failed: {str(e)}", "error")
        return redirect(url_for('main.settings'))

    try:
        db.collection('users').document(uid).delete() 
        invalidate_user_cache(uid)  
    except Exception as e:
        print(f"Delete Account Firestore Error: {e}")

    session.clear()
    flash("Your account has been permanently deleted.", "success")
    return redirect(url_for('main.index'))

@main_bp.route("/help")
def help_page():
    setup_status = is_user_setup_complete(session['user_id']) if 'user_id' in session else False
    return render_template("pages/help.html", is_setup_complete=setup_status)
    
@main_bp.route("/privacy-policy")
def privacy_policy():
    setup_status = is_user_setup_complete(session['user_id']) if 'user_id' in session else False
    return render_template("pages/privacy-policy.html", is_setup_complete=setup_status)

@main_bp.route("/deep-diver")
@login_required
def deep_diver():
    uid = session['user_id']
    if not is_user_setup_complete(uid):
        return redirect(url_for('main.setup'))
    return render_template("dashboard/deep_diver.html")

# --- Administration --- #

@main_bp.route("/admin")
@login_required
def admin_dashboard():
    uid = session['user_id']
    admin_id = os.environ.get('ADMIN_UID', '')
    is_admin = uid == admin_id or uid in admin_id.split(',')
    if not is_admin:
        return redirect(url_for('main.home'))
    # Query user count from Firestore
    try:
        if db:
            user_count = db.collection('users').count().get()[0][0].value
        else:
            user_count = "DB Error"
    except Exception:
        user_count = "DB Error"

    stats = get_global_stats()
    lifetime_scans = stats.get('lifetime_scans', 0)
    report_views = stats.get('report_views', 0)

    # Calculate total disk usage of temp directory
    total_size = 0
    if TEMP_DIR.exists():
        for dirpath, _, filenames in os.walk(TEMP_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    storage_mb = round(total_size / (1024 * 1024), 2)

    error_logs = get_recent_errors(limit=50)

    return render_template("admin/admin.html", 
        user_count=user_count,
        active_tasks=lifetime_scans, 
        report_views=report_views,  
        storage_usage=storage_mb,
        progress=USER_PROGRESS,
        error_logs=error_logs,
        server_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

@main_bp.route("/admin/error-logs/download")
@login_required
def download_error_logs():
    uid = session['user_id']
    admin_id = os.environ.get('ADMIN_UID', '')
    is_admin = uid == admin_id or uid in admin_id.split(',')
    if not is_admin:
        return redirect(url_for('main.home'))
    import csv
    import io as _csv_io
    from flask import Response
    buf = _csv_io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "source", "uid", "message"])
    for e in get_recent_errors(limit=1000):
        writer.writerow([e.get('timestamp'), e.get('source'), e.get('uid') or '', e.get('message')])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=error_logs.csv"}
    )


@main_bp.route("/admin/error-logs/clear", methods=["POST"])
@login_required
def clear_admin_error_logs():
    uid = session['user_id']
    admin_id = os.environ.get('ADMIN_UID', '')
    is_admin = uid == admin_id or uid in admin_id.split(',')
    if not is_admin:
        return redirect(url_for('main.home'))
    if clear_error_logs():
        flash("Error logs cleared.", "success")
    else:
        flash("Failed to clear error logs — see server console.", "error")
    return redirect(url_for('main.admin_dashboard'))

# --- Reports Management --- #

@main_bp.route("/reports-list")
@login_required
def reports_list():
    uid = session['user_id']
    user_dir = get_user_temp_dir(uid)
    report_files = []
    # Collect all generated artifacts for listing
    if user_dir.exists():
        for pattern in ['*.html', '*.csv']:
            for f in user_dir.glob(pattern):
                if f.is_file():
                    report_files.append(f.name)
    
    return render_template("reports/list.html", report_files=sorted(report_files, reverse=True))


# --- Downloaded report links --- #

def _app_base_url() -> str:
    """Public base URL of the app, used to make downloaded reports link back here."""
    explicit = os.environ.get("APP_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    space_host = os.environ.get("SPACE_HOST", "").strip()  # set automatically on Hugging Face Spaces
    if space_host:
        return f"https://{space_host}"
    return "https://heisbuba-quantvat.hf.space"


def _absolutize_report_links(html: str, base_url: str) -> str:
    """Turn in-app links (href="/deep-diver?...", href="/") into absolute URLs so they work offline."""
    return re.sub(r'href="/(?!/)', f'href="{base_url}/', html)

@main_bp.route("/reports/<path:filename>")
@login_required
def serve_report(filename):
    uid = session['user_id']
    user_dir = get_user_temp_dir(uid) 
    is_download = request.args.get('dl') == '1'
    record_report_view()  # batched: one Firestore write per minute, not per view
    
    # Downloaded HTML leaves the app, so its relative links must point back to the site
    if is_download and filename.lower().endswith('.html'):
        file_path = safe_join(str(user_dir), filename)
        if not file_path or not os.path.isfile(file_path):
            return 'Not Found', 404
        with open(file_path, encoding='utf-8') as fh:
            body = _absolutize_report_links(fh.read(), _app_base_url())
        resp = make_response(body)
        resp.headers['Content-Type'] = 'text/html; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename="{os.path.basename(filename)}"'
        return resp

    if filename.lower().endswith('.csv'):
        mimetype = 'text/csv'
    else:
        mimetype = None
    
    return send_from_directory(
        str(user_dir), 
        filename, 
        as_attachment=is_download,
        mimetype=mimetype 
    )

@main_bp.route("/reports/delete/<path:filename>", methods=["POST"])
@login_required
def delete_report(filename):
    uid = session['user_id']
    user_dir = get_user_temp_dir(uid).resolve()

    # Reject any filename that isn't a plain, single-segment name
    if not filename or filename != os.path.basename(filename) or filename in ('.', '..'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return 'Not Found', 404
        flash("File not found.", "error")
        return redirect(url_for('main.reports_list'))

    file_path = (user_dir / filename).resolve()

    # Defense in depth: even after the basename check above, then verify
    if user_dir not in file_path.parents:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return 'Not Found', 404
        flash("File not found.", "error")
        return redirect(url_for('main.reports_list'))

    if file_path.exists() and file_path.is_file():
        try:
            os.remove(file_path)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return '', 200
            flash("Report deleted successfully.", "success")
        except Exception:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return 'Error', 500
            flash("Error: Could not delete file.", "error")
    else:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return 'Not Found', 404
        flash("File not found.", "error")

    return redirect(url_for('main.reports_list'))

# --- Trading Journal --- #

@main_bp.route("/journal")
@login_required
def trading_journal():
    # Page shell only — deliberately makes zero Drive API calls.
    uid = session['user_id']
    user_keys = get_user_keys(uid)
    drive_linked = "google_refresh_token" in user_keys
    journal_mode_pref = user_keys.get("journal_mode_pref", "both")
    if journal_mode_pref not in ("normal", "meme", "both"):
        journal_mode_pref = "both"

    return render_template(
        "dashboard/trading_journal.html",
        drive_linked=drive_linked,
        journal_mode_pref=journal_mode_pref,
        user_settings=user_keys
    )

# --- Service Worker & PWA --- #

@main_bp.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@main_bp.route('/sw.js')
def serve_sw():
    # Serve Service Worker from root to permit broad caching scope
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

# --- API & AI Integration --- #

@main_bp.route('/settings/save_ai_key', methods=['POST'])
@login_required
def save_ai_key():
    # Native form submission for Gemini API key persistence
    api_key = request.form.get('api_key', '').strip()
    
    if not api_key:
        flash('API Key cannot be empty.', 'error')
        return redirect(url_for('main.settings'))
        
    try:
        uid = session.get('user_id')
        update_user_keys(uid, {'gemini_key': api_key})
        flash('Gemini API Key saved successfully!', 'success')
    except Exception as e:
        print(f"Surgery Log Error: {e}")
        flash('Failed to update AI settings.', 'error')
        
    return redirect(url_for('main.settings'))

@main_bp.route("/api/ai/init_audit", methods=["POST"])
@login_required
def init_audit():
    data = request.get_json(silent=True) or {}
    csv_context = (data.get('csv_context') or '').strip() 
    uid = session['user_id']
    
    # Validation: Ensure we aren't sending empty prompts
    if not csv_context:
        return jsonify({"status": "error", "message": "No data provided for audit."}), 400

    response_text = AuditorEngine.initialize_firebase_session(uid, csv_context)

    text = (response_text or '').strip()
    if not text or text.startswith(('Error:', 'ERROR', 'Could not', 'Failed to')):
        return jsonify({"status": "error", "message": text or "Empty response"}), 400
        
    return jsonify({"status": "success", "response": response_text})

@main_bp.route("/api/ai/chat", methods=["POST"])
@login_required
def ai_chat():
    # Handle multi-turn conversation using Firebase-stored chat history
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({"status": "error", "message": "Empty prompt."}), 400
    uid = session['user_id']
    response_text = AuditorEngine.continue_firebase_chat(uid, prompt)
    
    return jsonify({"status": "success", "response": response_text})

@main_bp.route('/sitemap.xml')
def sitemap():
    pages = []
    today = datetime.now().strftime('%Y-%m-%d')
    
    # List of only PUBLIC endpoints for indexing
    public_endpoints = [
        ('main.index', '1.0', 'daily'),
        ('auth.login', '0.8', 'monthly'),
        ('auth.register', '0.8', 'monthly'),
        ('main.help_page', '0.7', 'weekly'),
        ('main.privacy_policy', '0.7', 'weekly')
    ]

    for endpoint, priority, freq in public_endpoints:
        pages.append({
            "loc": url_for(endpoint, _external=True),
            "lastmod": today,
            "changefreq": freq,
            "priority": priority
        })

    # Render from your new SEO template
    sitemap_xml = render_template('includes/sitemap_template.xml', pages=pages)
    response = make_response(sitemap_xml)
    response.headers["Content-Type"] = "application/xml"
    return response

@main_bp.route('/robots.txt')
def robots():
    sitemap_url = url_for('main.sitemap', _external=True)
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /dashboard", 
        "Disallow: /api/",
        f"Sitemap: {sitemap_url}"
    ]
    return make_response("\n".join(lines), 200, {'Content-Type': 'text/plain'})

@main_bp.route('/googlea10aea7d2db78dd5.html')
def google_verify():
    return send_from_directory('static', 'googlea10aea7d2db78dd5.html')

@main_bp.route('/watchlist')
@login_required
def watchlist_page():
    uid = session.get('user_id')
    user_data = get_user_keys(uid)
    if not isinstance(user_data, dict):
        user_data = {}
    watchlist = user_data.get('watchlist', [])
    try:
        watchlist = sorted(watchlist, key=lambda x: x.get('added_at', ""), reverse=True)
    except Exception as e:
        print(f"Sort Error: {e}")
        watchlist = []
    return render_template('reports/watchlist.html', watchlist=watchlist)