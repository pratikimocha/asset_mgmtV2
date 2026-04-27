# Quick Start Guide — Asset Management v2

## What You Have

A **production-ready Flask application skeleton** with:
- ✅ Secure authentication (MSAL SSO)
- ✅ Role-based access control (admin/manager/viewer)
- ✅ Proper database models (SQLAlchemy + PostgreSQL)
- ✅ Service layer (business logic separation)
- ✅ First route blueprint (auth) fully working
- ✅ Security headers, CSRF protection, rate limiting
- ✅ Complete documentation

## What You Need to Do

### Phase 1: Set Up Local Development (30 minutes)

```bash
# Clone/navigate to the new folder
cd /Users/pratikshinde/Desktop/asset_mgmt_v2

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up .env file
cp .env.example .env
# Edit .env with your values:
# - FLASK_SECRET_KEY=<generate a random secret>
# - DATABASE_URL=postgresql://user:password@localhost:5432/asset_mgmt_v2
# - AZURE_CLIENT_ID/SECRET from your Azure AD app registration

# Initialize database
flask db init     # One-time
flask db migrate -m "Initial schema"
flask db upgrade

# Test server
python run.py
# Visit http://localhost:5000 → should show "Not Found" (expected, no dashboard yet)
```

### Phase 2: Implement Route Blueprints (the main work)

Each blueprint follows this pattern. Use `app/routes/auth.py` as your template.

**Example: Dashboard Blueprint**

```python
# app/routes/dashboard.py
from flask import Blueprint, render_template, jsonify
from app.auth.decorators import login_required
from app.models import Asset, Issue
from app.services.audit import log_activity

bp = Blueprint('dashboard', __name__)

@bp.route('/', methods=['GET'])
@bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    """Main dashboard with KPIs."""
    total = Asset.query.count()
    deployed = Asset.query.filter_by(status='deployed').count()
    instock = Asset.query.filter_by(status='instock').count()
    issues = Issue.query.filter(Issue.status.in_(['open', 'in-progress'])).count()

    stats = {
        'total': total,
        'deployed': deployed,
        'instock': instock,
        'open_issues': issues,
    }

    return render_template('dashboard.html', stats=stats)
```

**Create these 9 blueprints in order:**

1. **dashboard.py** — `/`, `/dashboard` (status KPIs, charts, inline assets)
2. **assets.py** — `/assets/*` (add, edit, delete, list, bulk)
3. **assignments.py** — `/assignments/*` (assign, return, reassign)
4. **issues.py** — `/issues/*` (list, add, delete, start repair)
5. **repairs.py** — `/repairs/*` (list, add, complete)
6. **maintenance.py** — `/maintenance/*` (schedule, complete)
7. **purchase_orders.py** — `/assets/<id>/po*` (add, delete, serve PDF)
8. **reports.py** — `/reports*` (export CSV/XLSX)
9. **admin.py** — `/admin/*` (roles, activity log, DB backup)
10. **api.py** — `/api/*` (JSON endpoints for frontend)

Each blueprint **must**:
- Import `from app.extensions import db`
- Import decorators: `from app.auth.decorators import login_required, role_required`
- Use `log_activity()` on every write operation
- Validate input (don't trust form data)
- Return proper HTTP status codes

**Registration is automatic** — Flask will load any blueprint defined in `app/routes/` that's imported in `app/__init__.py`.

### Phase 3: Create Templates

All templates inherit from `base.html`. Key points:

```html
<!-- All forms must include CSRF token -->
<form method="POST" action="/assets/add">
    {{ form.hidden_tag() }}
    <!-- form fields -->
</form>

<!-- All AJAX requests must send CSRF token (site.js does this automatically) -->
<script nonce="{{ csp_nonce }}">
fetch('/api/notifications', {
    method: 'GET',
    headers: {
        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content
    }
})
</script>
```

**Template files to create** (in `app/templates/`):
- `base.html` — Layout (sidebar nav, topbar with bell, flash messages)
- `auth/login.html` — Microsoft SSO button
- Dashboard, assets, assignments, issues, repairs, maintenance, reports, admin, errors (see README.md)

Use the v1 design system from `/Users/pratikshinde/Desktop/asset_mgmt/static/css/style.css` — it's excellent.

### Phase 4: Create Static Files

Copy and adapt from v1:

```bash
cp /Users/pratikshinde/Desktop/asset_mgmt/static/css/style.css app/static/css/
cp /Users/pratikshinde/Desktop/asset_mgmt/static/js/site.js app/static/js/
# Create dashboard.js, assets.js, detail.js from v1 versions
```

Key: All AJAX requests must send the CSRF token header:

```javascript
// site.js (global helper)
fetch(url, {
    method: 'POST',
    headers: {
        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
        'Content-Type': 'application/json'
    },
    body: JSON.stringify(data)
})
```

### Phase 5: Migrate v1 Data

```bash
python migrate_v1_to_v2.py
# Reads /Users/pratikshinde/Desktop/asset_mgmt/assets.db
# Writes all assets to PostgreSQL (normalizes status, dates, fixes NULL/empty string issues)
```

### Phase 6: Test

```bash
# Locally
python run.py
# Visit http://localhost:5000/login
# Use your Azure AD account to SSO
# First user becomes admin
# Test adding assets, assigning, etc.
```

### Phase 7: Deploy

```bash
# Push to Azure
az webapp up --name <your-app-name> --resource-group <rg> --runtime PYTHON:3.11
```

---

## File Organization

```
asset_mgmt_v2/
├── app/
│   ├── routes/
│   │   ├── auth.py          ✅ DONE
│   │   ├── dashboard.py     ← START HERE
│   │   ├── assets.py
│   │   ├── assignments.py
│   │   ├── issues.py
│   │   ├── repairs.py
│   │   ├── maintenance.py
│   │   ├── purchase_orders.py
│   │   ├── reports.py
│   │   ├── admin.py
│   │   └── api.py
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── assets/
│   │   ├── assignments/
│   │   ├── issues/
│   │   ├── repairs/
│   │   ├── maintenance/
│   │   ├── reports/
│   │   ├── admin/
│   │   └── errors/
│   │
│   └── static/
│       ├── css/style.css
│       └── js/
│           ├── site.js
│           ├── dashboard.js
│           ├── assets.js
│           └── detail.js
```

---

## Common Patterns

### Adding a Route

```python
@bp.route('/assets/add', methods=['GET', 'POST'])
@role_required('manager', 'admin')  # Protect write operations
@login_required
def add_asset():
    if request.method == 'POST':
        # Validate input
        serial = request.form.get('serial_number', '').strip()
        if not serial:
            flash('Serial number is required', 'error')
            return redirect(url_for('assets.add_asset'))

        # Check for duplicates
        if Asset.query.filter_by(serial_number=serial).first():
            flash('Asset already exists', 'error')
            return redirect(url_for('assets.add_asset'))

        # Create
        asset = Asset(
            serial_number=serial,
            model=request.form.get('model'),
            # ... more fields
        )
        db.session.add(asset)
        db.session.commit()

        # Log activity
        log_activity('asset_created', 'asset', asset.id, details=f'Serial: {serial}')

        flash('Asset added successfully', 'success')
        return redirect(url_for('assets.view_assets'))

    return render_template('assets/add_edit.html')
```

### Querying with Relationships

```python
# Get asset with all related data
asset = Asset.query.get(asset_id)

# Get active assignment
active = asset.active_assignment  # Property returns Assignment where returned_at is NULL

# Get open issues
open_issues = [i for i in asset.issues if i.status in ['open', 'in-progress']]

# Get all lifecycle events
events = asset.lifecycle
```

### API Endpoints

```python
@bp.route('/api/assets/list', methods=['GET'])
@login_required
def api_assets_list():
    """JSON endpoint for dashboard asset table."""
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('filter', '')

    query = Asset.query
    if status_filter:
        query = query.filter_by(status=status_filter)

    paginated = query.paginate(page=page, per_page=50)

    return jsonify({
        'items': [{
            'id': a.id,
            'serial_number': a.serial_number,
            'model': a.model,
            'status': a.status,
            'warranty_state': a.warranty_state,
            'current_user': a.active_assignment.user_name if a.active_assignment else None,
            'open_issues': a.open_issues_count,
        } for a in paginated.items],
        'total': paginated.total,
        'pages': paginated.pages,
        'page': page,
    })
```

---

## Key Principles

✅ **Always validate user input** — never trust forms
✅ **Always log activities** — audit trail for compliance
✅ **Always check roles** — use `@role_required` decorator
✅ **Always use session values** — not form values for attribution (who made the change)
✅ **Always commit to database** — wrap in try/except
✅ **Never hardcode paths** — use `url_for()`, `request.form.get()`, etc.
✅ **Never store secrets in code** — use environment variables
✅ **Never skip CSRF checks** — Flask-WTF handles this, just include hidden_tag() in forms

---

## Debugging Tips

```bash
# Enable SQL logging
export SQLALCHEMY_ECHO=true
python run.py

# Check database state
flask shell
>>> Asset.query.count()
>>> Asset.query.first()
>>> UserRole.query.all()

# View migrations
flask db current
flask db history
flask db show
```

---

## Estimated Effort

- **Phase 1 (Setup)**: 30 min
- **Phase 2 (Routes)**: 3-4 days (9 blueprints × 4-5 hours each)
- **Phase 3 (Templates)**: 2 days (20+ templates)
- **Phase 4 (Static)**: 1 day (JS + CSS)
- **Phase 5 (Migration)**: 2-3 hours
- **Phase 6 (Testing)**: 1 day
- **Phase 7 (Deployment)**: 1 hour

**Total: 1-2 weeks** (depending on team size and parallelization)

---

## Need Help?

- Check `README.md` for full architecture
- Check `IMPLEMENTATION_SUMMARY.md` for what's built and what's next
- Check `app/routes/auth.py` for the implementation pattern
- Check `config.py` for settings and env vars
- Check model classes in `app/models/` for schema

You've got a solid foundation. Build with confidence! 🚀
