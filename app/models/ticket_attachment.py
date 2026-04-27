"""Ticket file attachments."""
import os
from datetime import datetime
from app.extensions import db


class TicketAttachment(db.Model):
    __tablename__ = 'ticket_attachments'
    __table_args__ = {'schema': 'helpdesk'}

    id                = db.Column(db.Integer, primary_key=True)
    ticket_id         = db.Column(db.Integer, db.ForeignKey('helpdesk.tickets.id', ondelete='CASCADE'),
                                  nullable=False, index=True)
    comment_id        = db.Column(db.Integer, db.ForeignKey('helpdesk.ticket_comments.id', ondelete='SET NULL'),
                                  nullable=True)
    filename          = db.Column(db.String(255), nullable=False)   # stored name on disk
    original_filename = db.Column(db.String(255))                   # user-visible name
    file_size         = db.Column(db.Integer)                       # bytes
    mime_type         = db.Column(db.String(100))
    uploaded_by       = db.Column(db.String(255))
    is_inline         = db.Column(db.Boolean, default=False)        # embedded in email body
    created_at        = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    ticket  = db.relationship('Ticket', backref=db.backref('attachments', lazy='dynamic'))
    comment = db.relationship('TicketComment',
                              backref=db.backref('attachments', lazy='dynamic'))

    @property
    def size_label(self):
        if not self.file_size:
            return '—'
        if self.file_size < 1024:
            return f'{self.file_size} B'
        if self.file_size < 1024 * 1024:
            return f'{self.file_size // 1024} KB'
        return f'{self.file_size / (1024*1024):.1f} MB'

    @property
    def icon(self):
        ext = os.path.splitext(self.original_filename or self.filename)[1].lower()
        return {'.pdf': '📄', '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️',
                '.gif': '🖼️', '.doc': '📝', '.docx': '📝',
                '.xls': '📊', '.xlsx': '📊', '.zip': '🗜️'}.get(ext, '📎')

    def __repr__(self):
        return f'<TicketAttachment {self.original_filename}>'
