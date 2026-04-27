"""SLA Policy model — per-category, per-priority response & resolution targets."""
from datetime import datetime
from app.extensions import db


class SLAPolicy(db.Model):
    __tablename__ = 'sla_policies'
    __table_args__ = {'schema': 'helpdesk'}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('helpdesk.categories.id', ondelete='SET NULL'), nullable=True, index=True)
    priority = db.Column(db.String(20), nullable=False)  # low | medium | high | critical
    first_response_hours = db.Column(db.Float, nullable=False, default=8.0)
    resolution_hours = db.Column(db.Float, nullable=False, default=48.0)
    business_hours_only = db.Column(db.Boolean, default=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('Category', backref=db.backref('sla_policies', lazy='dynamic'))

    PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category_id': self.category_id,
            'priority': self.priority,
            'first_response_hours': self.first_response_hours,
            'resolution_hours': self.resolution_hours,
            'business_hours_only': self.business_hours_only,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<SLAPolicy {self.name} [{self.priority}]>'
