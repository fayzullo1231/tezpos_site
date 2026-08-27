from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Python 3.14 + Django 5.1: admin template Context.__copy__ crash (ticket #35844)
        try:
            from tezpos_site.py314_patch import apply_django_py314_patches

            apply_django_py314_patches()
        except Exception:
            pass
