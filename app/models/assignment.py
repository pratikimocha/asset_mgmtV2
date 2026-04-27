"""Assignment model."""
from datetime import datetime
from app.extensions import db


class Assignment(db.Model):
    """Asset assignment to user."""
    __tablename__ = 'assignments'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id'), nullable=False, index=True)
    user_name = db.Column(db.String(255), nullable=False)
    assigned_date = db.Column(db.Date, nullable=False)
    returned_at = db.Column(db.Date)  # NULL = currently assigned
    condition_on_issue = db.Column(db.String(255))
    condition_on_return = db.Column(db.String(255))
    notes = db.Column(db.Text)
    assigned_by = db.Column(db.String(255))  # session['user']['name']

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship('Asset', back_populates='assignments')

    def __repr__(self):
        return f'<Assignment {self.user_name} → Asset {self.asset_id}>'

    @property
    def is_active(self):
        """Check if assignment is currently active (not returned)."""
        return self.returned_at is None
