"""Сквозной сценарий: регистрация → блокировки по номеру и чеку → выдача на кассе → розыгрыш.

Запуск: cd backend && ../.venv/bin/python test_app.py
"""

import io
import os
import threading
from pathlib import Path
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["ADMIN_PASSWORD"] = "secret"
os.environ["CASHIER_PASSWORD"] = "kassa"
os.environ["SMS_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app as backend  # noqa: E402

# Для in-memory SQLite все соединения должны смотреть в одну базу.
backend.engine = backend.create_engine("sqlite://", poolclass=StaticPool,
                                       connect_args={"check_same_thread": False})
backend.Base.metadata.create_all(backend.engine)

client = TestClient(backend.app)
ADMIN = {"Authorization": "Bearer secret"}
KASSA = {"Authorization": "Bearer kassa"}
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


def test_flow():
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
    assert r.status_code == 409 and "уже получил машинку" in r.json()["detail"]

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


if __name__ == "__main__":
    test_flow()
    test_parallel_same_receipt()
    print("OK")
