"""IMAP email ingestion — polls inbox and converts emails to tickets.

Uses only Python stdlib (imaplib, email) — no paid services needed.

Env vars:
  MAIL_IMAP_SERVER    e.g. imap.gmail.com
  MAIL_IMAP_PORT      993 (default, SSL)
  MAIL_USERNAME       e.g. support@yourcompany.com
  MAIL_PASSWORD       e.g. Gmail App Password
  MAIL_INBOX_FOLDER   INBOX (default)
  MAIL_AUTO_REPLY     1 (default) | 0  — send auto-response to requester
"""
import os
import re
import imaplib
import email
import logging
import traceback
from email import policy as email_policy
from email.utils import parseaddr, getaddresses
from datetime import datetime

log = logging.getLogger(__name__)

_TICKET_NUMBER_RE = re.compile(r'\bHD-\d{4,}\b', re.IGNORECASE)


def _cfg(key, default=''):
    return os.environ.get(key, default)


def _is_enabled() -> bool:
    return bool(_cfg('MAIL_IMAP_SERVER')) and bool(_cfg('MAIL_USERNAME')) and bool(_cfg('MAIL_PASSWORD'))


def _connect() -> imaplib.IMAP4_SSL | None:
    """Open an authenticated IMAP connection."""
    server = _cfg('MAIL_IMAP_SERVER')
    port   = int(_cfg('MAIL_IMAP_PORT', '993'))
    try:
        conn = imaplib.IMAP4_SSL(server, port)
        conn.login(_cfg('MAIL_USERNAME'), _cfg('MAIL_PASSWORD'))
        return conn
    except Exception as exc:
        log.error('IMAP connect failed: %s', exc)
        return None


def _extract_text(msg) -> str:
    """Extract plain-text body from a parsed email.Message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            if ct == 'text/plain' and 'attachment' not in cd:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8', errors='replace')
                except Exception:
                    pass
        # fallback: html → strip tags
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8', errors='replace')
                    return re.sub(r'<[^>]+>', ' ', html).strip()
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or 'utf-8', errors='replace')
        except Exception:
            pass
    return ''


def _extract_attachments(msg) -> list[dict]:
    """Return list of attachment dicts from a parsed email.Message."""
    attachments = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        cd = str(part.get('Content-Disposition', ''))
        if 'attachment' in cd:
            filename = part.get_filename()
            if filename:
                try:
                    data = part.get_payload(decode=True)
                    attachments.append({
                        'filename': filename,
                        'data': data,
                        'mime_type': part.get_content_type(),
                        'size': len(data) if data else 0,
                    })
                except Exception:
                    pass
    return attachments


def _save_attachment(ticket_id, att_dict) -> str | None:
    """Save raw bytes to disk; return stored filename."""
    import uuid, os
    folder = os.path.join('app', 'uploads', 'ticket_attachments', str(ticket_id))
    os.makedirs(folder, exist_ok=True)
    ext = os.path.splitext(att_dict['filename'])[1]
    stored = f'{uuid.uuid4().hex}{ext}'
    path = os.path.join(folder, stored)
    try:
        with open(path, 'wb') as f:
            f.write(att_dict['data'])
        return stored
    except Exception as exc:
        log.error('Attachment save failed: %s', exc)
        return None


def poll_inbox(app):
    """Called by APScheduler every 60 s. Ingests new emails."""
    if not _is_enabled():
        return

    with app.app_context():
        from app.extensions import db
        from app.models import Ticket, TicketComment, EmailLog, TicketAttachment
        from app.services.mailer import notify_ticket_created
        from app.services.sla import compute_sla_due
        from app.models.sla_policy import SLAPolicy

        conn = _connect()
        if not conn:
            return

        folder = _cfg('MAIL_INBOX_FOLDER', 'INBOX')
        try:
            conn.select(folder)
            # Search for UNSEEN emails
            status, data = conn.search(None, 'UNSEEN')
            if status != 'OK' or not data[0]:
                conn.logout()
                return

            uids = data[0].split()
            log.info('Found %d new email(s) to process', len(uids))

            for uid in uids:
                try:
                    _process_message(conn, uid, db, Ticket, TicketComment,
                                     EmailLog, TicketAttachment, notify_ticket_created)
                except Exception:
                    log.error('Error processing UID %s:\n%s', uid, traceback.format_exc())
        finally:
            try:
                conn.logout()
            except Exception:
                pass


def _process_message(conn, uid, db, Ticket, TicketComment, EmailLog,
                     TicketAttachment, notify_ticket_created):
    """Parse one IMAP message and either create a ticket or add a comment."""
    status, msg_data = conn.fetch(uid, '(RFC822)')
    if status != 'OK':
        return

    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw, policy=email_policy.compat32)

    message_id  = (msg.get('Message-ID') or '').strip()
    in_reply_to = (msg.get('In-Reply-To') or '').strip()
    references  = (msg.get('References') or '').strip()
    subject     = email.header.decode_header(msg.get('Subject', '(No Subject)'))[0]
    subject     = subject[0].decode(subject[1] or 'utf-8') if isinstance(subject[0], bytes) else subject[0]
    subject     = subject.strip()

    from_name, from_email = parseaddr(msg.get('From', ''))
    from_email = from_email.lower().strip()

    # Skip no-reply / system senders
    if any(p in from_email for p in ('no-reply', 'noreply', 'mailer-daemon', 'postmaster')):
        _log_email(db, EmailLog, message_id, from_email, 'in', subject, 'skipped', 'No-reply sender')
        conn.store(uid, '+FLAGS', '\\Seen')
        return

    # Dedup: already processed this Message-ID
    if message_id and EmailLog.query.filter_by(message_id=message_id).first():
        conn.store(uid, '+FLAGS', '\\Seen')
        return

    body = _extract_text(msg)
    attachments = _extract_attachments(msg)

    # ── Check if this is a reply to an existing ticket ──
    existing_ticket = None

    # 1. Check In-Reply-To / References for a known email_message_id
    for ref in [in_reply_to] + references.split():
        ref = ref.strip()
        if ref:
            t = Ticket.query.filter_by(email_message_id=ref).first()
            if t:
                existing_ticket = t
                break

    # 2. Scan subject for HD-XXXX ticket number
    if not existing_ticket:
        m = _TICKET_NUMBER_RE.search(subject)
        if m:
            existing_ticket = Ticket.query.filter_by(number=m.group(0).upper()).first()

    base_url = _cfg('APP_BASE_URL', '')

    if existing_ticket:
        # ── Add as comment on existing ticket ──
        clean_body = _strip_reply_quotes(body)
        comment = TicketComment(
            ticket_id=existing_ticket.id,
            body=clean_body or body,
            author_name=from_name or from_email,
            author_email=from_email,
            author_type='requester',
            is_internal=False,
        )
        db.session.add(comment)

        # Re-open if it was pending/solved
        if existing_ticket.status in ('pending', 'solved'):
            existing_ticket.status = 'open'

        existing_ticket.updated_at = datetime.utcnow()
        db.session.flush()

        # Save attachments
        _persist_attachments(db, attachments, existing_ticket.id, comment.id, TicketAttachment)

        db.session.commit()
        _log_email(db, EmailLog, message_id, from_email, 'in', subject, 'ok',
                   ticket_id=existing_ticket.id, snippet=body[:500])
        log.info('Reply added to ticket %s from %s', existing_ticket.number, from_email)

    else:
        # ── Create new ticket ──
        clean_subject = re.sub(r'^(re|fwd?):\s*', '', subject, flags=re.IGNORECASE).strip()
        if not clean_subject:
            clean_subject = '(No Subject)'

        ticket = Ticket(
            title=clean_subject,
            description=body,
            status='new',
            priority='medium',
            requester_name=from_name or from_email.split('@')[0],
            requester_email=from_email,
            source='email',
            email_message_id=message_id or None,
            email_thread_id=in_reply_to or message_id or None,
        )
        from sqlalchemy import text as _t
        next_id = db.session.execute(_t('SELECT COALESCE(MAX(id), 0) + 1 FROM tickets')).scalar()
        ticket.number = f'HD-{next_id:04d}'
        db.session.add(ticket)
        db.session.flush()

        # Apply SLA
        from app.models import SLAPolicy
        _apply_sla_to_ticket(ticket, db, SLAPolicy)

        # Auto-open if description present
        if ticket.description:
            ticket.status = 'open'

        db.session.flush()

        # Save attachments
        _persist_attachments(db, attachments, ticket.id, None, TicketAttachment)

        # System event
        ev = TicketComment(
            ticket_id=ticket.id,
            body=f'Ticket created automatically from inbound email (from: {from_email}).',
            author_name='System',
            author_type='system',
            is_internal=True,
        )
        db.session.add(ev)
        db.session.commit()

        _log_email(db, EmailLog, message_id, from_email, 'in', subject, 'ok',
                   ticket_id=ticket.id, snippet=body[:500])
        log.info('Created ticket %s from email %s', ticket.number, from_email)

        # Auto-reply to requester
        if _cfg('MAIL_AUTO_REPLY', '1') == '1':
            notify_ticket_created(ticket, base_url)

    # Mark email as read
    conn.store(uid, '+FLAGS', '\\Seen')


def _apply_sla_to_ticket(ticket, db, SLAPolicy):
    from app.services.sla import compute_sla_due
    policy = SLAPolicy.query.filter_by(
        category_id=ticket.category_id, priority=ticket.priority, is_active=True
    ).first()
    if not policy:
        policy = SLAPolicy.query.filter_by(
            category_id=None, priority=ticket.priority, is_active=True
        ).first()
    if policy:
        ticket.sla_policy_id = policy.id
        base = ticket.created_at or datetime.utcnow()
        ticket.first_response_due = compute_sla_due(base, policy.first_response_hours,
                                                    policy.business_hours_only)
        ticket.resolution_due = compute_sla_due(base, policy.resolution_hours,
                                                policy.business_hours_only)


def _persist_attachments(db, attachments, ticket_id, comment_id, TicketAttachment):
    for att in attachments:
        stored = _save_attachment(ticket_id, att)
        if stored:
            ta = TicketAttachment(
                ticket_id=ticket_id,
                comment_id=comment_id,
                filename=stored,
                original_filename=att['filename'],
                file_size=att['size'],
                mime_type=att['mime_type'],
                uploaded_by='email',
            )
            db.session.add(ta)


def _strip_reply_quotes(text: str) -> str:
    """Remove quoted reply lines (lines starting with '>') and common footers."""
    lines = text.splitlines()
    clean = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('>'):
            continue
        if re.match(r'^On .+wrote:$', stripped):
            break
        if stripped in ('--', '---', '________________________________'):
            break
        clean.append(line)
    return '\n'.join(clean).strip()


def _log_email(db, EmailLog, message_id, from_addr, direction, subject,
               status, error=None, ticket_id=None, snippet=None):
    """Write a record to email_logs."""
    try:
        el = EmailLog(
            direction=direction,
            ticket_id=ticket_id,
            message_id=message_id or None,
            from_addr=from_addr,
            subject=subject,
            status=status,
            error=error,
            raw_snippet=snippet,
        )
        db.session.add(el)
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.error('Failed to log email: %s', traceback.format_exc())
