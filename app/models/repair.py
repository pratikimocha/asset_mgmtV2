"""Repair model."""
from datetime import datetime
from app.extensions import db


class Repair(db.Model):
    """Asset repair record."""
    __tablename__ = 'repairs'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id'), nullable=False, index=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('asset_manager.issues.id'))
    repair_text = db.Column(db.Text)
    cost = db.Column(db.Float)
    repaired_by = db.Column(db.String(255))
    repair_date = db.Column(db.Date)
    action_taken = db.Column(db.Text)
    status = db.Column(db.String(50), default='in-progress')  # in-progress, completed
    status_after = db.Column(db.String(50))  # intended asset status post-repair

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship('Asset', back_populates='repairs')
    issue = db.relationship('Issue', back_populates='repairs')

    def __repr__(self):
        return f'<Repair {self.id}: {self.status}>'
