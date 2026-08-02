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

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ">>> .env yaratildi — SECRET_KEY va TEZPOS_API_URL ni to'ldiring, keyin qayta ishga tushiring."
fi

mkdir -p media staticfiles /var/log/tezpos_site
python manage.py migrate --noinput
python manage.py collectstatic --noinput

systemctl daemon-reload
systemctl enable tezpos-site
systemctl restart tezpos-site
systemctl reload nginx || true

echo "OK: https://tez-pos.uz (nginx + gunicorn)"
systemctl --no-pager status tezpos-site | head -n 15
