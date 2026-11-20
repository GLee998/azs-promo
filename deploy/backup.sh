#!/usr/bin/env bash
# Резервная копия базы: pg_dump в BACKUP_DIR (по умолчанию /var/backups/karavan).
# На сервере хранятся только последние KEEP_LOCAL дампов (по умолчанию 8 — двое суток при запуске
# каждые 6 часов): база с фото чеков весит гигабайты, и полная история заняла бы весь диск.
# История — в копии вне сервера (RCLONE_REMOTE), срок хранения там задаётся правилом хранилища.
# Cron на время акции, каждые 6 часов:
#   0 */6 * * * /opt/karavan/deploy/backup.sh >> /var/log/karavan/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
env_file="${ENV_FILE:-./.env}"
set -a; . "$env_file"; set +a

dir="${BACKUP_DIR:-/var/backups/karavan}"
keep="${KEEP_LOCAL:-8}"
mkdir -p "$dir"
file="$dir/promo-$(date +%Y%m%d-%H%M%S).dump"

# Нужно место хотя бы на два последних дампа: иначе переполненный диск остановит и саму базу.
last=$(ls -1t "$dir"/promo-*.dump 2>/dev/null | head -1 || true)
need_kb=$(( ${last:+$(du -k "$last" | cut -f1)}+0 ))
avail_kb=$(df -Pk "$dir" | awk 'NR==2 {print $4}')
if [ $((need_kb * 2)) -gt "$avail_kb" ]; then
  echo "$(date -Is) backup FAILED: свободно ${avail_kb} КБ, нужно больше $((need_kb * 2)) КБ" >&2
  exit 1
fi

# Пишем во временный файл: неполный дамп при сбое не подменит хороший.
docker compose exec -T db pg_dump -U promo -d promo -Fc > "$file.part"
mv "$file.part" "$file"
ls -1t "$dir"/promo-*.dump | tail -n +$((keep + 1)) | xargs -r rm --

if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$file" "$RCLONE_REMOTE"
fi
echo "$(date -Is) backup ok: $file ($(du -h "$file" | cut -f1))"
