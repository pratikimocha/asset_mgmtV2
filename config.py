"""Flask configuration."""
import os
from datetime import timedelta


class Config:
    """Base configuration."""
    FLASK_ENV = os.environ.get('ENV', 'development')
    DEBUG = FLASK_ENV == 'development'
    TESTING = False

    # Required: fail at startup if not set
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
    if not SECRET_KEY and not TESTING:
        raise RuntimeError(
            'FLASK_SECRET_KEY env var is required. '
            'Set it in Azure App Settings or .env'
        )

    # Database — PostgreSQL required
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL and not TESTING:
        raise RuntimeError(
            'DATABASE_URL env var is required. '
            'Format: postgresql://user:pass@host:5432/asset_mgmt_v2'
        )
    _db_url = DATABASE_URL or ''
    SQLALCHEMY_DATABASE_URI = _db_url or 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 5,          # 5 per worker is enough; avoids excess SSL handshakes
        'max_overflow': 2,       # allow 2 burst connections before blocking
        'pool_recycle': 1800,    # recycle every 30 min (Azure PostgreSQL drops idle connections)
        'pool_pre_ping': True,   # Azure can silently drop idle connections; pre_ping avoids 10-20s stale-connection stalls
        'pool_timeout': 10,      # fail fast if no connection available
        'connect_args': {
            'connect_timeout': 10,
            'application_name': 'asset_mgmt_v2',
            'options': '-csearch_path=asset_manager,helpdesk,public',
            'keepalives': 1,          # TCP keepalive — detects dropped connections without pre_ping
            'keepalives_idle': 60,
            'keepalives_interval': 10,
            'keepalives_count': 5,
        }
    } if _db_url.startswith('postgresql') else {}

    # Cache static files in browser for 7 days — avoids re-fetching CSS/JS on every page
    SEND_FILE_MAX_AGE_DEFAULT = 604800

    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_SECURE = FLASK_ENV != 'development'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # WTForms / CSRF
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    # Rate limiting
    RATELIMIT_ENABLED = True
    RATELIMIT_DEFAULT = '10000 per day, 1000 per hour'
    RATELIMIT_STORAGE_URI = 'memory://'

    # Azure AD / MSAL
    AZURE_CLIENT_ID = os.environ.get('AZURE_CLIENT_ID')
    AZURE_CLIENT_SECRET = os.environ.get('AZURE_CLIENT_SECRET')
    AZURE_AUTHORITY = os.environ.get('AZURE_AUTHORITY', 'https://login.microsoftonline.com/common')
    AZURE_SCOPE = os.environ.get('AZURE_SCOPE', ['https://graph.microsoft.com/.default'])
    AZURE_REDIRECT_PATH = os.environ.get('AZURE_REDIRECT_PATH', '/auth')
    AZURE_REDIRECT_HOST = os.environ.get('AZURE_REDIRECT_HOST')  # Only for dev overrides

    # Upload
    UPLOAD_FOLDER = 'app/uploads/po_files'
    UPLOAD_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
    ALLOWED_UPLOAD_EXT = {'.pdf'}

    # AI assistant — Gemini 2.0 Flash (primary) / Groq (fallback)
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
    GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')

    # Display timezone (stored as UTC, shown in local time)
    APP_TIMEZONE = os.environ.get('APP_TIMEZONE', 'Asia/Kolkata')

    # Pagination
    ITEMS_PER_PAGE = 50

    # Security headers (handled in app factory)
    HSTS_MAX_AGE = 31536000  # 1 year
    HSTS_INCLUDE_SUBDOMAINS = True
    HSTS_PRELOAD = False


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    SQLALCHEMY_ECHO = False  # Set to True for SQL logging


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    FLASK_ENV = 'production'

    # Azure PostgreSQL Flexible Server requires SSL — append if not already set
    if Config._db_url.startswith('postgresql') and 'sslmode' not in Config._db_url:
        SQLALCHEMY_DATABASE_URI = Config._db_url + (
            '&' if '?' in Config._db_url else '?'
        ) + 'sslmode=require'


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SECRET_KEY = 'test-secret-key'
    # SQLite doesn't support pool_size etc.
    SQLALCHEMY_ENGINE_OPTIONS = {}
    DATABASE_URL = 'sqlite:///:memory:'


# Select config class
config_name = os.environ.get('FLASK_ENV', 'development')
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}[config_name]
