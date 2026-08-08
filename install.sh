#!/usr/bin/env bash
# TezPOS — to'liq lokal o'rnatish (Linux / macOS)
# Ishlatish: bash install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> TezPOS o'rnatish: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "XATO: python3 topilmadi. Avval Python 3.10+ o'rnating."
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "==> Python: $PY_VER"

if [[ ! -d .venv ]]; then
  echo "==> Virtualenv yaratilmoqda..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Paketlar o'rnatilmoqda..."
python -m pip install -U pip
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  echo "==> .env yaratilmoqda (lokal mode)..."
  SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
  cat > .env <<EOF
DEBUG=1
SECRET_KEY=${SECRET}
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
USE_HTTPS=0
SECURE_SSL_REDIRECT=0
TEZPOS_API_URL=http://127.0.0.1:8000
EOF
else
  echo "==> .env allaqachon bor — o'zgartirilmadi"
fi

mkdir -p media staticfiles

echo "==> Migratsiya..."
python manage.py migrate --noinput

echo "==> Static fayllar..."
python manage.py collectstatic --noinput

echo ""
echo "========================================"
echo "  O'rnatish tugadi!"
echo "  Serverni ishga tushirish:"
echo "    source .venv/bin/activate"
echo "    python manage.py runserver"
echo ""
echo "  Brauzer: http://127.0.0.1:8000"
echo "========================================"
echo ""

if [[ "${1:-}" == "--run" ]]; then
  echo "==> Server ishga tushmoqda..."
  exec python manage.py runserver 0.0.0.0:8000
fi
