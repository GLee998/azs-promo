#!/usr/bin/env bash
# Собирает сайты для прода в dist/ (Caddy отдаёт их из /srv). Если задан тестовый стенд — ещё и dist/test/.
# Запуск после каждого обновления кода: deploy/build-sites.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . deploy/.env; set +a

for site in client kassa admin; do
  # API на том же адресе, что и сайт (Caddy проксирует /api/*), поэтому API_URL пустой.
  API_URL= CLIENT_URL="https://$CLIENT_HOST/" DEMO=false ./build.sh "$site"
done

if [ -n "${TEST_CLIENT_HOST:-}" ]; then
  for site in client kassa admin; do
    OUT_DIR=dist/test API_URL= CLIENT_URL="https://$TEST_CLIENT_HOST/" DEMO=true ./build.sh "$site"
  done
fi
