@echo off
REM TezPOS — to'liq lokal o'rnatish (Windows)
REM Ishlatish: install.bat ni ikki marta bosing yoki cmd da: install.bat

setlocal EnableExtensions
cd /d "%~dp0"

echo ==^> TezPOS o'rnatish: %CD%

where python >nul 2>&1
if errorlevel 1 (
  echo XATO: python topilmadi. Avval Python 3.10+ o'rnating: https://www.python.org/downloads/
  echo O'rnatishda "Add python.exe to PATH" ni belgilang.
  pause
  exit /b 1
)

python --version

if not exist ".venv\Scripts\python.exe" (
  echo ==^> Virtualenv yaratilmoqda...
  python -m venv .venv
)

call .venv\Scripts\activate.bat

echo ==^> Paketlar o'rnatilmoqda...
python -m pip install -U pip
pip install -r requirements.txt

if not exist ".env" (
  echo ==^> .env yaratilmoqda ^(lokal mode^)...
  for /f "delims=" %%S in ('python -c "import secrets; print(secrets.token_urlsafe(50))"') do set SECRET=%%S
  (
    echo DEBUG=1
    echo SECRET_KEY=%SECRET%
    echo ALLOWED_HOSTS=127.0.0.1,localhost
    echo CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
    echo USE_HTTPS=0
    echo SECURE_SSL_REDIRECT=0
    echo TEZPOS_API_URL=http://127.0.0.1:8000
  ) > .env
) else (
  echo ==^> .env allaqachon bor — o'zgartirilmadi
)

if not exist media mkdir media
if not exist staticfiles mkdir staticfiles

echo ==^> Migratsiya...
python manage.py migrate --noinput

echo ==^> Static fayllar...
python manage.py collectstatic --noinput

echo.
echo ========================================
echo   O'rnatish tugadi!
echo   Serverni ishga tushirish:
echo     .venv\Scripts\activate
echo     python manage.py runserver
echo.
echo   Brauzer: http://127.0.0.1:8000
echo ========================================
echo.

if /i "%~1"=="--run" (
  echo ==^> Server ishga tushmoqda...
  python manage.py runserver 0.0.0.0:8000
) else (
  pause
)
