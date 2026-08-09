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
# Contabo: bir serverda backend → http://127.0.0.1:8000 (.env da TEZPOS_API_URL)
TEZPOS_API_URL = os.environ.get("TEZPOS_API_URL", "http://127.0.0.1:8000")

# DevSMS — qarz/to'lov SMS (TezPOS bilan bir xil)
DEVSMS_TOKEN = os.environ.get("DEVSMS_TOKEN", "").strip()
DEVSMS_FROM = os.environ.get("DEVSMS_FROM", "4546").strip() or "4546"
