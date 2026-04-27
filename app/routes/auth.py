"""Authentication routes — local username/password login."""
from flask import Blueprint, render_template, redirect, url_for, session, request, current_app, flash
from app.models import UserRole
from app.extensions import db, csrf
from datetime import datetime

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user'):
        return redirect('/dashboard')

    # "no users" means no users with a password set (ignores legacy dev-login stubs)
    no_users = not UserRole.query.filter(UserRole.password_hash.isnot(None)).first()

    if request.method == 'POST':
        action = request.form.get('action')

        # ── First-run: create the initial admin account ──────────────────────
        if action == 'setup' and no_users:
            name = request.form.get('display_name', '').strip()
            uname = request.form.get('username', '').strip().lower()
            pwd = request.form.get('password', '')
            pwd2 = request.form.get('password2', '')
            if not name or not uname or not pwd:
                return render_template('auth/login.html', error='All fields are required.', setup=True)
            if pwd != pwd2:
                return render_template('auth/login.html', error='Passwords do not match.', setup=True)
            if len(pwd) < 8:
                return render_template('auth/login.html', error='Password must be at least 8 characters.', setup=True)
            user = UserRole.create(uname, pwd, name, role='admin', granted_by='setup')
            db.session.add(user)
            db.session.commit()
            _set_session(user)
            return redirect('/dashboard')

        # ── Normal login ──────────────────────────────────────────────────────
        uname = request.form.get('username', '').strip().lower()
        pwd = request.form.get('password', '')
        user = UserRole.query.filter_by(username=uname).first()
        if not user or not user.check_password(pwd):
            return render_template('auth/login.html', error='Invalid username or password.')
        _set_session(user)
        next_url = request.args.get('next', '/dashboard')
        return redirect(next_url if next_url.startswith('/') else '/dashboard')

    return render_template('auth/login.html', setup=no_users)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


# ── Dev bypass (only when no users exist and no Azure config) ─────────────────
@bp.route('/dev-login', methods=['POST'])
@csrf.exempt
def dev_login():
    """Dev-only bypass — only active if Azure SSO not configured AND no users exist."""
    if current_app.config.get('AZURE_CLIENT_ID') or UserRole.query.first():
        return redirect(url_for('auth.login'))
    return redirect(url_for('auth.login'))


# ── Kept dormant — activate by setting AZURE_CLIENT_ID env var ───────────────
@bp.route('/authorize')
def authorize():
    return redirect(url_for('auth.login'))


@bp.route('/auth')
def auth_callback():
    return redirect(url_for('auth.login'))


# ── Helpers ───────────────────────────────────────────────────────────────────
def _set_session(user: UserRole):
    session['user'] = {
        'name': user.display_name or user.username,
        'preferred_username': user.username,
        'oid': user.oid,
        'role': user.role,
    }
    session.modified = True
