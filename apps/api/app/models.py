import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    admin = "admin"
    doctor = "doctor"


class VisitState(str, enum.Enum):
    draft = "draft"
    recording = "recording"
    transcribed = "transcribed"
    summarized = "summarized"
    in_review = "in_review"
    approved = "approved"
    uploaded = "uploaded"
    upload_failed = "upload_failed"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Facility(Timestamped, Base):
    __tablename__ = "facilities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    commercial_reg: Mapped[str] = mapped_column(String(50), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    data_region: Mapped[str] = mapped_column(String(40), default="saudi-arabia")
    retention_days: Mapped[int] = mapped_column(Integer, default=3650)
    privacy_email: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Clinic(Timestamped, Base):
    __tablename__ = "clinics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Timestamped, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("facility_id", "username"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role))
    full_name: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    clinic_id: Mapped[str | None] = mapped_column(ForeignKey("clinics.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Subscription(Timestamped, Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), unique=True)
    seats_total: Mapped[int] = mapped_column(Integer, default=3)
    plan: Mapped[str] = mapped_column(String(30), default="trial")


class SeatEvent(Timestamped, Base):
    __tablename__ = "seat_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(30))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Patient(Timestamped, Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("facility_id", "hospital_mrn"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    hospital_mrn: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(200))
    dob: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)


class Template(Timestamped, Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    specialty: Mapped[str] = mapped_column(String(120), default="عام")
    visit_type: Mapped[str] = mapped_column(String(120), default="متابعة")
    structure_json: Mapped[dict] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(30), default="system")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Visit(Timestamped, Base):
    __tablename__ = "visits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinics.id"))
    doctor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"))
    state: Mapped[VisitState] = mapped_column(Enum(VisitState), default=VisitState.draft)
    transcript: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    patient: Mapped[Patient] = relationship()
    sections: Mapped[list["SummarySection"]] = relationship(cascade="all, delete-orphan")


class SummarySection(Timestamped, Base):
    __tablename__ = "summary_sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.id"), index=True)
    section_key: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)
    content_current: Mapped[str] = mapped_column(Text)
    content_original: Mapped[str] = mapped_column(Text)
    guidance: Mapped[list["GuidanceItem"]] = relationship(cascade="all, delete-orphan")


class GuidanceItem(Timestamped, Base):
    __tablename__ = "guidance_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("summary_sections.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    suggestion_text: Mapped[str] = mapped_column(Text)
    code_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
    code_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence_source: Mapped[str] = mapped_column(String(30))
    evidence_ref: Mapped[str] = mapped_column(String(200))
    safety_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class Approval(Timestamped, Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.id"), unique=True)
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    summary_hash: Mapped[str] = mapped_column(String(64))
    codes_hash: Mapped[str] = mapped_column(String(64))


class UploadJob(Timestamped, Base):
    __tablename__ = "upload_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("visits.id"), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    attempts_count: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Consent(Timestamped, Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(80))
    legal_basis: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="granted")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DataSubjectRequest(Timestamped, Base):
    __tablename__ = "data_subject_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="received")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handled_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class IntegrationConfig(Timestamped, Base):
    __tablename__ = "integration_configs"
    __table_args__ = (UniqueConstraint("facility_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(20), default="disabled")
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    facility_id: Mapped[str] = mapped_column(ForeignKey("facilities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
