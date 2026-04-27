"""Admin routes — asset management admin only."""
from flask import Blueprint, render_template, request, jsonify, session
from datetime import datetime
from sqlalchemy import func
from app.auth.decorators import role_required
from app.models import UserRole, ActivityLog
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/roles")
@role_required("admin")
def roles():
    users = UserRole.query.order_by(UserRole.display_name).all()
    return render_template("admin/roles.html", users=users)


@bp.route("/roles/<oid>", methods=["POST"])
@role_required("admin")
def set_role(oid):
    role = request.form.get("role", "viewer").strip()
    if role not in ("admin", "manager", "viewer"):
        return jsonify({"ok": False, "error": "Invalid role"}), 400

    user_role = UserRole.query.filter_by(oid=oid).first()
    if not user_role:
        display_name = request.form.get("display_name", oid)
        user_role = UserRole(oid=oid, display_name=display_name)
        db.session.add(user_role)

    old_role = user_role.role
    user_role.role = role
    user_role.granted_by = session.get("user", {}).get("name", "Unknown")
    user_role.granted_at = datetime.utcnow()
    db.session.commit()
    log_activity("role_changed", "user_role", None, f"OID {oid[:8]}...: {old_role}→{role}")
    return jsonify({"ok": True, "role": role})


@bp.route("/activity-log")
@role_required("admin")
def activity_log():
    action_f = request.args.get("action", "").strip()
    user_f = request.args.get("user_name", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50

    query = ActivityLog.query
    if action_f:
        query = query.filter(ActivityLog.action.ilike(f"%{action_f}%"))
    if user_f:
        query = query.filter(ActivityLog.user_name.ilike(f"%{user_f}%"))
    if date_from:
        try:
            query = query.filter(ActivityLog.timestamp >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(ActivityLog.timestamp <= datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59))
        except ValueError:
            pass

    total = query.count()
    logs = query.order_by(ActivityLog.timestamp.desc()).offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)
    actions = [r[0] for r in db.session.query(func.distinct(ActivityLog.action)).order_by(ActivityLog.action).all()]

    return render_template("admin/activity_log.html",
                           logs=logs, page=page, pages=pages, total=total,
                           action_f=action_f, user_f=user_f, date_from=date_from, date_to=date_to,
                           actions=actions)


@bp.route("/db/download")
@role_required("admin")
def db_download():
    return jsonify({"ok": True, "note": "Use Azure PostgreSQL backup or pg_dump for production backups."})


@bp.route("/users/create", methods=["POST"])
@role_required("admin")
def create_user():
    name = request.form.get("display_name", "").strip()
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "viewer").strip()

    if not name or not username or not password:
        return jsonify({"ok": False, "error": "Name, username and password are required"}), 400
    if role not in UserRole.ROLES:
        return jsonify({"ok": False, "error": "Invalid role"}), 400
    if len(password) < 8:
        return jsonify({"ok": False, "error": "Password must be at least 8 characters"}), 400
    if UserRole.query.filter_by(username=username).first():
        return jsonify({"ok": False, "error": f"Username '{username}' already exists"}), 400

    user = UserRole.create(
        username=username,
        password=password,
        display_name=name,
        role=role,
        granted_by=session.get("user", {}).get("name", "admin"),
    )
    db.session.add(user)
    db.session.commit()
    log_activity("user_created", "user_role", None, f"Created user {username} with role {role}")
    return jsonify({"ok": True, "oid": user.oid, "display_name": user.display_name, "username": user.username, "role": user.role})


@bp.route("/users/<oid>/reset-password", methods=["POST"])
@role_required("admin")
def reset_password(oid):
    user = UserRole.query.get(oid)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404
    new_password = request.form.get("password", "")
    if len(new_password) < 8:
        return jsonify({"ok": False, "error": "Password must be at least 8 characters"}), 400
    user.set_password(new_password)
    db.session.commit()
    log_activity("password_reset", "user_role", None, f"Password reset for {user.username}")
    return jsonify({"ok": True})


@bp.route("/users/<oid>/delete", methods=["POST"])
@role_required("admin")
def delete_user(oid):
    current_oid = session.get("user", {}).get("oid")
    if oid == current_oid:
        return jsonify({"ok": False, "error": "Cannot delete your own account"}), 400
    user = UserRole.query.get(oid)
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404
    username = user.username
    db.session.delete(user)
    db.session.commit()
    log_activity("user_deleted", "user_role", None, f"Deleted user {username}")
    return jsonify({"ok": True})
