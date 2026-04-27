"""Microsoft 365 / Graph API — email send + inbox poll.

Set up in Azure Portal:
  1. Azure Active Directory → App registrations → New registration
  2. API permissions → Add → Microsoft Graph → Application permissions:
       Mail.Send  +  Mail.ReadWrite
  3. Grant admin consent for your organisation
  4. Certificates & secrets → New client secret  → copy the VALUE

Required env vars:
  MAIL_PROVIDER        graph
  MAIL_TENANT_ID       xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  MAIL_CLIENT_ID       xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  MAIL_CLIENT_SECRET   the secret VALUE (not the ID)
  MAIL_MAILBOX         helpdesk@yourcompany.com   (shared mailbox or user)
  MAIL_FROM            iMocha Helpdesk <helpdesk@yourcompany.com>

Optional:
  MAIL_AUTO_REPLY      1 (default) | 0
  MAIL_POLL_INTERVAL   60 (seconds, default)
"""
from __future__ import annotations
import os
import re
import logging
import traceback
from datetime import datetime

import requests as _requests

log = logging.getLogger(__name__)

GRAPH = 'https://graph.microsoft.com/v1.0'
_TICKET_RE = re.compile(r'\bHD-\d{4,}\b', re.IGNORECASE)

# msal ConfidentialClientApplication is created once and caches tokens internally
_msal_app = None


def _e(key: str, default: str = '') -> str:
    return os.environ.get(key, default)


def is_enabled() -> bool:
    return _e('MAIL_PROVIDER') == 'graph' and bool(_e('MAIL_TENANT_ID'))


# ── Auth ───────────────────────────────────────────────────────────────────────

def _get_token() -> str | None:
    """Return a valid bearer token via msal client-credentials flow."""
    global _msal_app

    tenant = _e('MAIL_TENANT_ID')
    cid    = _e('MAIL_CLIENT_ID')
    secret = _e('MAIL_CLIENT_SECRET')

    if not all([tenant, cid, secret]):
        log.error('ms365: MAIL_TENANT_ID / MAIL_CLIENT_ID / MAIL_CLIENT_SECRET not set')
        return None

    try:
        import msal
        if _msal_app is None:
            _msal_app = msal.ConfidentialClientApplication(
                cid,
                authority=f'https://login.microsoftonline.com/{tenant}',
                client_credential=secret,
            )

        result = _msal_app.acquire_token_for_client(
            scopes=['https://graph.microsoft.com/.default']
        )
        if 'access_token' in result:
            return result['access_token']

        log.error('ms365: token error — %s: %s',
                  result.get('error'), result.get('error_description'))
        return None
    except Exception as exc:
        log.error('ms365: token fetch failed — %s', exc)
        return None


def _headers() -> dict | None:
    token = _get_token()
    if not token:
        return None
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


# ── Send ───────────────────────────────────────────────────────────────────────

def send_mail(to: str | list, subject: str, html_body: str,
              reply_to: str = None, message_id: str = None,
              in_reply_to: str = None) -> bool:
    """Send email via Graph API sendMail endpoint."""
    hdrs = _headers()
    if not hdrs:
        return False

    mailbox = _e('MAIL_MAILBOX')
    if not mailbox:
        log.error('ms365: MAIL_MAILBOX not set')
        return False

    recipients = ([to] if isinstance(to, str) else list(to))
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients:
        return False

    # Parse "Display Name <addr>" from MAIL_FROM
    from_raw = _e('MAIL_FROM', mailbox)
    m = re.match(r'^(.+?)\s*<(.+?)>\s*$', from_raw)
    from_name = m.group(1).strip() if m else ''
    from_addr = m.group(2).strip() if m else from_raw.strip()

    payload: dict = {
        'message': {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': html_body},
            'toRecipients': [{'emailAddress': {'address': r}} for r in recipients],
            'from': {'emailAddress': {'name': from_name, 'address': from_addr}},
        },
        'saveToSentItems': True,
    }

    if reply_to:
        payload['message']['replyTo'] = [{'emailAddress': {'address': reply_to}}]

    # Email threading headers
    inet_headers = []
    if in_reply_to:
        inet_headers += [
            {'name': 'In-Reply-To', 'value': in_reply_to},
            {'name': 'References',  'value': in_reply_to},
        ]
    if message_id:
        inet_headers.append({'name': 'Message-ID', 'value': message_id})
    if inet_headers:
        payload['message']['internetMessageHeaders'] = inet_headers

    try:
        r = _requests.post(
            f'{GRAPH}/users/{mailbox}/sendMail',
            headers=hdrs, json=payload, timeout=20,
        )
        if r.status_code == 202:
            log.info('ms365: sent "%s" → %s', subject, recipients)
            return True
        log.error('ms365: sendMail %s — %s', r.status_code, r.text[:400])
        return False
    except Exception as exc:
        log.error('ms365: sendMail exception — %s', exc)
        return False


# ── Inbox poll ────────────────────────────────────────────────────────────────

def poll_inbox(app) -> None:
    """Called by APScheduler. Fetches unread Inbox messages and ingests them."""
    if not is_enabled():
        return

    with app.app_context():
        from app.extensions import db
        from app.models import Ticket, TicketComment, EmailLog, TicketAttachment
        from app.services.mailer import notify_ticket_created

        hdrs = _headers()
        if not hdrs:
            return

        mailbox = _e('MAIL_MAILBOX')
        if not mailbox:
            log.error('ms365: MAIL_MAILBOX not set')
            return

        # Fetch unread messages (top 50, oldest first)
        url = (
            f'{GRAPH}/users/{mailbox}/mailFolders/Inbox/messages'
            '?$filter=isRead eq false'
            '&$orderby=receivedDateTime asc'
            '&$top=50'
            '&$select=id,subject,from,body,receivedDateTime,'
            'internetMessageId,internetMessageHeaders,hasAttachments'
        )

        try:
            r = _requests.get(url, headers=hdrs, timeout=20)
            r.raise_for_status()
            messages = r.json().get('value', [])
        except Exception as exc:
            log.error('ms365: inbox fetch failed — %s', exc)
            return

        log.info('ms365: %d unread message(s) to process', len(messages))

        for msg in messages:
            try:
                _process_message(
                    msg, mailbox, hdrs, db,
                    Ticket, TicketComment, EmailLog, TicketAttachment,
                    notify_ticket_created,
                )
            except Exception:
                log.error('ms365: error processing message %s:\n%s',
                          msg.get('id'), traceback.format_exc())


def _process_message(msg, mailbox, hdrs, db,
                     Ticket, TicketComment, EmailLog, TicketAttachment,
                     notify_ticket_created) -> None:
    """Convert one Graph message dict to a ticket or comment."""
    msg_id     = msg.get('internetMessageId', '').strip()
    graph_id   = msg['id']
    subject    = (msg.get('subject') or '(No Subject)').strip()
    from_obj   = msg.get('from', {}).get('emailAddress', {})
    from_email = from_obj.get('address', '').lower().strip()
    from_name  = from_obj.get('name', '')

    # Skip no-reply senders
    if any(p in from_email for p in ('no-reply', 'noreply', 'mailer-daemon', 'postmaster')):
        _mark_read(mailbox, graph_id, hdrs)
        return

    # Dedup
    if msg_id and EmailLog.query.filter_by(message_id=msg_id).first():
        _mark_read(mailbox, graph_id, hdrs)
        return

    # Extract In-Reply-To from internet message headers
    inet_hdrs  = {h['name'].lower(): h['value']
                  for h in msg.get('internetMessageHeaders', [])}
    in_reply_to = inet_hdrs.get('in-reply-to', '').strip()
    references  = inet_hdrs.get('references', '').strip()

    # Body — Graph returns HTML, strip tags for plain-text storage
    body_html = (msg.get('body', {}).get('content') or '')
    body = _html_to_text(body_html)

    # Attachments (fetch separately if flagged)
    attachments = []
    if msg.get('hasAttachments'):
        attachments = _fetch_attachments(mailbox, graph_id, hdrs)

    # ── Reply to existing ticket? ──────────────────────────────────────────────
    existing = None

    for ref in ([in_reply_to] + references.split()):
        ref = ref.strip()
        if ref:
            t = Ticket.query.filter_by(email_message_id=ref).first()
            if t:
                existing = t
                break

    if not existing:
        m = _TICKET_RE.search(subject)
        if m:
            existing = Ticket.query.filter_by(number=m.group(0).upper()).first()

    base_url = _e('APP_BASE_URL', '')

    if existing:
        clean = _strip_quotes(body)
        comment = TicketComment(
            ticket_id=existing.id,
            body=clean or body,
            author_name=from_name or from_email,
            author_email=from_email,
            author_type='requester',
            is_internal=False,
        )
        db.session.add(comment)
        if existing.status in ('pending', 'solved'):
            existing.status = 'open'
        existing.updated_at = datetime.utcnow()
        db.session.flush()
        _persist_attachments(db, attachments, existing.id, comment.id, TicketAttachment)
        db.session.commit()
        _log(db, EmailLog, msg_id, from_email, 'in', subject, 'ok',
             ticket_id=existing.id, snippet=body[:500])
        log.info('ms365: reply added to %s from %s', existing.number, from_email)

    else:
        clean_subj = re.sub(r'^(re|fwd?):\s*', '', subject, flags=re.IGNORECASE).strip()
        if not clean_subj:
            clean_subj = '(No Subject)'

        from sqlalchemy import text as _t
        next_id = db.session.execute(_t('SELECT COALESCE(MAX(id),0)+1 FROM tickets')).scalar()
        ticket = Ticket(
            title=clean_subj,
            description=body,
            status='open' if body else 'new',
            priority='medium',
            requester_name=from_name or from_email.split('@')[0],
            requester_email=from_email,
            source='email',
            email_message_id=msg_id or None,
            email_thread_id=in_reply_to or msg_id or None,
            number=f'HD-{next_id:04d}',
        )
        db.session.add(ticket)
        db.session.flush()

        _apply_sla(ticket, db)

        db.session.flush()
        _persist_attachments(db, attachments, ticket.id, None, TicketAttachment)

        ev = TicketComment(
            ticket_id=ticket.id,
            body=f'Ticket created from inbound email (from: {from_email}).',
            author_name='System', author_type='system', is_internal=True,
        )
        db.session.add(ev)
        db.session.commit()

        _log(db, EmailLog, msg_id, from_email, 'in', subject, 'ok',
             ticket_id=ticket.id, snippet=body[:500])
        log.info('ms365: created ticket %s from %s', ticket.number, from_email)

        if _e('MAIL_AUTO_REPLY', '1') == '1':
            notify_ticket_created(ticket, base_url)

    _mark_read(mailbox, graph_id, hdrs)


# ── Graph helpers ──────────────────────────────────────────────────────────────

def _mark_read(mailbox: str, graph_id: str, hdrs: dict) -> None:
    try:
        _requests.patch(
            f'{GRAPH}/users/{mailbox}/messages/{graph_id}',
            headers=hdrs, json={'isRead': True}, timeout=10,
        )
    except Exception as exc:
        log.warning('ms365: mark-read failed — %s', exc)


def _fetch_attachments(mailbox: str, graph_id: str, hdrs: dict) -> list[dict]:
    try:
        r = _requests.get(
            f'{GRAPH}/users/{mailbox}/messages/{graph_id}/attachments'
            '?$select=name,contentType,size,contentBytes',
            headers=hdrs, timeout=15,
        )
        r.raise_for_status()
        result = []
        import base64
        for att in r.json().get('value', []):
            if att.get('@odata.type') == '#microsoft.graph.fileAttachment':
                raw = base64.b64decode(att.get('contentBytes', ''))
                result.append({
                    'filename':  att.get('name', 'attachment'),
                    'data':      raw,
                    'mime_type': att.get('contentType', 'application/octet-stream'),
                    'size':      len(raw),
                })
        return result
    except Exception as exc:
        log.warning('ms365: attachment fetch failed — %s', exc)
        return []


# ── Text helpers ───────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    if not html:
        return ''
    # Remove style/script blocks
    html = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html, flags=re.IGNORECASE | re.DOTALL)
    # Replace block tags with newlines
    html = re.sub(r'<(br|p|div|tr|li)[^>]*/?>', '\n', html, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r'<[^>]+>', '', html)
    # Decode common HTML entities
    for ent, ch in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                    ('&nbsp;', ' '), ('&quot;', '"'), ('&#39;', "'")]:
        text = text.replace(ent, ch)
    # Collapse whitespace
    lines = [l.rstrip() for l in text.splitlines()]
    # Remove consecutive blank lines
    result, prev_blank = [], False
    for line in lines:
        blank = not line.strip()
        if blank and prev_blank:
            continue
        result.append(line)
        prev_blank = blank
    return '\n'.join(result).strip()


def _strip_quotes(text: str) -> str:
    lines, clean = text.splitlines(), []
    for line in lines:
        s = line.strip()
        if s.startswith('>'):
            continue
        if re.match(r'^On .+wrote:$', s):
            break
        if s in ('--', '---', '________________________________'):
            break
        clean.append(line)
    return '\n'.join(clean).strip()


def _apply_sla(ticket, db) -> None:
    from app.models import SLAPolicy
    from app.services.sla import compute_sla_due
    policy = (SLAPolicy.query.filter_by(priority=ticket.priority, is_active=True,
                                        category_id=None).first())
    if policy:
        ticket.sla_policy_id = policy.id
        base = ticket.created_at or datetime.utcnow()
        ticket.first_response_due = compute_sla_due(
            base, policy.first_response_hours, policy.business_hours_only)
        ticket.resolution_due = compute_sla_due(
            base, policy.resolution_hours, policy.business_hours_only)


def _persist_attachments(db, attachments, ticket_id, comment_id, TicketAttachment) -> None:
    import uuid, os as _os
    for att in attachments:
        folder = _os.path.join('app', 'uploads', 'ticket_attachments', str(ticket_id))
        _os.makedirs(folder, exist_ok=True)
        ext    = _os.path.splitext(att['filename'])[1]
        stored = f'{uuid.uuid4().hex}{ext}'
        path   = _os.path.join(folder, stored)
        try:
            with open(path, 'wb') as f:
                f.write(att['data'])
            db.session.add(TicketAttachment(
                ticket_id=ticket_id, comment_id=comment_id,
                filename=stored, original_filename=att['filename'],
                file_size=att['size'], mime_type=att['mime_type'],
                uploaded_by='email',
            ))
        except Exception as exc:
            log.error('ms365: attachment save failed — %s', exc)


def _log(db, EmailLog, message_id, from_addr, direction, subject,
         status, error=None, ticket_id=None, snippet=None) -> None:
    try:
        db.session.add(EmailLog(
            direction=direction, ticket_id=ticket_id,
            message_id=message_id or None, from_addr=from_addr,
            subject=subject, status=status, error=error, raw_snippet=snippet,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.error('ms365: email log failed:\n%s', traceback.format_exc())
