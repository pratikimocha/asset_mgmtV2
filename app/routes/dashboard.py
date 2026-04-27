"""Dashboard routes."""
from flask import Blueprint, render_template, request, make_response
from sqlalchemy import func, case, and_
from datetime import datetime, timedelta
from app.auth.decorators import login_required
from app.models import Asset, Assignment, Issue, Repair, MaintenanceTask
from app.extensions import db

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
def index():
    today = datetime.utcnow().date()
    expiry_soon = today + timedelta(days=60)

    # ── Query 1: all asset aggregates in one pass ──────────────────────────────
    issue_cnt_d = (
        db.session.query(Issue.asset_id.label('asset_id'), func.count(Issue.id).label('cnt'))
        .filter(Issue.status.in_(['open', 'in-progress']))
        .group_by(Issue.asset_id)
        .subquery('issue_cnt_d')
    )
    agg = db.session.query(
        # status counts
        func.sum(case((func.lower(Asset.status) == 'deployed',  1), else_=0)),
        func.sum(case((func.lower(Asset.status) == 'instock',   1), else_=0)),
        func.sum(case((func.lower(Asset.status) == 'sold',      1), else_=0)),
        func.sum(case((func.lower(Asset.status) == 'repair',    1), else_=0)),
        func.sum(case((func.lower(Asset.status) == 'retired',   1), else_=0)),
        func.count(Asset.id),
        # warranty
        func.sum(case((Asset.warranty_expiry >= expiry_soon, 1), else_=0)),
        func.sum(case((Asset.warranty_expiry.between(today, expiry_soon), 1), else_=0)),
        func.sum(case((Asset.warranty_expiry < today, 1), else_=0)),
        # cost
        func.coalesce(func.sum(Asset.cost), 0),
        func.coalesce(func.avg(Asset.cost), 0),
        # working/not-working (instock only)
        func.sum(case((
            and_(func.lower(Asset.status) == 'instock',
                 func.coalesce(issue_cnt_d.c.cnt, 0) == 0), 1), else_=0)),
        func.sum(case((
            and_(func.lower(Asset.status) == 'instock',
                 issue_cnt_d.c.cnt > 0), 1), else_=0)),
    ).outerjoin(issue_cnt_d, issue_cnt_d.c.asset_id == Asset.id).first()

    deployed, instock, sold, repair, retired, total = [int(agg[i] or 0) for i in range(6)]
    stats = {'total': total, 'deployed': deployed, 'instock': instock,
             'sold': sold, 'repair': repair, 'retired': retired}
    sm = {'deployed': deployed, 'instock': instock, 'sold': sold,
          'repair': repair, 'retired': retired}
    warranty_stats = {'active': int(agg[6] or 0), 'expiring': int(agg[7] or 0), 'expired': int(agg[8] or 0)}
    cost_stats = {'total_portfolio': float(agg[9] or 0),
                  'avg_cost': round(float(agg[10] or 0), 0), 'repair_spend': 0.0}
    working_count = int(agg[11] or 0)
    instock_with_issues = int(agg[12] or 0)

    # ── Query 2: repair spend + open issues + overdue (3 lightweight scalars) ──
    repair_spend = db.session.query(func.coalesce(func.sum(Repair.cost), 0)).scalar()
    cost_stats['repair_spend'] = float(repair_spend or 0)
    open_issues = db.session.query(func.count(Issue.id)).join(
        Asset, Asset.id == Issue.asset_id
    ).filter(
        Issue.status.in_(['open', 'in-progress']),
        func.lower(Asset.status).notin_(['sold', 'retired'])
    ).scalar() or 0
    overdue = db.session.query(func.count(MaintenanceTask.id)).filter(
        MaintenanceTask.status == 'scheduled',
        MaintenanceTask.scheduled_date < today).scalar() or 0

    # ── Query 3: model breakdown (instock) ─────────────────────────────────────
    model_rows = db.session.query(
        Asset.model,
        func.count(Asset.id).label('cnt'),
        func.sum(case((func.coalesce(issue_cnt_d.c.cnt, 0) == 0, 1), else_=0)).label('working'),
        func.sum(func.coalesce(issue_cnt_d.c.cnt, 0)).label('not_working'),
    ).outerjoin(issue_cnt_d, issue_cnt_d.c.asset_id == Asset.id
    ).filter(func.lower(Asset.status) == 'instock'
    ).group_by(Asset.model).order_by(func.count(Asset.id).desc()).limit(8).all()

    resp = make_response(render_template('dashboard.html', stats=stats, warranty_stats=warranty_stats,
        cost_stats=cost_stats, open_issues=open_issues, overdue_maintenance=overdue,
        working_count=working_count, instock_with_issues=instock_with_issues,
        model_breakdown=[{'model': r.model or 'Unknown', 'total': r.cnt,
                          'working': int(r.working or 0), 'not_working': int(r.not_working or 0)} for r in model_rows],
        status_breakdown=sm))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp

@bp.route('/stock')
@login_required
def stock():
    today = datetime.utcnow().date()
    expiry_soon = today + timedelta(days=60)

    # Per-asset open issue count — exclude sold/retired assets
    issue_cnt = (
        db.session.query(Issue.asset_id.label('asset_id'), func.count(Issue.id).label('cnt'))
        .join(Asset, Asset.id == Issue.asset_id)
        .filter(
            Issue.status.in_(['open', 'in-progress']),
            func.lower(Asset.status).notin_(['sold', 'retired'])
        )
        .group_by(Issue.asset_id)
        .subquery('issue_cnt')
    )

    rows = db.session.query(
        Asset.model,
        func.max(Asset.manufacturer).label('manufacturer'),   # representative value per model
        func.max(Asset.category).label('category'),           # representative value per model
        func.count(Asset.id).label('total'),
        func.sum(case((func.lower(Asset.status) == 'instock', 1), else_=0)).label('instock'),
        func.sum(case((func.lower(Asset.status) == 'deployed', 1), else_=0)).label('deployed'),
        func.sum(case((func.lower(Asset.status) == 'repair', 1), else_=0)).label('repair'),
        func.sum(case((func.lower(Asset.status) == 'retired', 1), else_=0)).label('retired'),
        func.sum(case((func.lower(Asset.status) == 'ordered', 1), else_=0)).label('ordered'),
        func.sum(case((func.lower(Asset.status) == 'received', 1), else_=0)).label('received'),
        # Working = instock/received assets with zero open issues
        func.sum(case(
            (and_(func.lower(Asset.status).in_(['instock', 'received']),
                  func.coalesce(issue_cnt.c.cnt, 0) == 0), 1),
            else_=0
        )).label('working'),
        # not_working = total open issue records for this model (sum of per-asset counts)
        func.sum(func.coalesce(issue_cnt.c.cnt, 0)).label('not_working'),
        func.sum(case((Asset.warranty_expiry < today, 1), else_=0)).label('warranty_expired'),
        func.sum(case((and_(Asset.warranty_expiry >= today, Asset.warranty_expiry <= expiry_soon), 1), else_=0)).label('warranty_expiring'),
    ).outerjoin(issue_cnt, issue_cnt.c.asset_id == Asset.id
    ).filter(func.lower(Asset.status) != 'sold'
    ).group_by(Asset.model                                    # group by model only — avoids duplicate rows
    ).order_by(func.count(Asset.id).desc()).all()

    models = []
    totals = {'total': 0, 'instock': 0, 'deployed': 0, 'repair': 0, 'working': 0, 'not_working': 0}
    categories = set()

    for r in rows:
        instock = int(r.instock or 0)
        deployed = int(r.deployed or 0)
        working = int(r.working or 0)
        not_working = int(r.not_working or 0)
        health_pct = round(working / instock * 100) if instock > 0 else 100
        utilization = round(deployed / (instock + deployed) * 100) if (instock + deployed) > 0 else 0

        m = {
            'model': r.model or 'Unknown',
            'manufacturer': r.manufacturer or '',
            'category': r.category or '',
            'total': int(r.total or 0),
            'instock': instock,
            'deployed': deployed,
            'repair': int(r.repair or 0),
            'retired': int(r.retired or 0),
            'ordered': int(r.ordered or 0),
            'received': int(r.received or 0),
            'working': working,
            'not_working': not_working,
            'health_pct': health_pct,
            'utilization': utilization,
            'warranty_expired': int(r.warranty_expired or 0),
            'warranty_expiring': int(r.warranty_expiring or 0),
        }
        models.append(m)
        totals['total'] += m['total']
        totals['instock'] += instock
        totals['deployed'] += deployed
        totals['repair'] += m['repair']
        totals['working'] += working
        totals['not_working'] += not_working
        if r.category:
            categories.add(r.category)

    overall_util = round(totals['deployed'] / (totals['deployed'] + totals['instock']) * 100) if (totals['deployed'] + totals['instock']) > 0 else 0
    totals['utilization'] = overall_util

    resp = make_response(render_template('stock.html', models=models, totals=totals,
                                         categories=sorted(categories)))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


@bp.route('/multi-assets')
@login_required
def multi_assets():
    user_name = request.args.get('user_name', '').strip()
    assets = []
    if user_name:
        asgns = Assignment.query.filter(func.lower(Assignment.user_name).like(f'%{user_name.lower()}%'), Assignment.returned_at.is_(None)).all()
        assets = [{'asset': a.asset, 'assignment': a} for a in asgns]
    return render_template('assets/multi.html', user_name=user_name, assets=assets)
