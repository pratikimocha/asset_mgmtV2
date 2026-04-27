"""Audit logging service."""
from flask import request, session
from app.models import ActivityLog
from app.extensions import db


def log_activity(action, entity_type=None, entity_id=None, details=None):
    """Log an activity to the audit trail."""
    try:
        user = session.get('user', {})
        user_oid = user.get('oid') or user.get('sub')
        user_name = user.get('name', 'Unknown')
        ip_address = request.remote_addr if request else None

        entry = ActivityLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            user_oid=user_oid,
            user_name=user_name,
            ip_address=ip_address
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        # Log but don't crash application
        import logging
        logging.error(f'Failed to log activity: {e}')
        db.session.rollback()
