#!/usr/bin/env bash
# Serverda: kuloloptom-2 taminotchilar + boshlang'ich qoldiq
set -euo pipefail
cd /opt/tezpos_site
git fetch origin
git pull --ff-only origin main || true
source .venv/bin/activate
python manage.py import_suppliers_list --shop kuloloptom-2
echo OK
