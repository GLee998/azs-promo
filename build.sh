#!/usr/bin/env bash
# Сборка одного из трёх сайтов: client (форма клиента), kassa (касса), admin (админка).
# Каждый сайт получает свою копию общих файлов и адреса из переменных окружения:
#   API_URL    — адрес API, например https://api.karavan.example
#   CLIENT_URL — адрес сайта клиента (нужен админке для QR на кассах)
set -euo pipefail
app="${1:?usage: build.sh client|kassa|admin}"
out="dist/$app"
rm -rf "$out"
mkdir -p "$out"
cp -r "web/$app/." "$out/"
cp -r web/shared "$out/shared"
cat > "$out/shared/config.js" <<CFG
window.API_BASE = '${API_URL:-}';
window.CLIENT_URL = '${CLIENT_URL:-}';
CFG
# Версия в адресах стилей и скриптов: после выкладки браузер не покажет старую копию из кэша.
ver="${RENDER_GIT_COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || date +%s)}"
ver="${ver:0:8}"
find "$out" -name '*.html' -exec sed -i -E "s#(href|src)=\"((shared/)?[a-z0-9-]+\.(css|js))\"#\1=\"\2?v=$ver\"#g" {} +
echo "built $out"
