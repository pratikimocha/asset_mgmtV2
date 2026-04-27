"""API JSON endpoints."""
import os
import requests as _http
from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import func, case, and_
from datetime import datetime, timedelta
from app.auth.decorators import login_required
from app.models import Asset, Assignment, Issue, MaintenanceTask
from app.services.notifications import get_notifications
from app.extensions import db

bp = Blueprint('api', __name__, url_prefix='/api')

@bp.route('/search')
@login_required
def search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify([])
    # Subquery: assets ever assigned to a user matching the query (including returned)
    ever_assigned = db.session.query(Assignment.asset_id).filter(
        Assignment.user_name.ilike(f'%{q}%')
    ).subquery()
    results = db.session.query(
        Asset,
        Assignment.user_name.label('current_user'),
    ).outerjoin(
        Assignment, (Assignment.asset_id == Asset.id) & Assignment.returned_at.is_(None)
    ).filter(
        Asset.serial_number.ilike(f'%{q}%') |
        Asset.asset_tag.ilike(f'%{q}%') |
        Asset.model.ilike(f'%{q}%') |
        Asset.manufacturer.ilike(f'%{q}%') |
        Asset.id.in_(ever_assigned)
    ).limit(8).all()
    return jsonify([{
        'id': asset.id,
        'serial_number': asset.serial_number,
        'model': asset.model or '',
        'manufacturer': asset.manufacturer or '',
        'status': asset.status,
        'current_user': current_user,
    } for asset, current_user in results])

@bp.route('/notifications')
@login_required
def notifications():
    return jsonify(get_notifications())

@bp.route('/status-breakdown')
@login_required
def status_breakdown():
    rows = db.session.query(func.lower(Asset.status), func.count(Asset.id)).group_by(func.lower(Asset.status)).all()
    return jsonify({s: c for s, c in rows})

@bp.route('/assets/list')
@login_required
def assets_list():
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('filter', '').strip()
    model_filter = request.args.get('model', '').strip()
    warranty_filter = request.args.get('warranty', '').strip()
    working_filter = request.args.get('working', '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    limit = min(100, request.args.get('limit', 50, type=int))

    query = db.session.query(
        Asset,
        Assignment.user_name.label('current_user'),
        func.count(Issue.id).label('open_issues_count'),
    ).outerjoin(
        Assignment, (Assignment.asset_id == Asset.id) & Assignment.returned_at.is_(None)
    ).outerjoin(
        Issue, (Issue.asset_id == Asset.id) & Issue.status.in_(['open', 'in-progress'])
    ).group_by(Asset.id, Assignment.user_name)

    if q:
        query = query.filter(
            Asset.serial_number.ilike(f'%{q}%') |
            Asset.model.ilike(f'%{q}%') |
            Asset.manufacturer.ilike(f'%{q}%') |
            Assignment.user_name.ilike(f'%{q}%')
        )
    if status_filter:
        query = query.filter(func.lower(Asset.status) == status_filter.lower())
    if model_filter:
        query = query.filter(func.lower(Asset.model) == model_filter.lower())

    today = datetime.utcnow().date()
    expiry_soon = today + timedelta(days=60)
    if warranty_filter == 'expired':
        query = query.filter(Asset.warranty_expiry < today)
    elif warranty_filter == 'expiring':
        query = query.filter(Asset.warranty_expiry.between(today, expiry_soon))
    elif warranty_filter == 'active':
        query = query.filter(Asset.warranty_expiry >= expiry_soon)

    if working_filter == '1':
        query = query.having(func.count(Issue.id) == 0)
    elif working_filter == '0':
        query = query.having(func.count(Issue.id) > 0)

    total = query.count()
    results = query.order_by(Asset.id.desc()).offset((page - 1) * limit).limit(limit).all()

    def warranty_state(asset):
        if not asset.warranty_expiry:
            return 'unknown'
        if asset.warranty_expiry < today:
            return 'expired'
        if asset.warranty_expiry <= expiry_soon:
            return 'expiring'
        return 'active'

    items = []
    for asset, current_user, open_issues in results:
        from app.services.assets import compute_age
        age = compute_age(asset.purchase_date)
        items.append({
            'id': asset.id,
            'serial_number': asset.serial_number,
            'asset_tag': asset.asset_tag,
            'model': asset.model,
            'manufacturer': asset.manufacturer,
            'category': asset.category,
            'status': asset.status,
            'location': asset.location,
            'department': asset.department,
            'current_user': current_user,
            'open_issues': open_issues,
            'warranty_state': warranty_state(asset),
            'age_label': age['label'],
        })

    return jsonify({'items': items, 'total': total, 'page': page, 'pages': max(1, (total + limit - 1) // limit)})

@bp.route('/assets/model-breakdown')
@login_required
def model_breakdown():
    status_filter = request.args.get('status', 'instock').lower()
    # Per-asset open issue count subquery — same logic as dashboard/stock routes
    issue_cnt = (
        db.session.query(Issue.asset_id.label('asset_id'), func.count(Issue.id).label('cnt'))
        .filter(Issue.status.in_(['open', 'in-progress']))
        .group_by(Issue.asset_id)
        .subquery('api_issue_cnt')
    )
    rows = db.session.query(
        Asset.model,
        func.count(Asset.id).label('total'),
        # working = assets with zero open issues
        func.sum(case((func.coalesce(issue_cnt.c.cnt, 0) == 0, 1), else_=0)).label('working'),
        # not_working = total open issue records (matches issues list count)
        func.sum(func.coalesce(issue_cnt.c.cnt, 0)).label('not_working'),
    ).outerjoin(issue_cnt, issue_cnt.c.asset_id == Asset.id
    ).filter(func.lower(Asset.status) == status_filter
    ).group_by(Asset.model).order_by(func.count(Asset.id).desc()).all()

    return jsonify({'models': [{'model': r.model or 'Unknown', 'total': r.total,
        'working': int(r.working or 0), 'not_working': int(r.not_working or 0)} for r in rows]})


def _build_manager_context():
    """Query the DB and build a rich data context for the assistant."""
    from app.models.ticket import Ticket
    from app.models.category import Category
    from app.models.sla_policy import SLAPolicy
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    thirty_days = now - timedelta(days=30)

    lines = ["=== LIVE DATA SNAPSHOT (as of now) ===\n"]

    # ── Assets ────────────────────────────────────────────────────────────────
    asset_by_status = dict(
        db.session.query(func.lower(Asset.status), func.count(Asset.id))
        .group_by(func.lower(Asset.status)).all()
    )
    total_assets = sum(asset_by_status.values())
    lines.append("ASSETS:")
    lines.append(f"  Total: {total_assets}")
    for status, cnt in sorted(asset_by_status.items(), key=lambda x: -x[1]):
        lines.append(f"  {status.capitalize()}: {cnt}")

    # Warranty expiring in 30 days
    expiring = db.session.query(func.count(Asset.id)).filter(
        Asset.warranty_expiry != None,
        Asset.warranty_expiry >= now,
        Asset.warranty_expiry <= now + timedelta(days=30)
    ).scalar() or 0
    expired = db.session.query(func.count(Asset.id)).filter(
        Asset.warranty_expiry != None,
        Asset.warranty_expiry < now
    ).scalar() or 0
    lines.append(f"  Warranty expiring in 30 days: {expiring}")
    lines.append(f"  Warranty already expired: {expired}")

    # Assets by category (all, any status)
    cat_rows = db.session.query(Asset.category, func.count(Asset.id))\
        .group_by(Asset.category).order_by(func.count(Asset.id).desc()).all()
    if cat_rows:
        lines.append("  Total by category: " + ", ".join(f"{c or 'Unknown'}={n}" for c, n in cat_rows))

    # Deployed assets by category
    dep_cat_rows = db.session.query(Asset.category, func.count(Asset.id))\
        .filter(func.lower(Asset.status) == 'deployed')\
        .group_by(Asset.category).order_by(func.count(Asset.id).desc()).all()
    if dep_cat_rows:
        lines.append("  Deployed by category: " + ", ".join(f"{c or 'Unknown'}={n}" for c, n in dep_cat_rows))

    # Instock assets by category
    stock_cat_rows = db.session.query(Asset.category, func.count(Asset.id))\
        .filter(func.lower(Asset.status) == 'instock')\
        .group_by(Asset.category).order_by(func.count(Asset.id).desc()).all()
    if stock_cat_rows:
        lines.append("  In-stock by category: " + ", ".join(f"{c or 'Unknown'}={n}" for c, n in stock_cat_rows))

    # ── Open Issues ───────────────────────────────────────────────────────────
    open_issues = db.session.query(func.count(Issue.id)).join(
        Asset, Asset.id == Issue.asset_id
    ).filter(
        Issue.status.in_(['open', 'in-progress']),
        func.lower(Asset.status).notin_(['sold', 'retired'])
    ).scalar() or 0
    lines.append(f"\nOPEN HARDWARE/SOFTWARE ISSUES: {open_issues}")

    # ── Helpdesk Tickets ──────────────────────────────────────────────────────
    ticket_by_status = dict(
        db.session.query(Ticket.status, func.count(Ticket.id))
        .group_by(Ticket.status).all()
    )
    total_tickets = sum(ticket_by_status.values())
    open_t  = ticket_by_status.get('open', 0) + ticket_by_status.get('new', 0)
    pending = ticket_by_status.get('pending', 0)
    on_hold = ticket_by_status.get('on_hold', 0)
    solved  = ticket_by_status.get('solved', 0)   # actual status name in DB
    closed  = ticket_by_status.get('closed', 0)
    lines.append(f"\nHELPDESK TICKETS (all time total: {total_tickets}):")
    lines.append(f"  Open/New: {open_t}  |  Pending: {pending}  |  On-hold: {on_hold}")
    lines.append(f"  Solved: {solved}  |  Closed: {closed}  |  Resolved/done: {solved + closed}")

    # By priority (open only)
    prio_rows = db.session.query(Ticket.priority, func.count(Ticket.id))\
        .filter(Ticket.status.in_(['open','new','pending','on_hold']))\
        .group_by(Ticket.priority).all()
    if prio_rows:
        lines.append("  Open by priority: " + ", ".join(f"{p}={n}" for p, n in prio_rows))

    # By category (top 5 open)
    cat_ticket = db.session.query(Category.name, func.count(Ticket.id))\
        .join(Category, Ticket.category_id == Category.id)\
        .filter(Ticket.status.in_(['open','new','pending']))\
        .group_by(Category.name).order_by(func.count(Ticket.id).desc()).limit(5).all()
    if cat_ticket:
        lines.append("  Open tickets by category: " + ", ".join(f"{c}={n}" for c, n in cat_ticket))

    # This month's tickets
    this_month = db.session.query(func.count(Ticket.id)).filter(
        Ticket.created_at >= month_start
    ).scalar() or 0
    resolved_month = db.session.query(func.count(Ticket.id)).filter(
        Ticket.created_at >= month_start,
        Ticket.status.in_(['solved', 'closed'])
    ).scalar() or 0
    lines.append(f"  Created this month: {this_month}  |  Resolved/solved this month: {resolved_month}")

    # SLA stats (this month)
    sla_rows = db.session.query(Ticket.resolution_due, Ticket.solved_at, Ticket.closed_at)\
        .filter(Ticket.created_at >= month_start, Ticket.resolution_due != None).all()
    sla_met = sla_missed = 0
    for row in sla_rows:
        solved = row.solved_at or row.closed_at
        if solved:
            if solved <= row.resolution_due:
                sla_met += 1
            else:
                sla_missed += 1
    if sla_met + sla_missed > 0:
        sla_pct = round(100 * sla_met / (sla_met + sla_missed))
        lines.append(f"  SLA compliance this month: {sla_pct}% ({sla_met} met, {sla_missed} missed)")

    # Avg resolution time (solved or closed this month)
    done_rows = db.session.query(Ticket.created_at, Ticket.solved_at, Ticket.closed_at)\
        .filter(
            Ticket.status.in_(['solved', 'closed']),
            func.coalesce(Ticket.solved_at, Ticket.closed_at) >= month_start,
            func.coalesce(Ticket.solved_at, Ticket.closed_at) != None,
        ).all()
    if done_rows:
        def _resolve_ts(r):
            return r.solved_at or r.closed_at
        hours = [(_resolve_ts(r) - r.created_at).total_seconds() / 3600 for r in done_rows if _resolve_ts(r)]
        if hours:
            avg_h = round(sum(hours) / len(hours), 1)
            lines.append(f"  Avg resolution time this month: {avg_h}h ({len(hours)} tickets)")

    # SLA policies summary
    sla_count = db.session.query(func.count(SLAPolicy.id)).filter(SLAPolicy.is_active == True).scalar() or 0
    lines.append(f"\nACTIVE SLA POLICIES: {sla_count}")

    lines.append("\n=== END OF DATA ===")
    return "\n".join(lines)


def _run_sql_safe(sql: str) -> str:
    """Execute a read-only SELECT/WITH query and return a result string."""
    import re
    from sqlalchemy import text as sa_text
    sql = sql.strip().rstrip(';')
    # Allow SELECT and CTEs (WITH ... SELECT ...)
    if not re.match(r'^\s*(SELECT|WITH)\b', sql, re.IGNORECASE):
        return "ERROR: Only SELECT queries are allowed."
    # Block any data-modification keywords anywhere in the query
    forbidden = re.compile(
        r'\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|EXEC|EXECUTE|COPY)\b',
        re.IGNORECASE,
    )
    if forbidden.search(sql):
        return "ERROR: Query contains disallowed operations."
    if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        sql += ' LIMIT 50'
    try:
        result = db.session.execute(sa_text(sql))
        cols = list(result.keys())
        rows = result.fetchall()
        if not rows:
            return "0 rows returned."
        lines = [" | ".join(cols)]
        lines.append("-" * len(lines[0]))
        for row in rows:
            lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
        return "\n".join(lines)
    except Exception as exc:
        return f"SQL_ERROR: {exc}"


def _gemini_call(api_key: str, messages: list, max_tokens: int = 500) -> str:
    """Call Gemini 2.0 Flash — best free model for reasoning + SQL generation."""
    # Split system message from conversation turns
    system_text = ''
    turns = []
    for m in messages:
        if m['role'] == 'system':
            system_text = m['content']
        else:
            # Gemini uses 'model' instead of 'assistant'
            role = 'model' if m['role'] == 'assistant' else 'user'
            turns.append({'role': role, 'parts': [{'text': m['content']}]})

    payload = {
        'contents': turns,
        'generationConfig': {'temperature': 0.1, 'maxOutputTokens': max_tokens},
    }
    if system_text:
        payload['system_instruction'] = {'parts': [{'text': system_text}]}

    resp = _http.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}',
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['candidates'][0]['content']['parts'][0]['text']


def _groq_call(api_key: str, messages: list, max_tokens: int = 500) -> str:
    """Groq llama-3.3-70b fallback."""
    resp = _http.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={'model': 'llama-3.3-70b-versatile', 'messages': messages,
              'max_tokens': max_tokens, 'temperature': 0.1},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def _llm_call(messages: list, max_tokens: int = 500) -> str:
    """Gemini 2.0 Flash (primary). Falls back to Groq if Gemini fails or key missing."""
    gemini_key = os.environ.get('GEMINI_API_KEY', '') or current_app.config.get('GEMINI_API_KEY', '')
    groq_key = os.environ.get('GROQ_API_KEY', '') or current_app.config.get('GROQ_API_KEY', '')

    if gemini_key:
        try:
            return _gemini_call(gemini_key, messages, max_tokens)
        except Exception as exc:
            current_app.logger.warning('Gemini failed (%s), falling back to Groq', exc)
            if groq_key:
                return _groq_call(groq_key, messages, max_tokens)
            raise
    if groq_key:
        return _groq_call(groq_key, messages, max_tokens)
    raise RuntimeError('No AI key configured. Set GEMINI_API_KEY or GROQ_API_KEY.')


_SQL_SYSTEM = """You are an intelligent IT data assistant for iMocha with access to a live PostgreSQL database.

Your job: translate any manager question into a single PostgreSQL SELECT query.

DATABASE SCHEMA
===============
asset_manager.assets
  id, serial_number, asset_tag, model, manufacturer, category, status,
  location, department, purchase_date (date), warranty_expiry (date), created_at
  status values: instock | deployed | repair | retired | sold | ordered | received

asset_manager.assignments
  id, asset_id, user_name, user_email, assigned_date (date), returned_at (date)
  *** returned_at IS NULL = asset is CURRENTLY assigned to this user ***

asset_manager.issues
  id, asset_id, title, status (open | in-progress | resolved | closed), reported_date

helpdesk.tickets
  id, number, title, description, status, priority,
  requester_name, requester_email, assigned_to, category_id,
  created_at, updated_at, solved_at, closed_at, resolution_due, first_response_due
  *** status: new | open | pending | on_hold | solved | closed ***
  *** OPEN tickets = status IN ('new','open','pending','on_hold') ***
  *** DONE tickets = status IN ('solved','closed') ***
  *** priority: low | medium | high | critical ***

helpdesk.categories — id, name, parent_id (NULL = top-level)
helpdesk.sla_policies — id, name, is_active, first_response_hours, resolution_hours

OUTPUT RULES
============
1. If the question is a greeting, thanks, or completely off-topic: reply conversationally in plain text.
2. For ALL data questions — even simple ones like "how many assets" — output ONLY:
   ```sql
   SELECT ...
   ```
   Nothing before or after the code block.
3. Use conversation history to understand follow-up questions.
   e.g. if user previously asked about laptops and now says "what about desktops?" — query desktops.
4. ALWAYS use fully-qualified table names: asset_manager.assets, helpdesk.tickets, etc.
5. Use LOWER() for category/status comparisons (data may have mixed case).
6. Use CURRENT_DATE and DATE_TRUNC for date logic.
7. Add LIMIT 50 on list queries. Omit LIMIT only for single-value aggregates.

QUERY PATTERNS (memorise these):
-- Currently assigned assets per user
SELECT user_name, COUNT(*) AS assets
FROM asset_manager.assignments WHERE returned_at IS NULL
GROUP BY user_name ORDER BY assets DESC LIMIT 20;

-- Deployed count for a category (e.g. laptop)
SELECT COUNT(*) AS count FROM asset_manager.assets
WHERE LOWER(status)='deployed' AND LOWER(category)='laptop';

-- Open tickets by priority
SELECT priority, COUNT(*) AS count FROM helpdesk.tickets
WHERE status IN ('new','open','pending','on_hold')
GROUP BY priority ORDER BY count DESC;

-- Tickets this month per requester
SELECT requester_name, COUNT(*) AS tickets FROM helpdesk.tickets
WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY requester_name ORDER BY tickets DESC LIMIT 10;

-- Warranty expired but still deployed
SELECT serial_number, model, category, warranty_expiry FROM asset_manager.assets
WHERE warranty_expiry < CURRENT_DATE AND LOWER(status)='deployed'
ORDER BY warranty_expiry LIMIT 50;

-- Assets assigned to a specific person
SELECT a.serial_number, a.model, a.category, a.status, asn.assigned_date
FROM asset_manager.assets a
JOIN asset_manager.assignments asn ON asn.asset_id=a.id AND asn.returned_at IS NULL
WHERE LOWER(asn.user_name) LIKE LOWER('%john%');

-- SLA breached tickets (past resolution_due and still open)
SELECT number, title, requester_name, resolution_due, priority
FROM helpdesk.tickets
WHERE status IN ('new','open','pending','on_hold') AND resolution_due < NOW()
ORDER BY resolution_due LIMIT 20;
"""

_FORMAT_SYSTEM = """You are a friendly, intelligent IT operations assistant — think of yourself like ChatGPT \
but specialised for IT managers at iMocha.

You just ran a live database query to answer the manager's question. Now write a natural, helpful response.

HOW TO RESPOND:
- Be conversational and warm, like a knowledgeable colleague — not a cold data report.
- Lead with the direct answer (the key number or finding) in the FIRST sentence.
- For lists of 3+ items, use bullet points with a short label and value on each line.
- After the data, add ONE brief insight or observation if it's meaningful \
  (e.g. "That's quite high — worth looking into", "All 3 are critical priority").
- If the result is 0 rows or empty, say so clearly and helpfully suggest what to check.
- NEVER mention SQL, queries, databases, or technical terms.
- NEVER say "based on the data" or "according to the results" — just answer directly.
- Keep it under 150 words. Be crisp and useful."""


@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    """Manager assistant — Gemini 2.0 Flash with Groq fallback."""
    import re
    has_gemini = bool(os.environ.get('GEMINI_API_KEY', '') or current_app.config.get('GEMINI_API_KEY', ''))
    has_groq = bool(os.environ.get('GROQ_API_KEY', '') or current_app.config.get('GROQ_API_KEY', ''))
    if not has_gemini and not has_groq:
        return jsonify({'reply': '⚠️ No AI key configured. Add GEMINI_API_KEY or GROQ_API_KEY to your .env file.'}), 200

    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])
    if not messages:
        return jsonify({'error': 'No messages'}), 400

    # ── Pass 1: generate SQL from the question ─────────────────────────────────
    try:
        sql_reply = _llm_call(
            [{'role': 'system', 'content': _SQL_SYSTEM}] + messages,
            max_tokens=500,
        )
    except _http.exceptions.Timeout:
        return jsonify({'reply': 'Request timed out. Please try again.'}), 200
    except Exception as exc:
        current_app.logger.error('Chat SQL-gen error: %s', exc)
        return jsonify({'reply': 'Something went wrong. Please try again.'}), 200

    sql_match = re.search(r'```(?:sql)?\s*((?:SELECT|WITH).*?)```', sql_reply, re.IGNORECASE | re.DOTALL)
    if not sql_match:
        # Conversational reply (greeting, off-topic, etc.)
        return jsonify({'reply': sql_reply})

    sql = sql_match.group(1).strip()
    current_app.logger.info('Assistant SQL: %s', sql)
    query_result = _run_sql_safe(sql)

    # ── Auto-retry if SQL errored ──────────────────────────────────────────────
    if query_result.startswith('SQL_ERROR'):
        retry_msgs = messages + [
            {'role': 'assistant', 'content': sql_reply},
            {'role': 'user', 'content': f'That query failed with: {query_result}. Fix it and return only the corrected SQL.'},
        ]
        try:
            sql_reply2 = _llm_call([{'role': 'system', 'content': _SQL_SYSTEM}] + retry_msgs, max_tokens=500)
            m2 = re.search(r'```(?:sql)?\s*((?:SELECT|WITH).*?)```', sql_reply2, re.IGNORECASE | re.DOTALL)
            if m2:
                query_result = _run_sql_safe(m2.group(1).strip())
        except Exception:
            pass

    # ── Pass 2: format into a natural, ChatGPT-like response ──────────────────
    history_for_format = list(messages[:-1]) + [{
        'role': 'user',
        'content': f"{messages[-1]['content']}\n\n[Live data]:\n{query_result}",
    }]
    try:
        final_reply = _llm_call(
            [{'role': 'system', 'content': _FORMAT_SYSTEM}] + history_for_format,
            max_tokens=450,
        )
        return jsonify({'reply': final_reply})
    except Exception as exc:
        current_app.logger.error('Chat format error: %s', exc)
        return jsonify({'reply': 'Something went wrong formatting the result. Please try again.'}), 200
