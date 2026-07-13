"""Production hardening baseline for existing and fresh Medify databases."""
from alembic import op
from app.database import Base
from app import models  # noqa: F401

revision = "20260714_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())
    statements = [
        "ALTER TABLE facilities ADD COLUMN IF NOT EXISTS data_region VARCHAR(40) NOT NULL DEFAULT 'saudi-arabia'",
        "ALTER TABLE facilities ADD COLUMN IF NOT EXISTS retention_days INTEGER NOT NULL DEFAULT 3650",
        "ALTER TABLE facilities ADD COLUMN IF NOT EXISTS privacy_email VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ",
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS restricted BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS previous_hash VARCHAR(64)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS event_hash VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_event_hash ON audit_logs(event_hash)",
        """CREATE TABLE IF NOT EXISTS consents (id VARCHAR(36) PRIMARY KEY, facility_id VARCHAR(36) NOT NULL REFERENCES facilities(id), patient_id VARCHAR(36) NOT NULL REFERENCES patients(id), purpose VARCHAR(80) NOT NULL, legal_basis VARCHAR(80) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'granted', granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), withdrawn_at TIMESTAMPTZ, evidence_json JSON NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        "CREATE INDEX IF NOT EXISTS ix_consents_facility_id ON consents(facility_id)",
        "CREATE INDEX IF NOT EXISTS ix_consents_patient_id ON consents(patient_id)",
        """CREATE TABLE IF NOT EXISTS data_subject_requests (id VARCHAR(36) PRIMARY KEY, facility_id VARCHAR(36) NOT NULL REFERENCES facilities(id), patient_id VARCHAR(36) NOT NULL REFERENCES patients(id), request_type VARCHAR(30) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'received', due_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ, handled_by VARCHAR(36) REFERENCES users(id), notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        "CREATE INDEX IF NOT EXISTS ix_data_subject_requests_facility_id ON data_subject_requests(facility_id)",
        """CREATE TABLE IF NOT EXISTS integration_configs (id VARCHAR(36) PRIMARY KEY, facility_id VARCHAR(36) NOT NULL REFERENCES facilities(id), kind VARCHAR(40) NOT NULL, mode VARCHAR(20) NOT NULL DEFAULT 'disabled', endpoint VARCHAR(500), encrypted_secret TEXT, verified_at TIMESTAMPTZ, config_json JSON NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT uq_integration_facility_kind UNIQUE(facility_id, kind))""",
        "CREATE INDEX IF NOT EXISTS ix_integration_configs_facility_id ON integration_configs(facility_id)",
        """CREATE TABLE IF NOT EXISTS refresh_sessions (id VARCHAR(36) PRIMARY KEY, facility_id VARCHAR(36) NOT NULL REFERENCES facilities(id), user_id VARCHAR(36) NOT NULL REFERENCES users(id), token_hash VARCHAR(64) NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_used_at TIMESTAMPTZ)""",
        "CREATE INDEX IF NOT EXISTS ix_refresh_sessions_facility_id ON refresh_sessions(facility_id)",
        "CREATE INDEX IF NOT EXISTS ix_refresh_sessions_user_id ON refresh_sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_refresh_sessions_token_hash ON refresh_sessions(token_hash)",
        "CREATE INDEX IF NOT EXISTS ix_visits_facility_doctor_created ON visits(facility_id, doctor_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_patients_facility_name ON patients(facility_id, display_name)",
    ]
    for statement in statements: op.execute(statement)


def downgrade():
    pass
