"""Maintenance task model."""
from datetime import datetime
from app.extensions import db


class MaintenanceTask(db.Model):
    """Scheduled maintenance task."""
    __tablename__ = 'maintenance_tasks'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id'), nullable=False, index=True)
    task_type = db.Column(db.String(255))
    scheduled_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)
    assigned_to = db.Column(db.String(255))
    status = db.Column(db.String(50), default='scheduled')  # scheduled, completed
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship('Asset', back_populates='maintenance')

    def __repr__(self):
        return f'<MaintenanceTask {self.id}: {self.task_type} ({self.status})>'
