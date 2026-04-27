"""Purchase order routes."""
import os
from flask import Blueprint, request, redirect, url_for, flash, send_from_directory, current_app, session
from datetime import date, datetime
from werkzeug.utils import secure_filename
from app.auth.decorators import login_required, role_required
from app.models import Asset, PurchaseOrder
from app.services.audit import log_activity
from app.extensions import db

bp = Blueprint("purchase_orders", __name__)

ALLOWED_MIME = {"application/pdf"}
ALLOWED_EXT = {".pdf"}

def _po_dir():
    return os.path.join(current_app.root_path, "uploads", "po_files")

@bp.route("/assets/<int:asset_id>/po", methods=["POST"])
@role_required("manager", "admin")
def upsert(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    po_number = request.form.get("po_number", "").strip() or None
    po_date_str = request.form.get("po_date", "").strip()
    vendor = request.form.get("vendor", "").strip() or None
    amount_str = request.form.get("amount", "").strip()

    try:
        po_date = datetime.strptime(po_date_str, "%Y-%m-%d").date() if po_date_str else None
    except ValueError:
        po_date = None

    try:
        amount = float(amount_str) if amount_str else None
    except ValueError:
        amount = None

    po = asset.purchase_order or PurchaseOrder(asset_id=asset_id)
    po.po_number = po_number
    po.po_date = po_date
    po.vendor = vendor
    po.amount = amount

    # Handle file upload
    file = request.files.get("po_pdf")
    if file and file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            flash("Only PDF files allowed.", "error")
            return redirect(url_for("assets.detail", asset_id=asset_id))

        # Delete old file if present
        if po.pdf_filename:
            old_path = os.path.join(_po_dir(), po.pdf_filename)
            if os.path.exists(old_path):
                os.unlink(old_path)

        safe_name = f"{asset_id}_{secure_filename(file.filename)}"
        os.makedirs(_po_dir(), exist_ok=True)
        file.save(os.path.join(_po_dir(), safe_name))
        po.pdf_filename = safe_name

    if not po.id:
        db.session.add(po)
    db.session.commit()
    log_activity("po_uploaded", "purchase_order", po.id, f"Asset {asset.serial_number}")
    flash("Purchase order saved.", "success")
    return redirect(url_for("assets.detail", asset_id=asset_id))

@bp.route("/assets/<int:asset_id>/po/delete", methods=["POST"])
@role_required("manager", "admin")
def delete(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    po = asset.purchase_order
    if po:
        if po.pdf_filename:
            path = os.path.join(_po_dir(), po.pdf_filename)
            if os.path.exists(path):
                os.unlink(path)
        db.session.delete(po)
        db.session.commit()
        log_activity("po_deleted", "purchase_order", None, f"Asset {asset.serial_number}")
        flash("Purchase order deleted.", "success")
    return redirect(url_for("assets.detail", asset_id=asset_id))

@bp.route("/po/file/<path:filename>")
@login_required
def serve(filename):
    safe = secure_filename(filename)
    if safe != filename:
        return "Not found", 404
    return send_from_directory(_po_dir(), safe, as_attachment=False)
