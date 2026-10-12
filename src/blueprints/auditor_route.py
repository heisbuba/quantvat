from flask import Blueprint, request, session, redirect, url_for, flash, jsonify

from ..config import update_user_keys
from ..services.auditor_engine import AuditorEngine
from .auth_route import login_required

auditor_bp = Blueprint('auditor', __name__)

# --- API & AI Integration --- #

@auditor_bp.route('/settings/save_ai_key', methods=['POST'])
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

@auditor_bp.route("/api/ai/init_audit", methods=["POST"])
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

@auditor_bp.route("/api/ai/chat", methods=["POST"])
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
