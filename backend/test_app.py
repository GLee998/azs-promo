"""Сквозной сценарий: заявка → дубликат → сверка с кассой → допуск → возврат.

Запуск: DATABASE_URL=sqlite:///:memory: pytest backend  (или просто python backend/test_app.py)
"""

import io
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["ADMIN_PASSWORD"] = "secret"

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
FISCAL = {"fn": "7380440700000001", "fd": "12345", "fp": "1234567890"}


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(buf, "JPEG")
    return buf.getvalue()


def submit(phone: str, **fiscal) -> str:
    code = client.post("/api/sms/send", json={"phone": phone}).json()["demo_code"]
    r = client.post("/api/applications",
                    data={"phone": phone, "consent": "true", "sms_code": code, **fiscal},
                    files={"photo": ("r.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def apps() -> dict:
    return {a["code"]: a for a in client.get("/api/admin/applications", headers=ADMIN).json()["items"]}


def test_flow():
    # Неверный SMS-код
    client.post("/api/sms/send", json={"phone": "0700000000"})
    r = client.post("/api/applications", data={"phone": "0700000000", "consent": "true", "sms_code": "0000x"},
                    files={"photo": ("r.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 400

    # Не фото
    code = client.post("/api/sms/send", json={"phone": "+996555334455"}).json()["demo_code"]
    r = client.post("/api/applications", data={"phone": "+996555334455", "consent": "true", "sms_code": code},
                    files={"photo": ("r.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400

    first = submit("+996 700 111 223", **FISCAL)
    dup = submit("0555 556 677", **FISCAL)  # кто-то отправил фото того же чека
    manual = submit("772000112")  # QR не распознан

    assert client.get("/api/admin/applications").status_code == 401
    a = apps()
    assert a[first]["duplicate_of"] is None
    assert a[dup]["duplicate_of"] == first

    # Без сверки допустить нельзя
    r = client.post(f"/api/admin/applications/{a[first]['id']}/approve", headers=ADMIN)
    assert r.status_code == 400 and "продажами" in r.json()["detail"]

    # Сверка с выгрузками: в чеке 2 комбо
    sales = f"fn;fd;fp;qty\n{FISCAL['fn']};{FISCAL['fd']};{FISCAL['fp']};2\n".encode()
    issued = f"fn,fd,fp\n{FISCAL['fn']},{FISCAL['fd']},{FISCAL['fp']}\n".encode()
    assert client.post("/api/admin/import/sales", headers=ADMIN,
                       files={"file": ("s.csv", sales)}).json()["matched"] == 2
    client.post("/api/admin/import/issued", headers=ADMIN, files={"file": ("i.csv", issued)})

    r = client.post(f"/api/admin/applications/{a[first]['id']}/approve", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert len(r.json()["numbers"]) == 2

    # Дубликат того же чека допустить нельзя
    r = client.post(f"/api/admin/applications/{a[dup]['id']}/approve", headers=ADMIN)
    assert r.status_code == 400 and first in r.json()["detail"]

    # Ручной ввод реквизитов для заявки без QR
    r = client.patch(f"/api/admin/applications/{apps()[manual]['id']}", headers=ADMIN,
                     json={"fn": "7380 0000 0000 0002", "fd": "77", "fp": "555"})
    assert r.json()["fiscal_source"] == "manual" and r.json()["fn"] == "7380000000000002"

    # Возврат снимает допущенную заявку с розыгрыша
    client.post("/api/admin/import/returns", headers=ADMIN, files={"file": ("r.csv", issued)})
    a = apps()
    assert a[first]["status"] == "rejected" and a[first]["numbers"] == []

    assert client.get(f"/api/admin/applications/{a[first]['id']}/photo", headers=ADMIN).headers[
        "content-type"] == "image/jpeg"
    csv_text = client.get("/api/admin/export.csv", headers=ADMIN).text
    assert first in csv_text and "Номера участников" in csv_text


if __name__ == "__main__":
    test_flow()
    print("OK")
