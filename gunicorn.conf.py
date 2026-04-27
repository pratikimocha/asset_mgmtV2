"""Gunicorn configuration for Azure App Service."""
import multiprocessing

# Auto-size workers: 2 per CPU + 1 (standard formula for sync workers)
# B1 = 1 vCPU → 3 workers, B2 = 2 vCPU → 5 workers
workers = min(2 * multiprocessing.cpu_count() + 1, 9)
worker_class = "sync"
timeout = 120
keepalive = 5          # reuse HTTP connections between requests
bind = "0.0.0.0:8000"
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Load the app in the master process BEFORE forking workers.
# This ensures APScheduler (email poll, SLA check) starts exactly once.
preload_app = True


def post_fork(server, worker):
    """Dispose the SQLAlchemy connection pool after fork.

    Without this, workers might inherit open DB connections from the master
    process, leading to 'SSL connection has been closed unexpectedly' errors
    on Azure PostgreSQL.
    """
    try:
        from app.extensions import db
        db.engine.dispose()
        server.log.info("Worker %s: connection pool disposed after fork", worker.pid)
    except Exception as exc:
        server.log.warning("post_fork pool dispose failed: %s", exc)
