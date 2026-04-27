"""
Bulk import script for asset_mgmt_v2.
Reads bulk_upload_template.csv and populates Asset, Assignment, and Issue tables.

Usage:
    python import_assets.py
"""

import os
import sys
import csv
import re
from datetime import date, datetime

# ── Load env before anything that touches config ─────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# ── Bootstrap Flask app ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from app.extensions import db
from app.models import Asset, Assignment, Issue

CSV_PATH = os.path.join(os.path.dirname(__file__), 'bulk_upload_template.csv')
TODAY = date.today()
FALLBACK_DATE = date(2022, 2, 2)

# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_date(value: str):
    """Parse m/d/yy or m/d/yyyy → date, or return None."""
    v = value.strip()
    if not v:
        return None
    for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def normalize_status(raw: str) -> str:
    """Lowercase + strip whitespace; map display names to enum values."""
    mapping = {
        'deployed': 'deployed',
        'instock':  'instock',
        'in stock': 'instock',
        'sold':     'sold',
        'repair':   'repair',
        'retired':  'retired',
        'ordered':  'ordered',
        'received': 'received',
    }
    return mapping.get(raw.strip().lower(), raw.strip().lower())


def normalize_device(model_raw: str, manufacturer_raw: str):
    """
    Apple entries arrive with model=chip and manufacturer=device_type.
    Dell entries may have leading/trailing spaces in model.

    Returns (manufacturer, model).
    """
    mfr = manufacturer_raw.strip()
    mdl = model_raw.strip()

    # Apple: manufacturer field contains "MacBook Air", "MacBook Pro", "Macbook Pro", etc.
    if re.search(r'mac', mfr, re.IGNORECASE):
        chip = mdl          # e.g. "M1", "M2", "M4", "M2, 16GB"
        device = mfr.strip()  # e.g. "MacBook Air", "Macbook Pro"
        return 'Apple', f'{chip} {device}'

    # Dell: normalise name and strip model spaces
    if mfr.upper() == 'DELL':
        return 'Dell', mdl   # mdl already stripped above

    return mfr, mdl


# ── Issue classification ─────────────────────────────────────────────────────

# Standalone battery-only notes to skip entirely
BATTERY_ONLY_PATTERNS = re.compile(
    r'^battery\s+(good|excellent|fair|is excellent|is good|is fair)\.?$',
    re.IGNORECASE
)

SKIP_PATTERNS = [
    BATTERY_ONLY_PATTERNS,
    re.compile(r'^outside hardware damaged but working\.?$', re.IGNORECASE),
    re.compile(r'received from', re.IGNORECASE),
    re.compile(r'purchased by', re.IGNORECASE),
]

CLOSED_KEYWORDS = re.compile(
    r'\b(now replaced|now fixed|now working|now repalced|now repaired)\b',
    re.IGNORECASE
)

# High severity keywords
HIGH_SEVERITY_PATTERNS = re.compile(
    r'\b(liquid damage|motherboard|not starting|ssd failed|bios lock|display damage|display issue)\b',
    re.IGNORECASE
)

# Medium severity keywords
MEDIUM_SEVERITY_PATTERNS = re.compile(
    r'\b(battery issue|camera|touchpad|keyboard|hinges broken|screen broken|less battery)\b',
    re.IGNORECASE
)

# Keywords that indicate there IS an actual issue (vs pure info notes)
ISSUE_PRESENCE_PATTERNS = re.compile(
    r'\b(hinges|battery issue|display|motherboard|camera|liquid|ram|fan|ssd|microphone|port|keyboard|'
    r'bios|touchpad|screen broken|not starting|apple id|loose|poor battery|less battery|'
    r'damaged|issue|problem|broken|failed|lock|replaced|fixed|working|repaired)\b',
    re.IGNORECASE
)


def classify_issue(issue_text: str, status: str):
    """
    Returns one of:
        ('skip', None, None)
        ('open', severity, issue_text)
        ('closed', severity, issue_text)
    """
    text = issue_text.strip()

    if not text:
        return 'skip', None, None

    if status == 'sold':
        return 'skip', None, None

    for pat in SKIP_PATTERNS:
        if pat.search(text):
            return 'skip', None, None

    # Does the text contain any real issue marker?
    if not ISSUE_PRESENCE_PATTERNS.search(text):
        return 'skip', None, None

    # Determine open vs closed
    issue_status = 'closed' if CLOSED_KEYWORDS.search(text) else 'open'

    # Severity
    if HIGH_SEVERITY_PATTERNS.search(text):
        severity = 'high'
    elif MEDIUM_SEVERITY_PATTERNS.search(text):
        severity = 'medium'
    else:
        severity = 'low'

    return issue_status, severity, text


# ── sold_to extraction ───────────────────────────────────────────────────────

def extract_sold_to(assigned_to: str, issue_text: str) -> str:
    """Determine the buyer for a sold asset.

    Priority order:
      1. assigned_to if it is a real name (not "Sold" / "IT")
      2. "Purchased by <name>" pattern in issue_text
      3. Short name in issue_text (no issue keywords)
    """
    at = assigned_to.strip()
    text = issue_text.strip()

    # Priority 1: assigned_to is a real person name
    if at and at.lower() not in ('sold', 'it'):
        return at

    # Priority 2: "Purchased by <name>" pattern
    m = re.search(r'purchased by\s+(.+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip('.,')

    # Priority 3: short name in issue_text with no issue keywords
    if text and not ISSUE_PRESENCE_PATTERNS.search(text):
        return text

    return ''


# ── Main import logic ────────────────────────────────────────────────────────

def run_import():
    app = create_app()

    inserted       = 0
    skipped_dup    = 0
    skipped_err    = 0
    errors         = []

    with app.app_context():
        with open(CSV_PATH, newline='', encoding='utf-8-sig') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        for row_num, row in enumerate(rows, start=2):   # row 1 = header
            serial_raw = row.get('serial_number', '').strip()

            # Strip stray whitespace from the serial (e.g. "DF6WML3  ")
            serial = serial_raw.strip()

            if not serial:
                skipped_err += 1
                errors.append((row_num, serial, 'Empty serial number'))
                continue

            # ── Duplicate check ──────────────────────────────────────────────
            if Asset.query.filter_by(serial_number=serial).first():
                print(f'  [SKIP-DUP]  row {row_num} | {serial}')
                skipped_dup += 1
                continue

            try:
                # ── Raw field extraction ─────────────────────────────────────
                asset_tag     = row.get('asset_tag', '').strip()
                model_raw     = row.get('model', '').strip()
                mfr_raw       = row.get('manufacturer', '').strip()
                status_raw    = row.get('status', '').strip()
                purchase_raw  = row.get('purchase_date', '').strip()
                warranty_raw  = row.get('warranty_expiry', '').strip()
                cost_raw      = row.get('cost', '').strip()
                vendor        = row.get('vendor', '').strip()
                location      = row.get('location', '').strip()
                department    = row.get('department', '').strip()
                last_user     = row.get('last_user', '').strip()
                assigned_to   = row.get('assigned_to', '').strip()
                assigned_date_raw = row.get('assigned_date', '').strip()
                issue_text    = row.get('issue_text', '').strip()

                # ── Normalise status ─────────────────────────────────────────
                status = normalize_status(status_raw)

                # ── Normalise device / manufacturer ──────────────────────────
                manufacturer, model = normalize_device(model_raw, mfr_raw)

                # ── Category (all laptops) ───────────────────────────────────
                category = 'Laptop'

                # ── Date parsing ─────────────────────────────────────────────
                purchase_date  = parse_date(purchase_raw) or FALLBACK_DATE
                warranty_expiry = parse_date(warranty_raw)

                cost = None
                if cost_raw:
                    try:
                        cost = float(cost_raw.replace(',', ''))
                    except ValueError:
                        pass

                # ── sold_to ──────────────────────────────────────────────────
                sold_to = None
                if status == 'sold':
                    sold_to = extract_sold_to(assigned_to, issue_text) or None

                # ── Create Asset ─────────────────────────────────────────────
                asset = Asset(
                    serial_number   = serial,
                    asset_tag       = asset_tag or None,
                    model           = model,
                    manufacturer    = manufacturer,
                    category        = category,
                    status          = status,
                    purchase_date   = purchase_date,
                    warranty_expiry = warranty_expiry,
                    cost            = cost,
                    vendor          = vendor or None,
                    location        = location or None,
                    department      = department or None,
                    sold_to         = sold_to,
                )
                db.session.add(asset)
                db.session.flush()   # get asset.id before creating children

                # ── last_user → historical Assignment ────────────────────────
                # Skip if empty, or is "IT" / "Sold"
                skip_values = {'it', 'sold', ''}
                if last_user and last_user.lower() not in skip_values:
                    hist_assigned_date = purchase_date  # fallback already applied above
                    hist = Assignment(
                        asset_id       = asset.id,
                        user_name      = last_user,
                        assigned_date  = hist_assigned_date,
                        returned_at    = TODAY,          # returned today
                        notes          = 'Historical assignment imported from CSV',
                        assigned_by    = 'import_script',
                    )
                    db.session.add(hist)

                # ── assigned_to → current Assignment ─────────────────────────
                if (
                    status == 'deployed'
                    and assigned_to
                    and assigned_to.lower() not in skip_values
                ):
                    current_date = parse_date(assigned_date_raw) or TODAY
                    cur = Assignment(
                        asset_id       = asset.id,
                        user_name      = assigned_to,
                        assigned_date  = current_date,
                        returned_at    = None,           # still assigned
                        notes          = 'Imported from CSV',
                        assigned_by    = 'import_script',
                    )
                    db.session.add(cur)

                # If status is instock, sold, or assigned_to is "IT" → no active assignment.
                # (already handled by not adding one above)

                # ── Issue ─────────────────────────────────────────────────────
                issue_action, severity, clean_text = classify_issue(issue_text, status)
                if issue_action != 'skip':
                    issue_status_val = 'closed' if issue_action == 'closed' else 'open'
                    iss = Issue(
                        asset_id      = asset.id,
                        issue_text    = clean_text,
                        severity      = severity,
                        status        = issue_status_val,
                        date_reported = purchase_date,
                        reported_by   = 'import_script',
                    )
                    db.session.add(iss)

                inserted += 1

            except Exception as exc:
                db.session.rollback()
                skipped_err += 1
                errors.append((row_num, serial, str(exc)))
                print(f'  [ERROR]  row {row_num} | {serial} | {exc}')
                continue

        # ── Commit everything at once ────────────────────────────────────────
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'\nFATAL: commit failed — {exc}')
            sys.exit(1)

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print('=' * 60)
    print('  IMPORT COMPLETE')
    print('=' * 60)
    print(f'  Inserted    : {inserted}')
    print(f'  Skipped dup : {skipped_dup}')
    print(f'  Skipped err : {skipped_err}')
    if errors:
        print()
        print('  Errors:')
        for row_num, serial, msg in errors:
            print(f'    row {row_num:>4} | {serial:<25} | {msg}')
    print('=' * 60)


if __name__ == '__main__':
    run_import()
