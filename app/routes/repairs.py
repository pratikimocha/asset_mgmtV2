"""Repairs routes."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from datetime import date
from sqlalchemy import func
from app.auth.decorators import login_required, role_required
from app.models import Asset, Repair, Issue, LifecycleEvent
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("repairs", __name__)

@bp.route("/repairs/")
@bp.route("/view/repairs")
@login_required
def list():
    status_f = request.args.get("status", "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50
    query = db.session.query(Repair, Asset).join(Asset, Asset.id == Repair.asset_id)
    if status_f:
        query = query.filter(func.lower(Repair.status) == status_f.lower())
    total = query.count()
    results = query.order_by(Repair.repair_date.desc()).offset((page-1)*per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template("repairs/list.html", repairs=results, page=page, pages=pages, total=total, status_f=status_f)

@bp.route("/repairs/<int:repair_id>/complete", methods=["POST"])
@role_required("manager", "admin")
def complete(repair_id):
    repair = Repair.query.get_or_404(repair_id)
    repair.action_taken = request.form.get("action_taken", "").strip()
    repair.cost = float(request.form.get("repair_cost", 0) or 0) or None
    repair.status = "completed"
    repair.repair_date = date.today()
    status_after = request.form.get("status_after", "instock").strip().lower()

    if repair.issue_id:
        issue = Issue.query.get(repair.issue_id)
        if issue:
            issue.status = "closed"

    asset = repair.asset
    asset.status = status_after
    db.session.add(LifecycleEvent(
        asset_id=asset.id, event_type="repair_completed",
        event_notes=repair.action_taken or "Repair completed",
        status_after=status_after, event_date=date.today(),
        performed_by=session.get("user", {}).get("name", "Unknown"),
    ))
    db.session.commit()
    log_activity("repair_completed", "repair", repair_id, f"Asset {asset.serial_number} → {status_after}")
    flash("Repair completed.", "success")
    return redirect(url_for("repairs.list"))

@bp.route("/repairs/<int:repair_id>/delete", methods=["POST"])
@role_required("admin")
def delete(repair_id):
    repair = Repair.query.get_or_404(repair_id)
    db.session.delete(repair)
    db.session.commit()
    log_activity("repair_deleted", "repair", repair_id)
    flash("Repair deleted.", "success")
    return redirect(url_for("repairs.list"))
