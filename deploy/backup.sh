#!/usr/bin/env bash
# Резервная копия базы: pg_dump в BACKUP_DIR (по умолчанию /var/backups/karavan), хранение 14 дней,
# копия вне сервера через rclone, если задан RCLONE_REMOTE.
# Cron на время акции, каждые 6 часов:
#   0 */6 * * * /opt/karavan/deploy/backup.sh >> /var/log/karavan-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a

dir="${BACKUP_DIR:-/var/backups/karavan}"
mkdir -p "$dir"
file="$dir/promo-$(date +%Y%m%d-%H%M).dump"

# Пишем во временный файл: неполный дамп при сбое не перезапишет и не подменит хороший.
docker compose exec -T db pg_dump -U promo -d promo -Fc > "$file.part"
mv "$file.part" "$file"
find "$dir" -name 'promo-*.dump' -mtime +14 -delete

if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$file" "$RCLONE_REMOTE"
fi
echo "$(date -Is) backup ok: $file ($(du -h "$file" | cut -f1))"
