"""Email log — records every inbound and outbound email."""
from datetime import datetime
from app.extensions import db


class EmailLog(db.Model):
    __tablename__ = 'email_logs'
    __table_args__ = {'schema': 'helpdesk'}

    DIRECTION_IN  = 'in'
    DIRECTION_OUT = 'out'
    STATUS_OK      = 'ok'
    STATUS_FAILED  = 'failed'
    STATUS_SKIPPED = 'skipped'

    id          = db.Column(db.Integer, primary_key=True)
    direction   = db.Column(db.String(5), nullable=False)       # 'in' | 'out'
    ticket_id   = db.Column(db.Integer, db.ForeignKey('helpdesk.tickets.id', ondelete='SET NULL'), nullable=True, index=True)
    message_id  = db.Column(db.String(500), unique=True, nullable=True)  # RFC 2822 Message-ID
    from_addr   = db.Column(db.String(500))
    to_addr     = db.Column(db.Text)                            # comma-separated
    subject     = db.Column(db.String(1000))
    status      = db.Column(db.String(20), default='ok')        # ok | failed | skipped
    error       = db.Column(db.Text)
    raw_snippet = db.Column(db.Text)                            # first 500 chars of body
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    ticket = db.relationship('Ticket', lazy='joined',
                             backref=db.backref('email_logs', lazy='dynamic'))

    def __repr__(self):
        return f'<EmailLog {self.direction} {self.from_addr} → {self.to_addr} [{self.status}]>'
