"""
WSGI config for tezpos_site project.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tezpos_site.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

# Python 3.14 + Django 5.1 admin crash (ticket #35844)
from tezpos_site.py314_patch import apply_django_py314_patches

apply_django_py314_patches()
