"""Helpdesk platform routes."""
import re
import os
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session, send_from_directory
from datetime import datetime
from sqlalchemy import func, case
from app.auth.decorators import login_required, role_required
from app.models import Category, SLAPolicy, Ticket, TicketComment, Asset
from app.models import EmailLog, CannedResponse, TicketAttachment
from app.services.audit import log_activity
from app.services.sla import compute_sla_due, sla_remaining
from app.extensions import db

bp = Blueprint('helpdesk', __name__, url_prefix='/helpdesk')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower().strip()).strip('-')


def _next_ticket_number():
    """Generate next HD-XXXX number safely for both SQLite and PostgreSQL.

    SQLite doesn't allow ALTER COLUMN to drop NOT NULL, so we can't use the
    flush-then-assign pattern.  Instead we predict the next ID by querying
    MAX(id)+1 and assign the number before the INSERT.
    """
    from sqlalchemy import text as _t
    row = db.session.execute(_t('SELECT COALESCE(MAX(id), 0) + 1 FROM tickets')).scalar()
    return f'HD-{row:04d}'


def _apply_sla(ticket):
    """Find best matching SLA policy and compute due datetimes.
    Lookup order: sub-category → category → global default."""
    policy = None
    if ticket.subcategory_id:
        policy = SLAPolicy.query.filter_by(
            category_id=ticket.subcategory_id, priority=ticket.priority, is_active=True
        ).first()
    if not policy and ticket.category_id:
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
        ticket.first_response_due = compute_sla_due(base, policy.first_response_hours, policy.business_hours_only)
        ticket.resolution_due = compute_sla_due(base, policy.resolution_hours, policy.business_hours_only)


def _system_event(ticket, message):
    """Add a system event entry in the conversation thread."""
    ev = TicketComment(
        ticket_id=ticket.id,
        body=message,
        author_name='System',
        author_type='system',
        is_internal=True,
    )
    db.session.add(ev)


def _current_user():
    u = session.get('user', {})
    return {'name': u.get('name', 'Agent'), 'email': u.get('email', ''), 'role': u.get('role', 'viewer')}


# ─── Dashboard ────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def dashboard():
    cat_count = Category.query.filter_by(is_active=True).count()
    sla_count = SLAPolicy.query.filter_by(is_active=True).count()

    open_q = Ticket.query.filter(Ticket.status.in_(Ticket.OPEN_STATUSES))
    stats = {
        'total_open': open_q.count(),
        'new': Ticket.query.filter_by(status='new').count(),
        'open': Ticket.query.filter_by(status='open').count(),
        'pending': Ticket.query.filter_by(status='pending').count(),
        'on_hold': Ticket.query.filter_by(status='on_hold').count(),
        'solved_today': Ticket.query.filter(
            Ticket.status == 'solved',
            func.date(Ticket.solved_at) == datetime.utcnow().date()
        ).count(),
        'unassigned': Ticket.query.filter(
            Ticket.assigned_to.is_(None),
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        ).count(),
        'critical': Ticket.query.filter_by(priority='critical', status='new').count() +
                    Ticket.query.filter_by(priority='critical', status='open').count(),
        'sla_breached': Ticket.query.filter(
            Ticket.resolution_due < datetime.utcnow(),
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        ).count(),
    }

    # Category breakdown of open tickets (id included so template can link to filtered queue)
    cat_breakdown = db.session.query(
        Category.id, Category.name, Category.color, func.count(Ticket.id)
    ).outerjoin(Ticket, (Ticket.category_id == Category.id) & Ticket.status.in_(Ticket.OPEN_STATUSES)
    ).filter(Category.is_active == True, Category.parent_id == None
    ).group_by(Category.id).order_by(func.count(Ticket.id).desc()).limit(8).all()

    # Priority breakdown of open tickets
    priority_breakdown = {
        p: Ticket.query.filter(Ticket.priority == p, Ticket.status.in_(Ticket.OPEN_STATUSES)).count()
        for p in ['critical', 'high', 'medium', 'low']
    }

    recent = Ticket.query.order_by(Ticket.created_at.desc()).limit(5).all()

    return render_template('helpdesk/dashboard.html',
                           cat_count=cat_count, sla_count=sla_count,
                           stats=stats, cat_breakdown=cat_breakdown,
                           priority_breakdown=priority_breakdown, recent=recent)


# ─── Ticket Queue ─────────────────────────────────────────────────────────────

@bp.route('/tickets')
@login_required
def ticket_queue():
    view = request.args.get('view', 'unsolved')
    priority_f = request.args.get('priority', '').strip()
    category_f = request.args.get('category', type=int)
    q = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'updated')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 25

    user = _current_user()
    query = Ticket.query

    # View filters
    if view == 'unsolved':
        query = query.filter(Ticket.status.in_(Ticket.OPEN_STATUSES))
    elif view == 'mine':
        query = query.filter(
            Ticket.assigned_to_email == user['email'],
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        )
    elif view == 'unassigned':
        query = query.filter(
            Ticket.assigned_to.is_(None),
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        )
    elif view == 'solved':
        query = query.filter(Ticket.status.in_(['solved', 'closed']))
    elif view == 'breached':
        query = query.filter(
            Ticket.resolution_due < datetime.utcnow(),
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        )
    # view == 'all' → no filter

    if priority_f:
        query = query.filter(Ticket.priority == priority_f)
    if category_f:
        query = query.filter(Ticket.category_id == category_f)
    if q:
        query = query.filter(
            Ticket.title.ilike(f'%{q}%') |
            Ticket.number.ilike(f'%{q}%') |
            Ticket.requester_name.ilike(f'%{q}%') |
            Ticket.requester_email.ilike(f'%{q}%')
        )

    # Sort
    if sort == 'priority':
        # Critical → High → Medium → Low, then by updated
        from sqlalchemy import case as sa_case
        priority_rank = sa_case(
            (Ticket.priority == 'critical', 0), (Ticket.priority == 'high', 1),
            (Ticket.priority == 'medium', 2), (Ticket.priority == 'low', 3), else_=4
        )
        query = query.order_by(priority_rank, Ticket.updated_at.desc())
    elif sort == 'created':
        query = query.order_by(Ticket.created_at.desc())
    elif sort == 'sla':
        query = query.order_by(Ticket.resolution_due.asc().nullslast())
    else:  # 'updated'
        query = query.order_by(Ticket.updated_at.desc())

    total = query.count()
    tickets = query.offset((page - 1) * per_page).limit(per_page).all()

    # Sidebar view counts
    counts = {
        'unsolved': Ticket.query.filter(Ticket.status.in_(Ticket.OPEN_STATUSES)).count(),
        'mine': Ticket.query.filter(
            Ticket.assigned_to_email == user['email'],
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        ).count(),
        'unassigned': Ticket.query.filter(
            Ticket.assigned_to.is_(None),
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        ).count(),
        'breached': Ticket.query.filter(
            Ticket.resolution_due < datetime.utcnow(),
            Ticket.status.in_(Ticket.OPEN_STATUSES)
        ).count(),
    }

    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()

    # Attach sla_remaining to each ticket
    for t in tickets:
        t._sla = sla_remaining(t.resolution_due, t.status) if t.resolution_due else None

    return render_template('helpdesk/tickets/queue.html',
                           tickets=tickets, total=total, page=page,
                           pages=max(1, (total + per_page - 1) // per_page),
                           view=view, priority_f=priority_f, category_f=category_f,
                           q=q, sort=sort, counts=counts, categories=categories)


# ─── New Ticket ───────────────────────────────────────────────────────────────

def _categories_json(cats):
    """Build JSON-serialisable tree for client-side category → sub-category cascade."""
    import json
    tree = []
    for c in cats:
        if c.parent_id is None:  # top-level
            children = [{'id': ch.id, 'name': ch.name, 'icon': ch.icon or ''}
                        for ch in c.children.filter_by(is_active=True).all()]
            tree.append({'id': c.id, 'name': c.name, 'icon': c.icon or '',
                         'color': c.color or '#6b7280', 'children': children})
    return json.dumps(tree)


@bp.route('/tickets/new', methods=['GET', 'POST'])
@login_required
def ticket_new():
    categories = Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        requester_name = request.form.get('requester_name', '').strip()
        requester_email = request.form.get('requester_email', '').strip()

        errors = []
        if not title:
            errors.append('Subject is required.')
        if not requester_name:
            errors.append('Requester name is required.')
        if not requester_email:
            errors.append('Requester email is required.')
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('helpdesk/tickets/new.html',
                                   categories=categories,
                                   form=request.form,
                                   categories_json=_categories_json(categories))

        cat_id    = request.form.get('category_id') or None
        subcat_id = request.form.get('subcategory_id') or None
        asset_id  = request.form.get('asset_id') or None
        agent_name  = request.form.get('assigned_to', '').strip()
        agent_email = request.form.get('assigned_to_email', '').strip()

        # Auto-assign to current agent if requested
        if request.form.get('assign_to_me') == '1':
            u = _current_user()
            agent_name = u['name']
            agent_email = u['email']

        # Optional backdated creation date
        ticket_date_str = request.form.get('ticket_date', '').strip()
        ticket_created_at = datetime.utcnow()
        if ticket_date_str:
            try:
                from datetime import date as _date
                parsed = datetime.strptime(ticket_date_str, '%Y-%m-%d')
                if parsed.date() <= _date.today():
                    ticket_created_at = parsed.replace(
                        hour=datetime.utcnow().hour,
                        minute=datetime.utcnow().minute,
                    )
            except ValueError:
                pass

        ticket = Ticket(
            number=_next_ticket_number(),
            title=title,
            description=request.form.get('description', '').strip(),
            status='new',
            priority=request.form.get('priority', 'medium'),
            category_id=int(cat_id) if cat_id else None,
            subcategory_id=int(subcat_id) if subcat_id else None,
            requester_name=requester_name,
            requester_email=requester_email.lower(),
            requester_phone=request.form.get('requester_phone', '').strip(),
            assigned_to=agent_name or None,
            assigned_to_email=agent_email.lower() if agent_email else None,
            asset_id=int(asset_id) if asset_id else None,
            source=request.form.get('source', 'manual'),
            tags=request.form.get('tags', '').strip(),
            created_at=ticket_created_at,
        )
        db.session.add(ticket)
        db.session.flush()
        _apply_sla(ticket)
        db.session.commit()

        # Auto-open if description present
        if ticket.description and ticket.status == 'new':
            ticket.status = 'open'

        # System event
        u = _current_user()
        _system_event(ticket, f'Ticket created by {u["name"]} via {ticket.source}.')
        if ticket.assigned_to:
            _system_event(ticket, f'Assigned to {ticket.assigned_to}.')
        db.session.commit()

        log_activity('ticket_created', 'ticket', ticket.id, f'Created {ticket.number}: {title}')
        flash(f'Ticket {ticket.number} created successfully.', 'success')
        return redirect(url_for('helpdesk.ticket_detail', number=ticket.number))

    user = _current_user()
    return render_template('helpdesk/tickets/new.html',
                           categories=categories, agent=user, form={},
                           categories_json=_categories_json(categories))


# ─── Ticket Detail ────────────────────────────────────────────────────────────

@bp.route('/tickets/<string:number>')
@login_required
def ticket_detail(number):
    ticket = Ticket.query.filter_by(number=number).first_or_404()
    categories = Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()

    sla_res = sla_remaining(ticket.resolution_due, ticket.status)
    sla_fr = sla_remaining(ticket.first_response_due, ticket.status) if not ticket.first_responded_at else None

    return render_template('helpdesk/tickets/detail.html',
                           ticket=ticket, categories=categories,
                           sla_res=sla_res, sla_fr=sla_fr,
                           agent=_current_user(),
                           categories_json=_categories_json(categories))


# ─── Reply ────────────────────────────────────────────────────────────────────

@bp.route('/tickets/<string:number>/reply', methods=['POST'])
@login_required
def ticket_reply(number):
    ticket = Ticket.query.filter_by(number=number).first_or_404()
    body = request.form.get('body', '').strip()
    if not body:
        flash('Reply cannot be empty.', 'error')
        return redirect(url_for('helpdesk.ticket_detail', number=number))

    u = _current_user()
    is_internal = request.form.get('is_internal') == '1'

    comment = TicketComment(
        ticket_id=ticket.id,
        body=body,
        author_name=u['name'],
        author_email=u['email'],
        author_type='agent',
        is_internal=is_internal,
    )
    db.session.add(comment)

    # Mark first response
    if not is_internal and not ticket.first_responded_at:
        ticket.first_responded_at = datetime.utcnow()

    # Handle submit action
    submit_as = request.form.get('submit_as', '')
    if submit_as in Ticket.STATUS_CHOICES and submit_as != ticket.status:
        old = ticket.status
        ticket.status = submit_as
        if submit_as == 'solved' and not ticket.solved_at:
            ticket.solved_at = datetime.utcnow()
        elif submit_as == 'closed' and not ticket.closed_at:
            ticket.closed_at = datetime.utcnow()
        _system_event(ticket, f'Status changed: {old.replace("_", " ").title()} → {submit_as.replace("_", " ").title()}')
    elif ticket.status == 'new':
        ticket.status = 'open'

    db.session.commit()
    return redirect(url_for('helpdesk.ticket_detail', number=number) + '#thread-bottom')


# ─── Delete Ticket ────────────────────────────────────────────────────────────

@bp.route('/tickets/<string:number>/delete', methods=['POST'])
@role_required('admin', 'manager')
def ticket_delete(number):
    ticket = Ticket.query.filter_by(number=number).first_or_404()
    title = ticket.title
    # Explicitly delete attachments first — ticket_id is NOT NULL so SQLAlchemy
    # cannot NULL it out, and SQLite doesn't enforce ondelete='CASCADE' by default.
    TicketAttachment.query.filter_by(ticket_id=ticket.id).delete()
    db.session.delete(ticket)
    db.session.commit()
    log_activity('ticket_deleted', 'ticket', None, f'Deleted ticket {number}: {title}')
    if request.headers.get('X-CSRFToken'):
        return jsonify({'ok': True})
    flash(f'Ticket {number} has been deleted.', 'success')
    return redirect(url_for('helpdesk.ticket_queue'))


# ─── Ticket Update (inline property changes) ─────────────────────────────────

@bp.route('/tickets/<string:number>/update', methods=['POST'])
@login_required
def ticket_update(number):
    ticket = Ticket.query.filter_by(number=number).first_or_404()
    field = request.form.get('field', '')
    value = request.form.get('value', '').strip()
    u = _current_user()

    if field == 'status' and value in Ticket.STATUS_CHOICES:
        old = ticket.status
        ticket.status = value
        if value == 'solved' and not ticket.solved_at:
            ticket.solved_at = datetime.utcnow()
        elif value == 'closed' and not ticket.closed_at:
            ticket.closed_at = datetime.utcnow()
        if old != value:
            _system_event(ticket, f'Status changed: {old.replace("_", " ").title()} → {value.replace("_", " ").title()}')

    elif field == 'priority' and value in Ticket.PRIORITY_CHOICES:
        old = ticket.priority
        ticket.priority = value
        _apply_sla(ticket)
        if old != value:
            _system_event(ticket, f'Priority changed: {old.title()} → {value.title()}')

    elif field == 'assigned_to':
        old = ticket.assigned_to or 'Unassigned'
        ticket.assigned_to = value or None
        ticket.assigned_to_email = request.form.get('email', '').strip().lower() or None
        if ticket.status == 'new':
            ticket.status = 'open'
        _system_event(ticket, f'Assigned to {value or "Unassigned"} by {u["name"]}.')

    elif field == 'category_id':
        ticket.category_id = int(value) if value else None
        ticket.subcategory_id = None  # reset sub-category when parent changes
        _apply_sla(ticket)

    elif field == 'subcategory_id':
        ticket.subcategory_id = int(value) if value else None

    elif field == 'tags':
        ticket.tags = value

    elif field == 'asset_id':
        ticket.asset_id = int(value) if value else None
        if value:
            asset = Asset.query.get(int(value))
            if asset:
                _system_event(ticket, f'Linked to asset {asset.serial_number} ({asset.model or ""}).')

    db.session.commit()
    # AJAX (fetch) requests send X-CSRFToken header; regular form POSTs use csrf_token field
    if request.headers.get('X-CSRFToken'):
        return jsonify({'ok': True, 'number': ticket.number})
    return redirect(url_for('helpdesk.ticket_detail', number=ticket.number))


# ─── Category Management ──────────────────────────────────────────────────────

@bp.route('/admin/categories')
@role_required('admin')
def categories():
    # Top-level (parent) categories only — children are loaded via relationship
    all_cats = Category.query.order_by(Category.sort_order, Category.name).all()
    parent_cats = [c for c in all_cats if c.parent_id is None]
    # Live ticket counts per category (category_id or subcategory_id)
    open_counts = dict(db.session.query(
        Ticket.category_id, func.count(Ticket.id)
    ).filter(Ticket.status.in_(Ticket.OPEN_STATUSES)).group_by(Ticket.category_id).all())
    total_counts = dict(db.session.query(
        Ticket.category_id, func.count(Ticket.id)
    ).group_by(Ticket.category_id).all())
    return render_template('helpdesk/admin/categories.html',
                           categories=parent_cats, all_cats=all_cats,
                           open_counts=open_counts, total_counts=total_counts)


@bp.route('/admin/categories/add', methods=['POST'])
@role_required('admin')
def categories_add():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Category name is required.', 'error')
        return redirect(url_for('helpdesk.categories'))
    # Check for duplicate name
    if Category.query.filter(Category.name.ilike(name)).first():
        flash(f"A category named '{name}' already exists.", 'error')
        return redirect(url_for('helpdesk.categories'))
    slug = _slugify(name)
    # Make slug unique if duplicate exists
    if Category.query.filter_by(slug=slug).first():
        base = slug
        i = 2
        while Category.query.filter_by(slug=f'{base}-{i}').first():
            i += 1
        slug = f'{base}-{i}'
    parent_id = request.form.get('parent_id') or None
    cat = Category(
        name=name, slug=slug,
        description=request.form.get('description', '').strip(),
        type=request.form.get('type', 'ticket'),
        color=request.form.get('color', '#6b7280'),
        icon=request.form.get('icon', '').strip(),
        sort_order=int(request.form.get('sort_order', 0) or 0),
        parent_id=int(parent_id) if parent_id else None,
        is_active=request.form.get('is_active_add') == '1',
    )
    db.session.add(cat)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash(f"Failed to create category '{name}'. Name may already exist.", 'error')
        return redirect(url_for('helpdesk.categories'))
    log_activity('category_created', 'category', cat.id, f'Created category: {name}')
    flash(f"Category '{name}' created.", 'success')
    return redirect(url_for('helpdesk.categories'))


@bp.route('/admin/categories/<int:cat_id>/edit', methods=['POST'])
@role_required('admin')
def categories_edit(cat_id):
    cat = Category.query.get_or_404(cat_id)
    name = request.form.get('name', '').strip()
    if name and name != cat.name:
        # Check for duplicate name (excluding self)
        dup = Category.query.filter(Category.name.ilike(name), Category.id != cat_id).first()
        if dup:
            return jsonify({'ok': False, 'error': f"A category named '{name}' already exists."})
        cat.name = name
        new_slug = _slugify(name)
        existing = Category.query.filter_by(slug=new_slug).first()
        if not existing or existing.id == cat.id:
            cat.slug = new_slug
    elif name:
        cat.name = name
    cat.description = request.form.get('description', cat.description or '').strip()
    cat.type = request.form.get('type', cat.type)
    cat.color = request.form.get('color', cat.color)
    cat.icon = request.form.get('icon', cat.icon or '').strip()
    cat.sort_order = int(request.form.get('sort_order', cat.sort_order) or 0)
    cat.is_active = request.form.get('is_active') == '1'
    parent_id = request.form.get('parent_id') or None
    # Prevent a category from being its own parent or circular reference
    if parent_id and int(parent_id) != cat.id:
        cat.parent_id = int(parent_id)
    elif not parent_id:
        cat.parent_id = None
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'ok': False, 'error': 'Failed to save. Name may already exist.'})
    log_activity('category_updated', 'category', cat.id, f'Updated: {cat.name}')
    return jsonify({'ok': True})


@bp.route('/admin/categories/<int:cat_id>/delete', methods=['POST'])
@role_required('admin')
def categories_delete(cat_id):
    cat = Category.query.get_or_404(cat_id)
    name = cat.name
    db.session.delete(cat)
    db.session.commit()
    log_activity('category_deleted', 'category', cat_id, f'Deleted: {name}')
    return jsonify({'ok': True})


# ─── SLA Management ───────────────────────────────────────────────────────────

@bp.route('/admin/sla')
@role_required('admin')
def sla():
    priorities = ['critical', 'high', 'medium', 'low']
    policies = SLAPolicy.query.order_by(SLAPolicy.category_id, SLAPolicy.priority).all()
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()

    # Build coverage matrix: {cat_key: {priority: policy}}
    # cat_key = None (global) or category.id
    matrix = {None: {p: None for p in priorities}}
    for cat in categories:
        matrix[cat.id] = {p: None for p in priorities}
    for pol in policies:
        key = pol.category_id  # None = global
        if key not in matrix:
            matrix[key] = {p: None for p in priorities}
        matrix[key][pol.priority] = pol

    # Coverage stats
    total_cells = len(matrix) * 4
    filled_cells = sum(1 for row in matrix.values() for v in row.values() if v)

    return render_template('helpdesk/admin/sla.html',
                           policies=policies, categories=categories,
                           matrix=matrix, priorities=priorities,
                           total_cells=total_cells, filled_cells=filled_cells,
                           priority_order=SLAPolicy.PRIORITY_ORDER)


@bp.route('/admin/sla/quick-setup', methods=['POST'])
@role_required('admin')
def sla_quick_setup():
    """Create all 4 priority tiers for a category using a preset template."""
    cat_id = request.form.get('category_id') or None
    if cat_id:
        cat_id = int(cat_id)
    template = request.form.get('template', 'standard')

    TEMPLATES = {
        'strict':   {'critical': (0.5, 2), 'high': (1, 4), 'medium': (4, 8), 'low': (8, 24)},
        'standard': {'critical': (1, 4), 'high': (4, 8), 'medium': (8, 24), 'low': (24, 72)},
        'relaxed':  {'critical': (2, 8), 'high': (8, 24), 'medium': (24, 72), 'low': (72, 168)},
    }
    timings = TEMPLATES.get(template, TEMPLATES['standard'])
    biz = request.form.get('business_hours_only') == '1'

    cat = Category.query.get(cat_id) if cat_id else None
    cat_label = cat.name if cat else 'Global'
    created = 0

    for priority, (fr, res) in timings.items():
        existing = SLAPolicy.query.filter_by(category_id=cat_id, priority=priority).first()
        if existing:
            continue  # skip, don't overwrite
        policy = SLAPolicy(
            name=f'{cat_label} – {priority.title()}',
            category_id=cat_id,
            priority=priority,
            first_response_hours=fr,
            resolution_hours=res,
            business_hours_only=biz,
        )
        db.session.add(policy)
        created += 1

    db.session.commit()
    log_activity('sla_quick_setup', 'sla_policy', None,
                 f'Quick setup ({template}) for {cat_label}: {created} policies created')
    flash(f'Created {created} SLA polic{"ies" if created != 1 else "y"} for {cat_label} ({template} template).', 'success')
    return redirect(url_for('helpdesk.sla'))


@bp.route('/admin/sla/add', methods=['POST'])
@role_required('admin')
def sla_add():
    priority = request.form.get('priority', '').strip()
    if priority not in ('low', 'medium', 'high', 'critical'):
        flash('Invalid priority.', 'error')
        return redirect(url_for('helpdesk.sla'))
    cat_id = request.form.get('category_id') or None
    policy = SLAPolicy(
        name=request.form.get('name', '').strip() or f'SLA – {priority.title()}',
        category_id=int(cat_id) if cat_id else None,
        priority=priority,
        first_response_hours=float(request.form.get('first_response_hours', 8) or 8),
        resolution_hours=float(request.form.get('resolution_hours', 48) or 48),
        business_hours_only=request.form.get('business_hours_only') == '1',
    )
    db.session.add(policy)
    db.session.commit()
    log_activity('sla_created', 'sla_policy', policy.id, f'Created SLA: {policy.name}')
    flash(f"SLA policy '{policy.name}' created.", 'success')
    return redirect(url_for('helpdesk.sla'))


@bp.route('/admin/sla/<int:policy_id>/edit', methods=['POST'])
@role_required('admin')
def sla_edit(policy_id):
    p = SLAPolicy.query.get_or_404(policy_id)
    p.name = request.form.get('name', p.name).strip()
    cat_id = request.form.get('category_id') or None
    p.category_id = int(cat_id) if cat_id else None
    priority = request.form.get('priority', p.priority)
    if priority in ('low', 'medium', 'high', 'critical'):
        p.priority = priority
    p.first_response_hours = float(request.form.get('first_response_hours', p.first_response_hours) or 8)
    p.resolution_hours = float(request.form.get('resolution_hours', p.resolution_hours) or 48)
    p.business_hours_only = request.form.get('business_hours_only') == '1'
    p.is_active = request.form.get('is_active') == '1'
    db.session.commit()
    log_activity('sla_updated', 'sla_policy', p.id, f'Updated SLA: {p.name}')
    return jsonify({'ok': True})


@bp.route('/admin/sla/<int:policy_id>/delete', methods=['POST'])
@role_required('admin')
def sla_delete(policy_id):
    p = SLAPolicy.query.get_or_404(policy_id)
    name = p.name
    db.session.delete(p)
    db.session.commit()
    log_activity('sla_deleted', 'sla_policy', policy_id, f'Deleted SLA: {name}')
    return jsonify({'ok': True})


# ─── Reports ──────────────────────────────────────────────────────────────────

@bp.route('/reports')
@login_required
def reports():
    from datetime import timedelta
    from collections import Counter, defaultdict
    import statistics

    now = datetime.utcnow()
    priorities = ['critical', 'high', 'medium', 'low']
    statuses   = ['new', 'open', 'pending', 'on_hold', 'solved', 'closed']

    # ── Date range (default: last 30 days) ────────────────────────────────────
    date_from_str = request.args.get('date_from', '').strip()
    date_to_str   = request.args.get('date_to', '').strip()
    try:
        date_to = datetime.strptime(date_to_str, '%Y-%m-%d') + timedelta(days=1) if date_to_str else now
    except ValueError:
        date_to = now
    try:
        date_from = datetime.strptime(date_from_str, '%Y-%m-%d') if date_from_str else date_to - timedelta(days=30)
    except ValueError:
        date_from = date_to - timedelta(days=30)

    period_days = max((date_to - date_from).days, 1)
    prev_to     = date_from
    prev_from   = date_from - timedelta(days=period_days)

    # ── Ticket sets ───────────────────────────────────────────────────────────
    period_tickets = Ticket.query.filter(
        Ticket.created_at >= date_from, Ticket.created_at < date_to
    ).all()

    solved_period = Ticket.query.filter(
        Ticket.status.in_(['solved', 'closed']),
        Ticket.solved_at >= date_from,
        Ticket.solved_at < date_to,
        Ticket.solved_at.isnot(None)
    ).all()

    prev_created = Ticket.query.filter(
        Ticket.created_at >= prev_from, Ticket.created_at < prev_to
    ).count()

    prev_solved = Ticket.query.filter(
        Ticket.status.in_(['solved', 'closed']),
        Ticket.solved_at >= prev_from,
        Ticket.solved_at < prev_to,
        Ticket.solved_at.isnot(None)
    ).all()

    # ── Volume chart ─────────────────────────────────────────────────────────
    daily_map, solved_day_map = {}, {}
    for t in period_tickets:
        d = str(t.created_at.date())
        daily_map[d] = daily_map.get(d, 0) + 1
    for t in solved_period:
        d = str(t.solved_at.date())
        solved_day_map[d] = solved_day_map.get(d, 0) + 1
    daily_labels = sorted(daily_map.keys())
    daily_counts  = [daily_map[d] for d in daily_labels]
    solved_counts = [solved_day_map.get(d, 0) for d in daily_labels]

    # ── Priority / Status counts (current period) ─────────────────────────────
    priority_counts = {p: sum(1 for t in period_tickets if t.priority == p) for p in priorities}
    status_counts   = {s: Ticket.query.filter_by(status=s).count() for s in statuses}

    # ── Resolution SLA ────────────────────────────────────────────────────────
    sla_met      = sum(1 for t in solved_period if t.resolution_due and t.solved_at <= t.resolution_due)
    sla_missed   = sum(1 for t in solved_period if t.resolution_due and t.solved_at > t.resolution_due)
    sla_no_policy = sum(1 for t in solved_period if not t.resolution_due)

    prev_sla_met   = sum(1 for t in prev_solved if t.resolution_due and t.solved_at <= t.resolution_due)
    prev_sla_total = sum(1 for t in prev_solved if t.resolution_due)
    prev_met_pct   = int(prev_sla_met / prev_sla_total * 100) if prev_sla_total else None
    curr_sla_total = sla_met + sla_missed
    curr_met_pct   = int(sla_met / curr_sla_total * 100) if curr_sla_total else None

    # ── First response SLA ────────────────────────────────────────────────────
    fr_met     = sum(1 for t in solved_period if t.first_response_due and t.first_responded_at and t.first_responded_at <= t.first_response_due)
    fr_missed  = sum(1 for t in solved_period if t.first_response_due and t.first_responded_at and t.first_responded_at > t.first_response_due)
    fr_no_data = sum(1 for t in solved_period if not t.first_responded_at or not t.first_response_due)
    fr_total   = fr_met + fr_missed
    fr_pct     = int(fr_met / fr_total * 100) if fr_total else None

    # ── Resolution time stats ─────────────────────────────────────────────────
    durations = [(t.solved_at - t.created_at).total_seconds() / 3600
                 for t in solved_period if t.created_at]
    avg_resolution = round(sum(durations) / len(durations), 1) if durations else None
    p50 = round(statistics.median(durations), 1) if durations else None
    p90 = round(sorted(durations)[int(len(durations) * 0.9)], 1) if len(durations) >= 5 else None

    prev_durations = [(t.solved_at - t.created_at).total_seconds() / 3600
                      for t in prev_solved if t.created_at and t.solved_at]
    prev_avg = round(sum(prev_durations) / len(prev_durations), 1) if prev_durations else None

    res_dist = {'< 4 h': 0, '4 – 24 h': 0, '1 – 3 d': 0, '3 d+': 0}
    for h in durations:
        if h < 4:    res_dist['< 4 h'] += 1
        elif h < 24: res_dist['4 – 24 h'] += 1
        elif h < 72: res_dist['1 – 3 d'] += 1
        else:        res_dist['3 d+'] += 1

    # ── Month-over-month deltas ───────────────────────────────────────────────
    def _pct_delta(curr, prev):
        if prev is None or prev == 0 or curr is None:
            return None
        return round(((curr - prev) / prev) * 100, 1)

    mom = {
        'tickets':        _pct_delta(len(period_tickets), prev_created),
        'avg_resolution': _pct_delta(avg_resolution, prev_avg),
        'sla_met_pct':    _pct_delta(curr_met_pct, prev_met_pct),
    }

    # ── Agent performance ─────────────────────────────────────────────────────
    agent_data = defaultdict(lambda: {'solved': 0, 'durations': [], 'open': 0})
    for t in solved_period:
        if not t.assigned_to:
            continue
        agent_data[t.assigned_to]['solved'] += 1
        if t.created_at and t.solved_at:
            agent_data[t.assigned_to]['durations'].append(
                (t.solved_at - t.created_at).total_seconds() / 3600
            )
    for t in Ticket.query.filter(Ticket.status.in_(Ticket.OPEN_STATUSES), Ticket.assigned_to.isnot(None)).all():
        agent_data[t.assigned_to]['open'] += 1

    agent_rows = []
    for name, data in sorted(agent_data.items(), key=lambda x: x[1]['solved'], reverse=True)[:8]:
        avg_r = round(sum(data['durations']) / len(data['durations']), 1) if data['durations'] else None
        agent_rows.append(type('R', (), {
            'assigned_to': name,
            'solved': data['solved'],
            'avg_resolution': avg_r,
            'open': data['open'],
        })())

    # ── Category volume + SLA compliance ─────────────────────────────────────
    from app.models.category import Category as _Cat
    top_cats = _Cat.query.filter_by(is_active=True, parent_id=None).filter(
        _Cat.type.in_(['ticket', 'both'])
    ).all()

    cat_rows, cat_sla_rows = [], []
    for c in top_cats:
        total_cnt = Ticket.query.filter_by(category_id=c.id).count()
        if total_cnt == 0:
            continue
        cat_solved = [t for t in solved_period if t.category_id == c.id]
        c_met    = sum(1 for t in cat_solved if t.resolution_due and t.solved_at <= t.resolution_due)
        c_missed = sum(1 for t in cat_solved if t.resolution_due and t.solved_at > t.resolution_due)
        c_sla_total = c_met + c_missed
        c_pct = int(c_met / c_sla_total * 100) if c_sla_total else None
        cat_rows.append(type('R', (), {'name': c.name, 'color': c.color or '#94a3b8', 'cnt': total_cnt})())
        cat_sla_rows.append(type('R', (), {
            'name': c.name, 'color': c.color or '#94a3b8',
            'met': c_met, 'missed': c_missed, 'total': c_sla_total, 'pct': c_pct,
        })())

    cat_rows.sort(key=lambda r: r.cnt, reverse=True)
    cat_sla_rows = [r for r in sorted(cat_sla_rows, key=lambda r: r.total, reverse=True) if r.total > 0]

    # ── Subcategory drill-down ────────────────────────────────────────────────
    subcats = _Cat.query.filter(_Cat.parent_id.isnot(None), _Cat.is_active == True).all()
    subcat_rows = sorted(
        [type('R', (), {'name': c.name, 'color': c.color or '#94a3b8',
                        'cnt': Ticket.query.filter_by(subcategory_id=c.id).count()})()
         for c in subcats],
        key=lambda r: r.cnt, reverse=True
    )
    subcat_rows = [r for r in subcat_rows if r.cnt > 0][:12]

    # ── Ticket aging (open tickets) ───────────────────────────────────────────
    open_all = Ticket.query.filter(Ticket.status.in_(Ticket.OPEN_STATUSES)).all()
    aging = {'< 1 day': 0, '1 – 3 days': 0, '3 – 7 days': 0, '7 days+': 0}
    oldest = None
    for t in open_all:
        age_h = (now - t.created_at).total_seconds() / 3600
        if age_h < 24:    aging['< 1 day'] += 1
        elif age_h < 72:  aging['1 – 3 days'] += 1
        elif age_h < 168: aging['3 – 7 days'] += 1
        else:             aging['7 days+'] += 1
        if oldest is None or t.created_at < oldest:
            oldest = t.created_at
    oldest_days = int((now - oldest).total_seconds() / 86400) if oldest else None

    # ── Repeat issues ────────────────────────────────────────────────────────
    title_counter = Counter(t.title for t in period_tickets)
    repeat_issues = [(title, cnt) for title, cnt in title_counter.most_common(10) if cnt > 1]

    # ── Global totals ─────────────────────────────────────────────────────────
    total_tickets = Ticket.query.count()
    open_tickets  = Ticket.query.filter(Ticket.status.in_(Ticket.OPEN_STATUSES)).count()
    breached_now  = Ticket.query.filter(
        Ticket.status.in_(Ticket.OPEN_STATUSES),
        Ticket.resolution_due.isnot(None),
        Ticket.resolution_due < now,
    ).count()

    return render_template('helpdesk/reports.html',
        # date range
        date_from=date_from.strftime('%Y-%m-%d'),
        date_to=(date_to - timedelta(days=1)).strftime('%Y-%m-%d'),
        period_days=period_days,
        # volume chart
        daily_labels=daily_labels, daily_counts=daily_counts, solved_counts=solved_counts,
        # breakdowns
        priority_counts=priority_counts, status_counts=status_counts,
        cat_rows=cat_rows[:10], cat_sla_rows=cat_sla_rows,
        subcat_rows=subcat_rows,
        # SLA
        sla_met=sla_met, sla_missed=sla_missed, sla_no_policy=sla_no_policy,
        curr_met_pct=curr_met_pct, prev_met_pct=prev_met_pct,
        fr_met=fr_met, fr_missed=fr_missed, fr_no_data=fr_no_data, fr_pct=fr_pct,
        # resolution time
        avg_resolution=avg_resolution, p50=p50, p90=p90, res_dist=res_dist,
        # agents
        agent_rows=agent_rows,
        # MoM
        mom=mom,
        # aging
        aging=aging, oldest_days=oldest_days,
        # repeat issues
        repeat_issues=repeat_issues,
        # totals
        total_tickets=total_tickets, open_tickets=open_tickets, breached_now=breached_now,
        priorities=priorities, statuses=statuses,
    )


@bp.route('/reports/export')
@login_required
def reports_export():
    """Export tickets as CSV. Query params: status, priority, date_from, date_to."""
    import csv
    from io import StringIO
    from flask import Response
    from datetime import timedelta

    status_f   = request.args.get('status', '').strip()
    priority_f = request.args.get('priority', '').strip()
    date_from  = request.args.get('date_from', '').strip()
    date_to    = request.args.get('date_to', '').strip()

    query = Ticket.query
    if status_f:
        query = query.filter_by(status=status_f)
    if priority_f:
        query = query.filter_by(priority=priority_f)
    if date_from:
        try:
            query = query.filter(Ticket.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Ticket.created_at < dt_to)
        except ValueError:
            pass
    tickets = query.order_by(Ticket.created_at.desc()).all()

    si = StringIO()
    w = csv.writer(si)
    w.writerow([
        'Ticket #', 'Title', 'Status', 'Priority', 'Category', 'Sub-category',
        'Requester Name', 'Requester Email', 'Assigned To',
        'Source', 'Tags', 'Created', 'Solved', 'Resolution Due', 'SLA Met',
    ])
    for t in tickets:
        sla_met = ''
        if t.resolution_due and t.solved_at:
            sla_met = 'Yes' if t.solved_at <= t.resolution_due else 'No'
        w.writerow([
            t.number, t.title, t.status, t.priority,
            t.category.name if t.category else '',
            t.subcategory.name if t.subcategory else '',
            t.requester_name, t.requester_email,
            t.assigned_to or '',
            t.source, t.tags or '',
            t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
            t.solved_at.strftime('%Y-%m-%d %H:%M') if t.solved_at else '',
            t.resolution_due.strftime('%Y-%m-%d %H:%M') if t.resolution_due else '',
            sla_met,
        ])

    output = si.getvalue()
    filename = f'helpdesk-tickets-{datetime.utcnow().strftime("%Y%m%d")}.csv'
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ─── Canned Responses ─────────────────────────────────────────────────────────

@bp.route('/admin/canned')
@role_required('admin')
def canned_list():
    items = CannedResponse.query.order_by(CannedResponse.name).all()
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    return render_template('helpdesk/admin/canned.html', items=items, categories=categories)


@bp.route('/admin/canned/add', methods=['POST'])
@role_required('admin')
def canned_add():
    name = request.form.get('name', '').strip()
    body = request.form.get('body', '').strip()
    if not name or not body:
        flash('Name and body are required.', 'error')
        return redirect(url_for('helpdesk.canned_list'))
    shortcut = request.form.get('shortcut', '').strip().lstrip('/') or None
    if shortcut and CannedResponse.query.filter_by(shortcut=shortcut).first():
        flash(f'Shortcut "/{shortcut}" is already used. Choose a different one.', 'error')
        return redirect(url_for('helpdesk.canned_list'))
    cat_id = request.form.get('category_id') or None
    cr = CannedResponse(
        name=name,
        shortcut=shortcut,
        subject=request.form.get('subject', '').strip() or None,
        body=body,
        category_id=int(cat_id) if cat_id else None,
    )
    db.session.add(cr)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('Failed to create canned response. Shortcut may already be in use.', 'error')
        return redirect(url_for('helpdesk.canned_list'))
    log_activity('canned_created', 'canned_response', cr.id, f'Created: {name}')
    flash(f'Canned response "{name}" created.', 'success')
    return redirect(url_for('helpdesk.canned_list'))


@bp.route('/admin/canned/<int:cr_id>/edit', methods=['POST'])
@role_required('admin')
def canned_edit(cr_id):
    cr = CannedResponse.query.get_or_404(cr_id)
    cr.name     = request.form.get('name', cr.name).strip()
    cr.body     = request.form.get('body', cr.body).strip()
    cr.subject  = request.form.get('subject', '').strip() or None
    shortcut    = request.form.get('shortcut', '').strip().lstrip('/')
    if shortcut and shortcut != (cr.shortcut or ''):
        dup = CannedResponse.query.filter(
            CannedResponse.shortcut == shortcut,
            CannedResponse.id != cr_id
        ).first()
        if dup:
            return jsonify({'ok': False, 'error': f'Shortcut "/{shortcut}" is already used.'})
    cr.shortcut = shortcut or None
    cat_id      = request.form.get('category_id') or None
    cr.category_id = int(cat_id) if cat_id else None
    cr.is_active   = request.form.get('is_active') == '1'
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'ok': False, 'error': 'Failed to save. Shortcut may already be in use.'})
    log_activity('canned_updated', 'canned_response', cr.id, f'Updated: {cr.name}')
    return jsonify({'ok': True})


@bp.route('/admin/canned/<int:cr_id>/delete', methods=['POST'])
@role_required('admin')
def canned_delete(cr_id):
    cr = CannedResponse.query.get_or_404(cr_id)
    name = cr.name
    db.session.delete(cr)
    db.session.commit()
    log_activity('canned_deleted', 'canned_response', cr_id, f'Deleted: {name}')
    return jsonify({'ok': True})


@bp.route('/canned/search')
@login_required
def canned_search():
    """AJAX endpoint for / shortcut autocomplete in reply box."""
    q = request.args.get('q', '').strip().lower()
    items = CannedResponse.query.filter_by(is_active=True).all()
    if q:
        items = [i for i in items if q in i.name.lower() or (i.shortcut and q in i.shortcut.lower())]
    return jsonify([{
        'id': i.id, 'name': i.name,
        'shortcut': i.shortcut or '',
        'body': i.body, 'subject': i.subject or '',
    } for i in items[:12]])


# ─── Ticket Attachments ────────────────────────────────────────────────────────

@bp.route('/tickets/<string:number>/attach', methods=['POST'])
@login_required
def ticket_attach(number):
    """Upload file attachment to a ticket."""
    import uuid, werkzeug
    ticket = Ticket.query.filter_by(number=number).first_or_404()
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file'}), 400

    MAX_SIZE = 10 * 1024 * 1024  # 10 MB
    data = f.read()
    if len(data) > MAX_SIZE:
        return jsonify({'error': 'File too large (max 10 MB)'}), 413

    original = werkzeug.utils.secure_filename(f.filename)
    ext = os.path.splitext(original)[1].lower()
    stored = f'{uuid.uuid4().hex}{ext}'
    folder = os.path.join('app', 'uploads', 'ticket_attachments', str(ticket.id))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, stored), 'wb') as fh:
        fh.write(data)

    u = _current_user()
    ta = TicketAttachment(
        ticket_id=ticket.id,
        filename=stored,
        original_filename=original,
        file_size=len(data),
        mime_type=f.content_type or 'application/octet-stream',
        uploaded_by=u['name'],
    )
    db.session.add(ta)
    db.session.commit()
    return jsonify({'ok': True, 'id': ta.id, 'name': original, 'size': ta.size_label})


@bp.route('/tickets/<string:number>/attachments/<int:att_id>/download')
@login_required
def ticket_download(number, att_id):
    """Serve a ticket attachment file."""
    ticket = Ticket.query.filter_by(number=number).first_or_404()
    att = TicketAttachment.query.filter_by(id=att_id, ticket_id=ticket.id).first_or_404()
    folder = os.path.abspath(os.path.join('app', 'uploads', 'ticket_attachments', str(ticket.id)))
    return send_from_directory(folder, att.filename, download_name=att.original_filename, as_attachment=True)


# ─── Email Inbox Admin ────────────────────────────────────────────────────────

@bp.route('/admin/email')
@role_required('admin')
def email_inbox():
    page = max(1, request.args.get('page', 1, type=int))
    direction = request.args.get('dir', '')
    status_f  = request.args.get('status', '')

    query = EmailLog.query
    if direction:
        query = query.filter_by(direction=direction)
    if status_f:
        query = query.filter_by(status=status_f)
    query = query.order_by(EmailLog.created_at.desc())

    per_page = 40
    total = query.count()
    logs = query.offset((page - 1) * per_page).limit(per_page).all()

    # Status summary
    summary = {
        'total':   EmailLog.query.count(),
        'in':      EmailLog.query.filter_by(direction='in').count(),
        'out':     EmailLog.query.filter_by(direction='out').count(),
        'failed':  EmailLog.query.filter_by(status='failed').count(),
        'skipped': EmailLog.query.filter_by(status='skipped').count(),
    }

    # Email config status
    import os as _os
    provider = _os.environ.get('MAIL_PROVIDER', 'smtp')
    if provider == 'graph':
        graph_ok = bool(_os.environ.get('MAIL_TENANT_ID') and _os.environ.get('MAIL_CLIENT_ID')
                        and _os.environ.get('MAIL_CLIENT_SECRET') and _os.environ.get('MAIL_MAILBOX'))
        mail_cfg = {
            'provider':    'graph',
            'mailbox':     _os.environ.get('MAIL_MAILBOX', ''),
            'tenant_id':   _os.environ.get('MAIL_TENANT_ID', '')[:8] + '…' if _os.environ.get('MAIL_TENANT_ID') else '',
            'client_id':   _os.environ.get('MAIL_CLIENT_ID', '')[:8] + '…' if _os.environ.get('MAIL_CLIENT_ID') else '',
            'auto_reply':  _os.environ.get('MAIL_AUTO_REPLY', '1'),
            'poll_interval': _os.environ.get('MAIL_POLL_INTERVAL', '60'),
            'configured':  graph_ok,
            # keep these so the template doesn't error on missing keys
            'imap_server': '', 'smtp_server': '', 'username': '',
        }
    else:
        mail_cfg = {
            'provider':    provider,
            'imap_server': _os.environ.get('MAIL_IMAP_SERVER', ''),
            'smtp_server': _os.environ.get('MAIL_SERVER', ''),
            'username':    _os.environ.get('MAIL_USERNAME', ''),
            'auto_reply':  _os.environ.get('MAIL_AUTO_REPLY', '1'),
            'poll_interval': _os.environ.get('MAIL_POLL_INTERVAL', '60'),
            'configured':  bool(_os.environ.get('MAIL_IMAP_SERVER') and _os.environ.get('MAIL_USERNAME')),
            'mailbox': '', 'tenant_id': '', 'client_id': '',
        }

    return render_template('helpdesk/admin/email_inbox.html',
                           logs=logs, total=total, page=page,
                           pages=max(1, (total + per_page - 1) // per_page),
                           summary=summary, mail_cfg=mail_cfg,
                           direction=direction, status_f=status_f)


@bp.route('/admin/email/test', methods=['POST'])
@role_required('admin')
def email_test():
    """Send a test email to verify SMTP config."""
    from app.services.mailer import send_email
    to = request.form.get('to', '').strip()
    if not to:
        flash('Enter a recipient email.', 'error')
        return redirect(url_for('helpdesk.email_inbox'))
    ok = send_email(
        to=to,
        subject='iMocha Helpdesk — SMTP Test',
        html_body='<p>Your email configuration is working correctly! ✅</p>'
                  '<p style="color:#64748b;font-size:12px;">Sent from iMocha Helpdesk.</p>',
    )
    flash('Test email sent successfully.' if ok else
          'Failed to send. Check MAIL_* environment variables.', 'success' if ok else 'error')
    return redirect(url_for('helpdesk.email_inbox'))


@bp.route('/admin/email/poll-now', methods=['POST'])
@role_required('admin')
def email_poll_now():
    """Manually trigger an immediate inbox poll."""
    try:
        import os as _os, threading
        if _os.environ.get('MAIL_PROVIDER') == 'graph':
            from app.services.ms365 import poll_inbox
        else:
            from app.services.email_ingestion import poll_inbox
        t = threading.Thread(target=poll_inbox, args=[_get_current_app()], daemon=True)
        t.start()
        flash('Inbox poll triggered. Check the log in a few seconds.', 'success')
    except Exception as exc:
        flash(f'Poll failed: {exc}', 'error')
    return redirect(url_for('helpdesk.email_inbox'))


def _get_current_app():
    from flask import current_app
    return current_app._get_current_object()


# ─── Agent Scorecard ──────────────────────────────────────────────────────────

@bp.route('/scorecard')
@login_required
def agent_scorecard():
    from datetime import timedelta
    now = datetime.utcnow()
    period = request.args.get('period', 'week')
    user = _current_user()
    role = user.get('role', 'agent')
    current_user_name = user.get('name', '')

    if role in ('admin', 'manager'):
        agent_name = request.args.get('agent', '').strip() or current_user_name
    else:
        agent_name = current_user_name

    period_days = 7 if period == 'week' else 30
    period_label = 'This Week' if period == 'week' else 'This Month'
    start = now - timedelta(days=period_days)
    prev_start = now - timedelta(days=period_days * 2)
    prev_end = start

    # All distinct agents (manager dropdown)
    agent_list = []
    if role in ('admin', 'manager'):
        rows = db.session.query(Ticket.assigned_to).filter(
            Ticket.assigned_to.isnot(None)
        ).distinct().order_by(Ticket.assigned_to).all()
        agent_list = [r.assigned_to for r in rows if r.assigned_to]

    def _get_metrics(agent, t_start, t_end):
        resolved_base = db.session.query(Ticket).filter(
            Ticket.assigned_to == agent,
            Ticket.status.in_(['solved', 'closed']),
            func.coalesce(Ticket.solved_at, Ticket.closed_at) >= t_start,
            func.coalesce(Ticket.solved_at, Ticket.closed_at) < t_end,
        )
        resolved_list = resolved_base.all()
        resolved = len(resolved_list)

        # SLA resolution compliance
        sla_tickets = [t for t in resolved_list if t.resolution_due]
        sla_met = sum(1 for t in sla_tickets if (t.solved_at or t.closed_at) <= t.resolution_due)
        sla_total = len(sla_tickets)
        sla_pct = round(100 * sla_met / sla_total) if sla_total else None

        # First response compliance
        fr_tickets = [t for t in resolved_list if t.first_response_due and t.first_responded_at]
        fr_met = sum(1 for t in fr_tickets if t.first_responded_at <= t.first_response_due)
        fr_total = len(fr_tickets)
        fr_pct = round(100 * fr_met / fr_total) if fr_total else None

        # Avg resolution time
        res_times = []
        for t in resolved_list:
            res_at = t.solved_at or t.closed_at
            if res_at:
                res_times.append((res_at - t.created_at).total_seconds() / 3600)
        avg_h = round(sum(res_times) / len(res_times), 1) if res_times else None

        # Speed buckets
        buckets = {'lt1h': 0, 'h1to4': 0, 'h4to24': 0, 'd1to3': 0, 'gt3d': 0}
        for h in res_times:
            if h < 1:      buckets['lt1h'] += 1
            elif h < 4:    buckets['h1to4'] += 1
            elif h < 24:   buckets['h4to24'] += 1
            elif h < 72:   buckets['d1to3'] += 1
            else:          buckets['gt3d'] += 1

        # Priority breakdown
        prio_rows = db.session.query(Ticket.priority, func.count(Ticket.id)).filter(
            Ticket.assigned_to == agent,
            Ticket.status.in_(['solved', 'closed']),
            func.coalesce(Ticket.solved_at, Ticket.closed_at) >= t_start,
            func.coalesce(Ticket.solved_at, Ticket.closed_at) < t_end,
        ).group_by(Ticket.priority).all()
        priority_counts = {p: c for p, c in prio_rows}

        # Category breakdown
        cat_rows = db.session.query(Category.name, func.count(Ticket.id).label('cnt')
        ).join(Category, Ticket.category_id == Category.id).filter(
            Ticket.assigned_to == agent,
            Ticket.status.in_(['solved', 'closed']),
            func.coalesce(Ticket.solved_at, Ticket.closed_at) >= t_start,
            func.coalesce(Ticket.solved_at, Ticket.closed_at) < t_end,
        ).group_by(Category.name).order_by(func.count(Ticket.id).desc()).limit(5).all()

        overdue = db.session.query(func.count(Ticket.id)).filter(
            Ticket.assigned_to == agent,
            Ticket.status.in_(['new', 'open', 'pending', 'on_hold']),
            Ticket.resolution_due < now,
        ).scalar() or 0

        open_count = db.session.query(func.count(Ticket.id)).filter(
            Ticket.assigned_to == agent,
            Ticket.status.in_(['new', 'open', 'pending', 'on_hold']),
        ).scalar() or 0

        return {
            'resolved': resolved, 'sla_pct': sla_pct, 'sla_met': sla_met,
            'sla_total': sla_total, 'fr_pct': fr_pct, 'fr_met': fr_met,
            'fr_total': fr_total, 'avg_h': avg_h, 'buckets': buckets,
            'priority_counts': priority_counts,
            'categories': [{'name': r.name, 'count': r.cnt} for r in cat_rows],
            'overdue': overdue, 'open_count': open_count,
        }

    current  = _get_metrics(agent_name, start, now)
    previous = _get_metrics(agent_name, prev_start, prev_end)

    # Team resolved counts (same period)
    team_rows = db.session.query(
        Ticket.assigned_to, func.count(Ticket.id).label('cnt')
    ).filter(
        Ticket.assigned_to.isnot(None),
        Ticket.status.in_(['solved', 'closed']),
        func.coalesce(Ticket.solved_at, Ticket.closed_at) >= start,
    ).group_by(Ticket.assigned_to).order_by(func.count(Ticket.id).desc()).all()

    team_counts = [r.cnt for r in team_rows]
    team_avg = round(sum(team_counts) / len(team_counts), 1) if team_counts else 0
    sorted_counts = sorted(team_counts, reverse=True)
    agent_vol = current['resolved']
    rank = next((i + 1 for i, v in enumerate(sorted_counts) if v <= agent_vol), len(sorted_counts))
    team_size = len(sorted_counts) or 1
    percentile = round(100 * (team_size - rank) / team_size) if team_size > 1 else 100

    leaderboard = []
    if role in ('admin', 'manager'):
        leaderboard = [{'agent': r.assigned_to, 'resolved': r.cnt} for r in team_rows[:10]]

    # Performance score
    sla_s  = current['sla_pct']  if current['sla_pct']  is not None else 70
    fr_s   = current['fr_pct']   if current['fr_pct']   is not None else 70
    vol_s  = min((agent_vol / team_avg) * 50, 100) if team_avg > 0 else (80 if agent_vol > 0 else 40)
    h      = current['avg_h']
    spd_s  = (100 if h < 2 else 85 if h < 8 else 65 if h < 24 else 40 if h < 72 else 20) if h else 70
    pc     = current['priority_counts']
    tot    = max(agent_vol, 1)
    w_pts  = pc.get('critical', 0)*4 + pc.get('high', 0)*3 + pc.get('medium', 0)*2 + pc.get('low', 0)
    prio_s = round(100 * w_pts / (tot * 4)) if tot * 4 > 0 else 70
    penalty = min(current['overdue'] * 4, 20)
    score  = max(0, min(100, round(sla_s*0.30 + fr_s*0.20 + vol_s*0.20 + spd_s*0.15 + prio_s*0.15 - penalty)))

    if score >= 90:   grade, grade_label, grade_color = 'S', 'Exceptional', '#7c3aed'
    elif score >= 80: grade, grade_label, grade_color = 'A', 'Great',        '#16a34a'
    elif score >= 70: grade, grade_label, grade_color = 'B', 'Good',         '#2563eb'
    elif score >= 60: grade, grade_label, grade_color = 'C', 'Needs Work',   '#ca8a04'
    else:             grade, grade_label, grade_color = 'D', 'At Risk',      '#dc2626'

    # Badges
    badges = []
    if current['sla_pct'] is not None and current['sla_pct'] >= 90:
        badges.append({'icon': '🏆', 'name': 'SLA Champion',     'desc': 'Resolution SLA ≥ 90%'})
    if h is not None and h < 4:
        badges.append({'icon': '⚡', 'name': 'Speed Demon',       'desc': 'Avg resolution under 4h'})
    if team_avg > 0 and agent_vol >= team_avg * 1.5:
        badges.append({'icon': '💪', 'name': 'Heavy Lifter',      'desc': '1.5× team average volume'})
    if pc.get('critical', 0) > 0:
        badges.append({'icon': '🚨', 'name': 'Critical Resolver', 'desc': f"{pc['critical']} critical ticket(s) resolved"})
    if current['fr_pct'] is not None and current['fr_pct'] >= 90:
        badges.append({'icon': '🎯', 'name': 'First Responder',   'desc': 'First-response SLA ≥ 90%'})
    if current['overdue'] == 0 and current['open_count'] > 0:
        badges.append({'icon': '✅', 'name': 'Zero Overdue',      'desc': 'No breached tickets'})
    if previous['resolved'] and current['resolved'] > previous['resolved']:
        badges.append({'icon': '📈', 'name': 'On the Rise',       'desc': f"+{current['resolved']-previous['resolved']} vs previous {period}"})

    # ── Active Ticket Snapshot ────────────────────────────────────────────────
    active_tickets_q = db.session.query(Ticket).filter(
        Ticket.assigned_to == agent_name,
        Ticket.status.in_(['new', 'open', 'pending', 'on_hold']),
    ).order_by(
        case((Ticket.resolution_due < now, 0), else_=1),
        case((Ticket.priority == 'critical', 0), (Ticket.priority == 'high', 1),
             (Ticket.priority == 'medium', 2), else_=3),
    ).limit(10).all()

    active_snapshot = []
    for t in active_tickets_q:
        age_h = (now - t.created_at).total_seconds() / 3600
        age_label = f"{int(age_h)}h" if age_h < 24 else f"{int(age_h/24)}d {int(age_h%24)}h"
        sla_st = 'none'
        if t.resolution_due:
            if now > t.resolution_due:
                sla_st = 'breached'
            elif (t.resolution_due - now).total_seconds() < 3600:
                sla_st = 'warning'
            else:
                sla_st = 'ok'
        active_snapshot.append({
            'number': t.number, 'title': t.title[:55],
            'priority': t.priority, 'status': t.status,
            'age_label': age_label, 'sla_state': sla_st,
            'due': t.resolution_due.strftime('%b %d %H:%M') if t.resolution_due else None,
        })

    # ── Daily Activity Pattern ────────────────────────────────────────────────
    from datetime import timedelta as _td
    daily_rows = db.session.query(
        func.date_trunc('day', func.coalesce(Ticket.solved_at, Ticket.closed_at)).label('day'),
        func.count(Ticket.id).label('cnt'),
    ).filter(
        Ticket.assigned_to == agent_name,
        Ticket.status.in_(['solved', 'closed']),
        func.coalesce(Ticket.solved_at, Ticket.closed_at) >= start,
    ).group_by('day').order_by('day').all()

    daily_map = {}
    for row in daily_rows:
        if row.day:
            daily_map[row.day.strftime('%Y-%m-%d')] = row.cnt

    daily_activity = []
    for i in range(period_days):
        d = (start + _td(days=i)).date()
        daily_activity.append({
            'label': d.strftime('%b %d'), 'short': d.strftime('%d'),
            'weekday': d.strftime('%a'), 'count': daily_map.get(d.strftime('%Y-%m-%d'), 0),
        })
    daily_max = max((x['count'] for x in daily_activity), default=1) or 1
    working_days = sum(1 for x in daily_activity if x['count'] > 0)
    consistency_pct = round(100 * working_days / period_days)

    # ── Repeat Requesters ─────────────────────────────────────────────────────
    repeat_rows = db.session.query(
        Ticket.requester_name, func.count(Ticket.id).label('cnt')
    ).filter(
        Ticket.assigned_to == agent_name,
        Ticket.created_at >= start,
    ).group_by(Ticket.requester_name).having(func.count(Ticket.id) > 1
    ).order_by(func.count(Ticket.id).desc()).limit(5).all()
    repeat_requesters = [{'name': r.requester_name, 'count': r.cnt} for r in repeat_rows]

    # ── Documentation & Engagement ────────────────────────────────────────────
    from app.models.ticket import TicketComment
    resolved_ids = [r.id for r in db.session.query(Ticket.id).filter(
        Ticket.assigned_to == agent_name,
        Ticket.status.in_(['solved', 'closed']),
        func.coalesce(Ticket.solved_at, Ticket.closed_at) >= start,
    ).all()]

    if resolved_ids:
        cs = db.session.query(
            func.count(TicketComment.id).label('total'),
            func.sum(case((TicketComment.is_internal == True, 1), else_=0)).label('internal'),
        ).filter(
            TicketComment.ticket_id.in_(resolved_ids),
            TicketComment.author_type == 'agent',
        ).first()
        total_comments = int(cs.total or 0)
        internal_notes = int(cs.internal or 0)
        avg_comments = round(total_comments / len(resolved_ids), 1)
    else:
        total_comments = internal_notes = 0
        avg_comments = 0.0

    doc_quality = 'Thorough' if avg_comments >= 3 else 'Average' if avg_comments >= 1.5 else 'Minimal'
    doc_score = {
        'total_comments': total_comments, 'internal_notes': internal_notes,
        'avg_per_ticket': avg_comments, 'quality': doc_quality,
    }

    # ── AI Narrative ──────────────────────────────────────────────────────────
    def _narrative(m, prev, _score, _grade, t_avg, _rank, t_size, _period):
        p = []
        vol = m['resolved']
        if _grade == 'S':   p.append(f"Outstanding {_period} — performing at the very top of the team.")
        elif _grade == 'A': p.append(f"Excellent {_period} overall with strong metrics across the board.")
        elif _grade == 'B': p.append(f"Good {_period} with solid work and a few areas to sharpen.")
        elif _grade == 'C': p.append(f"Average performance this {_period} — there are clear opportunities to improve.")
        else:               p.append(f"Performance is below expectations this {_period} and needs immediate attention.")

        if vol > 0 and t_avg > 0:
            ratio = vol / t_avg
            if ratio >= 1.5:   p.append(f"Resolved {vol} tickets — {round((ratio-1)*100)}% above the team average of {t_avg}.")
            elif ratio >= 1.0: p.append(f"Resolved {vol} tickets, at or above the team average ({t_avg}).")
            else:              p.append(f"Resolved {vol} tickets, {round((1-ratio)*100)}% below the team average of {t_avg}.")
        elif vol == 0:
            p.append("No tickets resolved this period — this needs to be addressed.")

        if m['sla_pct'] is not None:
            if m['sla_pct'] >= 90:   p.append(f"SLA compliance is excellent at {m['sla_pct']}% — requesters are being served on time.")
            elif m['sla_pct'] >= 75: p.append(f"SLA at {m['sla_pct']}% is acceptable but has room to improve.")
            else:                    p.append(f"SLA compliance of {m['sla_pct']}% is a concern — late resolutions are impacting service quality.")

        crit = m['priority_counts'].get('critical', 0)
        hi   = m['priority_counts'].get('high', 0)
        if crit > 0: p.append(f"Handled {crit} critical ticket{'s' if crit>1 else ''} — shows strong capability under pressure.")
        elif hi > 0: p.append(f"Resolved {hi} high-priority ticket{'s' if hi>1 else ''}.")

        avg_h = m['avg_h']
        if avg_h is not None:
            if avg_h < 4:    p.append(f"Resolution speed is excellent — averaging {avg_h}h per ticket.")
            elif avg_h < 24: p.append(f"Avg resolution of {avg_h}h is within acceptable range.")
            else:            p.append(f"Avg resolution of {round(avg_h/24,1)} days is higher than ideal.")

        if m['overdue'] > 0:
            p.append(f"⚠️ {m['overdue']} ticket{'s are' if m['overdue']>1 else ' is'} currently overdue and need immediate action.")

        if prev['resolved'] and vol and vol > prev['resolved']:
            p.append(f"Volume improved by {vol - prev['resolved']} vs the previous {_period} — good momentum.")
        elif prev['resolved'] and vol and vol < prev['resolved']:
            p.append(f"Volume dropped by {prev['resolved'] - vol} vs the previous {_period} — worth investigating.")

        if t_size > 2:
            if _rank == 1:                                    p.append(f"Currently #1 in the team for resolved volume this {_period}.")
            elif _rank <= max(1, round(t_size * 0.25)):      p.append(f"Ranked #{_rank} of {t_size} — in the top 25% of the team.")

        return " ".join(p)

    narrative = _narrative(current, previous, score, grade, team_avg, rank, team_size, period)

    def _delta(a, b):
        return (a - b) if (a is not None and b is not None) else None

    return render_template('helpdesk/reports/agent_scorecard.html',
        agent_name=agent_name, period=period, period_label=period_label,
        period_start=start.strftime('%b %d'), period_end=now.strftime('%b %d, %Y'),
        current=current, previous=previous,
        deltas={
            'resolved':   _delta(current['resolved'],  previous['resolved']),
            'sla_pct':    _delta(current['sla_pct'],   previous['sla_pct']),
            'fr_pct':     _delta(current['fr_pct'],    previous['fr_pct']),
            'avg_h':      _delta(current['avg_h'],     previous['avg_h']),
            'overdue':    _delta(current['overdue'],   previous['overdue']),
        },
        score=score, grade=grade, grade_label=grade_label, grade_color=grade_color,
        badges=badges, team_avg=team_avg, rank=rank, team_size=team_size,
        percentile=percentile, leaderboard=leaderboard, agent_list=agent_list, role=role,
        active_snapshot=active_snapshot,
        daily_activity=daily_activity, daily_max=daily_max,
        working_days=working_days, consistency_pct=consistency_pct,
        repeat_requesters=repeat_requesters,
        doc_score=doc_score,
        narrative=narrative,
        period_days=period_days,
    )
