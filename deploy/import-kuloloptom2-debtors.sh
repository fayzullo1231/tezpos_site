#!/usr/bin/env bash
# Serverda: qarzdorlar import (kuloloptom-2)
set -euo pipefail
cd /opt/tezpos_site
git fetch origin
git pull --ff-only origin main || true
source .venv/bin/activate
python manage.py import_debtors_list --shop kuloloptom-2
echo OK
