#!/usr/bin/env bash
set -euo pipefail

APP=/opt/jetix
SRC="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_URL="${1:-}"

if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo"; exit 1; fi

echo "==> Пакеты"
apt-get update -qq
apt-get install -y -qq python3 python3-venv rsync openssl >/dev/null

echo "==> Пользователь jetix"
id jetix >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin jetix

echo "==> Код в $APP"
mkdir -p "$APP"
if [[ "$SRC" != "$APP" ]]; then
  rsync -a --delete \
    --exclude .git --exclude .github --exclude .env --exclude cookies.txt --exclude .venv --exclude .cache \
    "$SRC"/ "$APP"/
fi

echo "==> yt-dlp"
[[ -x "$APP/.venv/bin/python" ]] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --upgrade pip yt-dlp

echo "==> .env"
if [[ ! -f "$APP/.env" ]]; then
  cp "$APP/.env.example" "$APP/.env"
  sed -i "s|^SECRET=.*|SECRET=$(openssl rand -hex 12)|" "$APP/.env"
  [[ -n "$PUBLIC_URL" ]] && sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=${PUBLIC_URL%/}|" "$APP/.env"
  echo "    создан $APP/.env"
fi
mkdir -p "$APP/.cache"
chown -R jetix:jetix "$APP"
chmod 600 "$APP/.env"
if [[ -f "$APP/cookies.txt" ]]; then chmod 600 "$APP/cookies.txt"; fi

echo "==> systemd"
for unit in jetix.service jetix-ytdlp-update.service jetix-ytdlp-update.timer; do
  install -m 644 "$APP/deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now jetix-ytdlp-update.timer >/dev/null
systemctl enable jetix >/dev/null
systemctl restart jetix || true

SECRET=$(grep '^SECRET=' "$APP/.env" | cut -d= -f2-)
PORT=$(grep '^PORT=' "$APP/.env" | cut -d= -f2-)
cat <<EOF

Готово. Сервис слушает 127.0.0.1:${PORT:-18731}, наружу пока ничего не открыто.
  SECRET: $SECRET
  Дальше: заполнить $APP/.env, положить cookies.txt, подключить location
  /$SECRET/ к существующему веб-серверу (deploy/nginx-location.conf).
  Проверка: sudo -u jetix $APP/.venv/bin/python $APP/server/check.py
  Логи:     journalctl -u jetix -f
EOF
