"""Assets routes."""
import os
import csv
import io
from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, send_file, make_response
from sqlalchemy import func
from werkzeug.utils import secure_filename

from app.auth.decorators import login_required, role_required
from app.models import Asset, Assignment, Issue, LifecycleEvent
from app.models.asset import StatusEnum
from app.services.audit import log_activity
from app.services.export import export_assets_to_csv, export_assets_to_xlsx
from app.extensions import db

bp = Blueprint("assets", __name__, url_prefix="/assets")

VALID_STATUSES = StatusEnum.values()

def _parse_date(s):
    if not s or not s.strip():
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None

def _safe_float(s):
    try:
        return float(str(s).strip()) if s else None
    except (ValueError, TypeError):
        return None

# ─── List ──────────────────────────────────────────────────────────────────
@bp.route("/view")
@bp.route("/")
@login_required
def list():
    q = request.args.get("q", "").strip()
    status_f = request.args.get("status", "").strip()
    category_f = request.args.get("category", "").strip()
    warranty_f = request.args.get("warranty", "").strip()
    model_f = request.args.get("model_f", "").strip()   # exact model (from Stock Intelligence)
    issues_f = request.args.get("issues", "").strip()   # "1"=has open issues  "0"=no open issues
    page = max(1, request.args.get("page", 1, type=int))
    per_page = current_app.config.get("ITEMS_PER_PAGE", 50)

    issue_counts = (
        db.session.query(
            Issue.asset_id.label("asset_id"),
            func.count(Issue.id).label("open_issues_count"),
        )
        .filter(Issue.status.in_(["open", "in-progress"]))
        .group_by(Issue.asset_id)
        .subquery("issue_counts")
    )

    query = db.session.query(
        Asset,
        Assignment.user_name.label("current_user"),
        func.coalesce(issue_counts.c.open_issues_count, 0).label("open_issues_count"),
    ).outerjoin(
        Assignment,
        (Assignment.asset_id == Asset.id) & Assignment.returned_at.is_(None)
    ).outerjoin(
        issue_counts,
        issue_counts.c.asset_id == Asset.id,
    )

    if q:
        query = query.filter(
            Asset.serial_number.ilike(f"%{q}%") |
            Asset.model.ilike(f"%{q}%") |
            Asset.manufacturer.ilike(f"%{q}%") |
            Assignment.user_name.ilike(f"%{q}%")
        )
    if model_f:
        query = query.filter(func.lower(Asset.model) == model_f.lower())
    if status_f:
        query = query.filter(func.lower(Asset.status) == status_f.lower())
    if category_f:
        query = query.filter(func.lower(Asset.category) == category_f.lower())

    today = datetime.utcnow().date()
    from datetime import timedelta
    expiry_soon = today + timedelta(days=60)
    if warranty_f == "expired":
        query = query.filter(Asset.warranty_expiry < today)
    elif warranty_f == "expiring":
        query = query.filter(Asset.warranty_expiry.between(today, expiry_soon))
    elif warranty_f == "active":
        query = query.filter(Asset.warranty_expiry >= expiry_soon)

    if issues_f == "1":
        query = query.filter(func.coalesce(issue_counts.c.open_issues_count, 0) > 0)
    elif issues_f == "0":
        query = query.filter(func.coalesce(issue_counts.c.open_issues_count, 0) == 0)

    total = query.count()
    rows = query.order_by(Asset.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    assets = []
    for asset, current_user, open_issues_count in rows:
        asset.current_user = current_user
        asset.current_open_issues_count = int(open_issues_count or 0)
        assets.append(asset)
    pages = max(1, (total + per_page - 1) // per_page)

    categories = [r[0] for r in db.session.query(func.distinct(Asset.category)).filter(Asset.category.isnot(None)).all()]

    resp = make_response(render_template("assets/list.html",
        assets=assets, page=page, pages=pages, total=total,
        q=q, status_f=status_f, category_f=category_f, warranty_f=warranty_f,
        model_f=model_f, issues_f=issues_f,
        categories=categories, valid_statuses=VALID_STATUSES))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp

# ─── Add ───────────────────────────────────────────────────────────────────
@bp.route("/add", methods=["GET", "POST"])
@role_required("manager", "admin")
def add():
    if request.method == "POST":
        serial = request.form.get("serial_number", "").strip()
        if not serial:
            flash("Serial number is required.", "error")
            return render_template("assets/add_edit.html", asset=None, valid_statuses=VALID_STATUSES, categories=_get_categories()), 400

        if Asset.query.filter_by(serial_number=serial).first():
            flash(f"Asset {serial} already exists.", "error")
            return render_template("assets/add_edit.html", asset=None, valid_statuses=VALID_STATUSES, categories=_get_categories()), 400

        status = request.form.get("status", "instock").strip().lower()
        if status not in VALID_STATUSES:
            status = "instock"

        asset = Asset(
            serial_number=serial,
            asset_tag=request.form.get("asset_tag", "").strip() or None,
            model=request.form.get("model", "").strip(),
            manufacturer=request.form.get("manufacturer", "").strip(),
            category=request.form.get("category", "").strip(),
            status=status,
            purchase_date=_parse_date(request.form.get("purchase_date")),
            warranty_expiry=_parse_date(request.form.get("warranty_expiry")),
            cost=_safe_float(request.form.get("cost")),
            vendor=request.form.get("vendor", "").strip() or None,
            location=request.form.get("location", "").strip() or None,
            department=request.form.get("department", "").strip() or None,
        )
        db.session.add(asset)
        db.session.commit()
        log_activity("asset_created", "asset", asset.id, f"Serial: {serial}")
        flash(f"Asset {serial} added successfully.", "success")
        return redirect(url_for("assets.detail", asset_id=asset.id))

    return render_template("assets/add_edit.html", asset=None, valid_statuses=VALID_STATUSES, categories=_get_categories())

# ─── Detail ────────────────────────────────────────────────────────────────
@bp.route("/<int:asset_id>")
@login_required
def detail(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    assignments = Assignment.query.filter_by(asset_id=asset_id).order_by(Assignment.assigned_date.desc()).all()
    open_issues = Issue.query.filter_by(asset_id=asset_id).filter(Issue.status.in_(["open", "in-progress"])).all()
    closed_issues = Issue.query.filter_by(asset_id=asset_id, status="closed").order_by(Issue.date_reported.desc()).all()
    active_assignment = next((a for a in assignments if a.returned_at is None), None)
    from app.models.ticket import Ticket
    helpdesk_tickets = Ticket.query.filter_by(asset_id=asset_id).order_by(Ticket.created_at.desc()).all()
    from app.services.assets import compute_age, compute_warranty_state, compute_health_score
    age_details = compute_age(asset.purchase_date)
    warranty_state = compute_warranty_state(asset.warranty_expiry)
    health_score = compute_health_score(
        age_years=age_details["years"],
        open_issues=len(open_issues),
        repair_count=len(asset.repairs),
    )
    return render_template("assets/detail.html",
        asset=asset, assignments=assignments,
        open_issues=open_issues, closed_issues=closed_issues,
        helpdesk_tickets=helpdesk_tickets,
        active_assignment=active_assignment,
        age_details=age_details,
        warranty_state=warranty_state,
        health_score=health_score,
        valid_statuses=VALID_STATUSES)

# ─── Edit ──────────────────────────────────────────────────────────────────
@bp.route("/<int:asset_id>/edit", methods=["GET", "POST"])
@role_required("manager", "admin")
def edit(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    if request.method == "POST":
        old_status = asset.status
        new_status = request.form.get("status", asset.status).strip().lower()
        if new_status not in VALID_STATUSES:
            new_status = asset.status

        asset.serial_number = request.form.get("serial_number", asset.serial_number).strip()
        asset.asset_tag = request.form.get("asset_tag", "").strip() or None
        asset.model = request.form.get("model", asset.model).strip()
        asset.manufacturer = request.form.get("manufacturer", asset.manufacturer).strip()
        asset.category = request.form.get("category", asset.category).strip()
        asset.status = new_status
        asset.purchase_date = _parse_date(request.form.get("purchase_date")) or asset.purchase_date
        asset.warranty_expiry = _parse_date(request.form.get("warranty_expiry")) or asset.warranty_expiry
        asset.cost = _safe_float(request.form.get("cost")) or asset.cost
        asset.vendor = request.form.get("vendor", "").strip() or asset.vendor
        asset.location = request.form.get("location", "").strip() or asset.location
        asset.department = request.form.get("department", "").strip() or asset.department

        # Close active assignment if status changed away from deployed
        if old_status != new_status and new_status in ("instock", "repair", "retired", "sold"):
            active = Assignment.query.filter_by(asset_id=asset_id).filter(Assignment.returned_at.is_(None)).first()
            if active:
                active.returned_at = date.today()
                active.condition_on_return = "Good"

        db.session.commit()
        log_activity("asset_updated", "asset", asset_id, f"Status: {old_status}→{new_status}")
        flash("Asset updated successfully.", "success")
        return redirect(url_for("assets.detail", asset_id=asset_id))

    return render_template("assets/add_edit.html", asset=asset, valid_statuses=VALID_STATUSES, categories=_get_categories())

# ─── Delete ────────────────────────────────────────────────────────────────
@bp.route("/<int:asset_id>/delete", methods=["POST"])
@role_required("admin")
def delete(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    serial = asset.serial_number
    db.session.delete(asset)
    db.session.commit()
    log_activity("asset_deleted", "asset", asset_id, f"Serial: {serial}")
    flash(f"Asset {serial} deleted.", "success")
    return redirect(url_for("assets.list"))

# ─── Update status ─────────────────────────────────────────────────────────
@bp.route("/<int:asset_id>/status", methods=["POST"])
@role_required("manager", "admin")
def update_status(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    new_status = request.form.get("status", "").strip().lower()
    notes = request.form.get("notes", "").strip()
    sold_to_name = request.form.get("sold_to_name", "").strip()
    sold_type = request.form.get("sold_type", "external").strip().lower()
    if sold_type not in ("internal", "external"):
        sold_type = "external"

    if new_status not in VALID_STATUSES:
        flash("Invalid status.", "error")
        return redirect(url_for("assets.detail", asset_id=asset_id))

    old_status = asset.status

    # Handle sold_to + sold_type
    if new_status == "sold":
        asset.sold_to = sold_to_name or asset.sold_to
        asset.sold_type = sold_type

    # Close active assignment when leaving deployed
    if new_status in ("instock", "repair", "retired", "sold"):
        active = Assignment.query.filter_by(asset_id=asset_id).filter(Assignment.returned_at.is_(None)).first()
        if active:
            active.returned_at = date.today()
            active.condition_on_return = "Good"

    asset.status = new_status

    # Auto-close open issues when asset leaves the org (sold or retired)
    if new_status in ("sold", "retired"):
        open_issues = Issue.query.filter_by(asset_id=asset_id).filter(
            Issue.status.in_(["open", "in-progress"])
        ).all()
        for issue in open_issues:
            issue.status = "closed"

    # Lifecycle event
    from flask import session
    db.session.add(LifecycleEvent(
        asset_id=asset_id,
        event_type="status_changed",
        event_notes=notes or f"Status changed from {old_status} to {new_status}",
        status_after=new_status,
        event_date=date.today(),
        performed_by=session.get("user", {}).get("name", "Unknown"),
    ))
    db.session.commit()
    log_activity("status_changed", "asset", asset_id, f"{old_status}→{new_status}")
    flash(f"Status updated to {new_status.title()}.", "success")
    return redirect(url_for("assets.detail", asset_id=asset_id))

# ─── Bulk upload ───────────────────────────────────────────────────────────
WORKING_NOTES = {"working", "ok", "good", "fine", "normal", "functional", "no issue", "no issues"}

@bp.route("/bulk-upload", methods=["GET", "POST"])
@role_required("manager", "admin")
def bulk_upload():
    if request.method == "GET":
        return render_template("assets/bulk_upload.html")

    f = request.files.get("csvfile")
    if not f or not f.filename.lower().endswith(".csv"):
        flash("Please upload a .csv file.", "error")
        return redirect(url_for("assets.bulk_upload"))

    try:
        text = f.read().decode("utf-8-sig", errors="replace")
    except Exception as e:
        flash(f"Could not read file: {e}", "error")
        return redirect(url_for("assets.bulk_upload"))

    reader = csv.DictReader(io.StringIO(text))

    # Validate headers
    required_field = "serial_number"
    fieldnames = reader.fieldnames or []
    if required_field not in fieldnames:
        flash(f"CSV is missing required column 'serial_number'. Found columns: {', '.join(fieldnames) or 'none'}", "error")
        return redirect(url_for("assets.bulk_upload"))

    inserted = skipped_dup = skipped_bad_status = skipped_validation = 0
    errors = []

    try:
        for i, row in enumerate(reader, start=2):
            serial = (row.get("serial_number") or "").strip()
            model = (row.get("model") or "").strip()
            manufacturer = (row.get("manufacturer") or "").strip()
            status = (row.get("status") or "instock").strip().lower()

            if not serial:
                continue

            # Validate required fields per row
            row_errors = []
            if not model:
                row_errors.append("model is required")
            if not manufacturer:
                row_errors.append("manufacturer is required")
            if status not in VALID_STATUSES:
                row_errors.append(f"invalid status '{status}'")

            assigned_to = (row.get("assigned_to") or "").strip()
            if status == "deployed" and not assigned_to:
                row_errors.append("assigned_to is required when status is 'deployed'")

            if row_errors:
                skipped_validation += 1
                errors.append(f"Row {i} ({serial or 'no serial'}): {', '.join(row_errors)}")
                continue

            if Asset.query.filter_by(serial_number=serial).first():
                skipped_dup += 1
                continue

            asset = Asset(
                serial_number=serial,
                asset_tag=(row.get("asset_tag") or "").strip() or None,
                model=model,
                manufacturer=manufacturer,
                category=(row.get("category") or "").strip() or None,
                status=status,
                purchase_date=_parse_date(row.get("purchase_date")),
                warranty_expiry=_parse_date(row.get("warranty_expiry")),
                cost=_safe_float(row.get("cost")),
                vendor=(row.get("vendor") or "").strip() or None,
                location=(row.get("location") or "").strip() or None,
                department=(row.get("department") or "").strip() or None,
            )
            db.session.add(asset)
            db.session.flush()

            # Create assignment if assigned_to present (required for deployed, optional otherwise)
            if assigned_to and assigned_to.lower() not in ("it", ""):
                from flask import session
                db.session.add(Assignment(
                    asset_id=asset.id,
                    user_name=assigned_to,
                    assigned_date=_parse_date(row.get("assigned_date")) or date.today(),
                    returned_at=None,
                    assigned_by="Bulk Import",
                ))

            # Create issue if issue_text present and not a "working" note
            issue_text = (row.get("issue_text") or "").strip()
            if issue_text and issue_text.lower() not in WORKING_NOTES and not issue_text.lower().startswith("working"):
                severity = (row.get("issue_severity") or "medium").strip().lower()
                if severity not in ("low", "medium", "high"):
                    severity = "medium"
                db.session.add(Issue(
                    asset_id=asset.id,
                    issue_text=issue_text,
                    severity=severity,
                    status="open",
                    date_reported=date.today(),
                    reported_by="Bulk Import",
                ))

            # Create lifecycle event if lifecycle_event column is populated
            lifecycle_note = (row.get("lifecycle_event") or "").strip()
            if lifecycle_note:
                lc_type = (row.get("lifecycle_event_type") or "note").strip().lower() or "note"
                lc_date = _parse_date(row.get("lifecycle_event_date")) or asset.purchase_date or date.today()
                db.session.add(LifecycleEvent(
                    asset_id=asset.id,
                    event_type=lc_type,
                    event_notes=lifecycle_note,
                    status_after=status,
                    event_date=lc_date,
                    performed_by="Bulk Import",
                ))

            inserted += 1

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Import failed: {e}", "error")
        return redirect(url_for("assets.bulk_upload"))

    log_activity("bulk_upload", "asset", None, f"Imported {inserted}, skipped {skipped_dup} duplicates, {skipped_validation} validation errors")
    parts = [f"Imported {inserted} assets."]
    if skipped_dup:
        parts.append(f"{skipped_dup} duplicate(s) skipped.")
    if skipped_validation:
        parts.append(f"{skipped_validation} row(s) skipped due to missing required fields.")
    flash(" ".join(parts), "success" if inserted > 0 else "warning")
    if errors:
        for e in errors[:5]:
            flash(e, "warning")
    return redirect(url_for("assets.list"))

# ─── Bulk action ───────────────────────────────────────────────────────────
@bp.route("/bulk-action", methods=["POST"])
@role_required("manager", "admin")
def bulk_action():
    from flask import session
    data = request.get_json() or {}
    action = data.get("action")
    asset_ids = [int(i) for i in (data.get("asset_ids") or []) if str(i).isdigit()]

    if not asset_ids:
        return jsonify({"ok": False, "error": "No assets selected"}), 400

    if action == "delete":
        from flask import session as sess
        if sess.get("user", {}).get("role") != "admin":
            return jsonify({"ok": False, "error": "Admin required for bulk delete"}), 403
        count = Asset.query.filter(Asset.id.in_(asset_ids)).delete(synchronize_session=False)
        db.session.commit()
        log_activity("bulk_delete", "asset", None, f"Deleted {count} assets")
        return jsonify({"ok": True, "count": count})

    elif action == "status_change":
        new_status = data.get("new_status", "").lower()
        if new_status not in VALID_STATUSES:
            return jsonify({"ok": False, "error": "Invalid status"}), 400
        count = Asset.query.filter(Asset.id.in_(asset_ids)).update({"status": new_status}, synchronize_session=False)
        db.session.commit()
        log_activity("bulk_status_change", "asset", None, f"Changed {count} assets to {new_status}")
        return jsonify({"ok": True, "count": count})

    return jsonify({"ok": False, "error": "Unknown action"}), 400

# ─── Sold assets ───────────────────────────────────────────────────────────
@bp.route("/sold")
@login_required
def sold():
    filter_type = request.args.get("type", "").strip().lower()  # 'internal', 'external', or ''
    query = Asset.query.filter(func.lower(Asset.status) == "sold")
    if filter_type in ("internal", "external"):
        query = query.filter(func.lower(Asset.sold_type) == filter_type)
    assets = query.order_by(Asset.updated_at.desc()).all()

    # Export CSV
    if request.args.get("export") == "1":
        import csv as _csv
        import io as _io
        out = _io.StringIO()
        writer = _csv.writer(out)
        writer.writerow(["Serial Number", "Asset Tag", "Model", "Manufacturer", "Category",
                         "Sale Type", "Sold To", "Purchase Cost", "Purchase Date", "Sold Date"])
        for a in assets:
            writer.writerow([
                a.serial_number, a.asset_tag or "", a.model or "", a.manufacturer or "",
                a.category or "", (a.sold_type or "external").title(), a.sold_to or "",
                a.cost or "", a.purchase_date or "", a.updated_at.date() if a.updated_at else "",
            ])
        label = f"sold_{filter_type or 'all'}.csv"
        return send_file(
            io.BytesIO(out.getvalue().encode("utf-8")),
            download_name=label, as_attachment=True, mimetype="text/csv"
        )

    all_sold = Asset.query.filter(func.lower(Asset.status) == "sold")
    counts = {
        "all":      all_sold.count(),
        "internal": all_sold.filter(func.lower(Asset.sold_type) == "internal").count(),
        "external": all_sold.filter(
            (func.lower(Asset.sold_type) == "external") | Asset.sold_type.is_(None)
        ).count(),
    }
    resp = make_response(render_template("assets/sold.html",
        assets=assets, filter_type=filter_type, counts=counts))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp

# ─── Export (via reports, but accessible here) ─────────────────────────────
@bp.route("/export")
@login_required
def export():
    fmt = request.args.get("format", "csv")
    status_f = request.args.get("status", "").strip()
    query = Asset.query
    if status_f:
        query = query.filter(func.lower(Asset.status) == status_f.lower())
    assets = query.all()

    if fmt == "xlsx":
        data = export_assets_to_xlsx(assets)
        return send_file(io.BytesIO(data), download_name="assets.xlsx",
            as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        data = export_assets_to_csv(assets)
        return send_file(io.BytesIO(data), download_name="assets.csv",
            as_attachment=True, mimetype="text/csv")

# ─── Helpers ───────────────────────────────────────────────────────────────
def _get_categories():
    return [r[0] for r in db.session.query(func.distinct(Asset.category)).filter(Asset.category.isnot(None)).all()]
