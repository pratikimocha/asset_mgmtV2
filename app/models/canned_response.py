"""Canned responses — saved reply templates for agents."""
from datetime import datetime
from app.extensions import db


class CannedResponse(db.Model):
    __tablename__ = 'canned_responses'
    __table_args__ = {'schema': 'helpdesk'}

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(150), nullable=False)
    shortcut    = db.Column(db.String(50), unique=True, nullable=True)   # /shortcut trigger
    subject     = db.Column(db.String(255))                              # for email replies
    body        = db.Column(db.Text, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('helpdesk.categories.id', ondelete='SET NULL'), nullable=True)
    use_count   = db.Column(db.Integer, default=0, nullable=False)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('Category', lazy='joined',
                               backref=db.backref('canned_responses', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'shortcut': self.shortcut or '',
            'subject': self.subject or '',
            'body': self.body,
            'category_id': self.category_id,
            'is_active': self.is_active,
        }

    # Template variables: {{requester_name}}, {{ticket_number}}, {{agent_name}}
    def render(self, context: dict) -> str:
        body = self.body
        for key, val in context.items():
            body = body.replace('{{' + key + '}}', str(val))
        return body

    def __repr__(self):
        return f'<CannedResponse {self.name!r}>'
