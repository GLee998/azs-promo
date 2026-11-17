"""API акции «Комбо Coca-Cola»: приём заявок с чеками и закрытый реестр маркетинга."""

import csv
import io
import os
import random
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, LargeBinary, String,
                        Text, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


# ---------- настройки ----------

def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./promo.db")
    # Render отдаёт postgres:// или postgresql:// — указываем драйвер psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "demo")
SMS_REQUIRED = os.getenv("SMS_REQUIRED", "true").lower() == "true"
# В демо SMS не отправляются: код возвращается в ответе и показывается на экране.
DEMO_SMS = os.getenv("DEMO_SMS", "true").lower() == "true"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
MAX_PHOTO_BYTES = 10 * 1024 * 1024
SMS_TTL = timedelta(minutes=10)
SMS_RESEND = timedelta(seconds=30)
SMS_MAX_ATTEMPTS = 5

engine = create_engine(_db_url(), pool_pre_ping=True)


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    # SQLite возвращает даты без часового пояса.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- модель данных ----------

class Base(DeclarativeBase):
    pass


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # «123 ABC»
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    phone: Mapped[str] = mapped_column(String(16), index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Фискальные реквизиты: ФН + ФД + ФП однозначно определяют чек.
    fn: Mapped[str | None] = mapped_column(String(32))
    fd: Mapped[str | None] = mapped_column(String(32))
    fp: Mapped[str | None] = mapped_column(String(32))
    receipt_sum: Mapped[str | None] = mapped_column(String(16))
    receipt_date: Mapped[str | None] = mapped_column(String(32))
    fiscal_source: Mapped[str | None] = mapped_column(String(8))  # qr | manual
    fiscal_key: Mapped[str | None] = mapped_column(String(100), index=True)

    combos: Mapped[int] = mapped_column(Integer, default=1)
    check_purchase: Mapped[bool] = mapped_column(Boolean, default=False)
    check_issued: Mapped[bool] = mapped_column(Boolean, default=False)
    returned: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    reject_reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    photo: Mapped["Photo"] = relationship(back_populates="application", cascade="all, delete-orphan")
    participants: Mapped[list["Participant"]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="Participant.id")

    def set_fiscal(self, fn: str | None, fd: str | None, fp: str | None, source: str | None) -> None:
        self.fn, self.fd, self.fp = (clean_digits(v) for v in (fn, fd, fp))
        self.fiscal_key = f"{self.fn}/{self.fd}/{self.fp}" if self.fn and self.fd and self.fp else None
        self.fiscal_source = source if self.fiscal_key else None


class Photo(Base):
    __tablename__ = "photos"

    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    application: Mapped[Application] = relationship(back_populates="photo")


class Participant(Base):
    """Номер участника розыгрыша. Один номер — одно комбо в допущенном чеке."""
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    application: Mapped[Application] = relationship(back_populates="participants")

    @property
    def number(self) -> str:
        return f"CC-{self.id:05d}"


class SmsCode(Base):
    __tablename__ = "sms_codes"

    phone: Mapped[str] = mapped_column(String(16), primary_key=True)
    code: Mapped[str] = mapped_column(String(8))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)


Base.metadata.create_all(engine)


def get_db():
    with Session(engine) as db:
        yield db


# ---------- утилиты ----------

def clean_digits(v: str | None) -> str | None:
    if v is None:
        return None
    v = "".join(ch for ch in str(v) if ch.isdigit())
    return v or None


def normalize_phone(raw: str) -> str | None:
    """Кыргызский номер: +996 XXX XXX XXX. Принимаем и местную запись 0XXX XXX XXX."""
    d = "".join(ch for ch in raw if ch.isdigit())
    if d.startswith("9960"):
        d = "996" + d[4:]
    if len(d) == 10 and d[0] == "0":
        d = d[1:]
    if len(d) == 9:
        d = "996" + d
    return "+" + d if len(d) == 12 and d.startswith("996") else None


def process_photo(raw: bytes) -> bytes:
    """Проверяем, что это изображение, поворачиваем по EXIF, убираем метаданные, сжимаем."""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "Файл не похож на фото. Загрузите изображение чека.")
    img.thumbnail((1800, 1800))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


def new_code(db: Session) -> str:
    while True:
        # Как на кыргызском номерном знаке: 3 цифры и 3 латинские буквы, например «123 ABC».
        code = f"{random.randint(0, 999):03d} " + "".join(random.choices("ABCEHKMOPTXY", k=3))
        if not db.scalar(select(Application.id).where(Application.code == code)):
            return code


def require_admin(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "Нужен пароль реестра")


# ---------- приложение ----------

app = FastAPI(title="Promo receipts API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def public_config():
    return {"sms_required": SMS_REQUIRED, "demo_sms": DEMO_SMS}


# ---------- клиент ----------

class SmsRequest(BaseModel):
    phone: str


@app.post("/api/sms/send")
def sms_send(body: SmsRequest, db: Session = Depends(get_db)):
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Введите номер в формате +996 XXX XXX XXX")
    row = db.get(SmsCode, phone)
    if row and now() - as_utc(row.sent_at) < SMS_RESEND:
        raise HTTPException(429, "Код уже отправлен. Повторить можно через 30 секунд.")
    code = f"{secrets.randbelow(10000):04d}"
    if row:
        row.code, row.sent_at, row.attempts = code, now(), 0
    else:
        db.add(SmsCode(phone=phone, code=code, sent_at=now()))
    db.commit()
    # TODO боевая версия: отправка через SMS-провайдера Кыргызстана (например, Nikita, SMSPRO).
    return {"sent": True, "demo_code": code if DEMO_SMS else None}


def verify_sms(db: Session, phone: str, code: str) -> None:
    row = db.get(SmsCode, phone)
    if not row or now() - as_utc(row.sent_at) > SMS_TTL:
        raise HTTPException(400, "Код устарел. Запросите новый.")
    if row.attempts >= SMS_MAX_ATTEMPTS:
        raise HTTPException(400, "Слишком много попыток. Запросите новый код.")
    if not secrets.compare_digest(row.code, code.strip()):
        row.attempts += 1
        db.commit()
        raise HTTPException(400, "Неверный код из SMS")
    db.delete(row)


@app.post("/api/applications")
async def create_application(
    phone: str = Form(...),
    consent: bool = Form(...),
    photo: UploadFile = File(...),
    sms_code: str = Form(""),
    fn: str = Form(""),
    fd: str = Form(""),
    fp: str = Form(""),
    receipt_sum: str = Form(""),
    receipt_date: str = Form(""),
    db: Session = Depends(get_db),
):
    phone_n = normalize_phone(phone)
    if not phone_n:
        raise HTTPException(400, "Введите номер в формате +996 XXX XXX XXX")
    if not consent:
        raise HTTPException(400, "Нужно согласие на обработку данных")
    if SMS_REQUIRED:
        verify_sms(db, phone_n, sms_code)

    raw = await photo.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Фото слишком большое (больше 10 МБ)")

    a = Application(
        code=new_code(db),
        phone=phone_n,
        phone_verified=SMS_REQUIRED,
        consent_at=now(),
        receipt_sum=receipt_sum[:16] or None,
        receipt_date=receipt_date[:32] or None,
    )
    # Реквизиты из QR распознаёт браузер клиента. Им не доверяем: маркетинг сверяет их с продажами.
    a.set_fiscal(fn, fd, fp, "qr")
    a.photo = Photo(data=process_photo(raw))
    db.add(a)
    db.commit()
    # Клиенту не сообщаем о дубликате: результат он узнает после проверки.
    return {"code": a.code}


# ---------- реестр маркетинга ----------

def first_by_key(db: Session) -> dict[str, tuple[int, str]]:
    """Для каждого чека — самая ранняя заявка. Остальные с тем же чеком считаются дубликатами."""
    rows = db.execute(
        select(Application.fiscal_key, Application.id, Application.code)
        .where(Application.fiscal_key.is_not(None))
        .order_by(Application.created_at, Application.id)
    ).all()
    first: dict[str, tuple[int, str]] = {}
    for key, id_, code in rows:
        first.setdefault(key, (id_, code))
    return first


def serialize(a: Application, first: dict[str, tuple[int, str]]) -> dict:
    dup_of = None
    if a.fiscal_key and first.get(a.fiscal_key, (a.id,))[0] != a.id:
        dup_of = first[a.fiscal_key][1]
    return {
        "id": a.id,
        "code": a.code,
        "created_at": as_utc(a.created_at).isoformat(),
        "phone": a.phone,
        "phone_verified": a.phone_verified,
        "fn": a.fn, "fd": a.fd, "fp": a.fp,
        "receipt_sum": a.receipt_sum,
        "receipt_date": a.receipt_date,
        "fiscal_source": a.fiscal_source,
        "combos": a.combos,
        "check_purchase": a.check_purchase,
        "check_issued": a.check_issued,
        "returned": a.returned,
        "status": a.status,
        "reject_reason": a.reject_reason,
        "note": a.note,
        "numbers": [p.number for p in a.participants],
        "duplicate_of": dup_of,
    }


def get_app(db: Session, app_id: int) -> Application:
    a = db.get(Application, app_id)
    if not a:
        raise HTTPException(404, "Заявка не найдена")
    return a


@app.post("/api/admin/login", dependencies=[Depends(require_admin)])
def admin_login():
    return {"ok": True}


@app.get("/api/admin/applications", dependencies=[Depends(require_admin)])
def list_applications(db: Session = Depends(get_db)):
    first = first_by_key(db)
    apps = db.scalars(select(Application).order_by(Application.created_at.desc())).all()
    items = [serialize(a, first) for a in apps]
    stats = {
        "total": len(items),
        "pending": sum(i["status"] == "pending" for i in items),
        "approved": sum(i["status"] == "approved" for i in items),
        "rejected": sum(i["status"] == "rejected" for i in items),
        "duplicates": sum(bool(i["duplicate_of"]) for i in items),
        "participants": sum(len(i["numbers"]) for i in items),
    }
    return {"items": items, "stats": stats}


@app.get("/api/admin/applications/{app_id}/photo", dependencies=[Depends(require_admin)])
def get_photo(app_id: int, db: Session = Depends(get_db)):
    photo = db.get(Photo, app_id)
    if not photo:
        raise HTTPException(404, "Фото не найдено")
    return Response(photo.data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class ApplicationPatch(BaseModel):
    fn: str | None = None
    fd: str | None = None
    fp: str | None = None
    combos: int | None = None
    check_purchase: bool | None = None
    check_issued: bool | None = None
    returned: bool | None = None
    note: str | None = None


@app.patch("/api/admin/applications/{app_id}", dependencies=[Depends(require_admin)])
def patch_application(app_id: int, body: ApplicationPatch, db: Session = Depends(get_db)):
    a = get_app(db, app_id)
    data = body.model_dump(exclude_unset=True)
    if a.status == "approved" and data.keys() & {"fn", "fd", "fp", "combos"}:
        raise HTTPException(400, "Заявка уже допущена. Верните её на проверку, чтобы изменить реквизиты.")
    if {"fn", "fd", "fp"} & data.keys():
        changed = any(clean_digits(data.get(k, getattr(a, k))) != getattr(a, k) for k in ("fn", "fd", "fp"))
        a.set_fiscal(data.get("fn", a.fn), data.get("fd", a.fd), data.get("fp", a.fp),
                     "manual" if changed else a.fiscal_source)
    if "combos" in data:
        if not 1 <= data["combos"] <= 20:
            raise HTTPException(400, "Количество комбо — от 1 до 20")
        a.combos = data["combos"]
    for field in ("check_purchase", "check_issued", "note"):
        if field in data:
            setattr(a, field, data[field])
    if "returned" in data:
        a.returned = data["returned"]
        if a.returned and a.status == "approved":
            revoke(a, "Возврат покупки")
    db.commit()
    return serialize(a, first_by_key(db))


def revoke(a: Application, reason: str) -> None:
    a.status = "rejected"
    a.reject_reason = reason
    a.participants.clear()


@app.post("/api/admin/applications/{app_id}/approve", dependencies=[Depends(require_admin)])
def approve(app_id: int, db: Session = Depends(get_db)):
    a = get_app(db, app_id)
    if a.status == "approved":
        raise HTTPException(400, "Заявка уже допущена")
    problems = []
    if not a.fiscal_key:
        problems.append("не заполнены ФН, ФД и ФП")
    if not a.check_purchase:
        problems.append("покупка не сверена с продажами")
    if not a.check_issued:
        problems.append("нет отметки о выдаче игрушки")
    if a.returned:
        problems.append("по чеку был возврат")
    if a.fiscal_key:
        other = db.scalar(select(Application.code).where(
            Application.fiscal_key == a.fiscal_key, Application.status == "approved", Application.id != a.id))
        if other:
            problems.append(f"этот чек уже допущен в заявке {other}")
    if problems:
        raise HTTPException(400, "Нельзя допустить: " + "; ".join(problems))
    a.status = "approved"
    a.reject_reason = None
    a.participants = [Participant() for _ in range(a.combos)]
    db.commit()
    return serialize(a, first_by_key(db))


class RejectBody(BaseModel):
    reason: str = ""


@app.post("/api/admin/applications/{app_id}/reject", dependencies=[Depends(require_admin)])
def reject(app_id: int, body: RejectBody, db: Session = Depends(get_db)):
    a = get_app(db, app_id)
    revoke(a, body.reason.strip() or "Не прошла проверку")
    db.commit()
    return serialize(a, first_by_key(db))


@app.post("/api/admin/applications/{app_id}/reset", dependencies=[Depends(require_admin)])
def reset(app_id: int, db: Session = Depends(get_db)):
    a = get_app(db, app_id)
    a.status = "pending"
    a.reject_reason = None
    a.participants.clear()
    db.commit()
    return serialize(a, first_by_key(db))


def read_csv_rows(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]


@app.post("/api/admin/import/{kind}", dependencies=[Depends(require_admin)])
async def import_csv(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Сверка с выгрузками кассы. CSV с колонками fn;fd;fp (для продаж ещё qty — число комбо в чеке)."""
    if kind not in ("sales", "issued", "returns"):
        raise HTTPException(404, "Неизвестный тип выгрузки")
    rows = read_csv_rows(await file.read())
    if rows and not {"fn", "fd", "fp"} <= rows[0].keys():
        raise HTTPException(400, "В CSV нужны колонки fn, fd, fp")
    matched = 0
    for row in rows:
        key = "/".join(clean_digits(row.get(k)) or "" for k in ("fn", "fd", "fp"))
        apps = db.scalars(select(Application).where(Application.fiscal_key == key)).all()
        for a in apps:
            matched += 1
            if kind == "sales":
                a.check_purchase = True
                qty = clean_digits(row.get("qty"))
                if qty and a.status != "approved":
                    a.combos = max(1, min(20, int(qty)))
            elif kind == "issued":
                a.check_issued = True
            else:
                a.returned = True
                if a.status == "approved":
                    revoke(a, "Возврат покупки")
    db.commit()
    return {"rows": len(rows), "matched": matched}


@app.get("/api/admin/export.csv", dependencies=[Depends(require_admin)])
def export_csv(db: Session = Depends(get_db)):
    first = first_by_key(db)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Заявка", "Дата", "Телефон", "Телефон подтверждён", "ФН", "ФД", "ФП", "Сумма",
                "Комбо", "Покупка сверена", "Игрушка выдана", "Возврат", "Статус", "Причина",
                "Дубликат заявки", "Номера участников"])
    yes = lambda v: "да" if v else "нет"
    for a in db.scalars(select(Application).order_by(Application.created_at)):
        s = serialize(a, first)
        w.writerow([s["code"], s["created_at"], s["phone"], yes(s["phone_verified"]), s["fn"], s["fd"],
                    s["fp"], s["receipt_sum"], s["combos"], yes(s["check_purchase"]),
                    yes(s["check_issued"]), yes(s["returned"]), s["status"], s["reject_reason"],
                    s["duplicate_of"], " ".join(s["numbers"])])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="registry.csv"'})


# Тот же сервер отдаёт и фронтенд, чтобы всё работало по одной ссылке.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
