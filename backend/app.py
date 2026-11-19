"""API акции «Караван новогодних чудес» (Partner Neft × Coca-Cola).

Клиент регистрирует чек до выдачи машинки, система проверяет номер и чек по общей базе
всех АЗС, кассир видит одобренную заявку, выдаёт машинку и отмечает выдачу. После выдачи
номер телефона получает одну заявку на розыгрыш.
"""

import csv
import hashlib
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
from sqlalchemy import (Boolean, DateTime, ForeignKey, LargeBinary, String, Text,
                        create_engine, select)
from sqlalchemy.exc import IntegrityError
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
CASHIER_PASSWORD = os.getenv("CASHIER_PASSWORD", "kassa")
# Подтверждение номера кодом выключено по решению заказчика. Механизм оставлен, чтобы его можно
# было включить (SMS или WhatsApp), если начнут регистрировать чужие номера.
SMS_REQUIRED = os.getenv("SMS_REQUIRED", "false").lower() == "true"
# В демо SMS не отправляются: код возвращается в ответе и показывается на экране.
DEMO_SMS = os.getenv("DEMO_SMS", "true").lower() == "true"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
MAX_PHOTO_BYTES = 10 * 1024 * 1024
SMS_TTL = timedelta(minutes=10)
SMS_RESEND = timedelta(seconds=30)
SMS_MAX_ATTEMPTS = 5
# Заявка, по которой машинку так и не выдали, перестаёт держать номер и чек.
WAITING_TTL = timedelta(hours=int(os.getenv("WAITING_TTL_HOURS", "3")))
DRAW_PRIZES = int(os.getenv("DRAW_PRIZES", "8"))  # 8 поездок на новогоднем караване
# Сброс розыгрыша нужен только на тестовом стенде, в проде он выключен.
DRAW_RESET_ALLOWED = os.getenv("DRAW_RESET_ALLOWED", "false").lower() == "true"
# Локально сервер отдаёт и три сайта (клиент, касса, админка). В проде они живут на отдельных адресах.
SERVE_WEB = os.getenv("SERVE_WEB", "true").lower() == "true"

engine = create_engine(_db_url(), pool_pre_ping=True)


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    # SQLite возвращает даты без часового пояса.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- модель данных ----------

class Base(DeclarativeBase):
    pass


class Registration(Base):
    """Регистрация чека.

    Статусы:
      waiting      — проверки пройдены, ждёт выдачи машинки на кассе;
      issued       — машинка выдана, номер телефона участвует в розыгрыше;
      cancelled    — отменена до выдачи (кассиром или по сроку), номер и чек свободны;
      disqualified — снята маркетингом после выдачи (например, возврат), номер остаётся занятым.

    phone_lock и receipt_lock заполнены, пока заявка действует. Уникальные индексы по ним
    не дают зарегистрировать тот же номер или чек дважды, в том числе одновременно на двух АЗС.
    """
    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # «123 ABC», показывается кассиру
    token: Mapped[str] = mapped_column(String(64), unique=True)  # для экрана статуса у клиента
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    station: Mapped[str | None] = mapped_column(String(32))
    phone: Mapped[str] = mapped_column(String(16), index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Фискальные реквизиты: ФН + ФД + ФМ однозначно определяют чек.
    fn: Mapped[str | None] = mapped_column(String(32))
    fd: Mapped[str | None] = mapped_column(String(32))
    fm: Mapped[str | None] = mapped_column(String(32))
    receipt_sum: Mapped[str | None] = mapped_column(String(16))
    receipt_date: Mapped[str | None] = mapped_column(String(32))
    fiscal_source: Mapped[str | None] = mapped_column(String(8))  # qr | cashier

    phone_lock: Mapped[str | None] = mapped_column(String(16), unique=True)
    receipt_lock: Mapped[str | None] = mapped_column(String(100), unique=True)

    status: Mapped[str] = mapped_column(String(16), default="waiting")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_station: Mapped[str | None] = mapped_column(String(32))
    status_reason: Mapped[str | None] = mapped_column(Text)
    check_purchase: Mapped[bool] = mapped_column(Boolean, default=False)
    returned: Mapped[bool] = mapped_column(Boolean, default=False)

    photo: Mapped["RegistrationPhoto"] = relationship(back_populates="registration", cascade="all, delete-orphan")
    entry: Mapped["Entry | None"] = relationship(back_populates="registration", cascade="all, delete-orphan")

    @property
    def fiscal_key(self) -> str | None:
        return f"{self.fn}/{self.fd}/{self.fm}" if self.fn and self.fd and self.fm else None

    def set_fiscal(self, fn, fd, fm, source: str) -> None:
        self.fn, self.fd, self.fm = (clean_digits(v) for v in (fn, fd, fm))
        self.fiscal_source = source if self.fiscal_key else None
        self.receipt_lock = self.fiscal_key

    def release(self, status: str, reason: str) -> None:
        self.status = status
        self.status_reason = reason
        self.phone_lock = None
        self.receipt_lock = None

    def expired(self) -> bool:
        return self.status == "waiting" and now() - as_utc(self.created_at) > WAITING_TTL


class RegistrationPhoto(Base):
    __tablename__ = "registration_photos"

    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    registration: Mapped[Registration] = relationship(back_populates="photo")


class Entry(Base):
    """Заявка на розыгрыш: одна на номер телефона, появляется после выдачи машинки."""
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id"), unique=True)
    registration: Mapped[Registration] = relationship(back_populates="entry")

    @property
    def number(self) -> str:
        return f"KN-{self.id:05d}"


class Draw(Base):
    """Розыгрыш. Хранит снимок списка участников и его хеш, чтобы результат можно было проверить."""
    __tablename__ = "draws"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    only_checked: Mapped[bool] = mapped_column(Boolean, default=True)
    entries_count: Mapped[int] = mapped_column()
    entries_hash: Mapped[str] = mapped_column(String(64))
    entries_snapshot: Mapped[str] = mapped_column(Text)  # номера участников через перевод строки
    winners: Mapped[list["DrawWinner"]] = relationship(
        back_populates="draw", cascade="all, delete-orphan", order_by="DrawWinner.id")


class DrawWinner(Base):
    __tablename__ = "draw_winners"

    id: Mapped[int] = mapped_column(primary_key=True)
    draw_id: Mapped[int] = mapped_column(ForeignKey("draws.id"))
    place: Mapped[int] = mapped_column()
    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id"))
    entry_number: Mapped[str] = mapped_column(String(16))
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    replaced_reason: Mapped[str | None] = mapped_column(Text)  # заполнено, если победителя переиграли
    draw: Mapped[Draw] = relationship(back_populates="winners")
    registration: Mapped[Registration] = relationship()


class SmsCode(Base):
    __tablename__ = "sms_codes"

    phone: Mapped[str] = mapped_column(String(16), primary_key=True)
    code: Mapped[str] = mapped_column(String(8))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)


Base.metadata.create_all(engine)


def get_db():
    with Session(engine) as db:
        yield db


# ---------- утилиты ----------

def clean_digits(v) -> str | None:
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


def mask_phone(phone: str) -> str:
    # +996 555 *** 456 — кассиру хватает, чтобы сверить с телефоном клиента.
    return f"{phone[:4]} {phone[4:7]} *** {phone[-3:]}"


def clean_station(v: str | None) -> str | None:
    v = (v or "").strip()[:32]
    return v or None


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
        if not db.scalar(select(Registration.id).where(Registration.code == code)):
            return code


def bearer(authorization: str) -> str:
    return authorization.removeprefix("Bearer ").strip()


def require_admin(authorization: str = Header(default="")) -> None:
    if not secrets.compare_digest(bearer(authorization).encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "Нужен пароль реестра")


def require_cashier(authorization: str = Header(default="")) -> None:
    # Маркетинг тоже может работать в кассовом интерфейсе своим паролем.
    token = bearer(authorization).encode()
    if not (secrets.compare_digest(token, CASHIER_PASSWORD.encode())
            or secrets.compare_digest(token, ADMIN_PASSWORD.encode())):
        raise HTTPException(401, "Нужен пароль кассира")


# ---------- приложение ----------

app = FastAPI(title="Karavan promo API")
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
    # Не тратим SMS на номер, который уже получил машинку.
    taken = db.scalar(select(Registration).where(Registration.phone_lock == phone))
    if taken and not taken.expired():
        raise HTTPException(409, phone_taken_message(taken))
    row = db.get(SmsCode, phone)
    if row and now() - as_utc(row.sent_at) < SMS_RESEND:
        raise HTTPException(429, "Код уже отправлен. Повторить можно через 30 секунд.")
    code = f"{secrets.randbelow(10000):04d}"
    if row:
        row.code, row.sent_at, row.attempts = code, now(), 0
    else:
        db.add(SmsCode(phone=phone, code=code, sent_at=now()))
    db.commit()
    # TODO боевая версия: отправка через SMS- или WhatsApp-провайдера.
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


def phone_taken_message(r: Registration) -> str:
    if r.status == "waiting":
        return (f"Этот номер уже зарегистрирован, заявка {r.code} ждёт выдачи игрушки. "
                "Покажите её кассиру.")
    return "Этот номер уже получил игрушечный караван. По условиям акции игрушка выдаётся один раз на номер."


def release_expired(db: Session, phone: str, fiscal_key: str | None) -> None:
    """Освобождаем номер и чек, если их держит заявка, по которой машинку так и не выдали."""
    conds = [Registration.phone_lock == phone]
    if fiscal_key:
        conds.append(Registration.receipt_lock == fiscal_key)
    for r in db.scalars(select(Registration).where(Registration.status == "waiting")
                        .where(conds[0] if len(conds) == 1 else (conds[0] | conds[1]))):
        if r.expired():
            r.release("cancelled", "Игрушку не забрали вовремя")
    db.flush()


def conflict_message(db: Session, phone: str, fiscal_key: str | None) -> str:
    taken = db.scalar(select(Registration).where(Registration.phone_lock == phone))
    if taken:
        return phone_taken_message(taken)
    if fiscal_key and db.scalar(select(Registration.id).where(Registration.receipt_lock == fiscal_key)):
        return "Этот чек уже зарегистрирован. Один чек можно зарегистрировать только один раз."
    return "Не удалось зарегистрировать чек. Попробуйте ещё раз."


@app.post("/api/registrations")
async def create_registration(
    phone: str = Form(...),
    consent: bool = Form(...),
    photo: UploadFile = File(...),
    sms_code: str = Form(""),
    station: str = Form(""),
    fn: str = Form(""),
    fd: str = Form(""),
    fm: str = Form(""),
    receipt_sum: str = Form(""),
    receipt_date: str = Form(""),
    db: Session = Depends(get_db),
):
    phone_n = normalize_phone(phone)
    if not phone_n:
        raise HTTPException(400, "Введите номер в формате +996 XXX XXX XXX")
    if not consent:
        raise HTTPException(400, "Нужно согласие с правилами акции и обработкой данных")

    raw = await photo.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Фото слишком большое (больше 10 МБ)")
    photo_data = process_photo(raw)

    r = Registration(
        code=new_code(db),
        token=secrets.token_urlsafe(24),
        station=clean_station(station),
        phone=phone_n,
        phone_lock=phone_n,
        phone_verified=SMS_REQUIRED,
        consent_at=now(),
        receipt_sum=receipt_sum[:16] or None,
        receipt_date=receipt_date[:32] or None,
    )
    # Реквизиты из QR распознаёт браузер клиента. Если QR не прочитался, их введёт кассир.
    r.set_fiscal(fn, fd, fm, "qr")
    r.photo = RegistrationPhoto(data=photo_data)

    release_expired(db, phone_n, r.fiscal_key)
    if SMS_REQUIRED:
        verify_sms(db, phone_n, sms_code)
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        # Уникальные индексы сработали: номер или чек уже заняты (возможно, секунду назад на другой АЗС).
        db.rollback()
        raise HTTPException(409, conflict_message(db, phone_n, r.fiscal_key))
    return {"code": r.code, "token": r.token, "status": r.status}


@app.get("/api/registrations/status/{token}")
def registration_status(token: str, db: Session = Depends(get_db)):
    r = db.scalar(select(Registration).where(Registration.token == token))
    if not r:
        raise HTTPException(404, "Заявка не найдена")
    if r.expired():
        r.release("cancelled", "Игрушку не забрали вовремя")
        db.commit()
    return {"code": r.code, "status": r.status, "reason": r.status_reason,
            "entry": r.entry.number if r.entry else None}


# ---------- касса ----------

def cashier_view(r: Registration) -> dict:
    return {
        "id": r.id,
        "code": r.code,
        "created_at": as_utc(r.created_at).isoformat(),
        "station": r.station,
        "phone": mask_phone(r.phone),
        "fn": r.fn, "fd": r.fd, "fm": r.fm,
        "receipt_sum": r.receipt_sum,
        "needs_fiscal": not r.fiscal_key,
        "status": r.status,
        "reason": r.status_reason,
        "issued_station": r.issued_station,
        "entry": r.entry.number if r.entry else None,
    }


def get_reg(db: Session, reg_id: int) -> Registration:
    r = db.get(Registration, reg_id)
    if not r:
        raise HTTPException(404, "Заявка не найдена")
    return r


@app.post("/api/cashier/login", dependencies=[Depends(require_cashier)])
def cashier_login():
    return {"ok": True}


@app.get("/api/cashier/queue", dependencies=[Depends(require_cashier)])
def cashier_queue(db: Session = Depends(get_db)):
    """Заявки, ожидающие выдачи, и последние выданные — по всем АЗС, свежие сверху."""
    waiting = db.scalars(select(Registration).where(Registration.status == "waiting")
                         .order_by(Registration.created_at.desc())).all()
    changed = False
    for r in waiting:
        if r.expired():
            r.release("cancelled", "Игрушку не забрали вовремя")
            changed = True
    if changed:
        db.commit()
    recent = db.scalars(select(Registration).where(Registration.status == "issued")
                        .order_by(Registration.issued_at.desc()).limit(10)).all()
    return {"waiting": [cashier_view(r) for r in waiting if r.status == "waiting"],
            "recent": [cashier_view(r) for r in recent]}


@app.get("/api/cashier/registrations/{reg_id}/photo", dependencies=[Depends(require_cashier)])
def cashier_photo(reg_id: int, db: Session = Depends(get_db)):
    photo = db.get(RegistrationPhoto, reg_id)
    if not photo:
        raise HTTPException(404, "Фото не найдено")
    return Response(photo.data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class IssueBody(BaseModel):
    station: str = ""
    fn: str | None = None
    fd: str | None = None
    fm: str | None = None


@app.post("/api/cashier/registrations/{reg_id}/issue", dependencies=[Depends(require_cashier)])
def cashier_issue(reg_id: int, body: IssueBody, db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "waiting" or r.expired():
        raise HTTPException(400, "Заявка уже не ждёт выдачи. Обновите список.")
    if not r.fiscal_key:
        r.set_fiscal(body.fn, body.fd, body.fm, "cashier")
        if not r.fiscal_key:
            raise HTTPException(400, "Введите ФН, ФД и ФМ с бумажного чека")
    r.status = "issued"
    r.issued_at = now()
    r.issued_station = clean_station(body.station) or r.station
    r.entry = Entry()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Этот чек уже зарегистрирован другой заявкой. Машинку не выдавайте.")
    return cashier_view(r)


class ReasonBody(BaseModel):
    reason: str = ""


@app.post("/api/cashier/registrations/{reg_id}/cancel", dependencies=[Depends(require_cashier)])
def cashier_cancel(reg_id: int, body: ReasonBody, db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "waiting":
        raise HTTPException(400, "Отменить можно только заявку, которая ждёт выдачи")
    r.release("cancelled", body.reason.strip() or "Отменена на кассе")
    db.commit()
    return cashier_view(r)


# ---------- реестр маркетинга ----------

def admin_view(r: Registration) -> dict:
    return {
        **cashier_view(r),
        "phone": r.phone,
        "phone_verified": r.phone_verified,
        "receipt_date": r.receipt_date,
        "fiscal_source": r.fiscal_source,
        "issued_at": as_utc(r.issued_at).isoformat() if r.issued_at else None,
        "check_purchase": r.check_purchase,
        "returned": r.returned,
    }


@app.post("/api/admin/login", dependencies=[Depends(require_admin)])
def admin_login():
    return {"ok": True}


@app.get("/api/admin/registrations", dependencies=[Depends(require_admin)])
def list_registrations(db: Session = Depends(get_db)):
    regs = db.scalars(select(Registration).order_by(Registration.created_at.desc())).all()
    items = [admin_view(r) for r in regs]
    stats = {
        "total": len(items),
        "waiting": sum(i["status"] == "waiting" for i in items),
        "issued": sum(i["status"] == "issued" for i in items),
        "cancelled": sum(i["status"] == "cancelled" for i in items),
        "disqualified": sum(i["status"] == "disqualified" for i in items),
        "unchecked": sum(i["status"] == "issued" and not i["check_purchase"] for i in items),
    }
    return {"items": items, "stats": stats}


@app.get("/api/admin/registrations/{reg_id}/photo", dependencies=[Depends(require_admin)])
def admin_photo(reg_id: int, db: Session = Depends(get_db)):
    return cashier_photo(reg_id, db)


class AdminPatch(BaseModel):
    check_purchase: bool | None = None
    returned: bool | None = None


@app.patch("/api/admin/registrations/{reg_id}", dependencies=[Depends(require_admin)])
def patch_registration(reg_id: int, body: AdminPatch, db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    data = body.model_dump(exclude_unset=True)
    if "check_purchase" in data:
        r.check_purchase = data["check_purchase"]
    if "returned" in data:
        r.returned = data["returned"]
        if r.returned and r.status == "issued":
            disqualify(r, "Возврат покупки")
    db.commit()
    return admin_view(r)


def disqualify(r: Registration, reason: str) -> None:
    # Машинка уже выдана: номер и чек остаются занятыми, снимается только заявка на розыгрыш.
    r.status = "disqualified"
    r.status_reason = reason
    r.entry = None


@app.post("/api/admin/registrations/{reg_id}/disqualify", dependencies=[Depends(require_admin)])
def admin_disqualify(reg_id: int, body: ReasonBody, db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "issued":
        raise HTTPException(400, "Снять с розыгрыша можно только заявку с выданной машинкой")
    disqualify(r, body.reason.strip() or "Снята маркетингом")
    db.commit()
    return admin_view(r)


@app.post("/api/admin/registrations/{reg_id}/restore", dependencies=[Depends(require_admin)])
def admin_restore(reg_id: int, db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "disqualified":
        raise HTTPException(400, "Вернуть в розыгрыш можно только снятую заявку")
    if r.returned:
        raise HTTPException(400, "По чеку был возврат. Сначала снимите отметку о возврате.")
    r.status = "issued"
    r.status_reason = None
    r.entry = Entry()
    db.commit()
    return admin_view(r)


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
    """Сверка с выгрузками кассы. CSV с колонками fn;fd;fm (колонка fp тоже подходит вместо fm)."""
    if kind not in ("sales", "returns"):
        raise HTTPException(404, "Неизвестный тип выгрузки")
    rows = read_csv_rows(await file.read())
    if rows and not ({"fn", "fd"} <= rows[0].keys() and ({"fm", "fp"} & rows[0].keys())):
        raise HTTPException(400, "В CSV нужны колонки fn, fd, fm")
    matched = 0
    for row in rows:
        fn, fd, fm = (clean_digits(row.get(k)) for k in ("fn", "fd", "fm" if "fm" in row else "fp"))
        regs = db.scalars(select(Registration).where(
            Registration.fn == fn, Registration.fd == fd, Registration.fm == fm)).all()
        for r in regs:
            matched += 1
            if kind == "sales":
                r.check_purchase = True
            else:
                r.returned = True
                if r.status == "issued":
                    disqualify(r, "Возврат покупки")
    db.commit()
    return {"rows": len(rows), "matched": matched}


STATUS_RU = {"waiting": "ждёт выдачи", "issued": "машинка выдана", "cancelled": "отменена",
             "disqualified": "снята с розыгрыша"}


@app.get("/api/admin/export.csv", dependencies=[Depends(require_admin)])
def export_csv(db: Session = Depends(get_db)):
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Заявка", "Дата", "АЗС", "Телефон", "Телефон подтверждён", "ФН", "ФД", "ФМ", "Сумма",
                "Статус", "Причина", "Выдано", "АЗС выдачи", "Покупка сверена", "Возврат",
                "Номер участника"])
    yes = lambda v: "да" if v else "нет"
    for r in db.scalars(select(Registration).order_by(Registration.created_at)):
        w.writerow([r.code, as_utc(r.created_at).isoformat(), r.station, r.phone, yes(r.phone_verified),
                    r.fn, r.fd, r.fm, r.receipt_sum, STATUS_RU[r.status], r.status_reason,
                    as_utc(r.issued_at).isoformat() if r.issued_at else "", r.issued_station,
                    yes(r.check_purchase), yes(r.returned), r.entry.number if r.entry else ""])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="registry.csv"'})


# ---------- розыгрыш ----------

def eligible(db: Session, only_checked: bool) -> list[Registration]:
    """Участники: машинка выдана, возврата не было; при only_checked — покупка сверена с продажами."""
    q = select(Registration).where(Registration.status == "issued", Registration.returned.is_(False))
    if only_checked:
        q = q.where(Registration.check_purchase.is_(True))
    return [r for r in db.scalars(q) if r.entry]


def draw_view(d: Draw) -> dict:
    return {
        "id": d.id,
        "created_at": as_utc(d.created_at).isoformat(),
        "only_checked": d.only_checked,
        "entries_count": d.entries_count,
        "entries_hash": d.entries_hash,
        "winners": [{
            "id": w.id,
            "place": w.place,
            "entry": w.entry_number,
            "code": w.registration.code,
            "phone": w.registration.phone,
            "station": w.registration.issued_station,
            "picked_at": as_utc(w.picked_at).isoformat(),
            "replaced_reason": w.replaced_reason,
        } for w in d.winners],
    }


def current_draw(db: Session) -> Draw | None:
    return db.scalar(select(Draw).order_by(Draw.id.desc()).limit(1))


@app.get("/api/admin/draw", dependencies=[Depends(require_admin)])
def get_draw(only_checked: bool = True, db: Session = Depends(get_db)):
    d = current_draw(db)
    return {
        "prizes": DRAW_PRIZES,
        "reset_allowed": DRAW_RESET_ALLOWED,
        "eligible": len(eligible(db, only_checked)),
        "eligible_all": len(eligible(db, False)),
        "draw": draw_view(d) if d else None,
    }


class DrawBody(BaseModel):
    only_checked: bool = True


@app.post("/api/admin/draw", dependencies=[Depends(require_admin)])
def run_draw(body: DrawBody, db: Session = Depends(get_db)):
    if current_draw(db):
        raise HTTPException(400, "Розыгрыш уже проведён. Победителя можно только переиграть.")
    pool = sorted(eligible(db, body.only_checked), key=lambda r: r.entry.id)
    if len(pool) < DRAW_PRIZES:
        raise HTTPException(400, f"Недостаточно участников: {len(pool)}, а призов {DRAW_PRIZES}")
    snapshot = "\n".join(r.entry.number for r in pool)
    # Криптографически стойкий генератор: результат нельзя предсказать или подогнать.
    picked = secrets.SystemRandom().sample(pool, DRAW_PRIZES)
    d = Draw(only_checked=body.only_checked, entries_count=len(pool),
             entries_hash=hashlib.sha256(snapshot.encode()).hexdigest(), entries_snapshot=snapshot)
    d.winners = [DrawWinner(place=i + 1, registration_id=r.id, entry_number=r.entry.number)
                 for i, r in enumerate(picked)]
    db.add(d)
    db.commit()
    return draw_view(d)


@app.post("/api/admin/draw/winners/{winner_id}/redraw", dependencies=[Depends(require_admin)])
def redraw_winner(winner_id: int, body: ReasonBody, db: Session = Depends(get_db)):
    """Переигрываем одно место: например, победитель не вышел на связь или снят с розыгрыша."""
    w = db.get(DrawWinner, winner_id)
    if not w or w.replaced_reason:
        raise HTTPException(404, "Победитель не найден или уже переигран")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Укажите причину, по которой переигрываете место")
    d = w.draw
    taken = {x.registration_id for x in d.winners}
    snapshot = set(d.entries_snapshot.split("\n"))
    pool = [r for r in eligible(db, d.only_checked) if r.id not in taken and r.entry.number in snapshot]
    if not pool:
        raise HTTPException(400, "Не осталось участников для переигрывания")
    r = secrets.SystemRandom().choice(pool)
    w.replaced_reason = reason
    d.winners.append(DrawWinner(place=w.place, registration_id=r.id, entry_number=r.entry.number))
    db.commit()
    return draw_view(d)


@app.get("/api/admin/draw/entries.txt", dependencies=[Depends(require_admin)])
def draw_entries(db: Session = Depends(get_db)):
    """Список участников на момент розыгрыша. SHA-256 этого файла совпадает с хешем розыгрыша."""
    d = current_draw(db)
    if not d:
        raise HTTPException(404, "Розыгрыш ещё не проведён")
    return Response(d.entries_snapshot, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="draw-entries.txt"'})


@app.delete("/api/admin/draw", dependencies=[Depends(require_admin)])
def reset_draw(db: Session = Depends(get_db)):
    if not DRAW_RESET_ALLOWED:
        raise HTTPException(403, "Сброс розыгрыша отключён")
    d = current_draw(db)
    if d:
        db.delete(d)
        db.commit()
    return {"ok": True}


# Локально тот же сервер отдаёт три сайта: клиент на /, касса на /kassa/, админка на /admin/.
# В проде это отдельные статические сайты на своих адресах (см. build.sh и render.yaml).
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if SERVE_WEB and WEB_DIR.is_dir():
    shared = StaticFiles(directory=WEB_DIR / "shared")
    for prefix in ("/kassa", "/admin"):
        app.mount(f"{prefix}/shared", shared)
        app.mount(prefix, StaticFiles(directory=WEB_DIR / prefix.strip("/"), html=True))
    app.mount("/shared", shared)
    app.mount("/", StaticFiles(directory=WEB_DIR / "client", html=True))
