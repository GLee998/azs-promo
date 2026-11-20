# Подготовка «Караван Coca‑Cola» к продакшену: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Цель:** сервис принимает около 10 000 заявок без зависаний и утечек доступа, работает на платном VPS, есть бэкапы и мониторинг.

**Архитектура:**
* Один VPS, на нём Docker Compose: Caddy (HTTPS, три статических сайта, прокси `/api/*`), FastAPI (uvicorn, 2 воркера) и Postgres 16.
* Код остаётся в `backend/app.py`, рядом появляются `manage.py` и миграции Alembic.
* У сотрудников личные учётки, их действия пишутся в журнал.

**Стек:** Python 3.12, FastAPI 0.115, SQLAlchemy 2.0, Pillow 11, Postgres 16, Caddy 2, Alembic. Фронтенд на чистом JS без сборщика.

**Spec:** `docs/superpowers/specs/2026-11-20-prod-readiness-audit.md`. Номера находок (B1…, F1…, D1…) ниже ссылаются на него.

## Global Constraints

* Новая runtime‑зависимость одна: `alembic`, версия фиксируется точно, как остальные в `backend/requirements.txt`. Пароли хешируются через `hashlib.scrypt` из стандартной библиотеки.
* Бэкенд‑тесты пишутся простыми функциями `test_*` в `backend/test_app.py`. Каждая новая функция добавляется в список под `if __name__ == "__main__":`. Запуск: `cd backend && ../.venv/bin/python test_app.py`, при успехе выводится `OK`.
* Фронтенд‑тесты: `node --test web/client/` (Node 22, без npm‑зависимостей).
* Каждая новая строка для клиента пишется на русском и на кыргызском (`web/client/i18n.js`: `KY`, `STRINGS.ky`). Новые сообщения сервера, которые видит клиент, добавляются в `SERVER_KY`. Кыргызские тексты перед запуском вычитывает носитель языка.
* Тексты интерфейса и комментарии в коде на русском, в стиле проекта.
* Тестовые элементы (плашка «Тестовая версия», раздел «Тестовые чеки», сброс розыгрыша) в прод‑сборке отсутствуют.

## Review Focus

1. **Много клиентов за одним IP оператора (CGNAT) на загруженной АЗС.** Законные регистрации не должны получать 429. Тест в Task 4: 60 регистраций с одного IP проходят.
2. **Вставка номера с кодом страны в поле, где уже подставлено `+996 `.** Номер нормализуется правильно или отклоняется, но никогда не искажается. Тесты в Task 6.
3. **Два кассира одновременно отмечают выдачу по одной заявке.** Ровно один успех, одна запись `Entry`, второй получает «Заявка уже не ждёт выдачи». Тест в Task 5.
4. **Сотни заявок истекают разом (TTL 3 часа).** Освобождаются одним SQL‑запросом, а не циклом при каждом опросе кассы. Тест в Task 3.
5. **Огромные изображения, «декомпрессионные бомбы» и файлы не‑картинки.** Ответ 400 с понятным текстом, никогда не 500 и без роста памяти. Тест в Task 2.

---

### Task 1: API не зависает под одновременными отправками (B1)

**Files:**
- Modify: `backend/app.py:60` (engine), `:202-204` (`get_db`), `:367-417` (`create_registration`), `:631-653` (`import_csv`)
- Test: `backend/test_app.py`

**Interfaces:**
- Produces:
  - `get_db()` выдаёт `Session(engine, expire_on_commit=False)`.
  - Для `postgresql` engine создаётся с `pool_size=DB_POOL_SIZE` (env, по умолчанию 10), `max_overflow=5`, `pool_timeout=10`. Для SQLite параметры пула не передаются: `SingletonThreadPool` их не принимает.
  - Обработчик `sqlalchemy.exc.TimeoutError` → `503 {"detail": "Сервер перегружен. Попробуйте ещё раз через минуту."}`.

- [ ] **Step 1: Write the failing tests**

```python
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
            print("FAIL test_parallel_registrations_small_pool: API завис, запросы не завершились за 40 с")
            os._exit(1)  # event loop заблокирован, корректно закрыть клиент нельзя
    assert results == [200] * 20, results


def test_pool_exhausted_returns_503():
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
```

- [ ] **Step 2: Run, verify both fail**

Run: `cd backend && ../.venv/bin/python test_app.py`.
Expected: `FAIL test_parallel_registrations_small_pool: API завис…`, exit 1. После того как эта проверка пройдёт, второй тест падает на исключении `sqlalchemy.exc.TimeoutError`, потому что обработчика 503 ещё нет.

- [ ] **Step 3: Implement**

* `create_registration` и `import_csv` объявляются через `def` вместо `async def`. Загрузку читать `photo.file.read(MAX_PHOTO_BYTES + 1)` и `file.file.read()`.
* Настройки пула и `expire_on_commit` сделать как в Interfaces, добавить обработчик 503.
* В `web/client/i18n.js` в `SERVER_KY` добавить: `/^Сервер перегружен/` → `'Сервер ашыкча жүктөлгөн. Бир мүнөттөн кийин кайра аракет кылыңыз.'`.

- [ ] **Step 4: Run tests**

Run: `cd backend && ../.venv/bin/python test_app.py`. Expected: `OK`.

- [ ] **Step 5: Commit** `git commit -m "Registration no longer blocks the event loop; 503 when the DB pool is exhausted"`

---

### Task 2: Безопасная обработка фото и превью для списков (B5, часть B2)

**Files:**
- Modify: `backend/app.py:141-146` (`RegistrationPhoto`), `:238-248` (`process_photo`), `:481-486` и `:566-568` (выдача фото), место вызова в `create_registration`
- Modify: `web/client/i18n.js` (`SERVER_KY`)
- Test: `backend/test_app.py`

**Interfaces:**
- Produces:
  - `process_photo(raw: bytes) -> tuple[bytes, bytes]` возвращает `(full, thumb)`: `full` в JPEG q82 не больше 1800 px, `thumb` в JPEG q70 не больше 480 px.
  - `MAX_PHOTO_PIXELS = 50_000_000`, и `Image.MAX_IMAGE_PIXELS = MAX_PHOTO_PIXELS`.
  - Колонка `RegistrationPhoto.thumb: Mapped[bytes | None]`.
  - `GET /api/cashier/registrations/{id}/photo?size=thumb|full` (по умолчанию `full`; при `thumb=None` отдаётся `full`) и такой же адрес в `/api/admin/...`. Заголовок `Cache-Control: private, max-age=86400`.
  - Сообщение об ошибке: `"Слишком большое изображение. Сфотографируйте чек ещё раз."`.

- [ ] **Step 1: Write the failing test**

```python
def test_photo_limits_and_thumb():
    fresh_db()  # helper: новый in-memory engine + create_all (вынести из test_draw)
    big = io.BytesIO(); Image.new("1", (9000, 9000)).save(big, "PNG")  # 81 Мпикс, файл ~10 КБ
    r = client.post("/api/registrations", data={"phone": "+996555000901", "consent": "true"},
                    files={"photo": ("b.png", big.getvalue(), "image/png")})
    assert r.status_code == 400 and "Слишком большое изображение" in r.json()["detail"]

    photo = io.BytesIO(); Image.new("RGB", (2400, 1600), "white").save(photo, "JPEG")
    ok = client.post("/api/registrations", data={"phone": "+996555000902", "consent": "true", "fn": "41", "fd": "1", "fm": "1"},
                     files={"photo": ("r.jpg", photo.getvalue(), "image/jpeg")})
    item = queue()[ok.json()["code"]]
    thumb = client.get(f"/api/cashier/registrations/{item['id']}/photo?size=thumb", headers=KASSA)
    full = client.get(f"/api/cashier/registrations/{item['id']}/photo", headers=KASSA)
    assert max(Image.open(io.BytesIO(thumb.content)).size) <= 480
    assert max(Image.open(io.BytesIO(full.content)).size) == 1800
```

- [ ] **Step 2: Run, verify it fails** (сейчас 9000×9000 даёт 500 `DecompressionBombError`)
- [ ] **Step 3: Implement**

После `Image.open` сначала проверить `width * height`. Перехватить `Image.DecompressionBombError` и тоже ответить 400. Для JPEG перед декодированием вызвать `img.draft("RGB", (1800, 1800))`, тогда декодирование идёт сразу в уменьшенном масштабе. В `SERVER_KY` добавить `/^Слишком большое изображение/` → `'Сүрөт өтө чоң. Чекти кайра сүрөткө тартыңыз.'`.

- [ ] **Step 4: Run tests**. Expected: `OK`.
- [ ] **Step 5: Commit** `git commit -m "Photo pixel limit, decompression bombs return 400, thumbnails for lists"`

---

### Task 3: Быстрые списки: индексы, очередь по АЗС, реестр с пагинацией (B2, B3, B6)

**Files:**
- Modify: `backend/app.py`: модель `Registration` (индексы), `:463-478` (`cashier_queue`), `:551-563` (`list_registrations`), `:631-653` (`import_csv`), `:679-684` (`eligible`)
- Test: `backend/test_app.py`

**Interfaces:**
- Produces:
  - Индексы `ix_registrations_status_created (status, created_at)`, `ix_registrations_issued_at (issued_at)`, `ix_registrations_fiscal (fn, fd, fm)`, `ix_registrations_station_status (station, status)`.
  - `release_all_expired(db: Session) -> int`: один `UPDATE` для всех `status='waiting' AND created_at < now() - WAITING_TTL`. Результат: `status='cancelled'`, `status_reason='Игрушку не забрали вовремя'`, `phone_lock = receipt_lock = NULL`. Возвращает число строк.
  - `GET /api/cashier/queue?station=&q=` → `{"waiting": [...], "recent": [...]}`. Правила отбора:
    - если в `q` (пробелы убраны, регистр игнорируется) 3 и больше символов, ищется код заявки по всем АЗС, не больше 20 результатов;
    - иначе, если передан `station`, берутся ожидающие этой АЗС плюс ожидающие с `station IS NULL`, не больше 50, свежие сверху;
    - иначе все ожидающие, не больше 50.
    - `recent`: 10 последних выданных, при заданном `station` только с `issued_station == station`.
  - `GET /api/admin/registrations?status=&q=&page=1&per_page=50` → `{"items", "total", "page", "per_page", "stats"}`:
    - `per_page` не больше 200;
    - `status` принимает `waiting`, `issued`, `unchecked`, `cancelled`, `disqualified` или пустое значение;
    - `q` ищет по коду заявки (пробелы и регистр не важны), по цифрам телефона (подстрока), по `fn`/`fd`/`fm` (точное совпадение) и по номеру участника `KN-00042` (ищется `Entry.id`).
  - `GET /api/admin/stats` → тот же словарь `stats`, считается через `GROUP BY status`.
  - Все запросы списков используют `selectinload(Registration.entry)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cashier_queue_by_station_and_code():
    fresh_db()
    a = register("+996555000401", station="7", fn="71", fd="1", fm="1").json()["code"]
    b = register("+996555000402", station="3", fn="71", fd="2", fm="1").json()["code"]
    c = register("+996555000403", station="", fn="71", fd="3", fm="1").json()["code"]
    q7 = {r["code"] for r in client.get("/api/cashier/queue?station=7", headers=KASSA).json()["waiting"]}
    assert a in q7 and c in q7 and b not in q7
    found = client.get(f"/api/cashier/queue?station=7&q={b.replace(' ', '').lower()}", headers=KASSA).json()
    assert [r["code"] for r in found["waiting"]] == [b]


def test_admin_registrations_paginated():
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
    fresh_db()
    old = [register(f"+996555000{i}50", fn="91", fd=str(i), fm="1").json()["code"] for i in range(3)]
    with backend.Session(backend.engine) as db:
        for r in db.scalars(backend.select(backend.Registration)):
            r.created_at = backend.now() - backend.WAITING_TTL - timedelta(minutes=1)
        db.commit()
        assert backend.release_all_expired(db) == 3
        db.commit()
    assert register("+996555000050", fn="91", fd="0", fm="1").status_code == 200  # номер и чек освободились
```

`queue_by_code(code: str, headers: dict | None = None) -> dict` — тестовый helper: `GET /api/cashier/queue?q=<код без пробела>` с заголовками `headers or KASSA`, возвращает первый элемент `waiting`.

- [ ] **Step 2: Run, verify they fail**
- [ ] **Step 3: Implement** по Interfaces. Python‑цикл по ожидающим в `cashier_queue` заменить вызовом `release_all_expired`. `import_csv` ищет по `(fn, fd, fm)`, пользуясь индексом. `eligible` делает join с `Entry`, а не фильтрует `r.entry` в Python.
- [ ] **Step 4: Run tests**. Expected: `OK`. Существующие `test_flow` и `test_draw` тоже проходят: их helper `queue()` работает без `station`.
- [ ] **Step 5: Commit** `git commit -m "Indexes, station-scoped cashier queue, paginated registry, bulk expiry"`

---

### Task 4: Лимиты частоты для регистрации и входа (B4)

**Files:**
- Modify: `backend/app.py` (модель, функция, вызовы), `web/client/i18n.js` (`SERVER_KY`)
- Test: `backend/test_app.py`

**Interfaces:**
- Produces:
  - Модель `RateHit`: `bucket str(32)`, `key str(64)`, `window_start datetime`, `hits int`, PK `(bucket, key, window_start)`.
  - `rate_limit(db: Session, bucket: str, key: str, limit: int, window: timedelta) -> None`: увеличивает счётчик текущего окна, при `hits > limit` бросает `HTTPException(429, "Слишком много попыток. Попробуйте через несколько минут.")`.
  - `purge_rate_hits(db: Session) -> int`: удаляет окна старше 24 часов.
  - Лимиты задаются env, по умолчанию:
    - `RL_REG_IP=60` за 10 минут;
    - `RL_REG_PHONE=5` за 1 час;
    - `RL_LOGIN_IP=10` за 15 минут.
  - IP клиента берётся из `request.client.host`. В проде uvicorn запускается с `--proxy-headers` (Task 9).
  - Значения лимитов читаются в константы модуля `RL_REG_IP`, `RL_REG_PHONE`, `RL_LOGIN_IP`.
  - Лимиты заодно мешают массово проверять, какие номера участвовали (B9).
- Consumes: `create_registration` (Task 1). В нём лимит проверяется **до** чтения и обработки фото.
- Тесты: в начале `test_app.py` env `RL_REG_IP`, `RL_REG_PHONE`, `RL_LOGIN_IP` выставляются в `100000`, иначе тесты с сотней регистраций (Task 3) упрутся в лимит. Тесты лимитов временно подменяют `backend.RL_REG_IP = 60`, `backend.RL_REG_PHONE = 5`, `backend.RL_LOGIN_IP = 10` и в `finally` возвращают прежние значения.

- [ ] **Step 1: Write the failing tests**

```python
def test_registration_rate_limits():
    fresh_db()
    codes = [register("+996 555 000 777", fn="51", fd=str(i), fm="1").status_code for i in range(6)]
    assert codes[0] == 200 and codes[1:5] == [409] * 4 and codes[5] == 429   # по телефону: 5 попыток в час
    fresh_db()
    codes = [register(f"+996 555 1{i:05d}", fn="52", fd=str(i), fm="1").status_code for i in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429                     # по IP: щадящий лимит (CGNAT)


def test_login_rate_limit():
    fresh_db()
    assert all(client.post("/api/admin/login", headers={"Authorization": "Bearer wrong"}).status_code == 401
               for _ in range(10))
    assert client.post("/api/admin/login", headers=ADMIN).status_code == 429
```

После Task 5 в `test_login_rate_limit` проверяется `POST /api/auth/login`, а не `/api/admin/login`.

- [ ] **Step 2: Run, verify they fail**
- [ ] **Step 3: Implement**. Счётчик обновлять одним атомарным запросом, если диалект позволяет (`INSERT … ON CONFLICT DO UPDATE` есть и в Postgres, и в SQLite 3.24+). Если нет, сначала `UPDATE`, затем `INSERT`, а на `IntegrityError` повторить `UPDATE`. В `SERVER_KY` добавить `/^Слишком много попыток/` → `'Аракет өтө көп. Бир нече мүнөттөн кийин кайталаңыз.'`.
- [ ] **Step 4: Run tests**. Expected: `OK`.
- [ ] **Step 5: Commit** `git commit -m "Rate limits for registration and logins, stored in the DB"`

---

### Task 5: Личные учётки сотрудников, сессии и журнал действий (B4, D5)

**Files:**
- Modify: `backend/app.py:259-273` (авторизация), `:458-460` и `:546-548` (старые login), `:496-528` (issue и cancel), а также `audit(...)` во всех изменяющих эндпоинтах админки
- Create: `backend/manage.py`
- Modify: `web/kassa/index.html`, `web/kassa/kassa.js`, `web/admin/index.html`, `web/admin/admin.js`, `web/admin/draw.html`, `web/admin/draw.js`, `render.yaml` (убрать `ADMIN_PASSWORD`/`CASHIER_PASSWORD`)
- Test: `backend/test_app.py` (helpers `ADMIN`/`KASSA` становятся токенами входа)

**Interfaces:**
- Produces:
  - Модели:
    - `StaffUser(id, login str(32) unique, password_hash str(200), role 'cashier'|'admin', station str(32)|None, active bool)`;
    - `StaffSession(token_hash str(64) PK, user_id, created_at, expires_at)`;
    - `AuditLog(id, at, user_id, action str(32), registration_id int|None, details Text|None)`.
  - Пароли:
    - `hash_password(pw: str) -> str` возвращает строку `"scrypt$<salt_hex>$<hash_hex>"` (n=2**14, r=8, p=1, соль 16 байт);
    - `check_password(pw: str, stored: str) -> bool` сравнивает через `secrets.compare_digest`.
  - `POST /api/auth/login {"login","password"}` → `{"token", "role", "station", "expires_at"}`:
    - токен `secrets.token_urlsafe(32)`, в базе хранится его sha256;
    - срок жизни `SESSION_TTL_HOURS` (по умолчанию 14);
    - ошибка: 401 `"Неверный логин или пароль"`;
    - лимит из Task 4.
  - `POST /api/auth/logout`.
  - `require_cashier` и `require_admin` возвращают `StaffUser`. Без сессии ответ 401, при неподходящей роли 403.
  - Эндпоинты `/api/admin/login`, `/api/cashier/login` и env `ADMIN_PASSWORD`/`CASHIER_PASSWORD` удаляются.
  - Выдача:
    - атомарный `UPDATE … WHERE id=:id AND status='waiting'`; если обновлено 0 строк, ответ 400 `"Заявка уже не ждёт выдачи. Обновите список."`;
    - `issued_station = user.station or clean_station(body.station) or r.station`.
  - Очередь: если в учётке есть `station`, он подставляется в `GET /api/cashier/queue`.
  - `audit(db, user, action: str, registration_id: int | None = None, details: str | None = None) -> None` вызывается для действий `login`, `issue`, `cancel`, `patch`, `disqualify`, `restore`, `import`, `draw`, `redraw`, `draw_reset`.
  - `GET /api/admin/audit.csv` выгружает журнал.
  - `manage.py` (по образцу `python manage.py <cmd>`):
    - `add-user <login> --role cashier|admin [--station N]` печатает сгенерированный пароль из 12 символов;
    - `set-password <login>`;
    - `disable-user <login>`;
    - `purge` удаляет истёкшие сессии и вызывает `purge_rate_hits`.

- [ ] **Step 1: Write the failing tests**

```python
def test_staff_login_station_and_audit():
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
    fresh_db_file()   # файловый SQLite: у каждого запроса своё соединение
    add_user("k1", "p", role="cashier", station="1")
    k = login("k1", "p")
    code = register("+996555000302", fn="62", fd="1", fm="1").json()["code"]
    reg_id = queue_by_code(code, headers=k)["id"]
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        client.post(f"/api/cashier/registrations/{reg_id}/issue", headers=k, json={}).status_code)) for _ in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(results) == [200, 400]
    with backend.Session(backend.engine) as db:
        assert db.scalar(backend.select(backend.func.count(backend.Entry.id))) == 1


def test_session_expiry_and_logout():
    fresh_db()
    add_user("k2", "p", role="cashier", station="2")
    k = login("k2", "p")
    assert client.post("/api/auth/logout", headers=k).status_code == 200
    assert client.get("/api/cashier/queue", headers=k).status_code == 401
```

Тестовые helpers:
* `add_user` пишет `StaffUser` напрямую через `hash_password`;
* `login` возвращает заголовок `Authorization`;
* `fresh_db_file` создаёт файловый SQLite, как в `test_parallel_same_receipt`.

После Task 5 `fresh_db()` также создаёт пользователей `admin` (role admin) и `kassa` (role cashier, без станции) и перезаписывает глобальные заголовки `ADMIN` и `KASSA` токенами входа. `test_flow` начинается с вызова `fresh_db()`.

- [ ] **Step 2: Run, verify they fail**
- [ ] **Step 3: Implement backend** по Interfaces.
- [ ] **Step 4: Implement фронтенд входа**
  * Формы входа в кассе, реестре и розыгрыше получают поля «Логин» и «Пароль».
  * В `sessionStorage` хранится токен, а не пароль.
  * В кассе поле «АЗС №» заменяется плашкой с `station` из ответа входа.
  * «Выйти» вызывает `/api/auth/logout`.
  * На 403 показывается «Нет доступа для этой учётной записи».
- [ ] **Step 5: Run tests**. Expected: `OK`. Затем проверить вручную: `cd backend && ../.venv/bin/python manage.py add-user kassa7 --role cashier --station 7`, войти на `/kassa/` и отметить выдачу. Заявка выдана на АЗС №7, а в `audit.csv` есть строка `issue`.
- [ ] **Step 6: Commit** `git commit -m "Personal staff accounts, sessions, audit log; station comes from the account"`

---

### Task 6: Клиентская форма: номер, экран заявки, прод‑сборка (F1, F2, F3, F6)

**Files:**
- Create: `web/client/phone.js` (сюда переезжают `normalizePhone` и `formatPhone` из `form.js:17-34`), `web/client/phone.test.js`, `web/shared/vendor/` (библиотеки и шрифты)
- Modify:
  - `web/client/form.js:17-34` и `:189`;
  - `web/client/index.html`;
  - `web/shared/style.css` (`@font-face`, `#ticket { scroll-margin-top }`, `.demo-only`);
  - `web/shared/api.js:30-31` (убрать пинг для пробуждения сервера);
  - `build.sh` (переменная `DEMO`);
  - `web/admin/*.html` (ссылку «Тестовые чеки» пометить `.demo-only`);
  - `web/admin/stand.html` (инлайн‑скрипт и `onclick` вынести в `stand.js`).

**Interfaces:**
- Produces:
  - `phone.js` задаёт `window.Phone = { normalizePhone, formatPhone }`, а при наличии `module` также `module.exports` для `node --test`.
  - `build.sh` пишет в `config.js` строку `window.DEMO = ${DEMO:-false}`. При `DEMO != true` из `dist/admin` удаляется `test-receipt.html`.
  - Локальный `web/shared/config.js` задаёт `window.DEMO = true`.
  - `api.js` вызывает `document.documentElement.classList.toggle('demo', !!window.DEMO)`.
  - CSS: `.demo-only` скрыт везде, кроме `.demo`.
  - Плашка `.demo-pill` получает класс `demo-only`.
- Алгоритм `formatPhone`, это и есть исправление F1:

```js
let d = raw.replace(/\D/g, '');
if (d.startsWith('996')) d = d.slice(3);                  // код страны: подставлен полем или введён
if (d.length > 9 && d.startsWith('996')) d = d.slice(3);  // после «+996 » вставили номер ещё раз с кодом
d = d.replace(/^0+/, '').slice(0, 9);
```

- [ ] **Step 1: Write the failing test** `web/client/phone.test.js`

```js
const test = require('node:test');
const assert = require('node:assert');
const { formatPhone, normalizePhone } = require('./phone.js');

test('вставка номера с кодом страны после подставленного «+996 »', () => {
  assert.equal(formatPhone('+996 +996700123456'), '+996 700 123 456');
  assert.equal(formatPhone('+996 996 700 123 456'), '+996 700 123 456');
  assert.equal(formatPhone('+996 0700123456'), '+996 700 123 456');
});
test('набор по цифрам и номер с кодом оператора 996', () => {
  assert.equal(formatPhone('+996 7'), '+996 7');
  assert.equal(formatPhone('+996 996 123 456'), '+996 996 123 456');
});
test('normalizePhone', () => {
  assert.equal(normalizePhone('+996 700 123 456'), '+996700123456');
  assert.equal(normalizePhone('+996 700 123'), null);
});
```

- [ ] **Step 2: Run** `node --test web/client/`. Expected: FAIL (`phone.js` ещё не существует).
- [ ] **Step 3: Implement** `phone.js` и подключить его в `index.html` перед `form.js`.
- [ ] **Step 4: Run** `node --test web/client/`. Expected: все тесты проходят.
- [ ] **Step 5: Экран заявки**
  * `openTicket` прокручивает к `#ticket`, а не к `#roadWrap`.
  * CSS: `#ticket { scroll-margin-top: 120px }`, это высота липкой полосы.
  * Проверка: в Chrome DevTools на 390×844 отправить форму. Заголовок «Чек принят» и плашка с кодом видны целиком.
- [ ] **Step 6: Свои копии библиотек и шрифтов**
  * В `web/shared/vendor/` положить jsQR 1.4.0, gsap 3.13.0, canvas‑confetti 1.9.3, qrcode‑generator 1.4.4 (те же версии, что сейчас на CDN).
  * Шрифты Golos Text 400–700, Unbounded 500–700, Russo One 400 в woff2, подмножества cyrillic и latin, из `@fontsource/*` с зафиксированной версией. Подключить через `@font-face` с `font-display: swap`.
  * Ссылки на `cdn.jsdelivr.net` и `fonts.googleapis.com` удалить.
  * Проверка: `grep -rn "jsdelivr\|googleapis" web/` ничего не находит, форма распознаёт QR из `test-data/test-receipt.png`.
- [ ] **Step 7: Прод‑сборка**
  * `API_URL= DEMO=false ./build.sh client && ./build.sh admin`.
  * Проверка: `grep -rl "demo-pill" dist/client`, в собранном `config.js` записано `window.DEMO = false`, файла `dist/admin/test-receipt.html` нет.
- [ ] **Step 8: Commit** `git commit -m "Phone paste fix, ticket not under the sticky bar, local libraries, prod build without test elements"`

---

### Task 7: Касса и реестр на новом API (F4, F5, F7)

**Files:**
- Modify: `web/kassa/index.html`, `web/kassa/kassa.js`, `web/admin/index.html`, `web/admin/admin.js`, `web/shared/style.css`
- Consumes:
  - `?size=thumb` из Task 2;
  - `GET /api/cashier/queue?station=&q=`, `GET /api/admin/registrations?status=&q=&page=&per_page=`, `GET /api/admin/stats` из Task 3;
  - вход из Task 5.

**Решения:**
* **Карточка в кассе.** Сверху код заявки, телефон и время, под ними кнопки. Справа превью 96×128. Полное фото загружается только по нажатию на превью и открывается в lightbox.
* **Поиск в кассе.** Видимое поле с подписью «Код заявки». Запрос `?q=` уходит на сервер с задержкой 300 мс и ищет по всем АЗС.
* **Отмена в кассе.** Вместо `prompt()` кнопки‑причины: «Чек не подходит под условия», «В чеке нет комбо», «Клиент передумал», «Другое…» (с полем ввода).
* **Реестр.**
  * Пагинация 50 на страницу, «Назад» и «Вперёд», надпись вида «1–50 из N».
  * Фильтр и поиск выполняются на сервере, запрос с задержкой 300 мс.
  * Превью грузятся через `IntersectionObserver`: `fetch` с заголовком авторизации, затем blob URL, и только для видимых карточек.
  * После PATCH, снятия или возврата перерисовывается одна карточка из ответа и обновляются плитки статистики через `/api/admin/stats`.
* **Поля поиска.** Белый фон и рамка `1.5px var(--line)`. Подпись видима или есть `aria-label`. Плейсхолдер больше не единственная подпись (F7).

- [ ] **Step 1: Засеять 10 000 заявок локально** через `backend/loadtest.py --seed 10000` (Task 10) или временным SQL‑скриптом.
- [ ] **Step 2: Implement кассу** по решениям.
- [ ] **Step 3: Implement реестр** по решениям.
- [ ] **Step 4: Проверить вручную**
  * На 10 000 заявках реестр открывается быстрее 1 с.
  * Во вкладке Network не больше 50 запросов превью, каждый до 40 КБ.
  * Галочка «Покупка сверена» не перезагружает список.
  * Касса на 390×844: код и кнопки видны без прокрутки.
- [ ] **Step 5: Commit** `git commit -m "Cashier: compact cards, code search, reason buttons; registry: pagination, lazy thumbnails"`

---

### Task 8: Миграции Alembic и health с проверкой базы (B7, D7)

**Files:**
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/0001_initial.py`
- Modify: `backend/app.py:199` (`create_all` только для SQLite), `:288-290` (`health`), `backend/requirements.txt`
- Test: `backend/test_app.py`

**Interfaces:**
- Produces:
  - `env.py` берёт `app.Base.metadata` и `app._db_url()`.
  - Для Postgres схема создаётся только командой `alembic upgrade head`, для SQLite как раньше через `create_all`.
  - `GET /api/health` выполняет `SELECT 1` и возвращает `{"ok": true}`, при ошибке базы 503.

- [ ] **Step 1: Write the failing test**

```python
def test_health_checks_db():
    backend.engine = backend.create_engine("sqlite:////nonexistent-dir/x.db")
    assert client.get("/api/health").status_code == 503
    fresh_db()
    assert client.get("/api/health").json() == {"ok": True}
```

- [ ] **Step 2: Run, verify it fails** (сейчас всегда 200)
- [ ] **Step 3: Implement**
  * Добавить `alembic` в зависимости.
  * Сгенерировать миграцию `0001_initial` командой `alembic revision --autogenerate` против пустого Postgres, затем вычитать её.
- [ ] **Step 4: Verify**
  * На пустом Postgres: `alembic upgrade head`, затем `alembic check`. Expected: `No new upgrade operations detected.`
  * `cd backend && ../.venv/bin/python test_app.py`. Expected: `OK`.
- [ ] **Step 5: Commit** `git commit -m "Alembic migrations; health checks the database"`

---

### Task 9: Прод‑инфраструктура: Docker Compose, Caddy, бэкапы, мониторинг (D1, D3, D6)

**Files:**
- Create: `backend/Dockerfile`, `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/.env.example`, `deploy/build-sites.sh`, `deploy/backup.sh`, `deploy/restore.sh`, `deploy/README.md`
- Modify: `README.md` (раздел «Развёртывание» про VPS), `render.yaml` (удалить; тестовый стенд переезжает на тот же VPS, см. ниже), `.gitignore` (`deploy/.env`)

**Решения:**
* **Dockerfile.**
  * Образ `python:3.12-slim`, пользователь не root.
  * Команда: `alembic upgrade head && uvicorn app:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips="*"`.
  * Порт наружу не публикуется: API доступен только Caddy во внутренней сети.
* **docker-compose.**
  * `db`: `postgres:16`, volume, healthcheck `pg_isready`, `command: postgres -c shared_buffers=1GB -c max_connections=100`.
  * `api`: стартует после `db: service_healthy`, `restart: unless-stopped`.
  * `caddy`: `caddy:2`, порты 80 и 443, volumes `Caddyfile`, `dist/` и `caddy_data`.
  * У всех сервисов `logging: {driver: json-file, options: {max-size: 10m, max-file: "5"}}`.
* **Caddyfile.**
  * Три сайта: `{$CLIENT_HOST}`, `{$KASSA_HOST}`, `{$ADMIN_HOST}`.
  * На каждом: `encode zstd gzip`, `request_body { max_size 12MB }`, `handle /api/* { reverse_proxy api:8000 }`, остальное отдаёт `file_server` из `/srv/<сайт>`.
  * Заголовки: HSTS, `X-Content-Type-Options nosniff`, `Referrer-Policy strict-origin-when-cross-origin`, `Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'"`.
  * На кассе и в админке дополнительно `X-Frame-Options DENY` и `X-Robots-Tag noindex`.
  * Кэш: `/shared/*` получает `public, max-age=31536000, immutable` (в адресах уже есть `?v=`), `*.html` получает `no-cache`.
* **`.env.example`:** `POSTGRES_PASSWORD`, `DATABASE_URL`, `CLIENT_HOST`, `KASSA_HOST`, `ADMIN_HOST`, `SMS_REQUIRED=false`, `DRAW_RESET_ALLOWED=false`, `WEB_CONCURRENCY=2`, `DB_POOL_SIZE=10`, переменные `RL_*`.
* **`build-sites.sh`:** собирает три сайта командой `API_URL= CLIENT_URL=https://$CLIENT_HOST/ DEMO=false ./build.sh <site>`.
* **Бэкапы.**
  * `backup.sh`: `pg_dump -Fc` в `/var/backups/karavan/`, хранение 14 дней, затем `rclone copy` в удалённое хранилище (remote настраивает владелец сервера). Cron каждые 6 часов на время акции.
  * `restore.sh <dump>` восстанавливает дамп в чистую базу.
* **Тестовый стенд.** Второй compose‑проект на том же VPS со своей базой, на поддоменах `test-*`, с `DEMO=true` и `DRAW_RESET_ALLOWED=true`. Render больше не используется.
* **`deploy/README.md`, порядок действий:**
  1. Ubuntu 24.04: вход по SSH только по ключам, `ufw allow 22,80,443`, `unattended-upgrades`, часовой пояс `Asia/Bishkek`, Docker.
  2. A‑записи DNS для трёх хостов.
  3. Первый запуск и обновления: `git pull`, затем `deploy/build-sites.sh`, затем `docker compose up -d --build`.
  4. Учётки сотрудников: `docker compose exec api python manage.py add-user …`.
  5. Cron `manage.py purge` раз в сутки.
  6. Мониторинг: внешний uptime‑монитор опрашивает `https://$CLIENT_HOST/api/health` раз в минуту. Уведомление при заполнении диска больше 80% (cron и почта, либо алерт провайдера).
  7. Учебное восстановление из бэкапа.

- [ ] **Step 1: Implement** файлы по решениям.
- [ ] **Step 2: Verify локально**
  * Поднять стек с `CLIENT_HOST=client.localhost`, `KASSA_HOST=kassa.localhost`, `ADMIN_HOST=admin.localhost`: Caddy сам выпускает локальные сертификаты для `.localhost`.
  * `curl -sk https://client.localhost/api/health`. Expected: `{"ok":true}`.
  * `curl -skI https://admin.localhost/`: в ответе есть `x-robots-tag: noindex` и `content-security-policy`.
- [ ] **Step 3: Verify бэкап**
  * Создать 3 заявки, выполнить `deploy/backup.sh`, затем `deploy/restore.sh` в новую базу.
  * В восстановленной базе `select count(*) from registrations` возвращает 3.
- [ ] **Step 4: Commit** `git commit -m "Prod deploy: Docker Compose, Caddy, backups, runbook"`

---

### Task 10: Нагрузочный тест и приёмка на сервере

**Files:**
- Create: `backend/loadtest.py`. Основа: скрипт из аудита (async httpx, фото 1800 px, отчёт p50/p95/p99 и коды ответов). Режимы `--seed N` для заполнения и `--run N --concurrency C`. Аргументы `--base` и `--login/--password` для служебных эндпоинтов.

- [ ] **Step 1: Implement** `loadtest.py`.
- [ ] **Step 2: Run на тестовом стенде VPS**
  * Перед прогоном поднять лимиты `RL_*` (весь тест идёт с одного IP).
  * Команда: `python loadtest.py --base https://test-client.<домен> --run 10000 --concurrency 50`.
- [ ] **Step 3: Проверить критерии приёмки**
  * Ни одного ответа 5xx.
  * p95 регистрации меньше 2 с.
  * `/api/health` отвечает во время всего теста.
  * При 10 000 заявках:
    * `GET /api/admin/registrations?page=1` быстрее 300 мс;
    * `GET /api/cashier/queue?station=1` быстрее 100 мс;
    * 100 параллельных опросов очереди укладываются в 2 с.
  * Записать размер базы. После теста на диске свободно больше 50%.
- [ ] **Step 4: Удалить тестовые данные**, вернуть лимиты, затем commit `git commit -m "Load test and acceptance criteria"`

---

## Чек‑лист запуска (после задач)

1. От юриста получены: решение о месте хранения данных (выбор дата‑центра), тексты `rules.html` и `privacy.html`, срок хранения данных после розыгрыша.
2. Принято решение по SMS‑подтверждению (B8). Если SMS включается, отдельной задачей подключается провайдер (`TODO` в `sms_send`).
3. Сборка с `DEMO=false`, переменная `DRAW_RESET_ALLOWED=false`. На боевом сайте нет «Тестовой версии» и раздела «Тестовые чеки».
4. Созданы учётки: администраторы и по одной на каждую АЗС. Пароли переданы лично, вход проверен с каждой кассы.
5. QR‑постеры напечатаны с боевым адресом и номером АЗС (`stand.html`).
6. Бэкап сделан и восстановлен в чистую базу. Мониторинг присылает уведомление (проверить, остановив `api`).
7. Нагрузочный тест пройден, тестовые данные удалены.
8. После розыгрыша: выгрузить результаты и журнал, затем удалить или обезличить фото и телефоны в срок из политики.
