import io
import threading
import time

from flask import (
    Blueprint, jsonify, session, request, redirect, url_for,
    render_template, flash, make_response,
)
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from ..config import get_user_keys, update_user_keys, firestore, log_error
from ..services.journal_engine import JournalEngine
from .auth_route import login_required

journal_bp = Blueprint('journal', __name__)

# --- Journal Trades Cache --- 
JOURNAL_CACHE = {}
JOURNAL_CACHE_TTL = 300
JOURNAL_CACHE_LOCK = threading.Lock()

# --- Chart Snapshot Upload Limits --- #
ALLOWED_IMAGE_MIMETYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB max image upload size

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

def _get_cached_journal(uid):
    with JOURNAL_CACHE_LOCK:
        entry = JOURNAL_CACHE.get(uid)
        if entry is None or (time.time() - entry["ts"]) >= JOURNAL_CACHE_TTL:
            return None
        return entry["payload"]

def _set_cached_journal(uid, payload):
    with JOURNAL_CACHE_LOCK:
        _journal_cache_prune()
        JOURNAL_CACHE[uid] = {"ts": time.time(), "payload": payload}

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
        JOURNAL_CACHE.pop(uid, None)

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

# --- Trading Journal --- #

@journal_bp.route("/journal")
@login_required
def trading_journal():
    # Page shell only — deliberately makes zero Drive API calls.
    uid = session['user_id']
    user_keys = get_user_keys(uid)
    drive_linked = "google_refresh_token" in user_keys

    return render_template(
        "dashboard/trading_journal.html",
        drive_linked=drive_linked,
        user_settings=user_keys
    )

# --- Trading Journal Routes --- #
def _load_journal_payload(uid, user_data):
    # The single load path shared by /journal/api/trades and the login warm-up.
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return {"status": "success", "drive_linked": True, "trades": [], "needs_drive_reconnect": False}
    if not JournalEngine.has_drive_file_scope(creds):
        return {"status": "success", "drive_linked": True, "trades": [], "needs_drive_reconnect": True}

    service = JournalEngine.get_drive_service(creds)
    journal_history = JournalEngine.load_trades(
        service, uid, user_data=user_data, creds=creds
    )

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
            _journal_cache_fill(uid, user_data)
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

def _journal_cache_fill(uid, user_data):
    gen_at_start = _journal_gen(uid)
    with _journal_fill_lock(uid):
        if _get_cached_journal(uid) is not None:
            return
        payload = _load_journal_payload(uid, user_data)
        if _journal_gen(uid) != gen_at_start:
            return  # invalidated mid-flight — drop the stale payload
        _set_cached_journal(uid, payload)


@journal_bp.route("/journal/api/trades")
@login_required
def journal_get_trades():
    # Async endpoint called after the page shell paints
    uid = session['user_id']
    user_data = get_user_keys(uid)  
    drive_linked = "google_refresh_token" in user_data

    if not drive_linked:
        return jsonify({"status": "success", "drive_linked": False, "trades": [], "needs_drive_reconnect": False})

    cached = _get_cached_journal(uid)
    if cached is not None:
        return jsonify(cached)

    try:
        _journal_cache_fill(uid, user_data)
        return jsonify(_get_cached_journal(uid))
    except Exception as e:
        print(f"⚠️ Journal Trades Fetch Error: {e}")
        return jsonify({"status": "error", "message": "Could not load journal from Drive.", "trades": []}), 500


@journal_bp.route("/journal/save", methods=["POST"])
@login_required
def save_journal_trade():
    # Persistent storage of trade logs to Google Drive
    uid = session['user_id']
    trade_entry = request.get_json(silent=True)
    if not trade_entry:
        return jsonify({"status": "error", "message": "Invalid or empty trade payload."}), 400

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive not linked"}), 401
    if not JournalEngine.has_drive_file_scope(creds):
        return jsonify({"status": "error", "message": "Reconnect Google Drive to sync trades.", "needs_drive_reconnect": True}), 403

    try:
        service = JournalEngine.get_drive_service(creds)

        if trade_entry.get('id'):
            existing = JournalEngine.get_trade_by_id(service, uid, trade_entry['id'], user_data=user_data)
            if existing:
                for slot in JournalEngine.IMAGE_SLOTS:
                    key = f"{slot}_image_id"
                    if key not in trade_entry:
                        trade_entry[key] = existing.get(key)

        JournalEngine.save_trade(service, uid, trade_entry, user_data=user_data)
        _invalidate_journal_cache(uid)

        return jsonify({
            "status": "success",
            "message": "Trade synced",
            "trade": trade_entry
        })
    except Exception as e:
        print(f"❌ Journal Save Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@journal_bp.route("/journal/delete/<trade_id>", methods=["POST"])
@login_required
def delete_journal_trade(trade_id):
    uid = session['user_id']

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive session expired. Please reconnect."}), 401
    if not JournalEngine.has_drive_file_scope(creds):
        return jsonify({"status": "error", "message": "Reconnect Google Drive to manage trades.", "needs_drive_reconnect": True}), 403

    try:
        service = JournalEngine.get_drive_service(creds)
        success = JournalEngine.delete_trade(service, uid, str(trade_id), user_data=user_data)

        if success:
            _invalidate_journal_cache(uid)
            return jsonify({"status": "success", "message": "Trade Log deleted successfully"})
        else:
            return jsonify({"status": "error", "message": "Trade not found in your journal file"}), 404

    except Exception as e:
        print(f"❌ Deletion Error: {str(e)}")
        return jsonify({"status": "error", "message": "Internal Server Error during deletion"}), 500


@journal_bp.route("/journal/stats")
@login_required
def get_journal_stats():
    uid = session['user_id']
    user_keys = get_user_keys(uid)  
    creds = JournalEngine.get_creds(uid, user_data=user_keys)
    if not creds:
        return jsonify({})
    try:
        if not JournalEngine.has_drive_file_scope(creds):
            return jsonify({})

        # no duplicate Drive fetch
        _journal_cache_fill(uid, user_keys)
        cached = _get_cached_journal(uid)
        if cached is None:
            return jsonify({})
        return jsonify(JournalEngine.calculate_stats(cached["trades"]))
    except Exception as e:
        print(f"⚠️ Journal Stats Error: {e}")
        return jsonify({})


# --- Chart Snapshots (before/after trade images) ---

@journal_bp.route("/journal/upload-image", methods=["POST"])
@login_required
def upload_journal_image():
    uid = session['user_id']
    trade_id = request.form.get('id')
    slot = request.form.get('slot')

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

        trade = JournalEngine.get_trade_by_id(service, uid, trade_id, user_data=user_data)
        if not trade:
            return jsonify({"status": "error", "message": "Trade not found."}), 404

        old_image_id = trade.get(f"{slot}_image_id")
        image_id = JournalEngine.upload_chart_image(
            service, uid, trade, slot, file.stream, user_data=user_data
        )
        if old_image_id and old_image_id != image_id:
            _image_cache_invalidate(old_image_id)
        trade[f"{slot}_image_id"] = image_id
        JournalEngine.save_trade(service, uid, trade, user_data=user_data)
        _invalidate_journal_cache(uid)

        return jsonify({
            "status": "success",
            "image_id": image_id,
            "url": url_for('journal.get_journal_image', file_id=image_id),
            "trade": trade
        })
    except Exception as e:
        print(f"❌ Chart Image Upload Error: {e}")
        return jsonify({"status": "error", "message": "Upload failed. Please try again."}), 500


@journal_bp.route("/journal/delete-image", methods=["POST"])
@login_required
def delete_journal_image():
    uid = session['user_id']
    data = request.get_json() or {}
    trade_id = data.get('id')
    slot = data.get('slot')

    if slot not in JournalEngine.IMAGE_SLOTS or not trade_id:
        return jsonify({"status": "error", "message": "Invalid request."}), 400

    user_data = get_user_keys(uid) 
    creds = JournalEngine.get_creds(uid, user_data=user_data)
    if not creds:
        return jsonify({"status": "error", "message": "Google Drive not linked"}), 401

    try:
        service = JournalEngine.get_drive_service(creds)

        trade = JournalEngine.get_trade_by_id(service, uid, trade_id, user_data=user_data)
        if not trade:
            return jsonify({"status": "error", "message": "Trade not found."}), 404

        image_id = trade.get(f"{slot}_image_id")
        if image_id:
            JournalEngine.delete_chart_image(service, image_id)
            _image_cache_invalidate(image_id)
            trade[f"{slot}_image_id"] = None
            JournalEngine.save_trade(service, uid, trade, user_data=user_data)
            _invalidate_journal_cache(uid)

        return jsonify({"status": "success", "trade": trade})
    except Exception as e:
        print(f"❌ Chart Image Delete Error: {e}")
        return jsonify({"status": "error", "message": "Delete failed."}), 500


@journal_bp.route("/journal/image/<file_id>")
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

@journal_bp.route("/auth/google/login")
@login_required
def google_login():
    session.pop('oauth_purpose', None)
    flow = JournalEngine.get_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )
    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    return redirect(authorization_url)


@journal_bp.route("/auth/google/callback")
@login_required
def google_callback():
    if session.pop('oauth_purpose', None) == 'chart_browser':
        from .chart_browser_route import handle_google_callback
        return handle_google_callback()

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


@journal_bp.route("/auth/google/disconnect", methods=["POST"])
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
