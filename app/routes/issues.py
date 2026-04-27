"""Issues routes."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import date, datetime
from sqlalchemy import func
from app.auth.decorators import login_required, role_required
from app.models import Asset, Issue, Repair
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("issues", __name__)

WORKING_NOTES = {"working", "ok", "good", "fine", "normal"}

@bp.route("/issues/")
@bp.route("/view/issues")
@login_required
def list():
    status_f  = request.args.get("status",   "").strip()
    severity_f = request.args.get("severity", "").strip()
    model_f   = request.args.get("model_f",  "").strip()
    q         = request.args.get("q",        "").strip()
    page      = max(1, request.args.get("page", 1, type=int))
    per_page  = 50

    query = db.session.query(Issue, Asset).join(Asset, Asset.id == Issue.asset_id).filter(
        func.lower(Asset.status).notin_(["sold", "retired"])
    )
    if status_f:
        query = query.filter(func.lower(Issue.status) == status_f.lower())
    else:
        query = query.filter(Issue.status.in_(["open", "in-progress"]))
    if severity_f:
        query = query.filter(func.lower(Issue.severity) == severity_f.lower())
    if model_f:
        query = query.filter(func.lower(Asset.model) == model_f.lower())
    if q:
        query = query.filter(Issue.issue_text.ilike(f"%{q}%") | Asset.serial_number.ilike(f"%{q}%"))

    total = query.count()
    results = query.order_by(Issue.date_reported.desc()).offset((page-1)*per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template("issues/list.html",
        issues=results, page=page, pages=pages, total=total,
        status_f=status_f, severity_f=severity_f, model_f=model_f, q=q)

@bp.route("/issues/add", methods=["POST"])
@bp.route("/add_issue", methods=["POST"])
@role_required("manager", "admin")
def add():
    asset_id = request.form.get("asset_id", type=int)
    issue_text = request.form.get("issue_text", "").strip()
    severity = request.form.get("severity", "medium").strip().lower()
    if severity not in ("low", "medium", "high"):
        severity = "medium"

    if not asset_id:
        flash("Asset ID required.", "error")
        return redirect(request.referrer or url_for("issues.list"))

    asset = Asset.query.get_or_404(asset_id)
    if not issue_text:
        flash("Issue description required.", "error")
        return redirect(url_for("assets.detail", asset_id=asset_id))

    if issue_text.lower() in WORKING_NOTES or issue_text.lower().startswith("working"):
        flash("\"Working\" notes don\'t create issues. Asset is marked as working.", "info")
        return redirect(url_for("assets.detail", asset_id=asset_id))

    issue = Issue(
        asset_id=asset_id,
        issue_text=issue_text,
        severity=severity,
        status="open",
        date_reported=date.today(),
        reported_by=session.get("user", {}).get("name", "Unknown"),
    )
    db.session.add(issue)
    db.session.commit()
    log_activity("issue_created", "issue", issue.id, f"Asset {asset.serial_number}: {issue_text[:50]}")
    flash("Issue reported.", "success")
    return redirect(url_for("assets.detail", asset_id=asset_id))

@bp.route("/issues/<int:issue_id>/resolve", methods=["POST"])
@role_required("manager", "admin")
def resolve(issue_id):
    """Mark issue as resolved. If it was in-progress (repair started) and no other
    active issues remain, complete the repair and set asset back to instock."""
    issue = Issue.query.get_or_404(issue_id)
    asset = issue.asset
    was_in_progress = issue.status == "in-progress"
    issue.status = "closed"

    if was_in_progress and asset and asset.status == "repair":
        other_active = Issue.query.filter(
            Issue.asset_id == asset.id,
            Issue.id != issue.id,
            Issue.status.in_(["open", "in-progress"])
        ).count()
        if other_active == 0:
            Repair.query.filter_by(asset_id=asset.id, status="in-progress").update(
                {"status": "completed"}, synchronize_session=False
            )
            asset.status = "instock"

    db.session.commit()
    log_activity("issue_resolved", "issue", issue_id,
                 f"Issue {issue_id} marked resolved on asset {issue.asset_id}")
    return jsonify({"ok": True})


@bp.route("/issues/<int:issue_id>/delete", methods=["POST"])
@role_required("manager", "admin")
def delete(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    asset_id = issue.asset_id
    asset = issue.asset
    was_active = issue.status in ("open", "in-progress")

    # Check OTHER active issues before deleting this one
    other_active = 0
    if was_active and asset and asset.status == "repair":
        other_active = Issue.query.filter(
            Issue.asset_id == asset_id,
            Issue.id != issue_id,
            Issue.status.in_(["open", "in-progress"])
        ).count()

    db.session.delete(issue)

    # If asset is stuck in repair with no remaining active issues, reset it
    if was_active and asset and asset.status == "repair" and other_active == 0:
        Repair.query.filter_by(asset_id=asset_id, status="in-progress").update(
            {"status": "completed"}, synchronize_session=False
        )
        asset.status = "instock"

    db.session.commit()
    log_activity("issue_deleted", "issue", issue_id)
    if request.headers.get("X-CSRFToken"):
        return jsonify({"ok": True})
    flash("Issue deleted.", "success")
    return redirect(url_for("assets.detail", asset_id=asset_id))

@bp.route("/issues/<int:issue_id>/start_repair", methods=["POST"])
@bp.route("/issues/<int:issue_id>/start-repair", methods=["POST"])
@role_required("manager", "admin")
def start_repair(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    asset = issue.asset
    repair = Repair(
        asset_id=asset.id,
        issue_id=issue_id,
        repair_text=f"Repair started for: {issue.issue_text}",
        status="in-progress",
        repair_date=date.today(),
        repaired_by=session.get("user", {}).get("name", "Unknown"),
    )
    db.session.add(repair)
    issue.status = "in-progress"
    asset.status = "repair"
    db.session.commit()
    log_activity("repair_started", "repair", repair.id, f"Issue {issue_id} on asset {asset.serial_number}")
    return jsonify({"ok": True, "message": "Repair started"})
