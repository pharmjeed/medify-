"""Backfill a deterministic audit hash chain for legacy events."""
import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "20260714_03"
down_revision = "20260714_02"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    facilities = connection.execute(sa.text("SELECT DISTINCT facility_id FROM audit_logs")).scalars().all()
    for facility_id in facilities:
        previous = None
        rows = connection.execute(sa.text("SELECT id, facility_id, actor_user_id, action, entity, entity_id, meta_json, at FROM audit_logs WHERE facility_id = :facility_id ORDER BY at, id"), {"facility_id": facility_id}).mappings().all()
        for row in rows:
            payload = json.dumps({"facility_id": row["facility_id"], "actor": row["actor_user_id"], "action": row["action"], "entity": row["entity"], "entity_id": row["entity_id"], "meta": row["meta_json"] or {}, "at": row["at"].isoformat()}, sort_keys=True, ensure_ascii=False)
            event_hash = hashlib.sha256(f"{previous or ''}|{payload}".encode()).hexdigest()
            connection.execute(sa.text("UPDATE audit_logs SET previous_hash = :previous, event_hash = :event_hash WHERE id = :id"), {"previous": previous, "event_hash": event_hash, "id": row["id"]})
            previous = event_hash


def downgrade():
    pass
