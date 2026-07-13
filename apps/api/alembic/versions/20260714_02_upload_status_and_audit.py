"""Expand upload status for explicit production states."""
from alembic import op

revision = "20260714_02"
down_revision = "20260714_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE upload_jobs ALTER COLUMN status TYPE VARCHAR(40)")


def downgrade():
    pass
