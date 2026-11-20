#!/usr/bin/env bash
# Восстановление базы из дампа backup.sh.
#   restore.sh <файл.dump>                  — в отдельную базу promo_restore: проверка бэкапа, рабочая не трогается
#   restore.sh <файл.dump> promo --replace  — заменить рабочую базу. Сначала: docker compose stop api
set -euo pipefail
dump="${1:?использование: restore.sh <файл.dump> [база] [--replace]}"
target="${2:-promo_restore}"
cd "$(dirname "$0")"

if [ "$target" = promo ] && [ "${3:-}" != --replace ]; then
  echo "Рабочая база promo заменяется только с флагом --replace (и остановленным api)" >&2
  exit 1
fi

docker compose exec -T db dropdb -U promo --if-exists "$target"
docker compose exec -T db createdb -U promo "$target"
docker compose exec -T db pg_restore -U promo -d "$target" --no-owner < "$dump"
count=$(docker compose exec -T db psql -U promo -d "$target" -tAc "select count(*) from registrations")
echo "Восстановлено в базу $target: заявок $count"
