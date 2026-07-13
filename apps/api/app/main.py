import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .models import (
    Approval, AuditLog, Clinic, Facility, GuidanceItem, Patient, Role, SeatEvent,
    Subscription, SummarySection, Template, UploadJob, User, Visit, VisitState,
)
from .security import create_token, current_user, hash_password, require, verify_password


app = FastAPI(title="Medify API", version="1.0.0", docs_url="/docs", redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    db.add(AuditLog(facility_id=user.facility_id, actor_user_id=user.id, action=action, entity=entity, entity_id=entity_id, meta_json=meta or {}))


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
    password: str = Field(min_length=8)
    seats: int = Field(default=3, ge=1, le=500)


class ClinicIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class DoctorIn(BaseModel):
    full_name: str
    username: str
    password: str = Field(min_length=8)
    specialty: str
    clinic_id: str


class DoctorPatch(BaseModel):
    full_name: str | None = None
    specialty: str | None = None
    clinic_id: str | None = None
    is_active: bool | None = None


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


@app.get("/health")
def health():
    return {"status": "ok", "service": "medify-api"}


@app.post("/api/v1/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    facility = db.scalar(select(Facility).where((Facility.slug == body.facility) | (Facility.commercial_reg == body.facility)))
    user = db.scalar(select(User).where(User.facility_id == facility.id, User.username == body.username)) if facility else None
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "MDF-4011")
    if not user.is_active or facility.status != "active":
        raise HTTPException(403, "MDF-4013")
    clinic = db.get(Clinic, user.clinic_id) if user.clinic_id else None
    return data({"access_token": create_token(user), "token_type": "bearer", "user": {**user_json(user), "facility_name": facility.name, "clinic_name": clinic.name if clinic else None}, "facility": {"id": facility.id, "name": facility.name, "slug": facility.slug}})


@app.post("/api/v1/auth/refresh")
def refresh(user: User = Depends(current_user)):
    return data({"access_token": create_token(user), "token_type": "bearer"})


@app.post("/api/v1/auth/logout")
def logout(_: User = Depends(current_user)):
    return data({"logged_out": True})


@app.get("/api/v1/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    facility = db.get(Facility, user.facility_id)
    clinic = db.get(Clinic, user.clinic_id) if user.clinic_id else None
    return data({**user_json(user), "facility_name": facility.name, "clinic_name": clinic.name if clinic else None})


@app.post("/api/v1/facilities/register")
def register(body: FacilityIn, db: Session = Depends(get_db)):
    if db.scalar(select(Facility).where((Facility.slug == body.slug) | (Facility.commercial_reg == body.commercial_reg))):
        raise HTTPException(422, "MDF-4225")
    facility = Facility(name=body.name, commercial_reg=body.commercial_reg, slug=body.slug)
    db.add(facility); db.flush()
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
def reset_password(doctor_id: str, body: dict, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    row = db.scalar(select(User).where(User.id == doctor_id, User.facility_id == user.facility_id, User.role == Role.doctor))
    if not row: raise HTTPException(404, "MDF-4041")
    row.password_hash = hash_password(body.get("password", "Doctor123!")); audit(db, user, "doctor.password_reset", "user", row.id); db.commit()
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
def integration(user: User = Depends(require(Role.admin))):
    return data({"endpoint_url": "https://his.example.sa/fhir", "mode": "test", "last_test_ok": True, "last_test_at": stamp(datetime.now(timezone.utc)), "secret_configured": False})


@app.patch("/api/v1/settings/integration")
def patch_integration(body: dict, user: User = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    audit(db, user, "settings.integration_updated", "facility", user.facility_id, {"mode": body.get("mode", "test")}); db.commit(); return data({**body, "secret_configured": bool(body.get("auth_secret"))})


@app.post("/api/v1/settings/integration/test")
def test_integration(user: User = Depends(require(Role.admin))):
    return data({"ok": True, "latency_ms": 184, "mode": "test"})


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
    job = UploadJob(facility_id=user.facility_id, visit_id=row.id, status="confirmed", attempts_count=1, result_json={"bundle_id": f"Bundle/{row.id[:8]}", "nphies_validation": "passed", "demo": settings.demo_mode})
    db.add(job); row.state = VisitState.uploaded; audit(db, user, "visit.approved", "visit", row.id); audit(db, user, "upload.result", "visit", row.id, {"status": "confirmed", "attempts": 1}); db.commit()
    return data({"approval_id": approval.id, "summary_hash": approval.summary_hash, "upload": job.result_json, "status": job.status})


@app.get("/api/v1/visits/{visit_id}/upload-status")
def upload_status(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user); job = db.scalar(select(UploadJob).where(UploadJob.visit_id == row.id)); return data({"status": job.status if job else "not_started", "attempts": job.attempts_count if job else 0, "result": job.result_json if job else {}})


@app.post("/api/v1/visits/{visit_id}/upload-retry")
def upload_retry(visit_id: str, user: User = Depends(require(Role.doctor)), db: Session = Depends(get_db)):
    row = owned_visit(db, visit_id, user); job = db.scalar(select(UploadJob).where(UploadJob.visit_id == row.id))
    if not job: raise HTTPException(404, "MDF-4041")
    job.attempts_count += 1; job.status = "confirmed"; row.state = VisitState.uploaded; db.commit(); return data({"status": job.status, "attempts": job.attempts_count})


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


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    seed_demo()
