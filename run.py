#!/usr/bin/env python
"""Application entry point."""
import os
from dotenv import load_dotenv
load_dotenv()
from app import create_app, db

app = create_app()


@app.shell_context_processor
def make_shell_context():
    """Add models to flask shell."""
    from app.models import (
        Asset, Assignment, Issue, Repair, MaintenanceTask,
        LifecycleEvent, PurchaseOrder, UserRole, ActivityLog
    )
    return {
        'db': db,
        'Asset': Asset,
        'Assignment': Assignment,
        'Issue': Issue,
        'Repair': Repair,
        'MaintenanceTask': MaintenanceTask,
        'LifecycleEvent': LifecycleEvent,
        'PurchaseOrder': PurchaseOrder,
        'UserRole': UserRole,
        'ActivityLog': ActivityLog,
    }


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 8000)),
        debug=app.debug
    )
