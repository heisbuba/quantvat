import asyncio
import time

import requests
from flask import (
    Blueprint, jsonify, session, request, redirect, url_for, render_template,
)

from ..config import get_user_keys, is_user_setup_complete
from ..services.deep_diver_engine import calculate_deep_dive, get_mean_reversion_async
from .auth_route import login_required

deep_diver_bp = Blueprint('deep_diver', __name__)

# --- Simple DD Search Cache --- #
SEARCH_CACHE = {}
SEARCH_TTL = 3600  # 1-hour TTL for search results

#--- Deep Diver --- #
@deep_diver_bp.route("/deep-diver")
@login_required
def deep_diver():
    uid = session['user_id']
    if not is_user_setup_complete(uid):
        return redirect(url_for('main.setup'))
    return render_template("dashboard/deep_diver.html")

# --- Deep Diver's Mean Reversion -- #
@deep_diver_bp.route('/api/mean-reversion', methods=['POST'])
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
@deep_diver_bp.route("/api/search-tickers")
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

@deep_diver_bp.route("/api/dive/<coin_id>")
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
