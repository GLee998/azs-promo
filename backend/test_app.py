"""Сквозной сценарий: регистрация → блокировки по номеру и чеку → выдача на кассе → розыгрыш.

Запуск: cd backend && ../.venv/bin/python test_app.py
"""

import io
import os
import threading
from pathlib import Path
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["SMS_REQUIRED"] = "false"
# Тесты шлют сотни запросов с одного адреса: лимиты частоты поднимаем, тесты лимитов задают свои.
for _limit in ("RL_REG_IP", "RL_REG_PHONE", "RL_LOGIN_IP"):
    os.environ[_limit] = "100000"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app as backend  # noqa: E402

# Для in-memory SQLite все соединения должны смотреть в одну базу.
backend.engine = backend.create_engine("sqlite://", poolclass=StaticPool,
                                       connect_args={"check_same_thread": False})
backend.Base.metadata.create_all(backend.engine)

client = TestClient(backend.app)
# Заголовки входа администратора и кассира (без АЗС). Их заполняет fresh_db() для каждой новой базы.
ADMIN: dict = {}
KASSA: dict = {}
RECEIPT = {"fn": "0000440700000001", "fd": "12345", "fm": "1234567890"}


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(buf, "JPEG")
    return buf.getvalue()


def register(phone: str, station: str = "1", **fiscal):
    return client.post("/api/registrations",
                       data={"phone": phone, "consent": "true", "station": station, **fiscal},
                       files={"photo": ("r.jpg", jpeg(), "image/jpeg")})


def queue() -> dict:
    return {r["code"]: r for r in client.get("/api/cashier/queue", headers=KASSA).json()["waiting"]}


def queue_by_code(code: str, headers: dict | None = None) -> dict:
    """Заявка из очереди кассы по коду, как её ищет кассир."""
    r = client.get("/api/cashier/queue", params={"q": code.replace(" ", "")}, headers=headers or KASSA)
    return r.json()["waiting"][0]


def add_user(login_: str, password: str, role: str, station: str | None = None) -> None:
    with backend.Session(backend.engine) as db:
        db.add(backend.StaffUser(login=login_, password_hash=backend.hash_password(password),
                                 role=role, station=station, active=True))
        db.commit()


def login(login_: str, password: str) -> dict:
    """Вход через API, как в браузере. Возвращает заголовок авторизации."""
    r = client.post("/api/auth/login", json={"login": login_, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def staff_headers(login_: str) -> dict:
    """Сессия без HTTP-входа: не расходует лимит попыток входа в тестах лимитов."""
    with backend.Session(backend.engine) as db:
        user = db.scalar(backend.select(backend.StaffUser).where(backend.StaffUser.login == login_))
        token, _ = backend.start_session(db, user)
        db.commit()
    return {"Authorization": "Bearer " + token}


def fresh_db(file: bool = False) -> None:
    """Новая пустая база: тест не зависит от данных предыдущих тестов.

    file=True — файловый SQLite, у каждого запроса своё соединение, как в Postgres.
    Создаёт администратора admin и кассира kassa (без АЗС) и обновляет заголовки ADMIN и KASSA.
    """
    if file:
        import tempfile
        backend.engine = backend.create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'test.db'}",
                                               connect_args={"timeout": 15})
    else:
        backend.engine = backend.create_engine("sqlite://", poolclass=StaticPool,
                                               connect_args={"check_same_thread": False})
    backend.Base.metadata.create_all(backend.engine)
    add_user("admin", "admin-pw", role="admin")
    add_user("kassa", "kassa-pw", role="cashier")
    ADMIN.clear(); ADMIN.update(staff_headers("admin"))
    KASSA.clear(); KASSA.update(staff_headers("kassa"))


def fresh_db_file() -> None:
    fresh_db(file=True)


def test_flow():
    fresh_db()
    # Не фото, нет согласия, неверный номер
    r = client.post("/api/registrations", data={"phone": "+996555334455", "consent": "true"},
                    files={"photo": ("r.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400
    r = client.post("/api/registrations", data={"phone": "+996555334455", "consent": "false"},
                    files={"photo": ("r.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 400
    assert register("+7 999 123 45 67").status_code == 400

    # Регистрация с распознанным QR
    first = register("+996 700 111 223", station="7", **RECEIPT)
    assert first.status_code == 200, first.text
    token = first.json()["token"]
    assert client.get(f"/api/registrations/status/{token}").json()["status"] == "waiting"

    # Тот же чек с другого номера — блок
    r = register("0555 556 677", station="3", **RECEIPT)
    assert r.status_code == 409 and "чек уже зарегистрирован" in r.json()["detail"]

    # Тот же номер с другим чеком — блок
    r = register("+996700111223", fn="1", fd="2", fm="3")
    assert r.status_code == 409 and "ждёт выдачи" in r.json()["detail"]

    # Касса без пароля не открывается
    assert client.get("/api/cashier/queue").status_code == 401

    # Кассир выдаёт машинку → номер участника
    item = queue()[first.json()["code"]]
    assert item["phone"] == "+996 700 *** 223" and not item["needs_fiscal"]
    r = client.post(f"/api/cashier/registrations/{item['id']}/issue", headers=KASSA, json={"station": "7"})
    assert r.status_code == 200, r.text
    st = client.get(f"/api/registrations/status/{token}").json()
    assert st["status"] == "issued" and st["entry"].startswith("KN-")

    # Повторная покупка тем же номером на другой АЗС — машинки нет
    r = register("+996700111223", station="2", fn="9", fd="9", fm="9")
    assert r.status_code == 409 and "уже получил игрушечный караван" in r.json()["detail"]

    # QR не распознан: реквизиты вводит кассир, повторный чек при этом тоже ловится
    manual = register("772000112")
    assert manual.status_code == 200
    item = queue()[manual.json()["code"]]
    assert item["needs_fiscal"]
    r = client.post(f"/api/cashier/registrations/{item['id']}/issue", headers=KASSA, json={"station": "1"})
    assert r.status_code == 400
    r = client.post(f"/api/cashier/registrations/{item['id']}/issue", headers=KASSA,
                    json={"station": "1", **RECEIPT})
    assert r.status_code == 409  # этот чек уже выдан по первой заявке
    r = client.post(f"/api/cashier/registrations/{item['id']}/issue", headers=KASSA,
                    json={"station": "1", "fn": "0000 4407 0000 0002", "fd": "77", "fm": "555"})
    assert r.status_code == 200, r.text

    # Отмена на кассе освобождает номер
    third = register("+996 500 000 001", fn="5", fd="5", fm="5")
    item = queue()[third.json()["code"]]
    client.post(f"/api/cashier/registrations/{item['id']}/cancel", headers=KASSA, json={"reason": "не то комбо"})
    assert register("+996 500 000 001", fn="5", fd="5", fm="5").status_code == 200

    # Заявка, по которой не забрали машинку, истекает и не держит номер
    stale = register("+996 500 000 002", fn="6", fd="6", fm="6")
    with backend.Session(backend.engine) as db:
        reg = db.scalar(backend.select(backend.Registration).where(backend.Registration.code == stale.json()["code"]))
        reg.created_at = backend.now() - backend.WAITING_TTL - timedelta(minutes=1)
        db.commit()
    assert register("+996 500 000 002", fn="6", fd="6", fm="6").status_code == 200

    # Реестр: сверка с продажами и возврат снимают заявку с розыгрыша, но номер остаётся занятым
    sales = f"fn;fd;fm\n{RECEIPT['fn']};{RECEIPT['fd']};{RECEIPT['fm']}\n".encode()
    assert client.post("/api/admin/import/sales", headers=ADMIN,
                       files={"file": ("s.csv", sales)}).json()["matched"] == 1
    client.post("/api/admin/import/returns", headers=ADMIN, files={"file": ("r.csv", sales)})
    items = {i["code"]: i for i in client.get("/api/admin/registrations", headers=ADMIN).json()["items"]}
    returned = items[first.json()["code"]]
    assert returned["status"] == "disqualified" and returned["entry"] is None and returned["check_purchase"]
    assert register("+996700111223", fn="8", fd="8", fm="8").status_code == 409

    csv_text = client.get("/api/admin/export.csv", headers=ADMIN).text
    assert "снята с розыгрыша" in csv_text and "Номер участника" in csv_text


def test_draw():
    """8 разных победителей из выданных, повторный розыгрыш запрещён, переигрывание даёт нового победителя."""
    import hashlib
    fresh_db()
    for i in range(12):
        r = register(f"+996 555 00{i:04d}"[:16], fn="100", fd=str(i), fm="7")
        item = queue()[r.json()["code"]]
        client.post(f"/api/cashier/registrations/{item['id']}/issue", headers=KASSA, json={"station": "1"})

    info = client.get("/api/admin/draw?only_checked=false", headers=ADMIN).json()
    assert info["eligible"] == 12 and info["draw"] is None
    # Покупки не сверены — со строгим условием участников нет
    assert client.post("/api/admin/draw", headers=ADMIN, json={"only_checked": True}).status_code == 400

    d = client.post("/api/admin/draw", headers=ADMIN, json={"only_checked": False}).json()
    entries = {w["entry"] for w in d["winners"]}
    assert len(d["winners"]) == 8 and len(entries) == 8
    assert client.post("/api/admin/draw", headers=ADMIN, json={"only_checked": False}).status_code == 400

    snapshot = client.get("/api/admin/draw/entries.txt", headers=ADMIN).text
    assert hashlib.sha256(snapshot.encode()).hexdigest() == d["entries_hash"]

    first = d["winners"][0]
    assert client.post(f"/api/admin/draw/winners/{first['id']}/redraw", headers=ADMIN, json={"reason": ""}).status_code == 400
    d2 = client.post(f"/api/admin/draw/winners/{first['id']}/redraw", headers=ADMIN, json={"reason": "не на связи"}).json()
    new = [w for w in d2["winners"] if w["place"] == first["place"] and not w["replaced_reason"]][0]
    assert new["entry"] not in entries
    assert client.delete("/api/admin/draw", headers=ADMIN).status_code == 403  # в проде сброса нет


def test_parallel_same_receipt():
    """Один чек одновременно с двух АЗС: пройдёт ровно одна регистрация.

    Нужна база с отдельными соединениями на каждый запрос, как в Postgres, поэтому файловый SQLite.
    """
    import tempfile
    db_file = Path(tempfile.mkdtemp()) / "parallel.db"
    backend.engine = backend.create_engine(f"sqlite:///{db_file}", connect_args={"timeout": 15})
    backend.Base.metadata.create_all(backend.engine)
    fiscal = {"fn": "0000999900000001", "fd": "42", "fm": "4242"}
    phones = ["+996 777 000 101", "+996 777 000 102"]
    results = []

    def go(p):
        results.append(register(p, **fiscal).status_code)

    threads = [threading.Thread(target=go, args=(p,)) for p in phones]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200, 409], results


def test_parallel_registrations_small_pool():
    """20 одновременных регистраций при пуле в 2 соединения: все приняты, API не зависает (B1)."""
    import tempfile, time
    db_file = Path(tempfile.mkdtemp()) / "pool.db"
    backend.engine = backend.create_engine(f"sqlite:///{db_file}", pool_size=2, max_overflow=0,
                                           pool_timeout=8, connect_args={"timeout": 15})
    backend.Base.metadata.create_all(backend.engine)
    results = []
    # Общий event loop на все запросы, как у uvicorn: без with у TestClient зависание не воспроизводится.
    with TestClient(backend.app, raise_server_exceptions=False) as c:
        def go(i):
            r = c.post("/api/registrations",
                       data={"phone": f"+996 777 100 {i:03d}", "consent": "true", "fn": "31", "fd": str(i), "fm": "1"},
                       files={"photo": ("r.jpg", jpeg(), "image/jpeg")})
            results.append(r.status_code)
        threads = [threading.Thread(target=go, args=(i,), daemon=True) for i in range(20)]
        [t.start() for t in threads]
        deadline = time.time() + 40
        [t.join(timeout=max(0, deadline - time.time())) for t in threads]
        if any(t.is_alive() for t in threads):
            print("FAIL test_parallel_registrations_small_pool: API завис, запросы не завершились за 40 с", flush=True)
            os._exit(1)  # event loop заблокирован, корректно закрыть клиент нельзя
    assert results == [200] * 20, results


def test_pool_exhausted_returns_503():
    """База не отдаёт соединение: клиент получает понятный 503, а не 500."""
    import tempfile
    backend.engine = backend.create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'p.db'}",
                                           pool_size=1, max_overflow=0, pool_timeout=0.2)
    backend.Base.metadata.create_all(backend.engine)
    held = backend.engine.connect()  # занимаем единственное соединение
    try:
        r = client.get("/api/registrations/status/nope")
        assert r.status_code == 503 and "перегружен" in r.json()["detail"]
    finally:
        held.close()


def test_photo_limits_and_thumb():
    """Огромная картинка отклоняется до декодирования, для списков есть лёгкое превью (B5, B2)."""
    fresh_db()
    big = io.BytesIO(); Image.new("1", (9000, 9000)).save(big, "PNG")  # 81 Мпикс, файл ~10 КБ
    r = client.post("/api/registrations", data={"phone": "+996555000901", "consent": "true"},
                    files={"photo": ("b.png", big.getvalue(), "image/png")})
    assert r.status_code == 400 and "Слишком большое изображение" in r.json()["detail"], (r.status_code, r.text)

    photo = io.BytesIO(); Image.new("RGB", (2400, 1600), "white").save(photo, "JPEG")
    ok = client.post("/api/registrations", data={"phone": "+996555000902", "consent": "true", "fn": "41", "fd": "1", "fm": "1"},
                     files={"photo": ("r.jpg", photo.getvalue(), "image/jpeg")})
    item = queue()[ok.json()["code"]]
    thumb = client.get(f"/api/cashier/registrations/{item['id']}/photo?size=thumb", headers=KASSA)
    full = client.get(f"/api/cashier/registrations/{item['id']}/photo", headers=KASSA)
    assert max(Image.open(io.BytesIO(thumb.content)).size) <= 480
    assert max(Image.open(io.BytesIO(full.content)).size) == 1800


def test_cashier_queue_by_station_and_code():
    """Кассир видит свою АЗС и заявки без АЗС; по коду находит заявку любой АЗС (B3)."""
    fresh_db()
    a = register("+996555000401", station="7", fn="71", fd="1", fm="1").json()["code"]
    b = register("+996555000402", station="3", fn="71", fd="2", fm="1").json()["code"]
    c = register("+996555000403", station="", fn="71", fd="3", fm="1").json()["code"]
    q7 = {r["code"] for r in client.get("/api/cashier/queue?station=7", headers=KASSA).json()["waiting"]}
    assert a in q7 and c in q7 and b not in q7
    found = client.get(f"/api/cashier/queue?station=7&q={b.replace(' ', '').lower()}", headers=KASSA).json()
    assert [r["code"] for r in found["waiting"]] == [b]


def test_admin_registrations_paginated():
    """Реестр отдаётся страницами, фильтр и поиск работают на сервере, статистика по всей базе (B2)."""
    fresh_db()
    codes = [register(f"+996555{i:06d}", fn="81", fd=str(i), fm="1").json()["code"] for i in range(120)]
    for code in codes[:5]:
        client.post(f"/api/cashier/registrations/{queue_by_code(code)['id']}/issue", headers=KASSA, json={"station": "1"})
    r = client.get("/api/admin/registrations?page=2&per_page=50", headers=ADMIN).json()
    assert r["total"] == 120 and len(r["items"]) == 50 and r["page"] == 2
    assert r["stats"]["total"] == 120 and r["stats"]["issued"] == 5 and r["stats"]["waiting"] == 115
    issued = client.get("/api/admin/registrations?status=issued", headers=ADMIN).json()
    assert issued["total"] == 5 and {i["status"] for i in issued["items"]} == {"issued"}
    assert client.get("/api/admin/registrations?q=555000077", headers=ADMIN).json()["total"] == 1
    entry = issued["items"][0]["entry"]
    assert client.get(f"/api/admin/registrations?q={entry}", headers=ADMIN).json()["total"] == 1


def test_expired_released_in_bulk():
    """Просроченные заявки освобождаются одним запросом, номер и чек снова свободны."""
    fresh_db()
    [register(f"+996555000{i}50", fn="91", fd=str(i), fm="1").json()["code"] for i in range(3)]
    with backend.Session(backend.engine) as db:
        for r in db.scalars(backend.select(backend.Registration)):
            r.created_at = backend.now() - backend.WAITING_TTL - timedelta(minutes=1)
        db.commit()
        assert backend.release_all_expired(db) == 3
        db.commit()
    assert register("+996555000050", fn="91", fd="0", fm="1").status_code == 200  # номер и чек освободились


def with_limits(**limits):
    """Временно задаёт лимиты частоты (константы модуля app) на время теста."""
    def wrap(test):
        def run():
            saved = {k: getattr(backend, k) for k in limits}
            for k, v in limits.items():
                setattr(backend, k, v)
            try:
                test()
            finally:
                for k, v in saved.items():
                    setattr(backend, k, v)
        run.__name__ = test.__name__
        return run
    return wrap


@with_limits(RL_REG_IP=60, RL_REG_PHONE=5)
def test_registration_rate_limits():
    """Не больше 5 попыток на номер в час и 60 регистраций с одного IP за 10 минут (B4)."""
    fresh_db()
    codes = [register("+996 555 000 777", fn="51", fd=str(i), fm="1").status_code for i in range(6)]
    assert codes[0] == 200 and codes[1:5] == [409] * 4 and codes[5] == 429, codes  # по телефону
    fresh_db()
    codes = [register(f"+996 555 1{i:05d}", fn="52", fd=str(i), fm="1").status_code for i in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429, codes  # по IP: щадящий лимит (CGNAT)


@with_limits(RL_LOGIN_IP=10)
def test_login_rate_limit():
    """После 10 попыток входа с одного IP за 15 минут вход закрыт даже с верным паролем."""
    fresh_db()
    assert all(client.post("/api/auth/login", json={"login": "admin", "password": "wrong"}).status_code == 401
               for _ in range(10))
    assert client.post("/api/auth/login", json={"login": "admin", "password": "admin-pw"}).status_code == 429


def test_purge_rate_hits():
    """Счётчики старше суток удаляются, свежие остаются."""
    fresh_db()
    with backend.Session(backend.engine) as db:
        db.add_all([backend.RateHit(bucket="reg_ip", key="1.2.3.4", window_start=backend.now() - timedelta(days=2), hits=3),
                    backend.RateHit(bucket="reg_ip", key="1.2.3.4", window_start=backend.now(), hits=1)])
        db.commit()
        assert backend.purge_rate_hits(db) == 1
        db.commit()
        assert db.scalar(backend.select(backend.func.count()).select_from(backend.RateHit)) == 1


def test_staff_login_station_and_audit():
    """Личный вход кассира: АЗС берётся из учётки, а не из формы; действие попадает в журнал (B4)."""
    fresh_db()
    add_user("kassa7", "pw7", role="cashier", station="7"); add_user("boss", "pwb", role="admin")
    assert client.post("/api/auth/login", json={"login": "kassa7", "password": "bad"}).status_code == 401
    t = client.post("/api/auth/login", json={"login": "kassa7", "password": "pw7"}).json()
    assert t["role"] == "cashier" and t["station"] == "7"
    k7 = {"Authorization": "Bearer " + t["token"]}
    boss = login("boss", "pwb")
    code = register("+996555000301", station="", fn="61", fd="1", fm="1").json()["code"]
    reg_id = queue_by_code(code, headers=k7)["id"]
    assert client.post(f"/api/cashier/registrations/{reg_id}/issue", headers=k7, json={"station": "99"}).json()["issued_station"] == "7"
    assert client.get("/api/admin/registrations", headers=k7).status_code == 403
    log = client.get("/api/admin/audit.csv", headers=boss).text
    assert "issue" in log and "kassa7" in log


def test_concurrent_issue_single_winner():
    """Два кассира одновременно отмечают выдачу одной заявки: успех один, номер участника один."""
    fresh_db_file()   # файловый SQLite: у каждого запроса своё соединение
    add_user("k1", "p", role="cashier", station="1")
    k = login("k1", "p")
    code = register("+996555000302", fn="62", fd="1", fm="1").json()["code"]
    reg_id = queue_by_code(code, headers=k)["id"]
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        client.post(f"/api/cashier/registrations/{reg_id}/issue", headers=k, json={}).status_code)) for _ in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(results) == [200, 400], results
    with backend.Session(backend.engine) as db:
        assert db.scalar(backend.select(backend.func.count(backend.Entry.id))) == 1


def test_session_expiry_and_logout():
    """После выхода и по истечении срока токен не действует."""
    fresh_db()
    add_user("k2", "p", role="cashier", station="2")
    k = login("k2", "p")
    assert client.post("/api/auth/logout", headers=k).status_code == 200
    assert client.get("/api/cashier/queue", headers=k).status_code == 401
    k = login("k2", "p")
    with backend.Session(backend.engine) as db:
        for s in db.scalars(backend.select(backend.StaffSession)):
            s.expires_at = backend.now() - timedelta(minutes=1)
        db.commit()
    assert client.get("/api/cashier/queue", headers=k).status_code == 401


def test_manage_cli():
    """manage.py: новый сотрудник входит с выданным паролем, отключённый — нет, purge чистит старые сессии."""
    import contextlib
    import manage
    fresh_db()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        manage.main(["add-user", "Azs12", "--role", "cashier", "--station", "12"])
    password = out.getvalue().strip().split()[-1]
    assert len(password) == 12
    t = client.post("/api/auth/login", json={"login": "azs12", "password": password}).json()
    assert t["role"] == "cashier" and t["station"] == "12"
    with contextlib.redirect_stdout(io.StringIO()):
        manage.main(["disable-user", "azs12"])
    assert client.post("/api/auth/login", json={"login": "azs12", "password": password}).status_code == 401
    with backend.Session(backend.engine) as db:
        for s in db.scalars(backend.select(backend.StaffSession)):
            s.expires_at = backend.now() - timedelta(days=1)
        db.add(backend.SmsCode(phone="+996555000999", code="1234", sent_at=backend.now() - timedelta(days=2)))
        db.commit()
    with contextlib.redirect_stdout(io.StringIO()):
        manage.main(["purge"])
    with backend.Session(backend.engine) as db:
        assert db.scalar(backend.select(backend.func.count()).select_from(backend.StaffSession)) == 0
        assert db.scalar(backend.select(backend.func.count()).select_from(backend.SmsCode)) == 0  # I4


def test_phone_normalization():
    """Неполный номер после «+996» не превращается в другой, правдоподобный номер (F1)."""
    n = backend.normalize_phone
    assert n("+996 700 123 456") == "+996700123456"
    assert n("+996 0700 123 456") == "+996700123456"
    assert n("0700 123 456") == "+996700123456"
    assert n("700123456") == "+996700123456"
    assert n("+996 700 123") is None
    assert n("00996 700 123 456") == "+996700123456"  # международный префикс 00 (I3)


def test_health_checks_db():
    """Health проверяет базу: мониторинг узнает, если API жив, а база недоступна (B7)."""
    backend.engine = backend.create_engine("sqlite:////nonexistent-dir/x.db")
    assert client.get("/api/health").status_code == 503
    fresh_db()
    assert client.get("/api/health").json() == {"ok": True}


def test_large_png_memory_bounded():
    """PNG в 150 КБ с картинкой 50 Мпикс не съедает сотни мегабайт памяти: отказ или экономная обработка (C2)."""
    import subprocess, sys, tempfile
    png = Path(tempfile.mkdtemp()) / "big.png"
    Image.new("RGB", (7071, 7071), "white").save(png)
    code = ("import resource, sys, app\n"
            "raw = open(sys.argv[1], 'rb').read()\n"
            "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "try:\n    app.process_photo(raw)\nexcept app.HTTPException as e:\n    print('rejected', e.status_code)\n"
            "print('delta_kb', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before)\n")
    out = subprocess.run([sys.executable, "-c", code, str(png)], capture_output=True, text=True, check=True,
                         env={**os.environ, "DATABASE_URL": "sqlite://"}, cwd=Path(__file__).parent).stdout
    delta_mb = int(out.split("delta_kb")[1]) / 1024
    assert "rejected 400" in out or delta_mb < 150, out


def test_odd_image_files_return_400():
    """Бомба в текстовом блоке PNG, огромный ICC-профиль и DDS — понятный 400, а не 500 (I1)."""
    import struct
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("comment", "A" * 2_000_000, zip=True)
    ztxt = io.BytesIO(); Image.new("RGB", (10, 10)).save(ztxt, "PNG", pnginfo=info)
    icc = io.BytesIO(); Image.new("RGB", (10, 10)).save(icc, "PNG", icc_profile=b"\0" * 3_000_000)
    dds = (b"DDS " + struct.pack("<7I", 124, 0x1007, 4, 4, 0, 0, 0) + b"\0" * 44
           + struct.pack("<2I4s5I", 32, 0x4, b"ABCD", 0, 0, 0, 0, 0) + b"\0" * 20 + b"\0" * 64)
    fresh_db()
    for i, (name, data) in enumerate([("z.png", ztxt.getvalue()), ("i.png", icc.getvalue()), ("x.dds", dds)]):
        r = client.post("/api/registrations", data={"phone": f"+99655500090{i}", "consent": "true"},
                        files={"photo": (name, data, "application/octet-stream")})
        assert r.status_code == 400, (name, r.status_code, r.text[:120])


@with_limits(RL_LOGIN_IP=10)
def test_successful_logins_not_limited():
    """Кассиры за одним IP входят в начале смены: удачные входы лимит не расходуют (I2)."""
    fresh_db()
    for i in range(12):
        add_user(f"azs{i}", "pw", role="cashier", station=str(i))
    codes = [client.post("/api/auth/login", json={"login": f"azs{i}", "password": "pw"}).status_code for i in range(12)]
    assert codes == [200] * 12, codes


def test_sms_send_disabled_without_sms():
    """Без подтверждения по SMS адрес отправки кода закрыт: через него нельзя перебирать номера (I4)."""
    fresh_db()
    assert client.post("/api/sms/send", json={"phone": "+996555000111"}).status_code == 404


def test_csv_empty_fiscal_row_matches_nothing():
    """Пустая строка в выгрузке кассы не помечает заявки без реквизитов возвратом (I8)."""
    fresh_db()
    register("+996555000501")  # QR не распознан: реквизитов нет
    r = client.post("/api/admin/import/returns", headers=ADMIN,
                    files={"file": ("r.csv", b"fn;fd;fm\n1111;2222;3333\n;;\n")})
    assert r.json()["matched"] == 0, r.json()
    assert not client.get("/api/admin/registrations", headers=ADMIN).json()["items"][0]["returned"]


def test_photo_exif_orientation_kept():
    """Фото, снятое боком (EXIF Orientation=6), поворачивается и после уменьшения до 1800 px."""
    exif = Image.Exif()
    exif[0x0112] = 6  # повернуть на 90° по часовой
    buf = io.BytesIO(); Image.new("RGB", (2400, 1200), "white").save(buf, "JPEG", exif=exif)
    full, thumb = backend.process_photo(buf.getvalue())
    assert Image.open(io.BytesIO(full)).size == (900, 1800)
    assert Image.open(io.BytesIO(thumb)).size == (240, 480)


if __name__ == "__main__":
    test_flow()
    test_draw()
    test_parallel_same_receipt()
    test_parallel_registrations_small_pool()
    test_pool_exhausted_returns_503()
    test_photo_limits_and_thumb()
    test_cashier_queue_by_station_and_code()
    test_admin_registrations_paginated()
    test_expired_released_in_bulk()
    test_registration_rate_limits()
    test_login_rate_limit()
    test_purge_rate_hits()
    test_staff_login_station_and_audit()
    test_concurrent_issue_single_winner()
    test_session_expiry_and_logout()
    test_manage_cli()
    test_phone_normalization()
    test_health_checks_db()
    test_large_png_memory_bounded()
    test_odd_image_files_return_400()
    test_successful_logins_not_limited()
    test_sms_send_disabled_without_sms()
    test_csv_empty_fiscal_row_matches_nothing()
    test_photo_exif_orientation_kept()
    print("OK")
