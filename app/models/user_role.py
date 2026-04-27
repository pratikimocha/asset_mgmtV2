"""User role model (RBAC) with local password authentication."""
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class UserRole(db.Model):
    """User with role for RBAC. Supports local username/password login."""
    __tablename__ = 'user_roles'

    oid = db.Column(db.String(255), primary_key=True)   # UUID for local users
    role = db.Column(db.String(50), default='viewer', nullable=False)  # admin, manager, viewer
    display_name = db.Column(db.String(255))
    username = db.Column(db.String(255), unique=True)   # login username
    password_hash = db.Column(db.String(255))           # hashed password
    granted_by = db.Column(db.String(255))
    granted_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<UserRole {self.username or self.oid}: {self.role}>'

    ROLES = {'admin', 'manager', 'viewer'}

    @classmethod
    def is_valid_role(cls, role):
        return role in cls.ROLES

    @classmethod
    def create(cls, username, password, display_name, role='viewer', granted_by='admin'):
        user = cls(
            oid=uuid.uuid4().hex,
            username=username.strip().lower(),
            display_name=display_name.strip(),
            role=role,
            granted_by=granted_by,
            granted_at=datetime.utcnow(),
        )
        user.set_password(password)
        return user

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return bool(self.password_hash and check_password_hash(self.password_hash, password))
