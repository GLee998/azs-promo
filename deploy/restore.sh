#!/usr/bin/env bash
# Восстановление базы из дампа backup.sh.
#   restore.sh <файл.dump>                  — в отдельную базу promo_restore: проверка бэкапа, рабочая не трогается
#   restore.sh <файл.dump> promo --replace  — заменить рабочую базу. Сначала: docker compose stop api
# При замене дамп восстанавливается во временную базу promo_new, и только после успеха она становится
# рабочей. Прежняя рабочая база не удаляется, а переименовывается в promo_old_<время>.
set -euo pipefail
dump_arg="${1:?использование: restore.sh <файл.dump> [база] [--replace]}"
target="${2:-promo_restore}"
# Путь к дампу — до смены каталога: относительный путь иначе указал бы не туда.
dump="$(realpath -e -- "$dump_arg" 2>/dev/null)" || { echo "Нет файла $dump_arg" >&2; exit 1; }
cd "$(dirname "$0")"

if [ "$target" = promo ] && [ "${3:-}" != --replace ]; then
  echo "Рабочая база promo заменяется только с флагом --replace (и остановленным api)" >&2
  exit 1
fi

# Сначала убеждаемся, что дамп читается, и только потом что-то удаляем.
if ! docker compose exec -T db pg_restore --list < "$dump" > /dev/null; then
  echo "Файл $dump не похож на дамп pg_dump, база не тронута" >&2
  exit 1
fi

into="$target"
[ "$target" = promo ] && into=promo_new
docker compose exec -T db dropdb -U promo --if-exists "$into"
docker compose exec -T db createdb -U promo "$into"
docker compose exec -T db pg_restore -U promo -d "$into" --no-owner < "$dump"

if [ "$target" = promo ]; then
  old="promo_old_$(date +%Y%m%d%H%M%S)"
  docker compose exec -T db psql -U promo -d postgres -v ON_ERROR_STOP=1 \
    -c "ALTER DATABASE promo RENAME TO $old" -c "ALTER DATABASE promo_new RENAME TO promo"
  echo "Рабочая база заменена, прежняя сохранена как $old"
fi
count=$(docker compose exec -T db psql -U promo -d "$target" -tAc "select count(*) from registrations")
echo "В базе $target заявок: $count"
