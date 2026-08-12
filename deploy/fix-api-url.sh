#!/usr/bin/env bash
# Contabo: API manzilini public IP ga qo'yish (127.0.0.1 EMAS) va saytni qayta ishga tushirish
#   bash /opt/tezpos_site/deploy/fix-api-url.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tezpos_site}"
API_URL="${TEZPOS_API_URL_FORCE:-http://13.140.146.78:8000}"

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ">>> .env yaratildi"
fi

# 127.0.0.1 / localhost ni olib tashlash — faqat Contabo API
if grep -q '^TEZPOS_API_URL=' .env; then
  sed -i "s|^TEZPOS_API_URL=.*|TEZPOS_API_URL=${API_URL}|" .env
else
  echo "TEZPOS_API_URL=${API_URL}" >> .env
fi

# Eski 127 qatorlari qolmasin
sed -i '/^TEZPOS_API_URL=http:\/\/127\.0\.0\.1/d' .env || true
sed -i '/^TEZPOS_API_URL=http:\/\/localhost/d' .env || true
if ! grep -q '^TEZPOS_API_URL=' .env; then
  echo "TEZPOS_API_URL=${API_URL}" >> .env
fi

echo "==> .env:"
grep '^TEZPOS_API_URL=' .env | head -1

echo "==> API tekshiruv (public IP):"
if curl -fsS --max-time 8 -o /dev/null -w "%{http_code}\n" "${API_URL}/admin/" ; then
  echo "API javob berdi."
else
  echo "WARNING: ${API_URL} ga ulanish muvaffaqiyatsiz. Backend (port 8000) ishlayotganini tekshiring."
fi

systemctl restart tezpos-site
sleep 1
systemctl --no-pager is-active tezpos-site || true
curl -sI http://127.0.0.1:8001/ | head -n 3 || true

echo "OK: TEZPOS_API_URL=${API_URL} (127.0.0.1 emas)"
