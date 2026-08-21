"""
Django settings for tezpos_site project.
Production: env orqali (.env) — DEBUG=0, SECRET_KEY, ALLOWED_HOSTS, domen.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-l=3snqxvp06c_(0wyyedsgg%w7n#ghtpbff*#613s%!)bj85q5",
)

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "ALLOWED_HOSTS",
        "localhost,127.0.0.1,[::1],tez-pos.uz,www.tez-pos.uz",
    ).split(",")
    if h.strip()
]
if DEBUG:
    ALLOWED_HOSTS = list({*ALLOWED_HOSTS, "*"})

CSRF_TRUSTED_ORIGINS = [
    o.strip().rstrip("/")
    for o in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        ",".join(
            [
                "http://localhost:8000",
                "http://127.0.0.1:8000",
                "https://tez-pos.uz",
                "https://www.tez-pos.uz",
                "http://tez-pos.uz",
                "http://www.tez-pos.uz",
            ]
        ),
    ).split(",")
    if o.strip()
]

CSRF_USE_SESSIONS = False
CSRF_COOKIE_HTTPONLY = False
# Har so‘rovda session yozish sekinlatadi — productionda o‘chirilgan
SESSION_SAVE_EVERY_REQUEST = DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# HTTPS (nginx + Let's Encrypt orqasida)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_HTTPS = env_bool("USE_HTTPS", False)
if USE_HTTPS:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "billing",
    "catalog",
    "sales",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "tezpos_site.slowlog_middleware.SlowRequestMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "tezpos_site.csrf_middleware.DynamicCsrfOriginMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "tezpos_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "tezpos_site.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "timeout": 20,
        },
        "ATOMIC_REQUESTS": False,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
# Production: hashed + compressed (brauzer uzoq kesh)
_static_backend = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
    if not DEBUG
    else "whitenoise.storage.CompressedStaticFilesStorage"
)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": _static_backend,
    },
}
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30  # 30 kun

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "accounts:cabinet"
LOGOUT_REDIRECT_URL = "landing"

# TezPOS desktop / API backend (kabinet shu manzildan ma'lumot oladi)
# TezPOS backend API — faqat Contabo IP (127.0.0.1 emas)
_api = (os.environ.get("TEZPOS_API_URL") or "http://13.140.146.78:8000").strip()
if "127.0.0.1" in _api or "localhost" in _api.lower() or "0.0.0.0" in _api:
    _api = "http://13.140.146.78:8000"
TEZPOS_API_URL = _api

# DevSMS — qarz/to'lov SMS (TezPOS bilan bir xil)
# Avvalo env, bo‘sh bo‘lsa repo ildizidagi devsms-token.txt
_devsms_env = os.environ.get("DEVSMS_TOKEN", "").strip()
if not _devsms_env:
    try:
        _tok_path = BASE_DIR / "devsms-token.txt"
        if _tok_path.is_file():
            _devsms_env = _tok_path.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    except OSError:
        _devsms_env = ""
DEVSMS_TOKEN = _devsms_env
# Bo‘sh = DevSMS default sender (TezPOS kabi). Noto‘g‘ri 4546 → Eskiz REJECTED bo‘lishi mumkin.
DEVSMS_FROM = os.environ.get("DEVSMS_FROM", "").strip()
# simple = tez kanal (odatda shablon moderatsiyasisiz); eskiz = brend SMS
DEVSMS_TYPE = (os.environ.get("DEVSMS_TYPE", "simple") or "simple").strip().lower()

# Smena Telegram sync (cron/systemd) — brauzersiz
TELEGRAM_CRON_SECRET = os.environ.get("TELEGRAM_CRON_SECRET", "").strip()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "tezpos.slow": {"handlers": ["console"], "level": "WARNING"},
        "tezpos.telegram": {"handlers": ["console"], "level": "INFO"},
    },
}
