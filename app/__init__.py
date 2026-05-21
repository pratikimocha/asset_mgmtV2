"""Flask application factory."""
import os
import secrets
from flask import Flask, g, render_template, request, jsonify
from datetime import datetime, timezone, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix

from config import config
from app.extensions import db, migrate, csrf, limiter


def create_app(config_name=None):
    """Create and configure Flask app."""
    app = Flask(__name__)
    app.config.from_object(config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    # ── Timezone filter ───────────────────────────────────────────────────────
    _tz_name = app.config.get('APP_TIMEZONE', 'Asia/Kolkata')
    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo(_tz_name)
        def _to_local(dt, fmt='%d %b %Y, %H:%M'):
            if dt is None:
                return '—'
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(_tz).strftime(fmt)
    except Exception:
        # fallback: fixed UTC+5:30 for IST
        _ist_offset = timedelta(hours=5, minutes=30)
        def _to_local(dt, fmt='%d %b %Y, %H:%M'):
            if dt is None:
                return '—'
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt + _ist_offset).strftime(fmt)

    app.jinja_env.filters['localtime'] = _to_local

    # Inject template globals
    def _format_hours(h):
        """Format hours as human-readable string: 0.5 → '30m', 4 → '4h', 48 → '2d'."""
        if h < 1:
            return f'{int(h * 60)}m'
        if h >= 24 and h % 24 == 0:
            return f'{int(h // 24)}d'
        if h == int(h):
            return f'{int(h)}h'
        return f'{h}h'

    @app.context_processor
    def inject_globals():
        return {
            'now': datetime.utcnow,
            'g': g,
            'format_hours': _format_hours,
        }

    # Security headers
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(),microphone=(),camera=()'
        if not app.debug:
            response.headers['Strict-Transport-Security'] = (
                f'max-age={app.config.get("HSTS_MAX_AGE", 31536000)}; includeSubDomains'
            )
        return response

    # CSP Nonce per request
    @app.before_request
    def set_csp_nonce():
        g.csp_nonce = secrets.token_hex(16)

    @app.after_request
    def set_csp_header(response):
        nonce = getattr(g, 'csp_nonce', '')
        response.headers['Content-Security-Policy'] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data:; "
            f"connect-src 'self'"
        )
        return response

    # Error handlers — render HTML templates
    @app.errorhandler(400)
    def bad_request(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Bad request'}), 400
        return render_template('errors/400.html'), 400

    @app.errorhandler(403)
    def forbidden(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Forbidden'}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Not found'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Rate limit exceeded'}), 429
        return render_template('errors/429.html'), 429

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Internal server error'}), 500
        return render_template('errors/500.html'), 500

    # Health check (no auth required)
    @app.route('/health')
    def health():
        return jsonify({'status': 'ok'}), 200

    # Register blueprints
    from app.routes import auth, dashboard, assets, assignments, issues, repairs
    from app.routes import maintenance, purchase_orders, reports, admin, api, helpdesk
    from app.routes import home

    app.register_blueprint(home.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(assets.bp)
    app.register_blueprint(assignments.bp)
    app.register_blueprint(issues.bp)
    app.register_blueprint(repairs.bp)
    app.register_blueprint(maintenance.bp)
    app.register_blueprint(purchase_orders.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(helpdesk.bp)

    # Create upload folder
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'app/uploads/po_files'), exist_ok=True)

    with app.app_context():
        db.create_all()
        # Ensure attachment upload folder exists
        os.makedirs(os.path.join('app', 'uploads', 'ticket_attachments'), exist_ok=True)

    # Start background scheduler (email poll, SLA check)
    # Skip in testing or when reloader is re-forking
    if not app.config.get('TESTING') and os.environ.get('WERKZEUG_RUN_MAIN') != 'false':
        try:
            from app.services.scheduler import start_scheduler
            start_scheduler(app)
        except Exception as exc:
            app.logger.warning('Scheduler not started: %s', exc)

    return app
