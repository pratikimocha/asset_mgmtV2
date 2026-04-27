"""Notifications service."""
from datetime import datetime, timedelta
from app.models import Asset, Issue
from app.extensions import db


def get_notifications():
    """Get active notifications for dashboard bell."""
    notifications = {'count': 0, 'items': []}

    # Warranties expiring within 60 days
    today = datetime.utcnow().date()
    expiry_threshold = today + timedelta(days=60)

    expiring = Asset.query.filter(
        Asset.warranty_expiry <= expiry_threshold,
        Asset.warranty_expiry >= today
    ).all()

    for asset in expiring:
        notifications['items'].append({
            'type': 'warranty_expiring',
            'message': f'{asset.model} ({asset.serial_number}) warranty expires {asset.warranty_expiry}',
            'link': f'/assets/{asset.id}',
            'asset_id': asset.id
        })

    # Open high-severity issues
    critical_issues = Issue.query.filter(
        Issue.status.in_(['open', 'in-progress']),
        Issue.severity == 'high'
    ).limit(5).all()

    for issue in critical_issues:
        notifications['items'].append({
            'type': 'critical_issue',
            'message': f'High-severity issue on Asset {issue.asset.serial_number}',
            'link': f'/assets/{issue.asset.id}',
            'asset_id': issue.asset_id
        })

    # Overdue maintenance (scheduled date passed)
    from app.models import MaintenanceTask
    overdue = MaintenanceTask.query.filter(
        MaintenanceTask.status == 'scheduled',
        MaintenanceTask.scheduled_date < today
    ).count()

    if overdue > 0:
        notifications['items'].append({
            'type': 'overdue_maintenance',
            'message': f'{overdue} maintenance task(s) overdue',
            'link': '/maintenance',
            'asset_id': None
        })

    notifications['count'] = len(notifications['items'])
    return notifications
