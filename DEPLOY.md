# TezPOS sayt — Contabo + HTTPS (tez-pos.uz)

Bu qo‘llanma `tezpos_site` (landing + shaxsiy kabinet) ni Contabo VPS ga qo‘yish
va `https://tez-pos.uz` orqali ishlatish uchun.

---

## 0. DNS (rasimdagi panel)

Contabo serveringizning **public IP** sini oling (`curl -4 ifconfig.me` serverda).

| NAME | TYPE | RDATA |
|------|------|--------|
| `@` | A | **Contabo IP** (hozirgi `13.140.146.78` o‘rniga) |
| `www` | CNAME | `tez-pos.uz` (o‘zgartirish shart emas) |

**Save Changes** bosing. DNS 5–60 daqiqa (ba’zan 24 soat) tarqaladi.

Tekshiruv (kompyuterda):

```bash
nslookup tez-pos.uz
```

Javob Contabo IP bo‘lishi kerak.

---

## 1. GitHub (bir marta)

Loyiha allaqachon GitHub ga push qilingan bo‘lishi kerak. Repo URL ni saqlang, masalan:

`https://github.com/fayzullo1231/tezpos_site.git`

---

## 2. Contabo server — paketlar

```bash
ssh root@CONTABO_IP

apt update && apt upgrade -y
apt install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

---

## 3. Loyihani clone / pull

```bash
export REPO_URL="https://github.com/fayzullo1231/tezpos_site.git"
git clone "$REPO_URL" /opt/tezpos_site
cd /opt/tezpos_site
```

Keyingi yangilanishlar:

```bash
cd /opt/tezpos_site
git pull origin main
```

yoki:

```bash
REPO_URL=https://github.com/fayzullo1231/tezpos_site.git bash deploy/deploy.sh
```

---

## 4. Virtualenv + .env

```bash
cd /opt/tezpos_site
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

cp .env.example .env
nano .env
```

`.env` da muhimlar:

```env
DEBUG=0
SECRET_KEY=juda-uzun-tasodifiy-kalit
ALLOWED_HOSTS=tez-pos.uz,www.tez-pos.uz
CSRF_TRUSTED_ORIGINS=https://tez-pos.uz,https://www.tez-pos.uz
USE_HTTPS=1
TEZPOS_API_URL=http://127.0.0.1:8000
```

`SECRET_KEY` generatsiya:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

```bash
mkdir -p media staticfiles /var/log/tezpos_site
chown -R www-data:www-data /opt/tezpos_site /var/log/tezpos_site
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

---

## 5. Gunicorn (systemd)

```bash
cp /opt/tezpos_site/deploy/tezpos-site.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tezpos-site
systemctl status tezpos-site
```

Tekshiruv:

```bash
curl -I http://127.0.0.1:8001/
```

---

## 6. Nginx + HTTPS

**Birinchi** HTTP config (SSL sertifikat olish uchun):

```bash
mkdir -p /var/www/certbot
cp /opt/tezpos_site/deploy/nginx-tez-pos.uz.http-only.conf /etc/nginx/sites-available/tez-pos.uz
ln -sf /etc/nginx/sites-available/tez-pos.uz /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

DNS Contabo IP ga qaraganini kutib, SSL:

```bash
certbot --nginx -d tez-pos.uz -d www.tez-pos.uz --redirect -m siz@email.com --agree-tos -n
```

Yoki to‘liq HTTPS conf:

```bash
cp /opt/tezpos_site/deploy/nginx-tez-pos.uz.conf /etc/nginx/sites-available/tez-pos.uz
nginx -t && systemctl reload nginx
```

Ochilishi kerak: **https://tez-pos.uz**

---

## 7. Firewall

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

---

## 8. Keyingi deploy (kod yangilash)

Kompyuterda:

```bash
git add -A && git commit -m "..." && git push
```

Serverda:

```bash
cd /opt/tezpos_site
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
systemctl restart tezpos-site
```

---

## Muammolar

| Belgi | Yechim |
|--------|--------|
| 502 Bad Gateway | `systemctl status tezpos-site` — gunicorn ishlamayapti |
| CSRF / login xato | `.env` da `CSRF_TRUSTED_ORIGINS=https://tez-pos.uz,...` |
| Static yo‘q | `collectstatic` + nginx `/static/` |
| SSL xato | DNS hali eski IP ga qaragan — `nslookup tez-pos.uz` |
| Kabinet API xato | `TEZPOS_API_URL` ni to‘g‘ri backendga qo‘ying |

---

## Qisqa checklist

1. DNS A `@` → Contabo IP, Save  
2. GitHub dan `git clone` → `/opt/tezpos_site`  
3. `.env` + venv + migrate + collectstatic  
4. systemd gunicorn `:8001`  
5. nginx + certbot → HTTPS  
6. Brauzerda `https://tez-pos.uz`

---

## Tezlik / production tayyor

- Kabinet: `bot` / `qarzdorlar` API kutmasdan ochiladi; splash faqat birinchi kirishda
- Static: WhiteNoise Manifest + nginx `30d immutable`
- Gunicorn: 4 worker × 2 thread
- `.env`: `DEBUG=0`, `USE_HTTPS=1`, `TEZPOS_API_URL` (backend), `DEVSMS_TOKEN`

Yangilash:

```bash
REPO_URL=https://github.com/fayzullo1231/tezpos_site.git bash /opt/tezpos_site/deploy/deploy.sh
```
