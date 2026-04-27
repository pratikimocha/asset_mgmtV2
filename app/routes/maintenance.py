"""Maintenance routes."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import date, datetime
from sqlalchemy import func
from app.auth.decorators import login_required, role_required
from app.models import Asset, MaintenanceTask
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("maintenance", __name__, url_prefix="/maintenance")

@bp.route("/")
@login_required
def list():
    status_f = request.args.get("status", "").strip()
    today = date.today()
    query = db.session.query(MaintenanceTask, Asset).join(Asset, Asset.id == MaintenanceTask.asset_id)
    if status_f:
        query = query.filter(func.lower(MaintenanceTask.status) == status_f.lower())
    results = query.order_by(MaintenanceTask.scheduled_date.desc()).all()
    overdue_count = sum(1 for t, a in results if t.status == "scheduled" and t.scheduled_date and t.scheduled_date < today)
    return render_template("maintenance/list.html", tasks=results, status_f=status_f, overdue_count=overdue_count, today=today)

@bp.route("/schedule", methods=["POST"])
@role_required("manager", "admin")
def schedule():
    asset_id = request.form.get("asset_id", type=int)
    task_type = request.form.get("task_type", "").strip()
    scheduled_date_str = request.form.get("scheduled_date", "").strip()
    assigned_to = request.form.get("assigned_to", "").strip()
    notes = request.form.get("notes", "").strip()

    if not asset_id:
        flash("Asset required.", "error")
        return redirect(url_for("maintenance.list"))

    Asset.query.get_or_404(asset_id)
    try:
        scheduled_date = datetime.strptime(scheduled_date_str, "%Y-%m-%d").date() if scheduled_date_str else date.today()
    except ValueError:
        scheduled_date = date.today()

    task = MaintenanceTask(
        asset_id=asset_id, task_type=task_type, scheduled_date=scheduled_date,
        assigned_to=assigned_to, notes=notes, status="scheduled",
    )
    db.session.add(task)
    db.session.commit()
    log_activity("maintenance_scheduled", "maintenance", task.id, f"Asset {asset_id}: {task_type}")
    flash("Maintenance scheduled.", "success")
    return redirect(url_for("maintenance.list"))

@bp.route("/<int:task_id>/complete", methods=["POST"])
@role_required("manager", "admin")
def complete(task_id):
    task = MaintenanceTask.query.get_or_404(task_id)
    task.status = "completed"
    task.completed_date = date.today()
    db.session.commit()
    log_activity("maintenance_completed", "maintenance", task_id)
    flash("Maintenance marked complete.", "success")
    return redirect(url_for("maintenance.list"))
