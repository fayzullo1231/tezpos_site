#!/usr/bin/env bash
# Contabo da 502 ni tuzatish: root sifatida ishga tushiring
#   bash /opt/tezpos_site/deploy/fix-502.sh
# yoki hali clone bo'lmasa:
#   curl -fsSL https://raw.githubusercontent.com/fayzullo1231/tezpos_site/main/deploy/fix-502.sh | bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tezpos_site}"
REPO_URL="${REPO_URL:-https://github.com/fayzullo1231/tezpos_site.git}"
BRANCH="${BRANCH:-main}"

echo "==> 1) Loyiha"
if [[ ! -d "$APP_DIR/.git" ]]; then
  mkdir -p "$(dirname "$APP_DIR")"
  git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  cd "$APP_DIR"
  git fetch origin
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH" || true
fi
cd "$APP_DIR"

echo "==> 2) venv + paketlar"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

echo "==> 3) .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  SK=$(python -c "import secrets; print(secrets.token_urlsafe(50))")
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SK}|" .env
  # HTTP hali (SSL yo'q) — SSL redirect o'chiriladi
  sed -i "s|^USE_HTTPS=.*|USE_HTTPS=0|" .env || echo "USE_HTTPS=0" >> .env
  sed -i "s|^SECURE_SSL_REDIRECT=.*|SECURE_SSL_REDIRECT=0|" .env || echo "SECURE_SSL_REDIRECT=0" >> .env
  echo ">>> .env yaratildi — keyin TEZPOS_API_URL ni tekshiring."
fi
# 502 paytida HTTPS redirect gunicornni buzmasin
grep -q '^USE_HTTPS=' .env || echo 'USE_HTTPS=0' >> .env
grep -q '^SECURE_SSL_REDIRECT=' .env || echo 'SECURE_SSL_REDIRECT=0' >> .env

echo "==> 4) papkalar + migrate"
mkdir -p media staticfiles /var/log/tezpos_site
export DJANGO_SETTINGS_MODULE=tezpos_site.settings
python manage.py migrate --noinput
python manage.py collectstatic --noinput

echo "==> 5) huquqlar"
chown -R www-data:www-data "$APP_DIR" /var/log/tezpos_site

echo "==> 6) gunicorn systemd"
cp -f "$APP_DIR/deploy/tezpos-site.service" /etc/systemd/system/tezpos-site.service
systemctl daemon-reload
systemctl enable tezpos-site
systemctl restart tezpos-site
sleep 2

echo "==> 7) nginx → :8001"
if [[ ! -f /etc/nginx/sites-available/tez-pos.uz ]]; then
  cp "$APP_DIR/deploy/nginx-tez-pos.uz.http-only.conf" /etc/nginx/sites-available/tez-pos.uz
  ln -sf /etc/nginx/sites-available/tez-pos.uz /etc/nginx/sites-enabled/
  rm -f /etc/nginx/sites-enabled/default
fi
nginx -t && systemctl reload nginx

echo "==> 8) tekshiruv"
systemctl --no-pager status tezpos-site | head -n 20 || true
ss -lntp | grep 8001 || echo "!!! 8001 ochilmagan"
curl -sI http://127.0.0.1:8001/ | head -n 5 || true
journalctl -u tezpos-site -n 30 --no-pager || true

echo
echo "OK bo'lsa: curl -I http://tez-pos.uz/"
echo "Hali 502 bo'lsa: journalctl -u tezpos-site -n 80 --no-pager"
