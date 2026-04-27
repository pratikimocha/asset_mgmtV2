"""SMTP mailer — uses only Python stdlib (smtplib + email.mime).
   No paid service required. Works with Gmail App Password, Outlook, or any SMTP.

   Required env vars:
     MAIL_SERVER    e.g. smtp.gmail.com
     MAIL_PORT      e.g. 587
     MAIL_USERNAME  e.g. support@yourcompany.com
     MAIL_PASSWORD  e.g. Gmail App Password (16-char)
     MAIL_FROM      e.g. "iMocha Helpdesk <support@yourcompany.com>"
     MAIL_USE_TLS   1 (default) | 0
"""
import os
import smtplib
import logging
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, parseaddr

log = logging.getLogger(__name__)

_CFG = {
    'server':   lambda: os.environ.get('MAIL_SERVER', ''),
    'port':     lambda: int(os.environ.get('MAIL_PORT', 587)),
    'username': lambda: os.environ.get('MAIL_USERNAME', ''),
    'password': lambda: os.environ.get('MAIL_PASSWORD', ''),
    'from':     lambda: os.environ.get('MAIL_FROM', os.environ.get('MAIL_USERNAME', '')),
    'tls':      lambda: os.environ.get('MAIL_USE_TLS', '1') == '1',
    'enabled':  lambda: bool(os.environ.get('MAIL_SERVER')),
}


def _cfg(key):
    return _CFG[key]()


def send_email(to: str | list, subject: str, html_body: str,
               reply_to: str = None, message_id: str = None,
               in_reply_to: str = None) -> bool:
    """Send a single HTML email. Returns True on success, False on failure.
    Routes to Microsoft Graph when MAIL_PROVIDER=graph, otherwise uses SMTP."""
    # Microsoft 365 / Graph API path
    from app.services.ms365 import is_enabled as _graph_on, send_mail as _graph_send
    if _graph_on():
        return _graph_send(to, subject, html_body, reply_to, message_id, in_reply_to)

    if not _cfg('enabled'):
        log.debug('Mailer disabled — MAIL_SERVER not set. Would send: %s → %s', subject, to)
        return False

    recipients = [to] if isinstance(to, str) else to
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients:
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = _cfg('from')
    msg['To']      = ', '.join(recipients)
    if reply_to:
        msg['Reply-To'] = reply_to
    if message_id:
        msg['Message-ID'] = message_id
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References']  = in_reply_to

    msg.attach(MIMEText(html_body, 'html'))

    try:
        if _cfg('tls'):
            smtp = smtplib.SMTP(_cfg('server'), _cfg('port'), timeout=15)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(_cfg('server'), _cfg('port'), timeout=15)
        smtp.login(_cfg('username'), _cfg('password'))
        smtp.sendmail(_cfg('from'), recipients, msg.as_string())
        smtp.quit()
        log.info('Email sent: %s → %s', subject, recipients)
        return True
    except Exception as exc:
        log.error('Email send failed: %s\n%s', exc, traceback.format_exc())
        return False


# ─── Ticket notification templates ────────────────────────────────────────────

def _base_template(title: str, body_html: str, ticket_link: str = None) -> str:
    cta = ''
    if ticket_link:
        cta = f'''
        <div style="margin: 24px 0;">
          <a href="{ticket_link}"
             style="display:inline-block;padding:10px 22px;background:#7c3aed;color:#fff;
                    border-radius:8px;text-decoration:none;font-size:14px;font-weight:600;">
            View Ticket →
          </a>
        </div>'''
    return f'''<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f8fafc;font-family:Inter,sans-serif;">
<div style="max-width:560px;margin:32px auto;background:#fff;border-radius:12px;
            border:1px solid #e2e8f0;overflow:hidden;">
  <div style="background:#7c3aed;padding:20px 28px;">
    <span style="color:#fff;font-size:18px;font-weight:700;">iMocha Helpdesk</span>
  </div>
  <div style="padding:28px;">
    <h2 style="margin:0 0 16px;font-size:18px;color:#0f172a;">{title}</h2>
    {body_html}
    {cta}
    <p style="font-size:12px;color:#94a3b8;margin-top:24px;border-top:1px solid #f1f5f9;padding-top:16px;">
      You received this because you are a requester or agent on this ticket.<br>
      Reply to this email to update the ticket directly.
    </p>
  </div>
</div></body></html>'''


def notify_ticket_created(ticket, base_url: str = '') -> bool:
    """Send confirmation to requester when ticket is created."""
    link = f'{base_url}/helpdesk/tickets/{ticket.number}'
    body = f'''
      <p style="color:#334155;font-size:14px;line-height:1.6;">
        Hi <strong>{ticket.requester_name}</strong>,<br><br>
        Your support request has been received and a ticket has been created.
      </p>
      <div style="background:#f8fafc;border-radius:8px;padding:16px;margin:16px 0;">
        <table style="font-size:13px;color:#334155;width:100%;border-collapse:collapse;">
          <tr><td style="padding:4px 0;color:#64748b;width:140px;">Ticket #</td>
              <td style="font-weight:700;color:#7c3aed;">{ticket.number}</td></tr>
          <tr><td style="padding:4px 0;color:#64748b;">Subject</td>
              <td style="font-weight:600;">{ticket.title}</td></tr>
          <tr><td style="padding:4px 0;color:#64748b;">Priority</td>
              <td style="text-transform:capitalize;">{ticket.priority}</td></tr>
          <tr><td style="padding:4px 0;color:#64748b;">Status</td>
              <td style="text-transform:capitalize;">{ticket.status.replace("_"," ")}</td></tr>
        </table>
      </div>
      <p style="font-size:13px;color:#64748b;">
        Our team will respond as soon as possible. You can reply to this email to add more details.
      </p>'''
    return send_email(
        to=ticket.requester_email,
        subject=f'[{ticket.number}] {ticket.title}',
        html_body=_base_template(f'Ticket Received — {ticket.number}', body, link),
        message_id=f'<{ticket.number}-created@helpdesk>',
    )


def notify_ticket_reply(ticket, comment, base_url: str = '') -> bool:
    """Notify requester when agent replies (non-internal only)."""
    if comment.is_internal or comment.author_type == 'system':
        return False
    link = f'{base_url}/helpdesk/tickets/{ticket.number}'
    body = f'''
      <p style="color:#334155;font-size:14px;line-height:1.6;">
        Hi <strong>{ticket.requester_name}</strong>,<br>
        <strong>{comment.author_name}</strong> has replied to your ticket.
      </p>
      <div style="background:#f8fafc;border-left:3px solid #7c3aed;
                  padding:14px 18px;border-radius:0 8px 8px 0;margin:16px 0;">
        <p style="margin:0;font-size:13px;color:#334155;white-space:pre-wrap;">{comment.body[:600]}{'...' if len(comment.body)>600 else ''}</p>
      </div>'''
    return send_email(
        to=ticket.requester_email,
        subject=f'Re: [{ticket.number}] {ticket.title}',
        html_body=_base_template(f'New Reply on {ticket.number}', body, link),
        in_reply_to=ticket.email_message_id,
    )


def notify_ticket_solved(ticket, base_url: str = '') -> bool:
    """Notify requester when ticket is solved."""
    link = f'{base_url}/helpdesk/tickets/{ticket.number}'
    body = f'''
      <p style="color:#334155;font-size:14px;line-height:1.6;">
        Hi <strong>{ticket.requester_name}</strong>,<br>
        Your ticket <strong>{ticket.number}</strong> has been marked as <strong>Solved</strong>.
      </p>
      <p style="font-size:13px;color:#64748b;">
        If you still need help, simply reply to this email and we'll reopen the ticket.
      </p>'''
    return send_email(
        to=ticket.requester_email,
        subject=f'[{ticket.number}] Ticket Solved — {ticket.title}',
        html_body=_base_template(f'Ticket Solved ✓', body, link),
    )


def notify_sla_warning(ticket, agent_email: str, admin_email: str, base_url: str = '') -> bool:
    """Warn agent + admin about impending SLA breach."""
    link = f'{base_url}/helpdesk/tickets/{ticket.number}'
    body = f'''
      <p style="color:#334155;font-size:14px;line-height:1.6;">
        ⚠️ Ticket <strong>{ticket.number}</strong> is approaching its SLA deadline.
      </p>
      <div style="background:#fef2f2;border-radius:8px;padding:14px 18px;margin:12px 0;">
        <p style="margin:0;font-size:13px;color:#991b1b;">
          <strong>{ticket.title}</strong><br>
          Priority: {ticket.priority.upper()} · Assigned to: {ticket.assigned_to or 'Unassigned'}
        </p>
      </div>
      <p style="font-size:13px;color:#64748b;">Please respond immediately to avoid an SLA breach.</p>'''
    to_list = [e for e in [agent_email, admin_email] if e]
    return send_email(
        to=to_list,
        subject=f'⚠️ SLA Warning [{ticket.number}] {ticket.title}',
        html_body=_base_template('SLA Breach Warning', body, link),
    )


def notify_assigned(ticket, agent_email: str, base_url: str = '') -> bool:
    """Notify an agent that a ticket has been assigned to them."""
    if not agent_email:
        return False
    link = f'{base_url}/helpdesk/tickets/{ticket.number}'
    body = f'''
      <p style="color:#334155;font-size:14px;line-height:1.6;">
        A ticket has been assigned to you.
      </p>
      <div style="background:#f8fafc;border-radius:8px;padding:16px;margin:16px 0;">
        <table style="font-size:13px;color:#334155;width:100%;border-collapse:collapse;">
          <tr><td style="padding:4px 0;color:#64748b;width:130px;">Ticket #</td>
              <td style="font-weight:700;color:#7c3aed;">{ticket.number}</td></tr>
          <tr><td style="padding:4px 0;color:#64748b;">Subject</td>
              <td>{ticket.title}</td></tr>
          <tr><td style="padding:4px 0;color:#64748b;">Priority</td>
              <td style="text-transform:capitalize;">{ticket.priority}</td></tr>
          <tr><td style="padding:4px 0;color:#64748b;">Requester</td>
              <td>{ticket.requester_name} &lt;{ticket.requester_email}&gt;</td></tr>
        </table>
      </div>'''
    return send_email(
        to=agent_email,
        subject=f'[{ticket.number}] Assigned to you — {ticket.title}',
        html_body=_base_template('Ticket Assigned to You', body, link),
    )
