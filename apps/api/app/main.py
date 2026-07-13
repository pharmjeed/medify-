import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .crypto import encrypt_value
from .database import Base, SessionLocal, engine, get_db
from .models import (
    Approval, AuditLog, Clinic, Consent, DataSubjectRequest, Facility, GuidanceItem,
    IntegrationConfig, Patient, RefreshSession, Role, SeatEvent, Subscription,
    SummarySection, Template, UploadJob, User, Visit, VisitState,
)
from .security import create_refresh_token, create_token, current_user, decode_token, hash_password, require, set_tenant_context, token_hash, verify_password


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment == "production":
        if len(settings.secret_key) < 32 or settings.secret_key == "medify-development-secret-change-me":
            raise RuntimeError("A strong SECRET_KEY is required in production")
        if not settings.field_encryption_key:
            raise RuntimeError("FIELD_ENCRYPTION_KEY is required in production")
    Base.metadata.create_all(bind=engine)
    seed_demo()
    yield


app = FastAPI(title="Medify API", version="1.0.0", docs_url="/docs" if settings.environment != "production" else None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=bool(settings.origins),
    allow_methods=["*"],
    allow_headers=["*"],
)
rate_store = redis.Redis.from_url(settings.redis_url, decode_responses=True)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws: wss:"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else response.headers.get("Cache-Control", "")
    return response


ERRORS = {
    "MDF-4011": (401, "بيانات الدخول غير صحيحة", "Invalid credentials"),
    "MDF-4012": (401, "انتهت الجلسة", "Session expired"),
    "MDF-4013": (403, "الحساب معطّل أو المنشأة معلّقة", "Account or facility suspended"),
    "MDF-4031": (403, "لا تملك صلاحية هذا الإجراء", "Forbidden"),
    "MDF-4041": (404, "المورد غير موجود", "Resource not found"),
    "MDF-4221": (422, "لا توجد مقاعد متاحة", "No seats available"),
    "MDF-4222": (422, "يجب حسم كل الإرشادات قبل الاعتماد", "Pending guidance must be resolved"),
    "MDF-4223": (409, "انتقال حالة الزيارة غير مسموح", "Invalid visit state transition"),
    "MDF-4225": (422, "بنية القالب غير مكتملة", "Invalid template structure"),
    "MDF-4226": (422, "لا يمكن تعديل زيارة معتمدة", "Approved visit is read-only"),
    "MDF-5033": (500, "فشل التحليل الذكي", "Guidance analysis failed"),
    "MDF-5052": (504, "تعذر الوصول لنظام المستشفى", "Hospital system unavailable"),
    "MDF-5001": (500, "حدث خطأ داخلي", "Internal error"),
}


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    code = str(exc.detail) if str(exc.detail).startswith("MDF-") else "MDF-5001"
    status_code, ar, en = ERRORS.get(code, (exc.status_code, str(exc.detail), str(exc.detail)))
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message_ar": ar, "message_en": en, "details": {}}})


def data(value: Any, meta: dict | None = None):
    return {"data": value, "meta": meta or {}}


def stamp(value: Any):
    return value.isoformat() if value else None


def audit(db: Session, user: User, action: str, entity: str, entity_id: str | None = None, meta: dict | None = None):
    previous = db.scalar(select(AuditLog.event_hash).where(AuditLog.facility_id == user.facility_id).order_by(AuditLog.at.desc()).limit(1))
    at = datetime.now(timezone.utc)
    payload = json.dumps({"facility_id": user.facility_id, "actor": user.id, "action": action, "entity": entity, "entity_id": entity_id, "meta": meta or {}, "at": at.isoformat()}, sort_keys=True, ensure_ascii=False)
    event_hash = hashlib.sha256(f"{previous or ''}|{payload}".encode()).hexdigest()
    db.add(AuditLog(facility_id=user.facility_id, actor_user_id=user.id, action=action, entity=entity, entity_id=entity_id, meta_json=meta or {}, at=at, previous_hash=previous, event_hash=event_hash))
    db.flush()


def clinic_json(item: Clinic):
    return {"id": item.id, "name": item.name, "archived_at": stamp(item.archived_at), "created_at": stamp(item.created_at)}


def user_json(item: User):
    return {"id": item.id, "full_name": item.full_name, "username": item.username, "role": item.role.value, "specialty": item.specialty, "clinic_id": item.clinic_id, "is_active": item.is_active}


def template_json(item: Template):
    return {"id": item.id, "name": item.name, "specialty": item.specialty, "visit_type": item.visit_type, "structure_json": item.structure_json, "origin": item.origin, "owner_user_id": item.owner_user_id, "is_default": item.is_default}


def visit_json(item: Visit, details: bool = False):
    result = {"id": item.id, "state": item.state.value, "patient_id": item.patient_id, "patient_name": item.patient.display_name if item.patient else None, "mrn": item.patient.hospital_mrn if item.patient else None, "template_id": item.template_id, "created_at": stamp(item.created_at)}
    if details:
        result["context_snapshot"] = item.context_snapshot
        result["transcript"] = item.transcript
    return result


class LoginIn(BaseModel):
    facility: str
    username: str
    password: str


class FacilityIn(BaseModel):
    name: str
    commercial_reg: str
    slug: str
    admin_name: str
    username: str
    password: str = Field(min_length=12)
    seats: int = Field(default=3, ge=1, le=500)


class ClinicIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class DoctorIn(BaseModel):
    full_name: str
    username: str
    password: str = Field(min_length=12)
    specialty: str
    clinic_id: str


class DoctorPatch(BaseModel):
    full_name: str | None = None
    specialty: str | None = None
    clinic_id: str | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=12)


class SeatsPatch(BaseModel):
    seats_total: int = Field(ge=1, le=500)


class TemplateIn(BaseModel):
    name: str
    specialty: str = "عام"
    visit_type: str = "متابعة"
    structure_json: dict
    origin: str = "reverse_built"


class ReverseTemplateIn(BaseModel):
    sample_text: str
    summarization_style: str
    specialty: str = "عام"


class VisitIn(BaseModel):
    patient_id: str
    template_id: str


class SectionPatch(BaseModel):
    content: str


class GuidancePatch(BaseModel):
    status: str
    modified_text: str | None = None


class ChatIn(BaseModel):
    message: str


class ConsentIn(BaseModel):
    patient_id: str
    purpose: str = Field(min_length=3, max_length=80)
    legal_basis: str = Field(min_length=3, max_length=80)
    evidence: dict = Field(default_factory=dict)


class DataRequestIn(BaseModel):
    patient_id: str
    request_type: str
    notes: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "medify-api"}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(select(1))
    rate_store.ping()
    return {"status": "ready", "database": "ok", "redis": "ok", "region": settings.data_region}


def set_auth_cookies(response: Response, access: str, refresh: str | None = None):
    response.set_cookie("medify_access", access, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.access_token_minutes * 60, path="/")
    if refresh:
        response.set_cookie("medify_refresh", refresh, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.refresh_token_days * 86400, path="/api/v1/auth")


def enforce_login_rate(request: Request, body: LoginIn):
    identity = hashlib.sha256(f"{request.client.host if request.client else 'unknown'}|{body.facility}|{body.username}".encode()).hexdigest()
    key = f"medify:login:{identity}"
    try:
        count = rate_store.incr(key)
        if count == 1: rate_store.expire(key, 60)
        if count > 12: raise HTTPException(429, "MDF-4011")
    except HTTPException:
        raise
    except Exception:
        pass


@app.post("/api/v1/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    enforce_login_rate(request, body)
    facility = db.scalar(select(Facility).where((Facility.slug == body.facility) | (Facility.commercial_reg == body.facility)))
    if facility: set_tenant_context(db, facility.id)
    user = db.scalar(select(User).where(User.facility_id == facility.id, User.username == body.username)) if facility else None
    now = datetime.now(timezone.utc)
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(403, "MDF-4013")
    if not user or not verify_password(body.password, user.password_hash):
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.login_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            audit(db, user, "auth.login_failed", "user", user.id, {"attempts": user.failed_login_attempts})
            db.commit()
        raise HTTPException(401, "MDF-4011")
    if not user.is_active or facility.status != "active":
        raise HTTPException(403, "MDF-4013")
    user.failed_login_attempts = 0; user.locked_until = None; user.last_login_at = now
    access = create_token(user)
    refresh_raw, refresh_hash = create_refresh_token()
    db.add(RefreshSession(facility_id=user.facility_id, user_id=user.id, token_hash=refresh_hash, expires_at=now + timedelta(days=settings.refresh_token_days)))
    audit(db, user, "auth.login_succeeded", "user", user.id)
    db.commit()
    clinic = db.get(Clinic, user.clinic_id) if user.clinic_id else None
    response = JSONResponse(content=data({"access_token": access, "token_type": "bearer", "user": {**user_json(user), "facility_name": facility.name, "clinic_name": clinic.name if clinic else None}, "facility": {"id": facility.id, "name": facility.name, "slug": facility.slug}}))
    set_auth_cookies(response, access, refresh_raw)
    return response


@app.post("/api/v1/auth/refresh")
def refresh(request: Request, db: Session = Depends(get_db)):
    raw = request.cookies.get("medify_refresh")
    if not raw: raise HTTPException(401, "MDF-4012")
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash(raw), RefreshSession.revoked_at.is_(None)))
    now = datetime.now(timezone.utc)
    if not session or session.expires_at <= now: raise HTTPException(401, "MDF-4012")
    set_tenant_context(db, session.facility_id)
    user = db.get(User, session.user_id)
    if not user or not user.is_active: raise HTTPException(403, "MDF-4013")
    session.last_used_at = now
    access = create_token(user); db.commit()
    response = JSONResponse(content=data({"access_token": access, "token_type": "bearer"}))
    set_auth_cookies(response, access)
    return response


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    raw = request.cookies.get("medify_refresh")
    if raw:
        session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash(raw), RefreshSession.revoked_at.is_(None)))
        if session: session.revoked_at = datetime.now(timezone.utc)
    audit(db, user, "auth.logout", "user", user.id); db.commit()
    response.delete_cookie("medify_access", path="/"); response.delete_cookie("medify_refresh", path="/api/v1/auth")
    return data({"logged_out": True})


@app.get("/api/v1/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    facility = db.get(Facility, user.facility_id)
    clinic = db.get(Clinic, user.clinic_id) if user.clinic_id else None
    return data({**user_json(user), "facility_name": facility.name, "clinic_name": clinic.name if clinic else None})


@app.post("/api/v1/facilities/register")
def register(body: FacilityIn, db: Session = Depends(get_db)):
    if not settings.public_registration_enabled and not settings.demo_mode:
        raise HTTPException(403, "MDF-4031")
    if db.scalar(select(Facility).where((Facility.slug == body.slug) | (Facility.commercial_reg == body.commercial_reg))):
        raise HTTPException(422, "MDF-4225")
    facility = Facility(name=body.name, commercial_reg=body.commercial_reg, slug=body.slug)
    db.add(facility); db.flush()
    set_tenant_context(db, facility.id)
    admin = User(facility_id=facility.id, role=Role.admin, full_name=body.admin_name, username=body.username, password_hash=hash_password(body.password))
    db.add(admin); db.flush()
    db.add(Subscription(facility_id=facility.id, seats_total=body.seats, plan="trial"))
    audit(db, admin, "facility.registered", "facility", facility.id)
    db.commit()
    return data({"facility_id": facility.id, "slug": facility.slug})


@app.get("/api/v1/clinics")
def clinics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == Role.doctor:
        rows = [db.get(Clinic, user.clinic_id)] if user.clinic_id else []
    else:
        rows = db.scalars(select(Clinic).where(Clinic.facility_id == user.facility_id).order_by(Clinic.created_at)).all()
    return data([clinic_json(x) for x in rows if x])


@app.post("/api/v1/clinics")
def create_clinic(body: ClinicIn, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = Clinic(facility_id=user.facility_id, name=body.name)
    db.add(row); db.flush(); audit(db, user, "clinic.created", "clinic", row.id); db.commit()
    return data(clinic_json(row))


@app.patch("/api/v1/clinics/{clinic_id}")
def update_clinic(clinic_id: str, body: ClinicIn, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(Clinic).where(Clinic.id == clinic_id, Clinic.facility_id == user.facility_id))
    if not row: raise HTTPException(404, "MDF-4041")
    row.name = body.name; audit(db, user, "clinic.updated", "clinic", row.id); db.commit()
    return data(clinic_json(row))


@app.delete("/api/v1/clinics/{clinic_id}")
def archive_clinic(clinic_id: str, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(Clinic).where(Clinic.id == clinic_id, Clinic.facility_id == user.facility_id))
    if not row: raise HTTPException(404, "MDF-4041")
    row.archived_at = datetime.now(timezone.utc); audit(db, user, "clinic.archived", "clinic", row.id); db.commit()
    return data(clinic_json(row))


@app.get("/api/v1/doctors")
def doctors(user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.facility_id == user.facility_id, User.role == Role.doctor).order_by(User.created_at)).all()
    return data([user_json(x) for x in rows])


@app.post("/api/v1/doctors")
def create_doctor(body: DoctorIn, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    seats = db.scalar(select(Subscription.seats_total).where(Subscription.facility_id == user.facility_id)) or 0
    active = db.scalar(select(func.count(User.id)).where(User.facility_id == user.facility_id, User.role == Role.doctor, User.is_active.is_(True))) or 0
    if active >= seats: raise HTTPException(422, "MDF-4221")
    row = User(facility_id=user.facility_id, role=Role.doctor, full_name=body.full_name, username=body.username, password_hash=hash_password(body.password), specialty=body.specialty, clinic_id=body.clinic_id)
    db.add(row); db.flush(); audit(db, user, "doctor.created", "user", row.id); db.commit()
    return data(user_json(row))


@app.patch("/api/v1/doctors/{doctor_id}")
def update_doctor(doctor_id: str, body: DoctorPatch, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(User).where(User.id == doctor_id, User.facility_id == user.facility_id, User.role == Role.doctor))
    if not row: raise HTTPException(404, "MDF-4041")
    for key, value in body.model_dump(exclude_none=True).items(): setattr(row, key, value)
    audit(db, user, "doctor.updated", "user", row.id); db.commit(); return data(user_json(row))


@app.post("/api/v1/doctors/{doctor_id}/reset-password")
def reset_password(doctor_id: str, body: PasswordResetIn, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(User).where(User.id == doctor_id, User.facility_id == user.facility_id, User.role == Role.doctor))
    if not row: raise HTTPException(404, "MDF-4041")
    row.password_hash = hash_password(body.password); row.password_changed_at = datetime.now(timezone.utc); audit(db, user, "doctor.password_reset", "user", row.id); db.commit()
    return data({"reset": True})


@app.get("/api/v1/subscription")
def subscription(user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(Subscription).where(Subscription.facility_id == user.facility_id))
    used = db.scalar(select(func.count(User.id)).where(User.facility_id == user.facility_id, User.role == Role.doctor, User.is_active.is_(True))) or 0
    events = db.scalars(select(SeatEvent).where(SeatEvent.facility_id == user.facility_id).order_by(SeatEvent.created_at.desc())).all()
    return data({"seats_total": row.seats_total, "seats_used": used, "seats_free": row.seats_total-used, "plan": row.plan, "events": [{"delta": x.delta, "reason": x.reason, "at": stamp(x.created_at)} for x in events]})


@app.patch("/api/v1/subscription/seats")
def change_seats(body: SeatsPatch, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(Subscription).where(Subscription.facility_id == user.facility_id))
    used = db.scalar(select(func.count(User.id)).where(User.facility_id == user.facility_id, User.role == Role.doctor, User.is_active.is_(True))) or 0
    if body.seats_total < used: raise HTTPException(422, "MDF-4221")
    delta = body.seats_total - row.seats_total; row.seats_total = body.seats_total
    db.add(SeatEvent(facility_id=user.facility_id, delta=delta, reason="expand" if delta > 0 else "reduce", actor_user_id=user.id)); audit(db, user, "subscription.seats_changed", "subscription", row.id, {"delta": delta}); db.commit()
    return data({"seats_total": row.seats_total})


@app.get("/api/v1/settings/coding-systems")
def coding_systems(_: User = Depends(current_user)):
    return data([{"system": "ICD10AM", "version": "12th", "is_active": True}, {"system": "ACHI", "version": "12th", "is_active": True}, {"system": "SBS", "version": "2026", "is_active": False}, {"system": "NDC", "version": "2026", "is_active": True}])


@app.patch("/api/v1/settings/coding-systems")
def patch_coding(body: list[dict], user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    audit(db, user, "settings.coding_updated", "facility", user.facility_id, {"systems": [x.get("system") for x in body]}); db.commit(); return data(body)


@app.get("/api/v1/settings/integration")
def integration(user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(IntegrationConfig).where(IntegrationConfig.facility_id == user.facility_id, IntegrationConfig.kind == "fhir"))
    if not row: return data({"kind": "fhir", "endpoint_url": None, "mode": "disabled", "last_test_ok": False, "last_test_at": None, "secret_configured": False})
    return data({"kind": row.kind, "endpoint_url": row.endpoint, "mode": row.mode, "last_test_ok": bool(row.verified_at), "last_test_at": stamp(row.verified_at), "secret_configured": bool(row.encrypted_secret), "config": row.config_json})


@app.patch("/api/v1/settings/integration")
def patch_integration(body: dict, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    mode = body.get("mode", "disabled")
    endpoint = body.get("endpoint_url")
    if mode not in {"disabled", "test", "production"}: raise HTTPException(422, "MDF-4225")
    if mode != "disabled" and (not endpoint or not endpoint.startswith("https://")): raise HTTPException(422, "MDF-4225")
    row = db.scalar(select(IntegrationConfig).where(IntegrationConfig.facility_id == user.facility_id, IntegrationConfig.kind == "fhir"))
    if not row:
        row = IntegrationConfig(facility_id=user.facility_id, kind="fhir"); db.add(row)
    row.mode = mode; row.endpoint = endpoint; row.config_json = body.get("config", {})
    if body.get("auth_secret"): row.encrypted_secret = encrypt_value(body["auth_secret"])
    row.verified_at = None
    audit(db, user, "settings.integration_updated", "integration", row.id, {"mode": mode, "endpoint": endpoint}); db.commit()
    return data({"kind": "fhir", "endpoint_url": endpoint, "mode": mode, "last_test_ok": False, "secret_configured": bool(row.encrypted_secret)})


@app.post("/api/v1/settings/integration/test")
def test_integration(user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(IntegrationConfig).where(IntegrationConfig.facility_id == user.facility_id, IntegrationConfig.kind == "fhir"))
    if not row or row.mode == "disabled" or not row.endpoint: raise HTTPException(504, "MDF-5052")
    # A real connectivity test is intentionally not simulated. It is enabled only after
    # endpoint allow-listing, credentials, and the facility's signed integration approval.
    return data({"ok": False, "mode": row.mode, "requires_external_credentials": True})


@app.get("/api/v1/dashboards/usage")
def usage(_: User = Depends(require(Role.admin))):
    return data({"visits_week": 142, "review_seconds_avg": 107, "active_doctors": 4, "doctors_total": 4, "stt_minutes": 863})


@app.get("/api/v1/dashboards/quality")
def quality(_: User = Depends(require(Role.admin))):
    return data({"light_edit_rate": 0.68, "clinical_guidance_acceptance": 0.54, "coding_guidance_acceptance": 0.76, "first_upload_success": 0.964})


@app.get("/api/v1/audit-logs")
def audit_logs(page: int = 1, per_page: int = 25, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    query = select(AuditLog).where(AuditLog.facility_id == user.facility_id).order_by(AuditLog.at.desc())
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.offset((page-1)*per_page).limit(min(per_page, 100))).all()
    return data([{"id": x.id, "action": x.action, "entity": x.entity, "entity_id": x.entity_id, "at": stamp(x.at), "meta": x.meta_json} for x in rows], {"total": total, "page": page})


@app.get("/api/v1/audit-logs/verify")
def verify_audit_chain(user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.facility_id == user.facility_id).order_by(AuditLog.at)).all()
    previous = None
    legacy_events = 0
    for index, row in enumerate(rows):
        if not row.event_hash:
            if previous is not None: return data({"valid": False, "broken_at": row.id, "events_checked": index, "legacy_events": legacy_events})
            legacy_events += 1; continue
        payload = json.dumps({"facility_id": row.facility_id, "actor": row.actor_user_id, "action": row.action, "entity": row.entity, "entity_id": row.entity_id, "meta": row.meta_json or {}, "at": row.at.isoformat()}, sort_keys=True, ensure_ascii=False)
        expected = hashlib.sha256(f"{previous or ''}|{payload}".encode()).hexdigest()
        if row.previous_hash != previous or row.event_hash != expected:
            return data({"valid": False, "broken_at": row.id, "events_checked": index})
        previous = row.event_hash
    return data({"valid": True, "events_checked": len(rows) - legacy_events, "legacy_events": legacy_events, "head_hash": previous})


@app.get("/api/v1/templates")
def templates(user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Template).where(Template.facility_id == user.facility_id, Template.archived_at.is_(None), (Template.owner_user_id.is_(None)) | (Template.owner_user_id == user.id))).all()
    return data([template_json(x) for x in rows])


@app.post("/api/v1/templates/reverse-build")
def reverse_build(body: ReverseTemplateIn, _: User = Depends(require(Role.doctor))):
    sections = [{"section_key": key, "title": title, "instructions": body.summarization_style} for key, title in [("S", "Subjective"), ("O", "Objective"), ("A", "Assessment"), ("P", "Plan")]]
    return data({"name": f"{body.specialty} — قالب مخصص", "sections": sections})


@app.post("/api/v1/templates/preview")
def preview_template(body: dict, _: User = Depends(require(Role.doctor))):
    return data({"sections": [{"section_key": x.get("section_key"), "content": "Preview generated from the provided sample."} for x in body.get("sections", [])]})


@app.post("/api/v1/templates")
def save_template(body: TemplateIn, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    if not body.structure_json.get("sections"): raise HTTPException(422, "MDF-4225")
    row = Template(facility_id=user.facility_id, owner_user_id=user.id, **body.model_dump())
    db.add(row); db.flush(); audit(db, user, "template.created", "template", row.id); db.commit(); return data(template_json(row))


@app.patch("/api/v1/templates/{template_id}/default")
def default_template(template_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Template).where(Template.facility_id == user.facility_id, (Template.owner_user_id.is_(None)) | (Template.owner_user_id == user.id))).all()
    found = False
    for row in rows: row.is_default = row.id == template_id; found = found or row.id == template_id
    if not found: raise HTTPException(404, "MDF-4041")
    db.commit(); return data({"is_default": True})


@app.get("/api/v1/patients")
def patients(query: str = Query(default=""), user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    stmt = select(Patient).where(Patient.facility_id == user.facility_id)
    if query: stmt = stmt.where((Patient.display_name.contains(query)) | (Patient.hospital_mrn.contains(query)))
    rows = db.scalars(stmt.limit(25)).all()
    return data([{"id": x.id, "display_name": x.display_name, "hospital_mrn": x.hospital_mrn, "dob": x.dob, "gender": x.gender, "context": x.context_json} for x in rows])


@app.post("/api/v1/consents")
def create_consent(body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = db.scalar(select(Patient).where(Patient.id == body.patient_id, Patient.facility_id == user.facility_id))
    if not patient: raise HTTPException(404, "MDF-4041")
    row = Consent(facility_id=user.facility_id, patient_id=patient.id, purpose=body.purpose, legal_basis=body.legal_basis, evidence_json=body.evidence)
    db.add(row); db.flush(); audit(db, user, "consent.granted", "consent", row.id, {"purpose": row.purpose}); db.commit()
    return data({"id": row.id, "status": row.status, "granted_at": stamp(row.granted_at)})


@app.get("/api/v1/patients/{patient_id}/consents")
def list_consents(patient_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    patient = db.scalar(select(Patient).where(Patient.id == patient_id, Patient.facility_id == user.facility_id))
    if not patient: raise HTTPException(404, "MDF-4041")
    rows = db.scalars(select(Consent).where(Consent.patient_id == patient.id, Consent.facility_id == user.facility_id).order_by(Consent.granted_at.desc())).all()
    return data([{"id": x.id, "purpose": x.purpose, "legal_basis": x.legal_basis, "status": x.status, "granted_at": stamp(x.granted_at), "withdrawn_at": stamp(x.withdrawn_at)} for x in rows])


@app.post("/api/v1/privacy/requests")
def create_privacy_request(body: DataRequestIn, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    if body.request_type not in {"access", "correction", "deletion", "restriction", "export"}: raise HTTPException(422, "MDF-4225")
    patient = db.scalar(select(Patient).where(Patient.id == body.patient_id, Patient.facility_id == user.facility_id))
    if not patient: raise HTTPException(404, "MDF-4041")
    row = DataSubjectRequest(facility_id=user.facility_id, patient_id=patient.id, request_type=body.request_type, due_at=datetime.now(timezone.utc) + timedelta(days=30), notes=body.notes)
    db.add(row); db.flush(); audit(db, user, "privacy.request_received", "data_subject_request", row.id, {"type": row.request_type}); db.commit()
    return data({"id": row.id, "status": row.status, "due_at": stamp(row.due_at)})


@app.get("/api/v1/privacy/requests")
def list_privacy_requests(user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    rows = db.scalars(select(DataSubjectRequest).where(DataSubjectRequest.facility_id == user.facility_id).order_by(DataSubjectRequest.created_at.desc())).all()
    return data([{"id": x.id, "patient_id": x.patient_id, "request_type": x.request_type, "status": x.status, "due_at": stamp(x.due_at), "completed_at": stamp(x.completed_at)} for x in rows])


@app.post("/api/v1/visits")
def create_visit(body: VisitIn, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    patient = db.scalar(select(Patient).where(Patient.id == body.patient_id, Patient.facility_id == user.facility_id))
    template = db.scalar(select(Template).where(Template.id == body.template_id, Template.facility_id == user.facility_id))
    if not patient or not template: raise HTTPException(404, "MDF-4041")
    row = Visit(facility_id=user.facility_id, clinic_id=user.clinic_id, doctor_id=user.id, patient_id=patient.id, template_id=template.id, context_snapshot=patient.context_json)
    db.add(row); db.flush(); audit(db, user, "visit.started", "visit", row.id); db.commit(); return data(visit_json(row, True))


@app.get("/api/v1/visits")
def list_visits(user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Visit).options(selectinload(Visit.patient)).where(Visit.facility_id == user.facility_id, Visit.doctor_id == user.id).order_by(Visit.created_at.desc())).all()
    return data([visit_json(x) for x in rows])


def owned_visit(db: Session, visit_id: str, user: User, sections: bool = False) -> Visit:
    stmt = select(Visit).options(selectinload(Visit.patient))
    if sections: stmt = stmt.options(selectinload(Visit.sections).selectinload(SummarySection.guidance))
    row = db.scalar(stmt.where(Visit.id == visit_id, Visit.facility_id == user.facility_id, Visit.doctor_id == user.id))
    if not row: raise HTTPException(404, "MDF-4041")
    return row


@app.post("/api/v1/visits/{visit_id}/recording/{action}")
def recording_action(visit_id: str, action: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user)
    if action == "start" and row.state == VisitState.draft: row.state = VisitState.recording
    elif action in {"pause", "resume"} and row.state == VisitState.recording: pass
    elif action == "stop" and row.state == VisitState.recording:
        row.state = VisitState.in_review
        row.transcript = {"segments": DEMO_TRANSCRIPT}
        build_demo_summary(db, row)
    else: raise HTTPException(409, "MDF-4223")
    audit(db, user, f"recording.{action}", "visit", row.id); db.commit(); return data({"state": row.state.value})


@app.get("/api/v1/visits/{visit_id}/transcript")
def transcript(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user); return data(row.transcript or {"segments": []})


@app.get("/api/v1/visits/{visit_id}/summary")
def summary(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user, True)
    sections = [{"id": s.id, "section_key": s.section_key, "content": s.content_current, "original": s.content_original, "guidance": [{"id": g.id, "kind": g.kind, "suggestion_text": g.suggestion_text, "code_system": g.code_system, "code_value": g.code_value, "evidence_source": g.evidence_source, "evidence_ref": g.evidence_ref, "safety_flag": g.safety_flag, "status": g.status} for g in s.guidance]} for s in sorted(row.sections, key=lambda x: x.position)]
    etag = hashlib.sha256(json.dumps(sections, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return data({"visit": visit_json(row, True), "sections": sections, "etag": etag})


@app.patch("/api/v1/summary-sections/{section_id}")
def patch_section(section_id: str, body: SectionPatch, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    section = db.scalar(select(SummarySection).join(Visit).where(SummarySection.id == section_id, Visit.facility_id == user.facility_id, Visit.doctor_id == user.id))
    if not section: raise HTTPException(404, "MDF-4041")
    visit = db.get(Visit, section.visit_id)
    if visit.state in {VisitState.approved, VisitState.uploaded, VisitState.upload_failed}: raise HTTPException(422, "MDF-4226")
    section.content_current = body.content; audit(db, user, "edit.applied", "summary_section", section.id, {"channel": "typing", "delta_chars": len(body.content)-len(section.content_original)}); db.commit(); return data({"id": section.id, "content": section.content_current})


@app.post("/api/v1/summary-sections/{section_id}/dictate")
def dictate(section_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    section = db.scalar(select(SummarySection).join(Visit).where(SummarySection.id == section_id, Visit.facility_id == user.facility_id, Visit.doctor_id == user.id))
    if not section: raise HTTPException(404, "MDF-4041")
    section.content_current += " Patient additionally reports improved adherence."; audit(db, user, "edit.applied", "summary_section", section.id, {"channel": "voice"}); db.commit(); return data({"content": section.content_current})


@app.patch("/api/v1/guidance-items/{guidance_id}")
def patch_guidance(guidance_id: str, body: GuidancePatch, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    item = db.scalar(select(GuidanceItem).join(SummarySection).join(Visit).where(GuidanceItem.id == guidance_id, Visit.facility_id == user.facility_id, Visit.doctor_id == user.id))
    if not item: raise HTTPException(404, "MDF-4041")
    if body.status not in {"accepted", "rejected", "modified", "pending"}: raise HTTPException(422, "MDF-4225")
    item.status = body.status
    if body.status == "modified" and body.modified_text: item.suggestion_text = body.modified_text
    audit(db, user, "guidance.resolved", "guidance_item", item.id, {"status": item.status, "kind": item.kind}); db.commit(); return data({"id": item.id, "status": item.status})


@app.post("/api/v1/visits/{visit_id}/ai-chat")
def ai_chat(visit_id: str, body: ChatIn, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user, True)
    if row.state != VisitState.in_review: raise HTTPException(422, "MDF-4226")
    patches = []
    if "أسبوع" in body.message:
        plan = next((x for x in row.sections if x.section_key == "P"), None)
        if plan:
            before = plan.content_current; plan.content_current = before.replace("2 weeks", "1 week")
            patches.append({"section_id": plan.id, "before": before, "after": plan.content_current})
        reply = "تم تعديل موعد المتابعة إلى أسبوع واحد."
    else:
        reply = "طلبك يحتاج توضيحاً: حدد القسم والصياغة المطلوبة، ولن أضيف واقعة غير موجودة في الزيارة."
    audit(db, user, "edit.applied", "visit", row.id, {"channel": "ai_chat", "patches_count": len(patches)}); db.commit(); return data({"reply": reply, "patches": patches})


@app.post("/api/v1/visits/{visit_id}/approve")
def approve(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user, True)
    if row.state != VisitState.in_review: raise HTTPException(409, "MDF-4223")
    if any(g.status == "pending" for s in row.sections for g in s.guidance): raise HTTPException(422, "MDF-4222")
    summary_text = "|".join(s.content_current for s in sorted(row.sections, key=lambda x: x.position))
    codes = "|".join((g.code_value or "") for s in row.sections for g in s.guidance if g.status in {"accepted", "modified"})
    approval = Approval(facility_id=user.facility_id, visit_id=row.id, approved_by=user.id, summary_hash=hashlib.sha256(summary_text.encode()).hexdigest(), codes_hash=hashlib.sha256(codes.encode()).hexdigest())
    db.add(approval); db.flush()
    integration = db.scalar(select(IntegrationConfig).where(IntegrationConfig.facility_id == user.facility_id, IntegrationConfig.kind == "fhir"))
    can_upload = bool(settings.demo_mode or (integration and integration.mode == "production" and integration.verified_at))
    status = "confirmed" if settings.demo_mode else ("queued" if can_upload else "awaiting_configuration")
    result = {"bundle_id": f"Bundle/{row.id[:8]}", "demo": settings.demo_mode, "human_approved": True}
    job = UploadJob(facility_id=user.facility_id, visit_id=row.id, status=status, attempts_count=1 if settings.demo_mode else 0, result_json=result)
    db.add(job); row.state = VisitState.uploaded if status == "confirmed" else VisitState.approved
    audit(db, user, "visit.approved", "visit", row.id); audit(db, user, "upload.queued" if can_upload else "upload.awaiting_configuration", "visit", row.id, {"status": status}); db.commit()
    return data({"approval_id": approval.id, "summary_hash": approval.summary_hash, "upload": job.result_json, "status": job.status})


def fhir_bundle(row: Visit) -> dict:
    sections = sorted(row.sections, key=lambda value: value.position)
    patient = row.patient
    composition_id = f"composition-{row.id}"
    return {
        "resourceType": "Bundle", "type": "transaction", "id": row.id,
        "meta": {"tag": [{"system": "https://medify.sa/tags", "code": "human-approved"}]},
        "entry": [
            {"fullUrl": f"urn:uuid:{patient.id}", "resource": {"resourceType": "Patient", "id": patient.id, "identifier": [{"system": "urn:medify:mrn", "value": patient.hospital_mrn}], "name": [{"text": patient.display_name}], "gender": "male" if patient.gender in {"ذكر", "male"} else "female" if patient.gender in {"أنثى", "female"} else "unknown", "birthDate": patient.dob}, "request": {"method": "PUT", "url": f"Patient/{patient.id}"}},
            {"fullUrl": f"urn:uuid:{row.doctor_id}", "resource": {"resourceType": "Practitioner", "id": row.doctor_id}, "request": {"method": "PUT", "url": f"Practitioner/{row.doctor_id}"}},
            {"fullUrl": f"urn:uuid:{row.id}", "resource": {"resourceType": "Encounter", "id": row.id, "status": "finished", "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"}, "subject": {"reference": f"Patient/{patient.id}"}, "participant": [{"individual": {"reference": f"Practitioner/{row.doctor_id}"}}]}, "request": {"method": "PUT", "url": f"Encounter/{row.id}"}},
            {"fullUrl": f"urn:uuid:{composition_id}", "resource": {"resourceType": "Composition", "id": composition_id, "status": "final", "type": {"coding": [{"system": "http://loinc.org", "code": "34117-2", "display": "History and physical note"}]}, "subject": {"reference": f"Patient/{patient.id}"}, "encounter": {"reference": f"Encounter/{row.id}"}, "date": row.updated_at.isoformat(), "author": [{"reference": f"Practitioner/{row.doctor_id}"}], "title": "Medify Clinical Note", "section": [{"title": section.section_key, "code": {"text": section.section_key}, "text": {"status": "generated", "div": f"<div xmlns=\"http://www.w3.org/1999/xhtml\">{escape(section.content_current)}</div>"}} for section in sections]}, "request": {"method": "PUT", "url": f"Composition/{composition_id}"}},
        ],
    }


@app.get("/api/v1/visits/{visit_id}/fhir-bundle")
def export_fhir_bundle(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user, True)
    if row.state not in {VisitState.approved, VisitState.uploaded, VisitState.upload_failed}: raise HTTPException(422, "MDF-4226")
    if not db.scalar(select(Approval).where(Approval.visit_id == row.id, Approval.facility_id == user.facility_id)): raise HTTPException(422, "MDF-4226")
    bundle = fhir_bundle(row); audit(db, user, "fhir.bundle_exported", "visit", row.id, {"entries": len(bundle["entry"])}); db.commit()
    return data(bundle)


@app.get("/api/v1/visits/{visit_id}/upload-status")
def upload_status(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user); job = db.scalar(select(UploadJob).where(UploadJob.visit_id == row.id)); return data({"status": job.status if job else "not_started", "attempts": job.attempts_count if job else 0, "result": job.result_json if job else {}})


@app.post("/api/v1/visits/{visit_id}/upload-retry")
def upload_retry(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user); job = db.scalar(select(UploadJob).where(UploadJob.visit_id == row.id))
    if not job: raise HTTPException(404, "MDF-4041")
    integration = db.scalar(select(IntegrationConfig).where(IntegrationConfig.facility_id == user.facility_id, IntegrationConfig.kind == "fhir", IntegrationConfig.mode == "production", IntegrationConfig.verified_at.is_not(None)))
    if not settings.demo_mode and not integration: raise HTTPException(504, "MDF-5052")
    job.attempts_count += 1; job.status = "confirmed" if settings.demo_mode else "queued"
    row.state = VisitState.uploaded if settings.demo_mode else VisitState.approved
    audit(db, user, "upload.retry_requested", "visit", row.id, {"attempts": job.attempts_count}); db.commit(); return data({"status": job.status, "attempts": job.attempts_count})


DEMO_TRANSCRIPT = [
    {"speaker": "doctor", "text": "كيف كانت قراءات السكر والضغط في الأسبوعين الماضيين؟", "t0": 0, "t1": 4},
    {"speaker": "patient", "text": "السكر أفضل لكن الضغط يرتفع أحياناً في المساء.", "t0": 4, "t1": 9},
    {"speaker": "doctor", "text": "سنستمر على metformin ونراجع جرعة الضغط بعد أسبوعين.", "t0": 9, "t1": 15},
]


def build_demo_summary(db: Session, visit: Visit):
    if visit.sections: return
    contents = {
        "S": "58-year-old male with type 2 diabetes and hypertension reports improved glycemic readings with occasional evening blood-pressure elevation.",
        "O": "Home glucose log improved. Clinic BP 148/92 mmHg, HR 76 bpm.",
        "A": "1. Type 2 diabetes mellitus — improving control. 2. Hypertension — suboptimal control.",
        "P": "Continue metformin. Maintain home BP log. Review antihypertensive dose and follow up in 2 weeks.",
    }
    for position, key in enumerate(["S", "O", "A", "P"]):
        section = SummarySection(facility_id=visit.facility_id, visit_id=visit.id, section_key=key, position=position, content_current=contents[key], content_original=contents[key])
        db.add(section); db.flush()
        if key == "A":
            db.add(GuidanceItem(facility_id=visit.facility_id, section_id=section.id, kind="coding_match", suggestion_text="Consider ICD-10-AM code E11.9 for type 2 diabetes without documented complication.", code_system="ICD-10-AM", code_value="E11.9", evidence_source="current_visit", evidence_ref="Assessment and current medications", status="pending"))
        if key == "P":
            db.add(GuidanceItem(facility_id=visit.facility_id, section_id=section.id, kind="clinical_rx", suggestion_text="Review antihypertensive regimen against the current medication list before dose adjustment.", evidence_source="patient_file", evidence_ref="Medication list and evening BP readings", safety_flag=True, status="pending"))


@app.websocket("/ws/visits/{visit_id}/transcribe")
async def transcribe_socket(websocket: WebSocket, visit_id: str):
    raw_token = websocket.query_params.get("token") or websocket.cookies.get("medify_access")
    try:
        payload = decode_token(raw_token or "")
    except HTTPException:
        await websocket.close(code=4401); return
    db = SessionLocal()
    set_tenant_context(db, payload.get("facility_id", ""))
    visit = db.scalar(select(Visit).where(Visit.id == visit_id, Visit.facility_id == payload.get("facility_id"), Visit.doctor_id == payload.get("sub")))
    db.close()
    if not visit:
        await websocket.close(code=4403); return
    await websocket.accept()
    seq = 0
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "audio_chunk":
                seq = int(message.get("seq", seq + 1))
                text = DEMO_TRANSCRIPT[min(seq - 1, len(DEMO_TRANSCRIPT)-1)]["text"]
                await websocket.send_json({"type": "partial", "seq": seq, "text": text})
                await websocket.send_json({"type": "final", "segment_id": f"s-{seq}", "text": text, "t0": seq*2, "t1": seq*2+2})
            elif message.get("type") == "end":
                await websocket.send_json({"type": "status", "state": "summarizing"}); break
            elif message.get("type") in {"pause", "resume"}:
                await websocket.send_json({"type": "status", "state": message["type"]})
    except WebSocketDisconnect:
        return


def seed_demo():
    if not settings.demo_mode: return
    db = SessionLocal()
    try:
        if db.scalar(select(Facility).where(Facility.slug == "demo")): return
        facility = Facility(name="مجمع الشفاء الطبي", commercial_reg="1010456789", slug="demo")
        db.add(facility); db.flush()
        set_tenant_context(db, facility.id)
        clinic = Clinic(facility_id=facility.id, name="عيادة الباطنة"); db.add(clinic); db.flush()
        admin = User(facility_id=facility.id, role=Role.admin, full_name="عبدالله محمد العتيبي", username="admin", password_hash=hash_password("Admin123!"))
        doctor = User(facility_id=facility.id, role=Role.doctor, full_name="د. أحمد سعد الغامدي", username="doctor", password_hash=hash_password("Doctor123!"), specialty="باطنة", clinic_id=clinic.id)
        db.add_all([admin, doctor]); db.flush()
        db.add(Subscription(facility_id=facility.id, seats_total=5, plan="monthly"))
        patient = Patient(facility_id=facility.id, hospital_mrn="1042376", display_name="عبدالله محمد العتيبي", dob="1968-04-12", gender="ذكر", context_json={"chronic": ["Type 2 diabetes", "Hypertension"], "medications": ["Metformin 1000 mg BID", "Amlodipine 5 mg"], "allergies": ["No known drug allergies"], "last_hba1c": "7.4%"})
        db.add(patient)
        template = Template(facility_id=facility.id, name="متابعة سكري وضغط — مختصر", specialty="باطنة", visit_type="متابعة", structure_json={"sections": [{"section_key": x} for x in ["S", "O", "A", "P"]]}, origin="system", is_default=True)
        db.add(template); db.flush()
        audit(db, admin, "facility.demo_seeded", "facility", facility.id)
        db.commit()
    finally:
        db.close()
