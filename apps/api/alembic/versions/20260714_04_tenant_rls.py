"""Enforce tenant isolation in PostgreSQL with row-level security."""
from alembic import op

revision = "20260714_04"
down_revision = "20260714_03"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "clinics", "users", "subscriptions", "seat_events", "patients", "templates",
    "visits", "summary_sections", "guidance_items", "approvals", "upload_jobs",
    "audit_logs", "notifications", "consents", "data_subject_requests", "integration_configs",
]


def upgrade():
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (facility_id = current_setting('app.current_facility', true)) WITH CHECK (facility_id = current_setting('app.current_facility', true))")


def downgrade():
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
