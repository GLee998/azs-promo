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
import re
import secrets
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, LargeBinary, String, Text,
                        create_engine, delete, func, or_, select, update)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, contains_eager, mapped_column, relationship,
                            selectinload)


# ---------- настройки ----------

def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./promo.db")
    # Render отдаёт postgres:// или postgresql:// — указываем драйвер psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# Сессия сотрудника живёт одну смену, потом нужно войти снова.
SESSION_TTL = timedelta(hours=int(os.getenv("SESSION_TTL_HOURS", "14")))
NO_ACCESS = "Нет доступа для этой учётной записи"
# Подтверждение номера кодом выключено по решению заказчика. Механизм оставлен, чтобы его можно
# было включить (SMS или WhatsApp), если начнут регистрировать чужие номера.
SMS_REQUIRED = os.getenv("SMS_REQUIRED", "false").lower() == "true"
# В демо SMS не отправляются: код возвращается в ответе и показывается на экране.
DEMO_SMS = os.getenv("DEMO_SMS", "true").lower() == "true"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
MAX_PHOTO_BYTES = 10 * 1024 * 1024
# Самые большие камеры телефонов дают около 50 Мпикс. Браузер клиента и так уменьшает фото до 1800 px,
# больше присылают только скриптом. Сверх 2 × лимита Pillow сам бросает DecompressionBombError.
MAX_PHOTO_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_PHOTO_PIXELS
# Предупреждение Pillow о слишком большой картинке считаем ошибкой: такие фото всё равно отклоняются.
warnings.simplefilter("error", Image.DecompressionBombWarning)
PHOTO_SIDE = 1800
THUMB_SIDE = 480
PHOTO_TOO_BIG = "Слишком большое изображение. Сфотографируйте чек ещё раз."
SMS_TTL = timedelta(minutes=10)
SMS_RESEND = timedelta(seconds=30)
SMS_MAX_ATTEMPTS = 5
# Заявка, по которой машинку так и не выдали, перестаёт держать номер и чек.
WAITING_TTL = timedelta(hours=int(os.getenv("WAITING_TTL_HOURS", "3")))
EXPIRED_REASON = "Игрушку не забрали вовремя"
# Лимиты частоты. С одного IP мобильного оператора выходят многие клиенты (CGNAT), поэтому лимит
# по IP мягкий, а строгий — по номеру телефона и на вход сотрудников.
RL_REG_IP = int(os.getenv("RL_REG_IP", "60"))
REG_IP_WINDOW = timedelta(minutes=10)
RL_REG_PHONE = int(os.getenv("RL_REG_PHONE", "5"))
REG_PHONE_WINDOW = timedelta(hours=1)
RL_LOGIN_IP = int(os.getenv("RL_LOGIN_IP", "10"))
LOGIN_WINDOW = timedelta(minutes=15)
TOO_MANY_ATTEMPTS = "Слишком много попыток. Попробуйте через несколько минут."
DRAW_PRIZES = int(os.getenv("DRAW_PRIZES", "8"))  # 8 поездок на новогоднем караване
# Сброс розыгрыша нужен только на тестовом стенде, в проде он выключен.
DRAW_RESET_ALLOWED = os.getenv("DRAW_RESET_ALLOWED", "false").lower() == "true"
# Локально сервер отдаёт и три сайта (клиент, касса, админка). В проде они живут на отдельных адресах.
SERVE_WEB = os.getenv("SERVE_WEB", "true").lower() == "true"

DB_URL = _db_url()
# Пул соединений настраиваем только для Postgres: у SQLite свой пул, который этих параметров не принимает.
# pool_timeout короткий: при перегрузке клиент быстрее получит 503, чем будет ждать полминуты.
_POOL = (dict(pool_size=int(os.getenv("DB_POOL_SIZE", "10")), max_overflow=5, pool_timeout=10)
         if DB_URL.startswith("postgresql") else {})
engine = create_engine(DB_URL, pool_pre_ping=True, **_POOL)


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
    __table_args__ = (
        # Очередь кассы и реестр выбирают по статусу, АЗС и дате, сверка с кассой ищет по реквизитам чека.
        Index("ix_registrations_status_created", "status", "created_at"),
        Index("ix_registrations_issued_at", "issued_at"),
        Index("ix_registrations_fiscal", "fn", "fd", "fm"),
        Index("ix_registrations_station_status", "station", "status"),
    )

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
    thumb: Mapped[bytes | None] = mapped_column(LargeBinary)  # превью для списков кассы и реестра
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


class RateHit(Base):
    """Счётчик попыток в окне времени. Хранится в базе, чтобы лимит был общим для всех воркеров API."""
    __tablename__ = "rate_hits"

    bucket: Mapped[str] = mapped_column(String(32), primary_key=True)  # что ограничиваем: reg_ip, reg_phone, login
    key: Mapped[str] = mapped_column(String(64), primary_key=True)     # IP или номер телефона
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    hits: Mapped[int] = mapped_column(default=0)


class StaffUser(Base):
    """Сотрудник: кассир конкретной АЗС (role=cashier) или маркетинг (role=admin)."""
    __tablename__ = "staff_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(8))
    station: Mapped[str | None] = mapped_column(String(32))  # АЗС кассира; выдача отмечается на неё
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class StaffSession(Base):
    """Вход сотрудника. Хранится только хеш токена: по копии базы войти нельзя."""
    __tablename__ = "staff_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("staff_users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Журнал действий сотрудников: кто выдал игрушку, отменил или снял заявку, провёл розыгрыш."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_id: Mapped[int] = mapped_column(ForeignKey("staff_users.id"))
    action: Mapped[str] = mapped_column(String(32))
    registration_id: Mapped[int | None] = mapped_column()
    details: Mapped[str | None] = mapped_column(Text)


Base.metadata.create_all(engine)


def get_db():
    # expire_on_commit=False: после commit ответ собирается из уже загруженных полей, без лишнего запроса.
    with Session(engine, expire_on_commit=False) as db:
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
    # 9 цифр без «+» — местный номер без кода страны. С «+» это неполный номер «+996 XXX XXX»,
    # приписав код, получили бы другой, но правдоподобный номер.
    if len(d) == 9 and not raw.strip().startswith("+"):
        d = "996" + d
    return "+" + d if len(d) == 12 and d.startswith("996") else None


def mask_phone(phone: str) -> str:
    # +996 555 *** 456 — кассиру хватает, чтобы сверить с телефоном клиента.
    return f"{phone[:4]} {phone[4:7]} *** {phone[-3:]}"


def clean_station(v: str | None) -> str | None:
    v = (v or "").strip()[:32]
    return v or None


def _jpeg(img: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def process_photo(raw: bytes) -> tuple[bytes, bytes]:
    """Проверяем, что это изображение разумного размера, поворачиваем по EXIF, убираем метаданные, сжимаем.

    Возвращаем фото для просмотра (до 1800 px) и превью для списков (до 480 px).
    """
    try:
        img = Image.open(io.BytesIO(raw))
        # Размер известен из заголовка файла: огромную картинку отклоняем до декодирования,
        # иначе одна такая загрузка занимает сотни мегабайт памяти.
        if img.width * img.height > MAX_PHOTO_PIXELS:
            raise HTTPException(400, PHOTO_TOO_BIG)
        img.draft("RGB", (PHOTO_SIDE, PHOTO_SIDE))  # JPEG декодируется сразу в уменьшенном масштабе
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, PHOTO_TOO_BIG)
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "Файл не похож на фото. Загрузите изображение чека.")
    img.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
    full = _jpeg(img, 82)
    img.thumbnail((THUMB_SIDE, THUMB_SIDE))
    return full, _jpeg(img, 70)


def new_code(db: Session) -> str:
    while True:
        # Как на кыргызском номерном знаке: 3 цифры и 3 латинские буквы, например «123 ABC».
        code = f"{random.randint(0, 999):03d} " + "".join(random.choices("ABCEHKMOPTXY", k=3))
        if not db.scalar(select(Registration.id).where(Registration.code == code)):
            return code


def client_ip(request: Request) -> str:
    # За Caddy uvicorn запущен с --proxy-headers, и здесь адрес клиента, а не прокси.
    return request.client.host if request.client else "unknown"


def rate_limit(db: Session, bucket: str, key: str, limit: int, window: timedelta) -> None:
    """Считаем попытку в текущем окне и отвечаем 429, если их больше limit.

    Счётчик увеличивается одним атомарным запросом (INSERT … ON CONFLICT DO UPDATE) и сразу
    сохраняется: попытка засчитывается, даже если запрос дальше завершится ошибкой.
    """
    seconds = window.total_seconds()
    ts = now().timestamp()
    start = datetime.fromtimestamp(ts - ts % seconds, timezone.utc)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = insert(RateHit).values(bucket=bucket, key=key[:64], window_start=start, hits=1)
    stmt = stmt.on_conflict_do_update(index_elements=["bucket", "key", "window_start"],
                                      set_={"hits": RateHit.hits + 1}).returning(RateHit.hits)
    hits = db.execute(stmt).scalar_one()
    db.commit()
    if hits > limit:
        raise HTTPException(429, TOO_MANY_ATTEMPTS)


def purge_rate_hits(db: Session) -> int:
    """Удаляем счётчики старше суток: самое длинное окно — час."""
    return db.execute(delete(RateHit).where(RateHit.window_start < now() - timedelta(days=1))).rowcount


def login_rate_limit(request: Request, db: Session = Depends(get_db)) -> None:
    rate_limit(db, "login", client_ip(request), RL_LOGIN_IP, LOGIN_WINDOW)


def bearer(authorization: str) -> str:
    return authorization.removeprefix("Bearer ").strip()


def hash_password(pw: str) -> str:
    """Пароль сотрудника для базы: scrypt со случайной солью, в виде scrypt$<соль>$<хеш>."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except ValueError:
        return False
    return scheme == "scrypt" and secrets.compare_digest(calc.hex(), digest)


# Для несуществующего логина сверяем пароль с этим хешем: ответ занимает столько же времени,
# и по нему не понять, есть ли такой сотрудник.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: Session, user: StaffUser) -> tuple[str, datetime]:
    """Новая сессия сотрудника. Токен отдаётся браузеру один раз, в базе остаётся его хеш."""
    token = secrets.token_urlsafe(32)
    expires = now() + SESSION_TTL
    db.add(StaffSession(token_hash=token_hash(token), user_id=user.id, expires_at=expires))
    return token, expires


def audit(db: Session, user: StaffUser, action: str, registration_id: int | None = None,
          details: str | None = None) -> None:
    """Запись в журнал действий. Сохраняется вместе с самим действием, тем же commit."""
    db.add(AuditLog(user_id=user.id, action=action, registration_id=registration_id, details=details))


def current_staff(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> StaffUser:
    token = bearer(authorization)
    s = db.get(StaffSession, token_hash(token)) if token else None
    user = db.get(StaffUser, s.user_id) if s and as_utc(s.expires_at) > now() else None
    if not user or not user.active:
        raise HTTPException(401, "Войдите заново")
    return user


def require_cashier(user: StaffUser = Depends(current_staff)) -> StaffUser:
    # Маркетинг тоже может работать в кассовом интерфейсе своей учёткой.
    if user.role not in ("cashier", "admin"):
        raise HTTPException(403, NO_ACCESS)
    return user


def require_admin(user: StaffUser = Depends(current_staff)) -> StaffUser:
    if user.role != "admin":
        raise HTTPException(403, NO_ACCESS)
    return user


# ---------- приложение ----------

app = FastAPI(title="Karavan promo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(PoolTimeout)
async def db_overloaded(request, exc):
    # Все соединения с базой заняты дольше pool_timeout: просим повторить, а не отдаём 500.
    return JSONResponse({"detail": "Сервер перегружен. Попробуйте ещё раз через минуту."}, status_code=503)


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


def release_all_expired(db: Session) -> int:
    """Одним запросом освобождаем номер и чек у всех заявок, по которым игрушку не выдали за WAITING_TTL.

    Возвращает число освобождённых заявок. Объекты, уже загруженные в сессию, не обновляются,
    поэтому вызывать до чтения списков.
    """
    result = db.execute(
        update(Registration)
        .where(Registration.status == "waiting", Registration.created_at < now() - WAITING_TTL)
        .values(status="cancelled", status_reason=EXPIRED_REASON, phone_lock=None, receipt_lock=None)
        .execution_options(synchronize_session=False))
    return result.rowcount


def release_expired(db: Session, phone: str, fiscal_key: str | None) -> None:
    """Освобождаем номер и чек, если их держит заявка, по которой машинку так и не выдали."""
    conds = [Registration.phone_lock == phone]
    if fiscal_key:
        conds.append(Registration.receipt_lock == fiscal_key)
    for r in db.scalars(select(Registration).where(Registration.status == "waiting")
                        .where(conds[0] if len(conds) == 1 else (conds[0] | conds[1]))):
        if r.expired():
            r.release("cancelled", EXPIRED_REASON)
    db.flush()


def conflict_message(db: Session, phone: str, fiscal_key: str | None) -> str:
    taken = db.scalar(select(Registration).where(Registration.phone_lock == phone))
    if taken:
        return phone_taken_message(taken)
    if fiscal_key and db.scalar(select(Registration.id).where(Registration.receipt_lock == fiscal_key)):
        return "Этот чек уже зарегистрирован. Один чек можно зарегистрировать только один раз."
    return "Не удалось зарегистрировать чек. Попробуйте ещё раз."


# Обработчики с базой и Pillow объявлены через def, а не async def: FastAPI выполняет их в пуле потоков.
# В async def этот синхронный код блокировал бы event loop, и при исчерпании пула соединений
# сервер зависал целиком (B1 в аудите).
@app.post("/api/registrations")
def create_registration(
    request: Request,
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
    # Лимиты проверяем до обработки фото: она самая дорогая часть запроса.
    rate_limit(db, "reg_ip", client_ip(request), RL_REG_IP, REG_IP_WINDOW)
    phone_n = normalize_phone(phone)
    if not phone_n:
        raise HTTPException(400, "Введите номер в формате +996 XXX XXX XXX")
    rate_limit(db, "reg_phone", phone_n, RL_REG_PHONE, REG_PHONE_WINDOW)
    if not consent:
        raise HTTPException(400, "Нужно согласие с правилами акции и обработкой данных")

    raw = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Фото слишком большое (больше 10 МБ)")
    photo_full, photo_thumb = process_photo(raw)

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
    r.photo = RegistrationPhoto(data=photo_full, thumb=photo_thumb)

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
        r.release("cancelled", EXPIRED_REASON)
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


class LoginBody(BaseModel):
    login: str
    password: str


@app.post("/api/auth/login", dependencies=[Depends(login_rate_limit)])
def auth_login(body: LoginBody, db: Session = Depends(get_db)):
    """Вход кассира или маркетинга по личному логину. Возвращает токен сессии, роль и АЗС."""
    user = db.scalar(select(StaffUser).where(StaffUser.login == body.login.strip().lower()))
    ok = check_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not user.active or not ok:
        raise HTTPException(401, "Неверный логин или пароль")
    token, expires = start_session(db, user)
    audit(db, user, "login")
    db.commit()
    return {"token": token, "role": user.role, "station": user.station, "expires_at": expires.isoformat()}


@app.post("/api/auth/logout")
def auth_logout(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    token = bearer(authorization)
    s = db.get(StaffSession, token_hash(token)) if token else None
    if s:
        db.delete(s)
        db.commit()
    return {"ok": True}


def code_matches(text: str):
    """Код заявки без учёта пробела и регистра: кассир может набрать «123abc» или часть кода."""
    return func.replace(Registration.code, " ", "").contains(text.upper(), autoescape=True)


@app.get("/api/cashier/queue")
def cashier_queue(station: str = "", q: str = "", user: StaffUser = Depends(require_cashier),
                  db: Session = Depends(get_db)):
    """Заявки, ожидающие выдачи, и последние выданные, свежие сверху.

    Кассир видит заявки своей АЗС и заявки без АЗС (QR без номера кассы). Заявку другой АЗС
    находит поиском по коду с экрана клиента.
    """
    if release_all_expired(db):
        db.commit()
    station = user.station or clean_station(station)  # у кассира АЗС из учётки, маркетинг выбирает сам
    code = "".join(q.split())
    waiting = (select(Registration).options(selectinload(Registration.entry))
               .where(Registration.status == "waiting").order_by(Registration.created_at.desc()))
    if len(code) >= 3:
        waiting = waiting.where(code_matches(code)).limit(20)
    elif station:
        waiting = waiting.where(or_(Registration.station == station, Registration.station.is_(None))).limit(50)
    else:
        waiting = waiting.limit(50)
    recent = (select(Registration).options(selectinload(Registration.entry))
              .where(Registration.status == "issued").order_by(Registration.issued_at.desc()).limit(10))
    if station:
        recent = recent.where(Registration.issued_station == station)
    return {"waiting": [cashier_view(r) for r in db.scalars(waiting)],
            "recent": [cashier_view(r) for r in db.scalars(recent)]}


PhotoSize = Literal["full", "thumb"]


def photo_response(db: Session, reg_id: int, size: PhotoSize) -> Response:
    # Читаем только нужную колонку: списку не нужно тянуть из базы полные фото.
    column = RegistrationPhoto.thumb if size == "thumb" else RegistrationPhoto.data
    row = db.execute(select(column).where(RegistrationPhoto.registration_id == reg_id)).first()
    if row is None:
        raise HTTPException(404, "Фото не найдено")
    data = row[0]
    if data is None:  # у заявок, созданных до появления превью, есть только полное фото
        data = db.scalar(select(RegistrationPhoto.data).where(RegistrationPhoto.registration_id == reg_id))
    # Фото чека после загрузки не меняется, браузер может держать его в кэше сутки.
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/cashier/registrations/{reg_id}/photo", dependencies=[Depends(require_cashier)])
def cashier_photo(reg_id: int, size: PhotoSize = "full", db: Session = Depends(get_db)):
    return photo_response(db, reg_id, size)


class IssueBody(BaseModel):
    station: str = ""
    fn: str | None = None
    fd: str | None = None
    fm: str | None = None


NOT_WAITING = "Заявка уже не ждёт выдачи. Обновите список."


@app.post("/api/cashier/registrations/{reg_id}/issue")
def cashier_issue(reg_id: int, body: IssueBody, user: StaffUser = Depends(require_cashier),
                  db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "waiting" or r.expired():
        raise HTTPException(400, NOT_WAITING)
    if not r.fiscal_key:
        r.set_fiscal(body.fn, body.fd, body.fm, "cashier")
        if not r.fiscal_key:
            raise HTTPException(400, "Введите ФН, ФД и ФМ с бумажного чека")
    station = user.station or clean_station(body.station) or r.station
    try:
        # Статус меняем условным UPDATE: если два кассира нажали «выдано» одновременно,
        # заявку получит только первый, второй увидит 0 изменённых строк.
        claimed = db.execute(
            update(Registration).where(Registration.id == r.id, Registration.status == "waiting")
            .values(status="issued", issued_at=now(), issued_station=station)
            .execution_options(synchronize_session=False)).rowcount
        if not claimed:
            db.rollback()
            raise HTTPException(400, NOT_WAITING)
        r.status, r.issued_at, r.issued_station = "issued", now(), station
        r.entry = Entry()
        audit(db, user, "issue", r.id, f"АЗС {station}" if station else None)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Этот чек уже зарегистрирован другой заявкой. Машинку не выдавайте.")
    return cashier_view(r)


class ReasonBody(BaseModel):
    reason: str = ""


@app.post("/api/cashier/registrations/{reg_id}/cancel")
def cashier_cancel(reg_id: int, body: ReasonBody, user: StaffUser = Depends(require_cashier),
                   db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "waiting":
        raise HTTPException(400, "Отменить можно только заявку, которая ждёт выдачи")
    r.release("cancelled", body.reason.strip() or "Отменена на кассе")
    audit(db, user, "cancel", r.id, r.status_reason)
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


STATUSES = ("waiting", "issued", "cancelled", "disqualified")
RegistryFilter = Literal["", "waiting", "issued", "unchecked", "cancelled", "disqualified"]


def registry_stats(db: Session) -> dict:
    """Плитки реестра: число заявок по статусам по всей базе, а не по текущей странице."""
    counts = dict(db.execute(select(Registration.status, func.count()).group_by(Registration.status)).all())
    unchecked = db.scalar(select(func.count()).select_from(Registration).where(
        Registration.status == "issued", Registration.check_purchase.is_(False)))
    return {"total": sum(counts.values()), **{s: counts.get(s, 0) for s in STATUSES}, "unchecked": unchecked}


def registry_filter(status: str, q: str) -> list:
    """Условия отбора реестра: статус и поиск по коду, номеру участника, телефону или реквизитам чека."""
    cond = []
    if status == "unchecked":
        cond += [Registration.status == "issued", Registration.check_purchase.is_(False)]
    elif status:
        cond.append(Registration.status == status)
    text = "".join(q.split())
    entry = re.fullmatch(r"KN-?(\d+)", text, re.IGNORECASE)
    if entry:
        cond.append(Registration.entry.has(Entry.id == int(entry.group(1))))
    elif text:
        alts = [code_matches(text)]
        # Цифры ищем по телефону и реквизитам, только если запрос целиком из цифр: иначе «123 ABC»
        # находил бы все телефоны с «123».
        if text.lstrip("+").isdigit():
            digits = text.lstrip("+")
            alts += [Registration.phone.contains(digits, autoescape=True),
                     Registration.fn == digits, Registration.fd == digits, Registration.fm == digits]
        cond.append(or_(*alts))
    return cond


@app.get("/api/admin/registrations", dependencies=[Depends(require_admin)])
def list_registrations(status: RegistryFilter = "", q: str = "",
                       page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
                       db: Session = Depends(get_db)):
    """Реестр постранично: фильтр и поиск выполняет база, браузер получает одну страницу."""
    if release_all_expired(db):
        db.commit()
    cond = registry_filter(status, q)
    total = db.scalar(select(func.count()).select_from(Registration).where(*cond))
    regs = db.scalars(select(Registration).options(selectinload(Registration.entry)).where(*cond)
                      .order_by(Registration.created_at.desc(), Registration.id.desc())
                      .offset((page - 1) * per_page).limit(per_page))
    return {"items": [admin_view(r) for r in regs], "total": total, "page": page, "per_page": per_page,
            "stats": registry_stats(db)}


@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats(db: Session = Depends(get_db)):
    if release_all_expired(db):
        db.commit()
    return registry_stats(db)


@app.get("/api/admin/registrations/{reg_id}/photo", dependencies=[Depends(require_admin)])
def admin_photo(reg_id: int, size: PhotoSize = "full", db: Session = Depends(get_db)):
    return photo_response(db, reg_id, size)


class AdminPatch(BaseModel):
    check_purchase: bool | None = None
    returned: bool | None = None


@app.patch("/api/admin/registrations/{reg_id}")
def patch_registration(reg_id: int, body: AdminPatch, user: StaffUser = Depends(require_admin),
                       db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    data = body.model_dump(exclude_unset=True)
    audit(db, user, "patch", r.id, ", ".join(f"{k}={v}" for k, v in data.items()))
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


@app.post("/api/admin/registrations/{reg_id}/disqualify")
def admin_disqualify(reg_id: int, body: ReasonBody, user: StaffUser = Depends(require_admin),
                     db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "issued":
        raise HTTPException(400, "Снять с розыгрыша можно только заявку с выданной машинкой")
    disqualify(r, body.reason.strip() or "Снята маркетингом")
    audit(db, user, "disqualify", r.id, r.status_reason)
    db.commit()
    return admin_view(r)


@app.post("/api/admin/registrations/{reg_id}/restore")
def admin_restore(reg_id: int, user: StaffUser = Depends(require_admin), db: Session = Depends(get_db)):
    r = get_reg(db, reg_id)
    if r.status != "disqualified":
        raise HTTPException(400, "Вернуть в розыгрыш можно только снятую заявку")
    if r.returned:
        raise HTTPException(400, "По чеку был возврат. Сначала снимите отметку о возврате.")
    r.status = "issued"
    r.status_reason = None
    r.entry = Entry()
    audit(db, user, "restore", r.id)
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


@app.post("/api/admin/import/{kind}")
def import_csv(kind: str, file: UploadFile = File(...), user: StaffUser = Depends(require_admin),
               db: Session = Depends(get_db)):
    """Сверка с выгрузками кассы. CSV с колонками fn;fd;fm (колонка fp тоже подходит вместо fm)."""
    if kind not in ("sales", "returns"):
        raise HTTPException(404, "Неизвестный тип выгрузки")
    rows = read_csv_rows(file.file.read())
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
    audit(db, user, "import", details=f"{kind}: строк {len(rows)}, совпало {matched}")
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
    for r in db.scalars(select(Registration).options(selectinload(Registration.entry))
                        .order_by(Registration.created_at)):
        w.writerow([r.code, as_utc(r.created_at).isoformat(), r.station, r.phone, yes(r.phone_verified),
                    r.fn, r.fd, r.fm, r.receipt_sum, STATUS_RU[r.status], r.status_reason,
                    as_utc(r.issued_at).isoformat() if r.issued_at else "", r.issued_station,
                    yes(r.check_purchase), yes(r.returned), r.entry.number if r.entry else ""])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="registry.csv"'})


@app.get("/api/admin/audit.csv", dependencies=[Depends(require_admin)])
def audit_csv(db: Session = Depends(get_db)):
    """Журнал действий сотрудников для проверки спорных выдач и розыгрыша."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Время", "Сотрудник", "Действие", "Заявка", "Подробности"])
    rows = db.execute(select(AuditLog, StaffUser.login, Registration.code)
                      .join(StaffUser, AuditLog.user_id == StaffUser.id)
                      .outerjoin(Registration, AuditLog.registration_id == Registration.id)
                      .order_by(AuditLog.id))
    for log, login, code in rows:
        w.writerow([as_utc(log.at).isoformat(), login, log.action, code or "", log.details or ""])
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="audit.csv"'})


# ---------- розыгрыш ----------

def eligible(db: Session, only_checked: bool) -> list[Registration]:
    """Участники: машинка выдана, возврата не было; при only_checked — покупка сверена с продажами."""
    # join с Entry вместо проверки r.entry в Python: один запрос на всех участников.
    q = (select(Registration).join(Registration.entry).options(contains_eager(Registration.entry))
         .where(Registration.status == "issued", Registration.returned.is_(False)))
    if only_checked:
        q = q.where(Registration.check_purchase.is_(True))
    return list(db.scalars(q))


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


@app.post("/api/admin/draw")
def run_draw(body: DrawBody, user: StaffUser = Depends(require_admin), db: Session = Depends(get_db)):
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
    audit(db, user, "draw", details=f"участников {len(pool)}, хеш {d.entries_hash}")
    db.commit()
    return draw_view(d)


@app.post("/api/admin/draw/winners/{winner_id}/redraw")
def redraw_winner(winner_id: int, body: ReasonBody, user: StaffUser = Depends(require_admin),
                  db: Session = Depends(get_db)):
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
    audit(db, user, "redraw", w.registration_id, f"место {w.place}: {reason}")
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


@app.delete("/api/admin/draw")
def reset_draw(user: StaffUser = Depends(require_admin), db: Session = Depends(get_db)):
    if not DRAW_RESET_ALLOWED:
        raise HTTPException(403, "Сброс розыгрыша отключён")
    d = current_draw(db)
    if d:
        db.delete(d)
        audit(db, user, "draw_reset")
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
