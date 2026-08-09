#!/usr/bin/env bash
# Contabo / Ubuntu: birinchi o'rnatish yoki git pull dan keyin yangilash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tezpos_site}"
REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"

if [[ ! -d "$APP_DIR/.git" ]]; then
  if [[ -z "$REPO_URL" ]]; then
    echo "Birinchi o'rnatish: REPO_URL=https://github.com/USER/tezpos_site.git $0"
    exit 1
  fi
  mkdir -p "$(dirname "$APP_DIR")"
  git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> Python venv"
# Eski buzilgan venv (pip yo'q) ni tozalash
if [[ ! -x .venv/bin/pip ]] && [[ ! -x .venv/bin/pip3 ]]; then
  rm -rf .venv
fi
python3 -m venv .venv || python3 -m venv --without-pip .venv

PY="$APP_DIR/.venv/bin/python"
if [[ ! -x "$APP_DIR/.venv/bin/pip" ]]; then
  echo "==> pip yo'q — get-pip.py bilan o'rnatiladi"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  "$PY" /tmp/get-pip.py
fi
PIP="$APP_DIR/.venv/bin/pip"

"$PIP" install -U pip
"$PIP" install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  SK=$("$PY" -c "import secrets; print(secrets.token_urlsafe(50))")
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SK}|" .env
  # SSL bo'lmaguncha
  sed -i "s|^USE_HTTPS=.*|USE_HTTPS=0|" .env || true
  sed -i "s|^SECURE_SSL_REDIRECT=.*|SECURE_SSL_REDIRECT=0|" .env || true
  grep -q '^USE_HTTPS=' .env || echo 'USE_HTTPS=0' >> .env
  grep -q '^SECURE_SSL_REDIRECT=' .env || echo 'SECURE_SSL_REDIRECT=0' >> .env
  echo ">>> .env yaratildi — TEZPOS_API_URL ni tekshiring (backend port)."
fi

mkdir -p media staticfiles /var/log/tezpos_site
chown -R www-data:www-data "$APP_DIR" /var/log/tezpos_site 2>/dev/null || true

export DJANGO_SETTINGS_MODULE=tezpos_site.settings
"$PY" manage.py migrate --noinput
"$PY" manage.py collectstatic --noinput

cp -f "$APP_DIR/deploy/tezpos-site.service" /etc/systemd/system/tezpos-site.service
systemctl daemon-reload
systemctl enable tezpos-site
systemctl restart tezpos-site
systemctl reload nginx || true

echo "OK: http://tez-pos.uz (nginx + gunicorn :8001)"
systemctl --no-pager status tezpos-site | head -n 15
ss -lntp | grep 8001 || true
curl -sI http://127.0.0.1:8001/ | head -n 5 || true
