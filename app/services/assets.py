"""Asset computation services."""
from datetime import datetime, date, timedelta


def compute_age(purchase_date):
    """Compute asset age from purchase date."""
    if not purchase_date:
        return {'years': 0, 'months': 0, 'label': 'Unknown', 'state': 'unknown'}

    if isinstance(purchase_date, str):
        purchase_date = datetime.strptime(purchase_date, '%Y-%m-%d').date()

    today = date.today()
    years = today.year - purchase_date.year - (
        (today.month, today.day) < (purchase_date.month, purchase_date.day)
    )
    months = (today.month - purchase_date.month) % 12

    label = f'{years}y {months}m' if years > 0 else f'{months}m'
    state = 'fresh' if years < 1 else 'mid' if years < 3 else 'aged'

    return {'years': years, 'months': months, 'label': label, 'state': state}


def compute_warranty_state(warranty_expiry):
    """Compute warranty state: active/expiring/expired/unknown."""
    if not warranty_expiry:
        return 'unknown'

    if isinstance(warranty_expiry, str):
        warranty_expiry = datetime.strptime(warranty_expiry, '%Y-%m-%d').date()

    today = date.today()
    days_until_expiry = (warranty_expiry - today).days

    if days_until_expiry < 0:
        return 'expired'
    elif days_until_expiry <= 60:
        return 'expiring'
    else:
        return 'active'


def compute_health_score(age_years=0, open_issues=0, repair_count=0):
    """Compute health score 0-100."""
    score = 100
    score -= (age_years * 8)  # -8 per year
    score -= (open_issues * 5)  # -5 per open issue
    score -= (repair_count * 3)  # -3 per repair
    return max(5, score)  # Minimum 5
