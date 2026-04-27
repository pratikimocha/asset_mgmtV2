"""Purchase order model."""
from datetime import datetime
from app.extensions import db


class PurchaseOrder(db.Model):
    """Purchase order for asset."""
    __tablename__ = 'purchase_orders'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset_manager.assets.id'), nullable=False, unique=True)
    po_number = db.Column(db.String(255))
    po_date = db.Column(db.Date)
    vendor = db.Column(db.String(255))
    amount = db.Column(db.Float)
    pdf_filename = db.Column(db.String(255))  # stored in app/uploads/po_files/

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship('Asset', back_populates='purchase_order')

    def __repr__(self):
        return f'<PurchaseOrder {self.po_number}>'

    @property
    def pdf_path(self):
        """Path to PDF file (for serving)."""
        if self.pdf_filename:
            return f'/app/uploads/po_files/{self.pdf_filename}'
        return None
