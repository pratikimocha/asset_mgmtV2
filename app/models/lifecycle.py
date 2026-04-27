"""Lifecycle event model (append-only audit)."""
from datetime import datetime
from app.extensions import db


class LifecycleEvent(db.Model):
    """Append-only lifecycle events for asset audit trail."""
    __tablename__ = 'lifecycle_events'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id'), nullable=False, index=True)
    event_type = db.Column(db.String(255), nullable=False)
    event_notes = db.Column(db.Text)
    status_after = db.Column(db.String(50))
    event_date = db.Column(db.Date, nullable=False)
    performed_by = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    asset = db.relationship('Asset', back_populates='lifecycle')

    def __repr__(self):
        return f'<LifecycleEvent {self.id}: {self.event_type} on {self.event_date}>'
