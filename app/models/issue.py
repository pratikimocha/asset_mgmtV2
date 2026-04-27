"""Issue model."""
from datetime import datetime
from app.extensions import db


class Issue(db.Model):
    """Asset issue/problem."""
    __tablename__ = 'issues'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id'), nullable=False, index=True)
    issue_text = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(50), default='medium')  # low, medium, high
    status = db.Column(db.String(50), default='open', index=True)  # open, in-progress, closed
    date_reported = db.Column(db.Date, nullable=False)
    reported_by = db.Column(db.String(255))  # session['user']['name']

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship('Asset', back_populates='issues')
    repairs = db.relationship(
        'Repair',
        back_populates='issue',
        cascade='all,delete',
        lazy='select'
    )

    def __repr__(self):
        return f'<Issue {self.id}: {self.status} ({self.severity})>'
