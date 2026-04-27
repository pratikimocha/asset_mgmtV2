"""SLA calculation service."""
from datetime import datetime, timedelta, time as dtime

BUSINESS_START = dtime(9, 0)
BUSINESS_END = dtime(18, 0)


def compute_sla_due(start: datetime, hours: float, business_hours_only: bool = True) -> datetime:
    """Compute SLA due datetime from start time and hour budget."""
    if not business_hours_only or hours <= 0:
        return start + timedelta(hours=hours)

    remaining = int(hours * 60)  # work in minutes
    current = start

    while remaining > 0:
        wd = current.weekday()

        # Skip weekends — jump to Monday 09:00
        if wd >= 5:
            days_to_mon = 7 - wd
            current = (current + timedelta(days=days_to_mon)).replace(
                hour=9, minute=0, second=0, microsecond=0)
            continue

        # Before business hours — snap to 09:00 same day
        if current.time() < BUSINESS_START:
            current = current.replace(hour=9, minute=0, second=0, microsecond=0)

        # After business hours — move to next business day 09:00
        if current.time() >= BUSINESS_END:
            nxt = current + timedelta(days=1)
            nxt = nxt.replace(hour=9, minute=0, second=0, microsecond=0)
            if nxt.weekday() >= 5:
                nxt += timedelta(days=7 - nxt.weekday())
            current = nxt
            continue

        end_today = current.replace(hour=18, minute=0, second=0, microsecond=0)
        avail = int((end_today - current).total_seconds() / 60)

        if avail >= remaining:
            current = current + timedelta(minutes=remaining)
            remaining = 0
        else:
            remaining -= avail
            nxt = current + timedelta(days=1)
            nxt = nxt.replace(hour=9, minute=0, second=0, microsecond=0)
            if nxt.weekday() >= 5:
                nxt += timedelta(days=7 - nxt.weekday())
            current = nxt

    return current


def sla_remaining(due: datetime, status: str = 'open') -> dict:
    """Return dict with text, status ('ok'|'warning'|'breached'|'met')."""
    if not due:
        return None
    if status in ('solved', 'closed'):
        return {'text': 'Met', 'state': 'met'}

    now = datetime.utcnow()
    diff = (due - now).total_seconds()

    if diff < 0:
        secs = abs(diff)
        if secs < 3600:
            text = f"{int(secs / 60)}m overdue"
        elif secs < 86400:
            h = int(secs / 3600)
            m = int((secs % 3600) / 60)
            text = f"{h}h {m}m overdue"
        else:
            text = f"{int(secs / 86400)}d overdue"
        return {'text': text, 'state': 'breached'}

    if diff < 3600:
        text = f"{int(diff / 60)}m left"
        state = 'warning'
    elif diff < 14400:
        h = int(diff / 3600)
        m = int((diff % 3600) / 60)
        text = f"{h}h {m}m left"
        state = 'warning'
    elif diff < 86400:
        text = f"{int(diff / 3600)}h left"
        state = 'ok'
    else:
        d = int(diff / 86400)
        h = int((diff % 86400) / 3600)
        text = f"{d}d {h}h left"
        state = 'ok'

    return {'text': text, 'state': state}
