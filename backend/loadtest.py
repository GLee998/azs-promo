"""Нагрузочный тест и проверка критериев приёмки перед запуском акции.

Запускать на тестовом стенде, не на боевой базе: тест создаёт настоящие заявки.
Весь тест идёт с одного IP, поэтому на стенде на время прогона поднять лимиты:
RL_REG_IP=1000000 RL_REG_PHONE=1000000 в deploy/.env, затем docker compose --profile test up -d.

    pip install httpx pillow       # если запускаете не из окружения проекта
    python loadtest.py --base https://test-client.example.kg --run 10000 --concurrency 50 \\
        --login demo --password <пароль администратора стенда>
    python loadtest.py --base http://127.0.0.1:8765 --seed 2000      # только заполнить базу

Критерии приёмки (печатаются как PASS/FAIL):
  * ни одного ответа 5xx, p95 регистрации меньше 2 с, /api/health отвечает весь тест;
  * при заполненной базе: первая страница реестра быстрее 300 мс, очередь кассы быстрее 100 мс,
    100 одновременных опросов очереди укладываются в 2 с.
"""

import argparse
import asyncio
import io
import random
import sys
import time

import httpx
from PIL import Image, ImageDraw


def receipt_photo(size: str) -> bytes:
    """Похоже на фото чека после сжатия в браузере. real — 1800 px с шумом (худший случай), small — лёгкое."""
    w, h = (900, 1800) if size == "real" else (360, 720)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    rnd = random.Random(1)
    for y in range(0, h, 14):
        d.text((16, y), "".join(rnd.choice("0123456789 ABCDEF =.") for _ in range(w // 13)), fill=(rnd.randint(0, 80),) * 3)
    if size == "real":
        for _ in range(4000):
            d.point((rnd.randrange(w), rnd.randrange(h)), fill=(rnd.randint(150, 255),) * 3)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))] if values else 0.0


async def register_many(cl: httpx.AsyncClient, n: int, concurrency: int, offset: int, photo: bytes):
    """n регистраций с разными телефонами и чеками. Возвращает задержки (с) и счётчик кодов ответа."""
    sem = asyncio.Semaphore(concurrency)
    latencies, codes = [], {}

    async def one(i: int):
        async with sem:
            t = time.perf_counter()
            try:
                r = await cl.post("/api/registrations", files={"photo": ("receipt.jpg", photo, "image/jpeg")},
                                  data={"phone": f"+996{500_000_000 + offset + i}", "consent": "true",
                                        "station": str(i % 40 + 1), "fn": "0000440799999999",
                                        "fd": str(offset + i), "fm": str(1_000_000 + offset + i)})
                key = r.status_code
            except httpx.HTTPError as e:
                key = type(e).__name__
            latencies.append(time.perf_counter() - t)
            codes[key] = codes.get(key, 0) + 1

    await asyncio.gather(*(one(i) for i in range(n)))
    return latencies, codes


async def watch_health(cl: httpx.AsyncClient, stop: asyncio.Event, failures: list):
    while not stop.is_set():
        try:
            r = await cl.get("/api/health", timeout=5)
            if r.status_code != 200:
                failures.append(r.status_code)
        except httpx.HTTPError as e:
            failures.append(type(e).__name__)
        await asyncio.sleep(1)


async def timed(cl: httpx.AsyncClient, path: str, headers: dict) -> tuple[float, int]:
    t = time.perf_counter()
    r = await cl.get(path, headers=headers)
    return (time.perf_counter() - t) * 1000, r.status_code


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="адрес сайта клиента, например https://test-client.example.kg")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", type=int, metavar="N", help="N регистраций и проверка критериев приёмки")
    mode.add_argument("--seed", type=int, metavar="N", help="только заполнить базу N заявками")
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--offset", type=int, default=0, help="сдвиг номеров телефонов и чеков для повторных прогонов")
    ap.add_argument("--photo", choices=["real", "small"], default="real")
    ap.add_argument("--login", help="логин администратора стенда (для замеров реестра и очереди)")
    ap.add_argument("--password")
    args = ap.parse_args()

    photo = receipt_photo(args.photo)
    n = args.run or args.seed
    limits = httpx.Limits(max_connections=args.concurrency + 20)
    async with httpx.AsyncClient(base_url=args.base.rstrip("/"), timeout=120, limits=limits) as cl:
        stop, health_failures = asyncio.Event(), []
        health = asyncio.create_task(watch_health(cl, stop, health_failures))
        t0 = time.perf_counter()
        latencies, codes = await register_many(cl, n, args.concurrency, args.offset, photo)
        elapsed = time.perf_counter() - t0
        stop.set()
        await health
        print(f"регистраций {n}, параллельно {args.concurrency}, фото {len(photo) // 1024} КБ: {elapsed:.1f} с, "
              f"{n / elapsed:.1f}/с; p50 {pct(latencies, .5) * 1000:.0f} мс, p95 {pct(latencies, .95) * 1000:.0f} мс, "
              f"p99 {pct(latencies, .99) * 1000:.0f} мс; коды {codes}")
        if args.seed:
            return 0

        results = []
        def check(name: str, ok: bool, detail: str):
            results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

        errors_5xx = sum(v for k, v in codes.items() if not isinstance(k, int) or k >= 500)
        check("нет ответов 5xx и сетевых ошибок", errors_5xx == 0, f"{errors_5xx}")
        check("p95 регистрации < 2 с", pct(latencies, .95) < 2, f"{pct(latencies, .95) * 1000:.0f} мс")
        check("/api/health отвечал весь тест", not health_failures, f"сбоев {len(health_failures)}")

        if not (args.login and args.password):
            print("Замеры реестра и очереди пропущены: нужны --login и --password администратора стенда")
            return 0 if all(results) else 1
        r = await cl.post("/api/auth/login", json={"login": args.login, "password": args.password})
        r.raise_for_status()
        auth = {"Authorization": "Bearer " + r.json()["token"]}
        await timed(cl, "/api/admin/registrations?page=1", auth)  # прогрев
        ms, code = await timed(cl, "/api/admin/registrations?page=1", auth)
        check("реестр, первая страница < 300 мс", code == 200 and ms < 300, f"{ms:.0f} мс, код {code}")
        ms, code = await timed(cl, "/api/cashier/queue?station=1", auth)
        check("очередь кассы < 100 мс", code == 200 and ms < 100, f"{ms:.0f} мс, код {code}")
        t = time.perf_counter()
        polls = await asyncio.gather(*(cl.get("/api/cashier/queue?station=1", headers=auth) for _ in range(100)))
        ms = (time.perf_counter() - t) * 1000
        check("100 одновременных опросов очереди < 2 с", ms < 2000 and all(p.status_code == 200 for p in polls),
              f"{ms:.0f} мс")
        print("Размер базы на сервере: docker compose exec db-test psql -U promo -d promo -c "
              "\"select pg_size_pretty(pg_database_size('promo'))\"; свободное место: df -h /")
        return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
