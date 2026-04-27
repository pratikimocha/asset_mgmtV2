"""Assignments routes."""
from flask import Blueprint, render_template, request, jsonify, session
from datetime import date, datetime
from sqlalchemy import func
from app.auth.decorators import login_required, role_required
from app.models import Asset, Assignment, LifecycleEvent
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("assignments", __name__, url_prefix="/assignments")

@bp.route("/")
@login_required
def workspace():
    from collections import defaultdict
    instock = Asset.query.filter(func.lower(Asset.status) == "instock").order_by(Asset.model).all()
    deployed = db.session.query(Asset, Assignment).join(
        Assignment, (Assignment.asset_id == Asset.id) & Assignment.returned_at.is_(None)
    ).filter(func.lower(Asset.status) == "deployed").order_by(Assignment.user_name, Assignment.assigned_date.desc()).all()

    # Group by employee name for the user-centric view
    by_user = defaultdict(list)
    for a, asgn in deployed:
        by_user[asgn.user_name].append({"asset": a, "assignment": asgn})
    deployed_by_user = sorted(by_user.items(), key=lambda x: x[0].lower())
    total_deployed = sum(len(v) for v in by_user.values())

    return render_template("assignments/workspace.html",
                           instock=instock,
                           deployed_by_user=deployed_by_user,
                           total_deployed=total_deployed)

@bp.route("/assign", methods=["POST"])
@role_required("manager", "admin")
def assign():
    asset_id = request.form.get("asset_id", type=int)
    user_name = request.form.get("user_name", "").strip()
    assigned_date_str = request.form.get("assigned_date", "").strip()
    notes = request.form.get("notes", "").strip()
    condition = request.form.get("condition_on_issue", "Good").strip()

    if not asset_id or not user_name:
        return jsonify({"ok": False, "error": "Asset and user name required"}), 400

    asset = Asset.query.get(asset_id)
    if not asset:
        return jsonify({"ok": False, "error": "Asset not found"}), 404

    if asset.status.lower() not in ("instock", "received"):
        return jsonify({"ok": False, "error": f"Asset is {asset.status}, not available"}), 400

    # Parse assigned date
    try:
        assigned_date = datetime.strptime(assigned_date_str, "%Y-%m-%d").date() if assigned_date_str else date.today()
    except ValueError:
        assigned_date = date.today()

    # Close any lingering open assignments (safety)
    old = Assignment.query.filter_by(asset_id=asset_id).filter(Assignment.returned_at.is_(None)).first()
    if old:
        old.returned_at = date.today()
        old.condition_on_return = "Good"

    asgn = Assignment(
        asset_id=asset_id,
        user_name=user_name,
        assigned_date=assigned_date,
        returned_at=None,
        notes=notes or None,
        condition_on_issue=condition,
        assigned_by=session.get("user", {}).get("name", "Unknown"),
    )
    db.session.add(asgn)

    asset.status = "deployed"
    db.session.add(LifecycleEvent(
        asset_id=asset_id, event_type="asset_assigned",
        event_notes=f"Assigned to {user_name}",
        status_after="deployed", event_date=assigned_date,
        performed_by=session.get("user", {}).get("name", "Unknown"),
    ))
    db.session.commit()
    log_activity("asset_assigned", "assignment", asgn.id, f"Assigned {asset.serial_number} to {user_name}")
    return jsonify({"ok": True, "message": f"Assigned to {user_name}"})

@bp.route("/<int:asgn_id>/return", methods=["POST"])
@role_required("manager", "admin")
def return_asset(asgn_id):
    asgn = Assignment.query.get_or_404(asgn_id)
    if asgn.returned_at:
        return jsonify({"ok": False, "error": "Already returned"}), 400

    condition = request.form.get("condition_on_return", "Good").strip()
    notes = request.form.get("notes", "").strip()
    returned_date_str = request.form.get("returned_date", "").strip()
    try:
        returned_at = datetime.strptime(returned_date_str, "%Y-%m-%d").date() if returned_date_str else date.today()
    except ValueError:
        returned_at = date.today()

    asgn.returned_at = returned_at
    asgn.condition_on_return = condition
    if notes:
        asgn.notes = (asgn.notes or "") + f"\nReturn note: {notes}"

    asset = asgn.asset
    asset.status = "instock"
    db.session.add(LifecycleEvent(
        asset_id=asset.id, event_type="asset_returned",
        event_notes=f"Returned by {asgn.user_name}. Condition: {condition}. {notes}",
        status_after="instock", event_date=returned_at,
        performed_by=session.get("user", {}).get("name", "Unknown"),
    ))
    db.session.commit()
    log_activity("asset_returned", "assignment", asgn_id, f"{asset.serial_number} returned by {asgn.user_name}")
    return jsonify({"ok": True, "message": f"{asset.serial_number} returned to stock"})

@bp.route("/employees")
@login_required
def employees():
    from collections import defaultdict
    # Active assignments only — exclude sold assets
    rows = db.session.query(Asset, Assignment).join(
        Assignment, (Assignment.asset_id == Asset.id) & Assignment.returned_at.is_(None)
    ).filter(func.lower(Asset.status) != 'sold').order_by(Assignment.user_name, Asset.model).all()

    by_user = defaultdict(list)
    for asset, asgn in rows:
        by_user[asgn.user_name].append({"asset": asset, "assignment": asgn})
    # Only employees holding 2+ active assets
    multi = [(name, items) for name, items in by_user.items() if len(items) >= 2]
    employees_list = sorted(multi, key=lambda x: x[0].lower())
    return render_template("assignments/employees.html", employees_list=employees_list)


@bp.route("/<int:asgn_id>/update", methods=["POST"])
@role_required("manager", "admin")
def update(asgn_id):
    asgn = Assignment.query.get_or_404(asgn_id)
    asgn.notes = request.form.get("notes", asgn.notes or "").strip()
    asgn.condition_on_issue = request.form.get("condition_on_issue", asgn.condition_on_issue or "Good")
    db.session.commit()
    log_activity("assignment_updated", "assignment", asgn_id)
    return jsonify({"ok": True})
