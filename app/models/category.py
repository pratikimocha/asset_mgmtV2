"""Category model — admin-managed category list."""
from datetime import datetime
from app.extensions import db


class Category(db.Model):
    __tablename__ = 'categories'
    __table_args__ = {'schema': 'helpdesk'}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))
    type = db.Column(db.String(20), default='asset')  # 'asset' | 'ticket' | 'both'
    color = db.Column(db.String(7), default='#6b7280')  # hex
    icon = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Sub-category support: nullable parent_id = top-level category
    parent_id = db.Column(db.Integer, db.ForeignKey('helpdesk.categories.id', ondelete='SET NULL'),
                          nullable=True, index=True)
    children = db.relationship('Category',
                               backref=db.backref('parent', remote_side=[id]),
                               lazy='dynamic',
                               order_by='Category.sort_order, Category.name')

    @property
    def is_subcategory(self):
        return self.parent_id is not None

    @property
    def display_name(self):
        """Full path e.g. 'Software Issues > Office 365 Issue'."""
        if self.parent:
            return f'{self.parent.name} › {self.name}'
        return self.name

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'icon': self.icon or '',
            'color': self.color or '#6b7280',
            'is_active': self.is_active,
            'sort_order': self.sort_order or 0,
            'parent_id': self.parent_id,
        }

    def __repr__(self):
        return f'<Category {self.name}>'
