#!/usr/bin/env bash
# Tez yangilash: git pull + collectstatic + gunicorn restart
# Contabo VNC / SSH:
#   curl -fsSL https://raw.githubusercontent.com/fayzullo1231/tezpos_site/main/deploy/quick-update.sh | bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tezpos_site}"
BRANCH="${BRANCH:-main}"

cd "$APP_DIR"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput

systemctl restart tezpos-site
systemctl --no-pager is-active tezpos-site
git rev-parse --short HEAD
echo "OK: yangilandi"
