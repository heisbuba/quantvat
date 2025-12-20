from flask import Blueprint, request, session, redirect, url_for, render_template_string
from firebase_admin import auth
from config import FirebaseHelper
from templates import LOGIN_TEMPLATE, SIGNUP_TEMPLATE, ADMIN_TEMPLATE

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        try:
            user = auth.get_user_by_email(request.form["email"])
            session['user_id'] = user.uid
            session['role'] = 'admin' if 'admin' in user.email else 'user'
            FirebaseHelper.log_activity(user.uid, "Login")
            return redirect('/')
        except:
            error = "Invalid Credentials or User Not Found"
    return render_template_string(LOGIN_TEMPLATE, error=error)

@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        try:
            user = auth.create_user(email=request.form["email"], password=request.form["password"])
            session['user_id'] = user.uid
            session['role'] = 'user'
            FirebaseHelper.log_activity(user.uid, "Signup")
            return redirect('/')
        except Exception as e:
            error = str(e)
    return render_template_string(SIGNUP_TEMPLATE, error=error)

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect('/login')

@auth_bp.route("/admin")
def admin():
    if session.get('role') != 'admin': return "Access Denied", 403
    db, _ = FirebaseHelper.initialize()
    logs = []
    if db:
        logs = [l.to_dict() for l in db.collection('analytics').order_by('timestamp', 'DESCENDING').limit(50).stream()]
    return render_template_string(ADMIN_TEMPLATE, logs=logs, log_count=len(logs))
