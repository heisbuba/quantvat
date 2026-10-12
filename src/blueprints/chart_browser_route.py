import gzip
import json
import time

from flask import (
    Blueprint, render_template, request, session, redirect, url_for,
    jsonify, send_file, flash, current_app,
)
from googleapiclient.errors import HttpError

from ..config import get_user_keys, update_user_keys, is_user_setup_complete, log_error
from ..services.journal_engine import JournalEngine
from ..services.chart_browser_engine import (
    ChartBrowserEngine, NotConnected, NotFound, TooLarge,
    TOKEN_FIELD, LAST_FOLDER_FIELD, SORT_ORDERS, DEFAULT_SORT, valid_id,
)
from .auth_route import login_required

chart_browser_bp = Blueprint('chart_browser', __name__)

NAV_IDS = ('root', 'shared')

_WARM_AT = {}
WARM_MIN_INTERVAL = 120


def _json_ok(payload):
    """Compact + gzip JSON: a 1000-image listing shrinks ~8x, which matters
    on 2G/3G."""
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    resp = current_app.response_class(body, mimetype='application/json')
    if len(body) > 1024 and 'gzip' in request.headers.get('Accept-Encoding', ''):
        resp.set_data(gzip.compress(body, 5))
        resp.headers['Content-Encoding'] = 'gzip'
    resp.headers['Vary'] = 'Accept-Encoding'
    return resp


def _needs_connect():
    return jsonify({
        "status": "error",
        "needs_connect": True,
        "message": "Google Drive access expired. Please reconnect.",
    }), 403


def _drive_error(e, where):
    status = getattr(getattr(e, 'resp', None), 'status', 500)
    if status == 401:
        return _needs_connect()
    if status == 404:
        return jsonify({"status": "error", "message": "Folder not found or no longer accessible."}), 404
    log_error(source=where, message=f"{type(e).__name__}: {e}", uid=session.get('user_id'))
    return jsonify({"status": "error", "message": "Google Drive request failed. Please try again."}), 502


@chart_browser_bp.route("/chart-browser")
@login_required
def page():
    uid = session['user_id']
    if not is_user_setup_complete(uid):
        return redirect(url_for('main.setup'))
    user_data = get_user_keys(uid) or {}
    return render_template(
        "dashboard/chart_browser.html",
        connected=bool(user_data.get(TOKEN_FIELD)),
        last_folder=user_data.get(LAST_FOLDER_FIELD),
    )


@chart_browser_bp.route("/chart-browser/connect")
@login_required
def connect():
    flow = JournalEngine.get_flow(scopes=ChartBrowserEngine.SCOPES)
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )
    session['oauth_state'] = state
    session['code_verifier'] = flow.code_verifier
    session['oauth_purpose'] = 'chart_browser'
    return redirect(authorization_url)


def handle_google_callback():
    uid = session['user_id']
    expected_state = session.pop('oauth_state', None)
    code_verifier = session.pop('code_verifier', None)

    if not expected_state or request.args.get('state') != expected_state:
        flash("Google Drive connection failed: the sign-in session did not match. Please try again.", "error")
        return redirect(url_for('chart_browser.page'))

    flow = JournalEngine.get_flow(scopes=ChartBrowserEngine.SCOPES)
    flow.code_verifier = code_verifier
    authorization_response = request.url.replace('http:', 'https:')

    try:
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        if not ChartBrowserEngine.has_scope(creds):
            flash("Image access was not granted. Please try again and approve read-only Drive access.", "error")
            return redirect(url_for('chart_browser.page'))
        update_user_keys(uid, {TOKEN_FIELD: creds.to_json()})
        flash("Chart Browser connected to Google Drive.", "success")
    except Exception as e:
        print(f"❌ Chart Browser OAuth Callback Error: {e}")
        log_error(source="chart_browser_callback", message=f"{type(e).__name__}: {e}", uid=uid)
        flash(f"Connection failed: {str(e)}", "error")
    return redirect(url_for('chart_browser.page'))


@chart_browser_bp.route("/chart-browser/disconnect", methods=["POST"])
@login_required
def disconnect():
    uid = session['user_id']
    try:
        ChartBrowserEngine.disconnect(uid)
        flash("Chart Browser disconnected and temporary images cleared.", "success")
    except Exception as e:
        print(f"Chart Browser disconnect error: {e}")
        flash(f"Disconnect failed: {str(e)}", "error")
    # Connections are managed in Settings, so that is where the result is shown.
    return redirect(url_for('main.settings'))


@chart_browser_bp.route("/chart-browser/api/folders")
@login_required
def api_folders():
    uid = session['user_id']
    parent = request.args.get('parent', 'root')
    if parent not in NAV_IDS and not valid_id(parent):
        return jsonify({"status": "error", "message": "Invalid folder."}), 400

    creds = ChartBrowserEngine.get_creds(uid)
    if not creds:
        return _needs_connect()

    try:
        svc = lambda: ChartBrowserEngine.get_service(creds, uid)
        folder, folders = ChartBrowserEngine.run_parallel(
            lambda: ChartBrowserEngine.folder_info(svc(), parent, uid),
            lambda: ChartBrowserEngine.list_folders(svc(), parent),
        )
        return _json_ok({"status": "success", "folder": folder, "folders": folders})
    except NotFound:
        return jsonify({"status": "error", "message": "That item is not a folder."}), 404
    except HttpError as e:
        return _drive_error(e, "chart_browser_folders")
    except Exception as e:
        log_error(source="chart_browser_folders", message=f"{type(e).__name__}: {e}", uid=uid)
        return jsonify({"status": "error", "message": "Could not load folders."}), 500


@chart_browser_bp.route("/chart-browser/api/images")
@login_required
def api_images():
    uid = session['user_id']
    folder_id = request.args.get('folder', '')
    sort = request.args.get('sort', DEFAULT_SORT)
    if sort not in SORT_ORDERS:
        sort = DEFAULT_SORT
    if folder_id != 'root' and not valid_id(folder_id):
        return jsonify({"status": "error", "message": "Invalid folder."}), 400

    user_data = get_user_keys(uid) or {}

    # Instant path (first open): answer from the listing saved on disk, with
    # no Drive call at all. The client re-checks Drive right after.
    if request.args.get('swr') == '1' and user_data.get(TOKEN_FIELD):
        hit = ChartBrowserEngine.load_cached_listing(uid, folder_id, sort)
        if hit:
            return _json_ok({
                "status": "success",
                "folder": {"id": hit["folder"]["id"], "name": hit["folder"]["name"], "parent": hit["parent"]},
                "images": hit["images"],
                "cached": ChartBrowserEngine.cached_ids(uid, hit["images"]),
                "truncated": hit.get("truncated", False),
                "stale": True,
            })

    creds = ChartBrowserEngine.get_creds(uid, user_data)
    if not creds:
        return _needs_connect()

    try:
        svc = lambda: ChartBrowserEngine.get_service(creds, uid)
        folder, (images, truncated) = ChartBrowserEngine.run_parallel(
            lambda: ChartBrowserEngine.folder_info(svc(), folder_id, uid),
            lambda: ChartBrowserEngine.list_images(svc(), folder_id, sort),
        )

        ChartBrowserEngine.purge_stale(uid)
        ChartBrowserEngine.save_index(
            uid, {"id": folder["id"], "name": folder["name"]}, images,
            sort=sort, parent=folder["parent"], truncated=truncated)

        last = user_data.get(LAST_FOLDER_FIELD) or {}
        if last.get("id") != folder["id"] or last.get("name") != folder["name"]:
            update_user_keys(uid, {LAST_FOLDER_FIELD: {"id": folder["id"], "name": folder["name"]}})

        return _json_ok({
            "status": "success",
            "folder": {"id": folder["id"], "name": folder["name"], "parent": folder["parent"]},
            "images": images,
            "cached": ChartBrowserEngine.cached_ids(uid, images),
            "truncated": truncated,
        })
    except NotFound:
        return jsonify({"status": "error", "message": "That item is not a folder."}), 404
    except HttpError as e:
        return _drive_error(e, "chart_browser_images")
    except Exception as e:
        log_error(source="chart_browser_images", message=f"{type(e).__name__}: {e}", uid=uid)
        return jsonify({"status": "error", "message": "Could not load images."}), 500


@chart_browser_bp.route("/chart-browser/api/warm")
@login_required
def api_warm():
    """Called quietly by the dashboard. Cheap no-op unless this user has
    connected Drive and opened a folder before."""
    uid = session['user_id']
    user_data = get_user_keys(uid) or {}
    last = user_data.get(LAST_FOLDER_FIELD) or {}
    if not user_data.get(TOKEN_FIELD) or not valid_id(last.get('id', '')):
        return '', 204
    now = time.time()
    if now - _WARM_AT.get(uid, 0) < WARM_MIN_INTERVAL:
        return '', 204
    if len(_WARM_AT) > 500:
        _WARM_AT.clear()
    _WARM_AT[uid] = now
    ChartBrowserEngine.warm(uid, last['id'])
    return '', 204


@chart_browser_bp.route("/chart-browser/api/image/<file_id>")
@login_required
def api_image(file_id):
    uid = session['user_id']
    try:
        as_download = request.args.get('download') == '1'
        want_full = as_download or request.args.get('size') == 'full'
        if want_full:
            path, mime, entry = ChartBrowserEngine.get_image_file(uid, file_id)
        else:
            path, mime, entry = ChartBrowserEngine.get_preview_file(uid, file_id)
    except NotConnected:
        return "Not authorized", 401
    except NotFound:
        return "Not found", 404
    except TooLarge:
        return "Image too large to preview", 413
    except HttpError as e:
        status = getattr(getattr(e, 'resp', None), 'status', 500)
        if status == 401:
            return "Not authorized", 401
        log_error(source="chart_browser_image", message=f"{type(e).__name__}: {e}", uid=uid)
        return "Error", 502
    except Exception as e:
        log_error(source="chart_browser_image", message=f"{type(e).__name__}: {e}", uid=uid)
        return "Error", 500

    response = send_file(
        path,
        mimetype=mime,
        conditional=True,
        max_age=48 * 60 * 60,
        as_attachment=as_download,
        download_name=entry.get("name") if as_download else None,
    )
    response.headers['Cache-Control'] = 'private, max-age=172800, immutable'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@chart_browser_bp.route("/chart-browser/api/clear-cache", methods=["POST"])
@login_required
def api_clear_cache():
    uid = session['user_id']
    freed = ChartBrowserEngine.purge_cache(uid, keep_index=True)
    return jsonify({"status": "success", "freed_mb": round(freed / (1024 * 1024), 2)})