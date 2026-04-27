"""Authentication and authorization decorators."""
from functools import wraps
from flask import session, render_template, request, redirect, url_for
from app.models import UserRole
from app.extensions import db


def login_required(f):
    """Require user login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    """Require specific role(s)."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('auth.login'))

            user = session['user']
            user_oid = user.get('oid') or user.get('sub')

            # Lazy-load role if missing from session (old sessions)
            if 'role' not in user:
                user_role = UserRole.query.filter_by(oid=user_oid).first()
                user['role'] = user_role.role if user_role else 'viewer'
                session.modified = True

            if user.get('role') not in allowed_roles:
                return render_template('errors/403.html'), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator
