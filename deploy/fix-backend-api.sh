#!/usr/bin/env bash
# Contabo: TezPOS backend API (port 8000) ni tiklash
#   bash /opt/tezpos_site/deploy/fix-backend-api.sh
set -euo pipefail

API_URL="${TEZPOS_API_URL_FORCE:-http://13.140.146.78:8000}"

echo "==> 1) Port 8000 kim egallagan?"
ss -lntp | grep -E ':8000\b' || echo "(8000 ochiq emas — backend o'chiq)"

echo "==> 2) Mumkin bo'lgan servislar"
for s in tezpos-backend tezpos-api tezpos backend gunicorn; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${s}"; then
    echo "--- $s ---"
    systemctl --no-pager is-active "$s" || true
  fi
done
systemctl list-units --type=service --all 2>/dev/null | grep -iE 'tezpos|gunicorn|backend' || true

echo "==> 3) Backend kataloglari"
for d in /opt/tezpos-backend /opt/tezpos_backend /opt/tezpos /home/*/tezpos*; do
  if [[ -d "$d" ]]; then
    echo "Found: $d"
    ls -la "$d" 2>/dev/null | head -8 || true
  fi
done

echo "==> 4) Qayta ishga tushirish urinishlari"
restarted=0
for s in tezpos-backend tezpos-api tezpos; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${s}.service"; then
    systemctl restart "$s" && restarted=1 && echo "restarted: $s"
  fi
done

# Docker?
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -iE 'tezpos|8000|backend' || true
fi

echo "==> 5) API tekshiruv: ${API_URL}/admin/"
code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 8 "${API_URL}/admin/" || echo "000")
echo "HTTP $code"
if [[ "$code" == "000" ]]; then
  echo "FAIL: Backend API javob bermayapti. Contabo da tezpos-backend / port 8000 ni qo'lda yoqing."
  echo "Masalan:"
  echo "  cd /opt/tezpos-backend && source .venv/bin/activate"
  echo "  gunicorn ... --bind 0.0.0.0:8000"
  exit 1
fi
echo "OK: API ishlayapti (HTTP $code)"
