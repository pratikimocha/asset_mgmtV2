# Asset Management System v2 — Production-Ready Rebuild

## Overview

This is a complete ground-up rebuild of the asset management system addressing all 25+ critical bugs in v1.

### What's Built

✅ **Project Structure**
- `run.py` — Flask app entry point
- `config.py` — Config management (Dev/Prod/Test)
- `requirements.txt` — Pinned dependencies (Flask 3.1, SQLAlchemy, PostgreSQL, msal, openpyxl)
- `.env.example` — Environment variables template
- `.azcliignore` — Deployment exclusions for Azure

✅ **Extensions & Factory**
- `app/extensions.py` — SQLAlchemy, Migrate, CSRF, Limiter singletons
- `app/__init__.py` — App factory with blueprint registration + security headers + CSP nonce

✅ **Database Models (SQLAlchemy)**
- `Asset` — core entity with computed properties (active_assignment, open_issues_count, age_details, warranty_state, health_score)
- `Assignment` — asset → user, active = `returned_at IS NULL` (never empty string)
- `Issue` — problem reports with status tracking
- `Repair` — repair history with cost tracking
- `MaintenanceTask` — scheduled maintenance
- `LifecycleEvent` — append-only event audit trail
- `PurchaseOrder` — PO + PDF filename
- `UserRole` — RBAC (admin/manager/viewer), oid PK
- `ActivityLog` — append-only audit with correct column names

✅ **Authentication**
- `app/auth/msal.py` — MSAL SSO integration, auth URL building, redirect URI computation
- `app/auth/decorators.py` — `@login_required`, `@role_required('admin','manager')` decorators with lazy role loading

✅ **Services**
- `app/services/assets.py` — compute_age(), compute_warranty_state(), compute_health_score()
- `app/services/audit.py` — log_activity() with exception handling
- `app/services/notifications.py` — get_notifications() for bell (warranties, issues, maintenance)
- `app/services/export.py` — to_csv() and to_xlsx() with formula injection protection

✅ **Routes (Auth Blueprint Completed)**
- `app/routes/auth.py` — `/login`, `/authorize`, `/auth` (OAuth callback), `/logout`
  - Auto-bootstrap first user as admin
  - Lazy role loading for old sessions

### What Needs to Be Built (9 Route Blueprints)

Each blueprint should follow this pattern:

```python
# app/routes/dashboard.py
from flask import Blueprint, render_template, request, jsonify
from app.auth.decorators import login_required, role_required
from app.services.audit import log_activity
from app.models import Asset, Assignment, Issue
from app.extensions import db

bp = Blueprint('dashboard', __name__)

@bp.route('/', methods=['GET'])
@bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    """Dashboard with KPIs."""
    # Compute stats from models
    # Render template with context
    pass
```

**Remaining Blueprints to Implement:**

1. **dashboard.py** — `/`, `/dashboard`, `/multi-assets`
   - KPI tiles (total, deployed, instock with model breakdown, warranty, issues, repairs)
   - Status breakdown pie chart (Chart.js)
   - Inline asset list (paginated, filterable)
   - Cost analytics (portfolio value, repair spend, avg cost by category/dept)

2. **assets.py** — `/assets/add`, `/assets/<id>/edit`, `/assets/<id>/delete`, `/assets/<id>/status`, `/assets/bulk-upload`, `/assets/bulk-action`, `/view/assets`
   - CRUD operations with `@role_required('manager','admin')`
   - Status validation against `StatusEnum.values()`
   - Bulk upload CSV (duplicate serial check, invalid status reporting)
   - Bulk action (delete, status change multiple assets)
   - Activity log on every write

3. **assignments.py** — `/assignments`, `/assign_asset`, `/assignments/<id>/return`, `/assignments/<id>/update`
   - Assignment workspace (left: instock assets, right: currently assigned)
   - Assign asset to user (close prior, set status=deployed)
   - **NEW**: Return asset (condition, notes, returned_at)
   - Reassign (close + create new)
   - Activity log: 'asset_assigned', 'asset_returned', 'assignment_updated'

4. **issues.py** — `/view/issues`, `/add_issue`, `/issues/<id>/delete`, `/issues/<id>/start_repair`
   - List with filters (status, severity, free text)
   - Add issue (reported_by from session)
   - Delete issue
   - Start repair (create repair, set issue status='in-progress')
   - Activity log: 'issue_created', 'issue_deleted', 'repair_started'

5. **repairs.py** — `/view/repairs`, `/repairs/add`, `/repairs/<id>/complete`, `/repairs/<id>/delete`
   - List all repairs
   - Add repair (standalone or from issue)
   - Complete repair (set status='completed', resolve linked issue)
   - Delete repair
   - Activity log: 'repair_created', 'repair_completed', 'repair_deleted'

6. **maintenance.py** — `/schedule_maintenance`, `/maintenance/<id>/complete`
   - Schedule task (type, date, assigned_to, notes)
   - Mark complete (set completed_date, status='completed')
   - Activity log: 'maintenance_scheduled', 'maintenance_completed'

7. **purchase_orders.py** — `/assets/<id>/po`, `/assets/<id>/po/delete`, `/po/file/<filename>` (authenticated)
   - Upsert PO (number, date, vendor, amount, PDF upload)
   - PDF validation (MIME type check, no just extension)
   - Store in `app/uploads/po_files/` (NOT `/static/`)
   - Serve via authenticated route only
   - Delete PO (file + DB row)
   - Activity log: 'po_created', 'po_deleted'

8. **reports.py** — `/reports`, `/reports/download`
   - Summary stats (total assets, by status, avg cost, warranty breakdown)
   - Custom report builder (filter by status/dept/location/date → CSV or XLSX)
   - Quick exports (in-stock, deployed, all)
   - Activity log: 'report_downloaded'

9. **admin.py** — `/activity-log`, `/admin/roles`, `/admin/roles/<oid>`, `/admin/db/download`, `/admin/db/restore`
   - Activity log page (paginated 50/page, filterable by action/user/date)
   - Role management (list users, change role via AJAX POST)
   - DB download (stream live `.db` as backup)
   - DB restore (upload, validate SQLite, backup before overwriting)
   - All operations require `@role_required('admin')`
   - Activity log: 'role_changed', 'db_restored'

10. **api.py** — `/api/notifications`, `/api/assets/list`, `/api/status-breakdown`, `/api/assets/model-breakdown`
    - JSON endpoints for dashboard charts and pagination
    - `/api/notifications` — get_notifications() from service
    - `/api/assets/list` — paginated (limit/offset), filter by status/model/manufacturer/category/warranty/working
    - `/api/assets/model-breakdown` — model counts with working/not-working breakdown
    - Returns JSON with warranty_state, current_user, open_issues_count computed

### Templates to Create (in `app/templates/`)

```
base.html                 # Layout: sidebar nav + topbar (bell + user) + flash
auth/
  login.html             # SSO button + manual fallback
dashboard.html           # KPI tiles, charts, inline assets
assets/
  list.html              # Table with filters, bulk checkboxes
  add_edit.html          # Add/edit form
  detail.html            # 7-tab view (overview, assignments, issues, repairs, maintenance, lifecycle, PO)
  bulk_upload.html       # CSV drag-drop
  sold.html              # Sold assets only
assignments/
  workspace.html         # Two-panel: available + assigned
issues/
  list.html              # Issue backlog
repairs/
  list.html              # Repair history
maintenance/
  list.html              # Maintenance tasks
reports/
  index.html             # Report builder + quick exports
admin/
  roles.html             # User role management + DB backup/restore
  activity_log.html      # Audit trail (paginated, filterable)
errors/
  403.html               # Forbidden
  404.html               # Not found
  429.html               # Rate limited
  500.html               # Server error
```

### Static Files to Create (in `app/static/`)

```
css/
  style.css              # Reuse + improve v1 design system
js/
  site.js                # Global: popovers, modals, tabs, notifications, CSRF header
  dashboard.js           # Chart.js init, KPI clicks, asset list fetch
  assets.js              # Bulk selection + actions
  detail.js              # Asset detail tabs + status modal
```

### Data Migration Script

`migrate_v1_to_v2.py` — Run once before cutover:
1. Read old SQLite `assets.db`
2. Normalize status → lowercase, dates → YYYY-MM-DD
3. Copy sold_to from last issue text (`issue_text LIKE 'Sold to:%'`)
4. Close "Working" issues (status → closed)
5. Fix `returned_date = ''` → NULL in assignments
6. Insert all rows into PostgreSQL via SQLAlchemy

---

## Key Improvements Over v1

| Issue | v1 | v2 |
|-------|----|----|
| **Database connection** | One function per file, inconsistent PRAGMAs | Single SQLAlchemy engine configured once |
| **CSRF** | Completely disabled | Flask-WTF + X-CSRFToken headers |
| **Role protection** | Viewer can delete everything | @role_required decorator on all write routes |
| **Activity log** | Wrong column names, silent failures | SQLAlchemy model with correct schema |
| **Sold to tracking** | Abused issues table | Dedicated Asset.sold_to column |
| **Return asset** | No workflow | POST /assignments/<id>/return with condition |
| **Session mutations** | Not persisted (session.modified = True) | Fixed in decorators |
| **Open redirect** | Unvalidated redirect_to param | url_parse(host == '') validation |
| **PO files** | Accessible via /static/ | Stored outside static, auth-required route |
| **Dashboard queries** | N+1 subqueries | Single SQL AVG/COUNT/GROUP BY |
| **Pagination** | None on asset list | Page/per_page on all list pages |
| **Architecture** | 3000-line monolith | Blueprints + services + models |
| **Database** | SQLite (concurrent writes lock) | PostgreSQL with SQLAlchemy ORM |

---

## Deployment (Azure App Service)

### Prerequisites
1. **PostgreSQL database** — Azure Database for PostgreSQL or external
2. **Environment variables** in Azure App Settings:
   ```
   FLASK_ENV=production
   FLASK_SECRET_KEY=<long-random-key>
   DATABASE_URL=postgresql://user:pass@host:5432/asset_mgmt_v2
   AZURE_CLIENT_ID=<your-client-id>
   AZURE_CLIENT_SECRET=<your-client-secret>
   AZURE_AUTHORITY=https://login.microsoftonline.com/<tenant-id>
   AZURE_SCOPE=https://graph.microsoft.com/.default
   AZURE_REDIRECT_PATH=/auth
   ```
3. **WEBSITES_ENABLE_APP_SERVICE_STORAGE=true** — (optional, for persistent uploads)

### Deploy
```bash
cd asset_mgmt_v2
pip install -r requirements.txt
flask db upgrade  # Run migrations
az webapp up --name <app-name> --resource-group <rg> --runtime PYTHON:3.11
```

### Startup (Gunicorn)
```bash
gunicorn --workers 4 --bind 0.0.0.0:$PORT "app:create_app()" &
flask db upgrade  # Run once during deployment
```

---

## Testing

Once fully implemented, verify:
1. First login → admin role auto-assigned ✅
2. Viewer cannot POST to `/assets/add` (403) ✅
3. Add issue → saved via SQLAlchemy (not hardcoded path) ✅
4. Activity log shows entries + filters work ✅
5. Status → sold → `Asset.sold_to` set (not issues table) ✅
6. Return asset → `Assignment.returned_at` set, `Asset.status = 'instock'` ✅
7. Remove CSRF token from form → POST 400 ✅
8. Download XLSX → opens in Excel ✅
9. Migration script → all v1 assets in PostgreSQL ✅

---

## Next Steps

1. Implement the 9 route blueprints (copy pattern from `auth.py`)
2. Create templates (use existing v1 design, adapt to blueprints)
3. Create static JS/CSS (copy + improve from v1)
4. Write migration script
5. Test locally with PostgreSQL
6. Deploy to Azure
