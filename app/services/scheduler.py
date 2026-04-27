"""APScheduler background jobs.

Started once when the Flask app factory finishes.
Jobs:
  • email_poll      — every 60 s  — ingest new IMAP emails
  • sla_check       — every 15 min — warn about impending SLA breaches
  • daily_digest    — 08:00 daily  — send admin summary email  (optional)
"""
from __future__ import annotations
import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from typing import Optional

log = logging.getLogger(__name__)
_scheduler: Optional[BackgroundScheduler] = None


# ─── Jobs ─────────────────────────────────────────────────────────────────────

def _job_email_poll(app):
    try:
        import os
        if os.environ.get('MAIL_PROVIDER') == 'graph':
            from app.services.ms365 import poll_inbox
        else:
            from app.services.email_ingestion import poll_inbox
        poll_inbox(app)
    except Exception as exc:
        log.error('email_poll error: %s', exc)


def _job_sla_check(app):
    """Warn agents / admins about tickets approaching SLA breach."""
    with app.app_context():
        try:
            from datetime import datetime, timedelta
            from app.models import Ticket
            from app.services.mailer import notify_sla_warning

            warn_before = timedelta(minutes=int(os.environ.get('SLA_WARN_MINUTES', '60')))
            now = datetime.utcnow()
            warn_cutoff = now + warn_before
            admin_email = os.environ.get('ADMIN_EMAIL', '')
            base_url    = os.environ.get('APP_BASE_URL', '')

            tickets = Ticket.query.filter(
                Ticket.status.in_(Ticket.OPEN_STATUSES),
                Ticket.resolution_due.isnot(None),
                Ticket.resolution_due > now,
                Ticket.resolution_due <= warn_cutoff,
            ).all()

            for t in tickets:
                agent_email = t.assigned_to_email or ''
                notified = notify_sla_warning(t, agent_email, admin_email, base_url)
                if notified:
                    log.info('SLA warning sent for %s', t.number)
        except Exception as exc:
            log.error('sla_check error: %s', exc)


# ─── Start / Stop ─────────────────────────────────────────────────────────────

def start_scheduler(app):
    """Start background jobs. Call once from the app factory."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30},
    )

    # Email poll — every 60 seconds
    poll_interval = int(os.environ.get('MAIL_POLL_INTERVAL', '60'))
    _scheduler.add_job(
        _job_email_poll, IntervalTrigger(seconds=poll_interval),
        id='email_poll', args=[app],
    )

    # SLA warning check — every 15 minutes
    _scheduler.add_job(
        _job_sla_check, IntervalTrigger(minutes=15),
        id='sla_check', args=[app],
    )

    _scheduler.start()
    log.info('Scheduler started. Jobs: %s', [j.id for j in _scheduler.get_jobs()])


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info('Scheduler stopped.')
