#!/usr/bin/env python
"""
Bootstrap the PostgreSQL database.

Run this ONCE before starting the app on a fresh database.
It is safe to re-run — every step is idempotent.

  python init_db.py          # reads DATABASE_URL from .env
  DATABASE_URL=... python init_db.py

Steps:
  1. Create PostgreSQL schemas  asset_manager  and  helpdesk  (IF NOT EXISTS)
  2. Create all ORM tables      (db.create_all — no-op if tables exist)
  3. Stamp Alembic version      (marks current migration as applied so
                                 flask db upgrade won't re-run it)
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()

db_url = os.environ.get('DATABASE_URL', '')
if not db_url:
    print("ERROR: DATABASE_URL is not set. Add it to .env or export it.")
    sys.exit(1)

os.environ.setdefault('FLASK_ENV', 'development')
# Suppress the RuntimeError for missing SECRET_KEY when running outside gunicorn
if not os.environ.get('FLASK_SECRET_KEY'):
    os.environ['FLASK_SECRET_KEY'] = 'init-db-bootstrap-key'

from app import create_app, db
from sqlalchemy import text, inspect as sa_inspect


def main():
    app = create_app()
    with app.app_context():
        # ── 1. Create schemas ──────────────────────────────────────────────
        print("Creating schemas...")
        with db.engine.connect() as conn:
            conn.execute(text('CREATE SCHEMA IF NOT EXISTS asset_manager'))
            conn.execute(text('CREATE SCHEMA IF NOT EXISTS helpdesk'))
            conn.commit()
        print("  ✓ asset_manager, helpdesk")

        # ── 2. Create all tables ───────────────────────────────────────────
        print("Creating tables (db.create_all)...")
        db.create_all()
        print("  ✓ All ORM tables created / verified")

        # ── 3. Stamp Alembic if alembic_version table is missing ──────────
        # alembic_version lands in the first schema of search_path
        # (asset_manager,helpdesk,public) so check all three.
        print("Checking Alembic version...")
        with db.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT table_schema FROM information_schema.tables "
                "WHERE table_name = 'alembic_version' "
                "  AND table_schema IN ('asset_manager', 'helpdesk', 'public') "
                "LIMIT 1"
            )).fetchone()

        if row is None:
            print("Fresh database — stamping Alembic migration as head...")
            from flask_migrate import stamp
            stamp()
            print("  ✓ Stamped")
        else:
            schema = row[0]
            with db.engine.connect() as conn:
                version = conn.execute(
                    text(f'SELECT version_num FROM {schema}.alembic_version')
                ).scalar()
            print(f"  ✓ Alembic already at: {version} (in {schema} schema)")

    print("\nDatabase bootstrap complete. You can now run the app.")


if __name__ == '__main__':
    main()
