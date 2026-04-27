"""Asset model."""
from datetime import datetime, date
from enum import Enum
from app.extensions import db


class StatusEnum(Enum):
    ORDERED = 'ordered'
    RECEIVED = 'received'
    INSTOCK = 'instock'
    DEPLOYED = 'deployed'
    REPAIR = 'repair'
    RETIRED = 'retired'
    SOLD = 'sold'

    @classmethod
    def values(cls):
        return [s.value for s in cls]


class Asset(db.Model):
    """Asset inventory item."""
    __tablename__ = 'assets'
    __table_args__ = {'schema': 'asset_manager'}

    id = db.Column(db.Integer, primary_key=True)
    serial_number = db.Column(db.String(255), unique=True, nullable=False, index=True)
    asset_tag = db.Column(db.String(255))
    model = db.Column(db.String(255))
    manufacturer = db.Column(db.String(255))
    category = db.Column(db.String(255), index=True)
    status = db.Column(db.String(50), default=StatusEnum.INSTOCK.value, index=True)
    purchase_date = db.Column(db.Date)
    warranty_expiry = db.Column(db.Date)
    cost = db.Column(db.Float)
    vendor = db.Column(db.String(255))
    location = db.Column(db.String(255))
    department = db.Column(db.String(255))
    sold_to = db.Column(db.String(255))      # Buyer/employee name when status=sold
    sold_type = db.Column(db.String(20))     # 'internal' or 'external'

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    assignments = db.relationship(
        'Assignment',
        back_populates='asset',
        cascade='all,delete',
        lazy='select'
    )
    issues = db.relationship(
        'Issue',
        back_populates='asset',
        cascade='all,delete',
        lazy='select'
    )
    repairs = db.relationship(
        'Repair',
        back_populates='asset',
        cascade='all,delete',
        lazy='select'
    )
    maintenance = db.relationship(
        'MaintenanceTask',
        back_populates='asset',
        cascade='all,delete',
        lazy='select'
    )
    lifecycle = db.relationship(
        'LifecycleEvent',
        back_populates='asset',
        cascade='all,delete',
        lazy='select'
    )
    purchase_order = db.relationship(
        'PurchaseOrder',
        back_populates='asset',
        uselist=False,
        cascade='all,delete',
        lazy='joined'
    )

    def __repr__(self):
        return f'<Asset {self.serial_number} ({self.model})>'

    # Computed properties
    @property
    def active_assignment(self):
        """Return currently active assignment (returned_at is NULL)."""
        return Assignment.query.filter(
            Assignment.asset_id == self.id,
            Assignment.returned_at.is_(None)
        ).first()

    @property
    def open_issues_count(self):
        """Count of open (unresolved) issues."""
        from app.models.issue import Issue
        return Issue.query.filter(
            Issue.asset_id == self.id,
            Issue.status.in_(['open', 'in-progress'])
        ).count()

    @property
    def age_details(self):
        """Compute asset age from purchase_date."""
        from app.services.assets import compute_age
        return compute_age(self.purchase_date)

    @property
    def warranty_state(self):
        """Warranty state: active/expiring/expired/unknown."""
        from app.services.assets import compute_warranty_state
        return compute_warranty_state(self.warranty_expiry)

    @property
    def warranty_state_display(self):
        """Human-readable warranty state."""
        states = {
            'active': 'Active',
            'expiring': 'Expiring Soon',
            'expired': 'Expired',
            'unknown': 'Unknown'
        }
        return states.get(self.warranty_state, 'Unknown')

    @property
    def health_score(self):
        """Calculate health score 0-100."""
        from app.services.assets import compute_health_score
        return compute_health_score(
            age_years=self.age_details['years'],
            open_issues=self.open_issues_count,
            repair_count=len(self.repairs)
        )


from app.models.assignment import Assignment
