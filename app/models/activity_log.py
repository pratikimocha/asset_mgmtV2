"""Activity log model (append-only audit trail)."""
from datetime import datetime
from app.extensions import db


class ActivityLog(db.Model):
    """Append-only activity/audit log."""
    __tablename__ = 'activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_oid = db.Column(db.String(255))  # Azure AD OID
    user_name = db.Column(db.String(255))
    action = db.Column(db.String(255), nullable=False)
    entity_type = db.Column(db.String(255))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<ActivityLog {self.id}: {self.action} at {self.timestamp}>'

    @classmethod
    def log_action(cls, action, entity_type=None, entity_id=None, details=None, user_oid=None, user_name=None, ip_address=None):
        """Create an activity log entry."""
        entry = cls(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            user_oid=user_oid,
            user_name=user_name,
            ip_address=ip_address,
            timestamp=datetime.utcnow()
        )
        db.session.add(entry)
        return entry
