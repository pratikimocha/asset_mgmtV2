"""Flask extensions initialized here."""
from flask import has_request_context, request, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()


def rate_limit_key():
    """Prefer authenticated user identity, then real client IP.

    Azure App Service commonly sits behind one or more proxies, so plain
    ``remote_addr`` can collapse many users onto the same limiter bucket.
    For logged-in users we rate-limit per account. For anonymous traffic we
    fall back to the forwarded client IP when present.
    """
    if not has_request_context():
        return 'global'

    user = session.get('user') or {}
    user_id = user.get('oid') or user.get('sub') or user.get('preferred_username')
    if user_id:
        return f'user:{user_id}'

    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        client_ip = forwarded_for.split(',')[0].strip()
        if client_ip:
            return f'ip:{client_ip}'

    return f'ip:{request.remote_addr or "anonymous"}'


limiter = Limiter(key_func=rate_limit_key, default_limits=['10000 per day', '1000 per hour'])
