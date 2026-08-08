# TezPOS — to‘liq o‘rnatish

## Talablar

- Python 3.10 yoki undan yuqori
- Internet (birinchi marta paketlar yuklash uchun)

---

## Windows

1. `tezpos-code.zip` ni oching (masalan `C:\tezpos_site`)
2. `install.bat` ni **ikki marta** bosing
3. Tugagach terminalda:

```bat
.venv\Scripts\activate
python manage.py runserver
```

Yoki birdan o‘rnatib ishga tushirish:

```bat
install.bat --run
```

Brauzer: http://127.0.0.1:8000

---

## Linux / macOS

```bash
unzip tezpos-code.zip -d tezpos_site
cd tezpos_site
bash install.sh
```

Keyin:

```bash
source .venv/bin/activate
python manage.py runserver
```

Yoki birdan:

```bash
bash install.sh --run
```

Brauzer: http://127.0.0.1:8000

---

## Admin (ixtiyoriy)

```bash
python manage.py createsuperuser
```

Keyin: http://127.0.0.1:8000/admin/

---

## Nima qiladi `install`?

1. `.venv` yaratadi  
2. `requirements.txt` dagi paketarni o‘rnatadi  
3. Lokal `.env` yaratadi (`DEBUG=1`, HTTPS o‘chiq)  
4. `migrate` qiladi  
5. `collectstatic` qiladi  

---

## Server (Contabo / production)

Lokal emas, domen + HTTPS uchun `DEPLOY.md` ga qarang.
