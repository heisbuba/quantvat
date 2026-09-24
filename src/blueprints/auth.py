import functools
import requests
import time
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify

from ..config import FIREBASE_WEB_API_KEY

auth_bp = Blueprint('auth', __name__)

# --- Rate Limiter ---
LOGIN_ATTEMPTS = defaultdict(list)
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300  # 5 minutes

# Periodically drop IPs with no attempts left 
_SWEEP_INTERVAL = 600  # 10 minutes
_last_sweep = [0.0]

def _sweep_login_attempts(now: float) -> None:
    if now - _last_sweep[0] < _SWEEP_INTERVAL:
        return
    for ip in list(LOGIN_ATTEMPTS.keys()):
        LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < WINDOW_SECONDS]
        if not LOGIN_ATTEMPTS[ip]:
            del LOGIN_ATTEMPTS[ip]
    _last_sweep[0] = now

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    _sweep_login_attempts(now)
    # Clean up old attempts outside the window
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < WINDOW_SECONDS]
    if len(LOGIN_ATTEMPTS[ip]) >= MAX_ATTEMPTS:
        return True
    LOGIN_ATTEMPTS[ip].append(now)
    return False

# --- Helper Decorator ---
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            wants_json = (
                request.path.startswith('/api/')
                or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            )
            if wants_json:
                return jsonify({"error": "Session expired. Please log in again."}), 401
            # Capture the full path the user was trying to access
            return redirect(url_for('auth.login', next=request.full_path))
        return f(*args, **kwargs)
    return decorated_function

# --- Login Route ---
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if 'user_id' in session:
        return redirect(url_for('main.home'))

    if request.method == "POST":
        if is_rate_limited(request.remote_addr):
            return render_template("auth/register.html", mode="register", error="Too many attempts. Please wait 5 minutes.")
        email = request.form.get("email")
        password = request.form.get("password")
        
        if FIREBASE_WEB_API_KEY:
            # Exchange password for auth token via Google Identity Toolkit
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
            try:
                resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}, timeout=10)
            except requests.exceptions.RequestException:
                return render_template("auth/login.html", mode="login", error="Authentication service is unavailable. Please try again.")
            
            if resp.status_code == 200:
                # Activate the 30-day persistent session
                session.permanent = True
                session['user_id'] = resp.json()['localId']
                from .tasks import warm_journal_for_user
                warm_journal_for_user(session['user_id'])

                # Retrieve the 'next' destination from the URL parameters
                next_page = request.args.get('next')
                
                # Security Check: Ensure 'next_page' is a relative, same-site path.
                if (
                    not next_page
                    or not next_page.startswith('/')
                    or next_page.startswith('//')
                    or next_page.startswith('/\\')
                ):
                    next_page = url_for('main.home')
                
                return redirect(next_page)
            else:
                return render_template("auth/login.html", mode="login", error="Invalid email or password")
        else:
            return render_template("auth/login.html", mode="login", error="Authentication system not configured")
    
    return render_template("auth/login.html", mode="login")

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if 'user_id' in session:
        return redirect(url_for('main.home'))

    if request.method == "POST":
        if is_rate_limited(request.remote_addr):
            return render_template("auth/register.html", mode="register", error="Too many attempts. Please wait 5 minutes.")
        email = request.form.get("email")
        password = request.form.get("password")
        
        if FIREBASE_WEB_API_KEY:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
            try:
                resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}, timeout=10)
            except requests.exceptions.RequestException:
                return render_template("auth/register.html", mode="register", error="Registration service is unavailable. Please try again.")
            if resp.status_code == 200:
                session.permanent = True
                session['user_id'] = resp.json()['localId']
                flash("Registration Successful! Welcome to the Toolkit.", "success")
                return redirect(url_for('main.setup'))
            else:
                error_msg = resp.json().get('error', {}).get('message', 'Registration failed')
                return render_template("auth/register.html", mode="register", error=error_msg)
        else:
            return render_template("auth/register.html", mode="register", error="Registration system not configured")
    
    return render_template("auth/register.html", mode="register")

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        if is_rate_limited(request.remote_addr):
            return render_template("auth/register.html", mode="register", error="Too many attempts. Please wait 5 minutes.")
        email = request.form.get("email")
        if not email:
            return render_template("auth/reset.html", mode="reset", error="Please enter your email.")
        if FIREBASE_WEB_API_KEY:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}"
            try:
                resp = requests.post(url, json={"requestType": "PASSWORD_RESET", "email": email}, timeout=10)
            except requests.exceptions.RequestException:
                return render_template("auth/reset.html", mode="reset", error="Password reset service is unavailable. Please try again.")
            if resp.status_code == 200:
                return render_template("auth/reset.html", mode="reset", success="Password reset email sent!")
            else:
                return render_template("auth/reset.html", mode="reset", error="Error sending reset email")
        else:
            return render_template("auth/reset.html", mode="reset", error="Password reset not configured")
    
    return render_template("auth/reset.html", mode="reset")

@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for('auth.login'))