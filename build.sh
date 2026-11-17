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
echo "built $out"
