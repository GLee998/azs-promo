# API акции. Перед запуском применяет миграции базы, затем стартует uvicorn за Caddy.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SERVE_WEB=false
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 app
USER app

EXPOSE 8000
# --proxy-headers: адрес клиента берётся из X-Forwarded-For от Caddy (нужен для лимитов частоты).
# Порт API наружу не публикуется, до него доходит только Caddy, поэтому forwarded-allow-ips='*'.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
