"""Ticket and TicketComment models for helpdesk."""
from datetime import datetime
from app.extensions import db


class Ticket(db.Model):
    __tablename__ = 'tickets'
    __table_args__ = {'schema': 'helpdesk'}

    STATUS_CHOICES = ['new', 'open', 'pending', 'on_hold', 'solved', 'closed']
    PRIORITY_CHOICES = ['low', 'medium', 'high', 'critical']
    OPEN_STATUSES = ['new', 'open', 'pending', 'on_hold']

    PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    STATUS_COLORS = {
        'new': ('#eff6ff', '#1d4ed8'),
        'open': ('#f0fdf4', '#15803d'),
        'pending': ('#fef3c7', '#92400e'),
        'on_hold': ('#f1f5f9', '#475569'),
        'solved': ('#d1fae5', '#065f46'),
        'closed': ('#f1f5f9', '#94a3b8'),
    }
    PRIORITY_COLORS = {
        'critical': ('#fef2f2', '#dc2626'),
        'high': ('#fff7ed', '#ea580c'),
        'medium': ('#fefce8', '#ca8a04'),
        'low': ('#f0fdf4', '#16a34a'),
    }

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True, nullable=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='new', nullable=False, index=True)
    priority = db.Column(db.String(20), default='medium', nullable=False, index=True)

    # Category + sub-category
    category_id    = db.Column(db.Integer, db.ForeignKey('helpdesk.categories.id', ondelete='SET NULL'), nullable=True, index=True)
    subcategory_id = db.Column(db.Integer, db.ForeignKey('helpdesk.categories.id', ondelete='SET NULL'), nullable=True, index=True)

    # Requester (no login required for end users)
    requester_name = db.Column(db.String(255), nullable=False)
    requester_email = db.Column(db.String(255), nullable=False, index=True)
    requester_phone = db.Column(db.String(50))

    # Agent assignment
    assigned_to = db.Column(db.String(255))
    assigned_to_email = db.Column(db.String(255), index=True)

    # Asset linkage
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id', ondelete='SET NULL'), nullable=True, index=True)

    # SLA
    sla_policy_id = db.Column(db.Integer, db.ForeignKey('helpdesk.sla_policies.id', ondelete='SET NULL'), nullable=True)
    first_response_due = db.Column(db.DateTime)
    resolution_due = db.Column(db.DateTime)
    first_responded_at = db.Column(db.DateTime)

    # Meta
    source = db.Column(db.String(20), default='web')  # web, email, phone, manual
    tags = db.Column(db.Text)  # comma-separated

    # Email threading
    email_message_id = db.Column(db.String(500), unique=True, nullable=True, index=True)
    email_thread_id = db.Column(db.String(500), nullable=True, index=True)
    email_cc = db.Column(db.Text)  # comma-separated CC addresses

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    solved_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)

    # Relationships
    comments = db.relationship('TicketComment', back_populates='ticket',
                               cascade='all,delete',
                               order_by='TicketComment.created_at',
                               lazy='select')
    category    = db.relationship('Category', foreign_keys=[category_id],    lazy='joined')
    subcategory = db.relationship('Category', foreign_keys=[subcategory_id], lazy='joined')
    asset = db.relationship('Asset', lazy='joined', foreign_keys=[asset_id],
                            backref=db.backref('tickets', lazy='dynamic'))
    sla_policy = db.relationship('SLAPolicy', lazy='joined')

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def tag_list(self):
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    @property
    def sla_state(self):
        """'ok', 'warning', 'breached', or None"""
        if not self.resolution_due:
            return None
        now = datetime.utcnow()
        if self.status in ('solved', 'closed'):
            return 'ok'
        if now > self.resolution_due:
            return 'breached'
        delta = self.resolution_due - now
        if delta.total_seconds() < 3600:
            return 'warning'
        return 'ok'

    @property
    def status_colors(self):
        return self.STATUS_COLORS.get(self.status, ('#f1f5f9', '#64748b'))

    @property
    def priority_colors(self):
        return self.PRIORITY_COLORS.get(self.priority, ('#f1f5f9', '#64748b'))

    def __repr__(self):
        return f'<Ticket {self.number}: {self.title[:40]}>'


class TicketComment(db.Model):
    __tablename__ = 'ticket_comments'
    __table_args__ = {'schema': 'helpdesk'}

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('helpdesk.tickets.id', ondelete='CASCADE'),
                          nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    author_name = db.Column(db.String(255), nullable=False)
    author_email = db.Column(db.String(255))
    author_type = db.Column(db.String(20), default='agent')  # agent | requester | system
    is_internal = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    ticket = db.relationship('Ticket', back_populates='comments')

    def __repr__(self):
        return f'<TicketComment {self.id} [{self.author_type}]>'
