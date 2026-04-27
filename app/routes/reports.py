"""Reports routes."""
import io
from flask import Blueprint, render_template, request, send_file
from sqlalchemy import func
from app.auth.decorators import login_required
from app.models import Asset
from app.services.export import export_assets_to_csv, export_assets_to_xlsx
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("reports", __name__, url_prefix="/reports")

@bp.route("/")
@login_required
def index():
    rows = db.session.query(func.lower(Asset.status), func.count(Asset.id)).group_by(func.lower(Asset.status)).all()
    sm = {s: c for s, c in rows}
    total = sum(sm.values())
    cr = db.session.query(func.coalesce(func.sum(Asset.cost), 0), func.coalesce(func.avg(Asset.cost), 0)).first()
    dept_rows = db.session.query(Asset.department, func.count(Asset.id)).filter(Asset.department.isnot(None)).group_by(Asset.department).order_by(func.count(Asset.id).desc()).limit(10).all()
    cat_rows = db.session.query(Asset.category, func.count(Asset.id)).filter(Asset.category.isnot(None)).group_by(Asset.category).order_by(func.count(Asset.id).desc()).all()
    return render_template("reports/index.html",
        total=total, status_counts=sm,
        total_value=float(cr[0] or 0), avg_cost=float(cr[1] or 0),
        dept_breakdown=dept_rows, cat_breakdown=cat_rows)

@bp.route("/download")
@login_required
def download():
    fmt = request.args.get("format", "csv").lower()
    status_f = request.args.get("status", "").strip()
    query = Asset.query
    if status_f:
        query = query.filter(func.lower(Asset.status) == status_f.lower())
    assets = query.all()
    log_activity("report_downloaded", "asset", None, f"Format={fmt}, status={status_f or 'all'}, count={len(assets)}")

    if fmt == "xlsx":
        data = export_assets_to_xlsx(assets)
        return send_file(io.BytesIO(data), download_name="assets.xlsx", as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        data = export_assets_to_csv(assets)
        return send_file(io.BytesIO(data), download_name="assets.csv", as_attachment=True, mimetype="text/csv")
