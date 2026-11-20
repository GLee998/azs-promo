#!/usr/bin/env bash
# Проверки скриптов развёртывания без контейнеров: docker подменяется заглушкой, которая пишет вызовы
# в журнал. Только «docker compose config» вызывает настоящий docker (разбор файла, без запуска).
# Запуск: bash deploy/tests/test_scripts.sh
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
deploy="$(dirname "$here")"
real_docker="$(command -v docker || true)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }

# Заглушка docker: pg_dump печатает «дамп», pg_restore --list принимает только файлы с сигнатурой PGDMP.
mkdir -p "$tmp/bin"
cat > "$tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
  *pg_dump*) printf 'PGDMP fake dump %s\n' "$(date +%s%N)" ;;
  *"pg_restore --list"*) head -c 5 | grep -q PGDMP ;;
  *pg_restore*) cat > /dev/null ;;
  *"select count(*)"*) echo 3 ;;
esac
EOF
chmod +x "$tmp/bin/docker"
export PATH="$tmp/bin:$PATH" DOCKER_LOG="$tmp/docker.log" ENV_FILE=/dev/null

# --- backup.sh: на сервере остаются только последние KEEP_LOCAL дампов (C1) ---
export BACKUP_DIR="$tmp/backups" KEEP_LOCAL=8
mkdir -p "$BACKUP_DIR"
for i in $(seq -w 1 20); do
  f="$BACKUP_DIR/promo-202611${i}-000000.dump"
  echo old > "$f"
  touch -d "$i hours ago" "$f"
done
"$deploy/backup.sh" > /dev/null 2>&1
check "backup keeps only the $KEEP_LOCAL newest dumps" '[ "$(ls "$BACKUP_DIR"/promo-*.dump | wc -l)" -eq 8 ]'
check "backup keeps the dump it just made" 'grep -q PGDMP "$(ls -t "$BACKUP_DIR"/promo-*.dump | head -1)"'

# --- backup.sh не начинает дамп, если свободно меньше двух размеров последнего дампа (C1) ---
cat > "$tmp/bin/df" <<'EOF'
#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/x 100 99 1 99%% /\n'
EOF
chmod +x "$tmp/bin/df"
dd if=/dev/zero of="$(ls -t "$BACKUP_DIR"/promo-*.dump | head -1)" bs=1k count=64 2>/dev/null
: > "$DOCKER_LOG"
"$deploy/backup.sh" > /dev/null 2>&1; rc=$?
check "backup refuses to dump when the disk is nearly full" '[ $rc -ne 0 ] && ! grep -q pg_dump "$DOCKER_LOG"'
rm "$tmp/bin/df"

# --- restore.sh: дамп проверяется до смены каталога и до удаления базы (I6) ---
: > "$DOCKER_LOG"
(cd "$tmp" && "$deploy/restore.sh" no-such.dump > /dev/null 2>&1); rc=$?
check "restore: missing dump → error, nothing dropped" '[ $rc -ne 0 ] && ! grep -q dropdb "$DOCKER_LOG"'
echo "not a dump" > "$tmp/bad.dump"
: > "$DOCKER_LOG"
(cd "$tmp" && "$deploy/restore.sh" bad.dump > /dev/null 2>&1); rc=$?
check "restore: unreadable dump → error, nothing dropped" '[ $rc -ne 0 ] && ! grep -q dropdb "$DOCKER_LOG"'
printf 'PGDMP good' > "$tmp/good.dump"
: > "$DOCKER_LOG"
(cd "$tmp" && "$deploy/restore.sh" good.dump promo --replace > /dev/null 2>&1); rc=$?
check "restore --replace: relative path works, working db renamed, never dropped" \
  '[ $rc -eq 0 ] && grep -q "RENAME TO promo" "$DOCKER_LOG" && ! grep -qE -- "--if-exists promo$" "$DOCKER_LOG"'

# --- docker-compose: лимиты тестового стенда не попадают в боевой API (I5) ---
if [ -n "$real_docker" ]; then
  mkdir -p "$tmp/c/deploy" "$tmp/c/backend"
  cp "$deploy/docker-compose.yml" "$deploy/Caddyfile" "$tmp/c/deploy/"
  printf '%s\n' POSTGRES_PASSWORD=x CLIENT_HOST=a.localhost KASSA_HOST=b.localhost ADMIN_HOST=c.localhost \
    TEST_RL_REG_IP=1000000 > "$tmp/c/deploy/.env"
  limits=$("$real_docker" compose -f "$tmp/c/deploy/docker-compose.yml" --profile test config --format json 2>/dev/null | python3 -c '
import json, sys
s = json.load(sys.stdin)["services"]
print(s["api"]["environment"].get("RL_REG_IP", "unset"), s["api-test"]["environment"].get("RL_REG_IP", "unset"))')
  check "TEST_RL_REG_IP raises only the test stand limit (api, api-test: $limits)" '[ "$limits" = "unset 1000000" ]'
else
  echo "skip compose check: docker not installed"
fi

[ $fail -eq 0 ] && echo OK
exit $fail
