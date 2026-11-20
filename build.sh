#!/usr/bin/env bash
# Сборка одного из трёх сайтов: client (форма клиента), kassa (касса), admin (админка).
# Каждый сайт получает свою копию общих файлов и адреса из переменных окружения:
#   API_URL    — адрес API, например https://api.karavan.example
#   CLIENT_URL — адрес сайта клиента (нужен админке для QR на кассах)
#   DEMO       — true для тестового стенда (плашка «Тестовая версия», тестовые чеки), по умолчанию false
set -euo pipefail
app="${1:?usage: build.sh client|kassa|admin}"
demo="${DEMO:-false}"
case "$demo" in true|false) ;; *) echo "DEMO должен быть true или false" >&2; exit 1 ;; esac
out="dist/$app"
rm -rf "$out"
mkdir -p "$out"
cp -r "web/$app/." "$out/"
cp -r web/shared "$out/shared"
cat > "$out/shared/config.js" <<CFG
window.API_BASE = '${API_URL:-}';
window.CLIENT_URL = '${CLIENT_URL:-}';
window.DEMO = ${demo};
CFG
# Тесты (*.test.js) на сайт не попадают.
find "$out" -name '*.test.js' -delete
# Генератор тестовых чеков нужен только на тестовом стенде.
if [ "$demo" != true ]; then rm -f "$out/test-receipt.html" "$out/test-receipt.js"; fi
# Версия в адресах стилей и скриптов: после выкладки браузер не покажет старую копию из кэша.
ver="${RENDER_GIT_COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || date +%s)}"
ver="${ver:0:8}"
find "$out" -name '*.html' -exec sed -i -E "s#(href|src)=\"((shared/)?[A-Za-z0-9._/-]+\.(css|js))\"#\1=\"\2?v=$ver\"#g" {} +
echo "built $out"
