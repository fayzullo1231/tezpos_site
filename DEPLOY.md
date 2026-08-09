# TezPOS — Contabo + tez-pos.uz + Google (bosqichma-bosqich)

Bu qo‘llanma: kompyuterdan GitHub → Contabo VPS → `https://tez-pos.uz` → Google qidiruv.

Repo: `https://github.com/fayzullo1231/tezpos_site.git`

---

## Umumiy tartib (5 bosqich)

```
1) DNS: tez-pos.uz → Contabo IP
2) Kompyuter: kodni GitHub ga push
3) Server: git clone/pull + .env + gunicorn + nginx + SSL
4) Backend API: TEZPOS_API_URL (desktop/backend)
5) Google: Search Console + sitemap
```

---

## 1-bosqich — DNS (domen Contabo ga)

Contabo server IP ni oling (serverda):

```bash
curl -4 ifconfig.me
```

Domen panelida (tez-pos.uz DNS):

| NAME | TYPE | RDATA |
|------|------|--------|
| `@` | A | **Contabo public IP** |
| `www` | CNAME | `tez-pos.uz` |

**Save Changes.** 5–60 daqiqa (ba’zan 24 soat) kutish mumkin.

Tekshiruv (Windows PowerShell):

```powershell
nslookup tez-pos.uz
```

Javob Contabo IP bo‘lishi kerak. Eski IP (`13.140.146.78` va hokazo) chiqsa — hali DNS yangilanmagan.

---

## 2-bosqich — Kompyuter: GitHub ga yuborish

Loyiha papkasida (`tezpos_site`):

```powershell
cd C:\Users\User\Documents\tezpos_site
git status
git add -A
git commit -m "Production: SEO, deploy ready"
git push origin main
```

Remote: `origin` → `https://github.com/fayzullo1231/tezpos_site.git`

Agar push so‘rasa login: GitHub Personal Access Token yoki `gh auth login`.

---

## 3-bosqich — Contabo: birinchi o‘rnatish

### 3.1 SSH

```bash
ssh root@CONTABO_IP
```

### 3.2 Paketlar

```bash
apt update && apt upgrade -y
apt install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw
```

### 3.3 Clone + deploy skript

```bash
export REPO_URL="https://github.com/fayzullo1231/tezpos_site.git"
bash -c "$(curl -fsSL https://raw.githubusercontent.com/fayzullo1231/tezpos_site/main/deploy/deploy.sh)" || true

# Yoki qo'lda:
git clone "$REPO_URL" /opt/tezpos_site
cd /opt/tezpos_site
REPO_URL="$REPO_URL" bash deploy/deploy.sh
```

Skript: venv, pip, migrate, collectstatic, gunicorn systemd (`:8001`).

### 3.4 `.env` ni to‘ldirish

```bash
nano /opt/tezpos_site/.env
```

Minimal:

```env
DEBUG=0
SECRET_KEY=juda-uzun-tasodifiy-kalit
ALLOWED_HOSTS=tez-pos.uz,www.tez-pos.uz
CSRF_TRUSTED_ORIGINS=https://tez-pos.uz,https://www.tez-pos.uz
USE_HTTPS=1
SECURE_SSL_REDIRECT=1
TEZPOS_API_URL=http://127.0.0.1:9000
DEVSMS_TOKEN=
```

**Muhim:** `TEZPOS_API_URL` — TezPOS **backend** (desktop API). Sayt o‘zi gunicorn da `8001` da. Backend boshqa portda bo‘lsa, shu URL ni yozing.

`SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Huquqlar:

```bash
chown -R www-data:www-data /opt/tezpos_site /var/log/tezpos_site
systemctl restart tezpos-site
```

Tekshiruv:

```bash
curl -I http://127.0.0.1:8001/
```

### 3.5 Nginx (HTTP → keyin SSL)

```bash
mkdir -p /var/www/certbot
cp /opt/tezpos_site/deploy/nginx-tez-pos.uz.http-only.conf /etc/nginx/sites-available/tez-pos.uz
ln -sf /etc/nginx/sites-available/tez-pos.uz /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

DNS Contabo IP ga qaraganini kutib:

```bash
certbot --nginx -d tez-pos.uz -d www.tez-pos.uz --redirect -m siz@email.com --agree-tos
```

To‘liq HTTPS conf (ixtiyoriy, certbot dan keyin):

```bash
cp /opt/tezpos_site/deploy/nginx-tez-pos.uz.conf /etc/nginx/sites-available/tez-pos.uz
nginx -t && systemctl reload nginx
```

Firewall:

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

Brauzer: **https://tez-pos.uz**

Admin (serverda yaratish):

```bash
cd /opt/tezpos_site
source .venv/bin/activate
python manage.py createsuperuser
```

---

## 4-bosqich — Har safar kod yangilash (git pull)

### Kompyuterda

```powershell
git add -A
git commit -m "..."
git push origin main
```

### Contabo da

```bash
cd /opt/tezpos_site
git pull origin main
# yoki bitta buyruq:
REPO_URL=https://github.com/fayzullo1231/tezpos_site.git bash deploy/deploy.sh
```

`deploy.sh` o‘zi: pull → pip → migrate → collectstatic → gunicorn restart.

---

## 5-bosqich — Google da «tez pos» chiqishi

Saytda tayyor: `robots.txt`, `sitemap.xml`, meta description / Open Graph.

### 5.1 Tekshiruv

Brauzerda oching:

- https://tez-pos.uz/robots.txt  
- https://tez-pos.uz/sitemap.xml  
- https://tez-pos.uz/ (title va description DevTools → Elements)

### 5.2 Google Search Console

1. https://search.google.com/search-console  
2. **Property qo‘shish** → `https://tez-pos.uz`  
3. DNS yoki HTML file orqali tasdiqlash  
4. **Sitemaps** → `https://tez-pos.uz/sitemap.xml` yuboring  
5. **URL inspection** → `https://tez-pos.uz/` → **Request indexing**

### 5.3 Kutish va SEO haqiqati

- Indekslash: odatda **bir necha kun** (ba’zan 1–2 hafta).  
- «tez pos» da **1-o‘rin** kafolat emas — raqobat, backlink, kontent kerak.  
- Yaxshilash: blog/sahifalar («TezPOS nima», «POS dasturi O‘zbekiston»), Google Business Profile, Telegram/Instagramda `tez-pos.uz` link.

### 5.4 Tez tekshiruv

```
site:tez-pos.uz
```

Google ga yozib qidirilsin — indeksga tushganini ko‘rsatadi.

---

## Telegram smena xabari (1 daqiqada)

Avval xabar faqat kabinet ochiq brauzerda tekshirilardi — shuning uchun kechikardi.

Endi:

1. Yopilishda **avval tezkor xabar**, keyin Excel  
2. Kabinetda har **30 soniya** tekshiruv  
3. Serverda **har 1 daqiqa** systemd timer (brauzer kerak emas)

```bash
cd /opt/tezpos_site
source .venv/bin/activate
python manage.py migrate --noinput

cp deploy/tezpos-telegram-sync.service /etc/systemd/system/
cp deploy/tezpos-telegram-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tezpos-telegram-sync.timer
systemctl list-timers | grep tezpos
```

Bir marta kabinetga **login** qiling (API token saqlanadi), bot yoqilgan bo‘lsin.

```bash
python manage.py sync_telegram_shifts
```

---

## Muammolar

| Belgi | Yechim |
|--------|--------|
| 502 Bad Gateway | `systemctl status tezpos-site` + `journalctl -u tezpos-site -n 50` |
| CSRF / login | `.env`: `CSRF_TRUSTED_ORIGINS=https://tez-pos.uz,...` |
| Static yo‘q | `collectstatic` + nginx `/static/` |
| SSL xato | DNS eski IP — `nslookup tez-pos.uz` |
| Kabinet API xato | `TEZPOS_API_URL` backendga to‘g‘ri |
| Permission denied | `chown -R www-data:www-data /opt/tezpos_site` |

---

## Qisqa checklist

- [ ] DNS A `@` → Contabo IP  
- [ ] `git push` GitHub `main`  
- [ ] Server: clone + `deploy/deploy.sh` + `.env`  
- [ ] nginx + certbot → HTTPS  
- [ ] `https://tez-pos.uz` ochiladi  
- [ ] Search Console + sitemap + Request indexing  

---

## Fayllar

| Fayl | Vazifa |
|------|--------|
| `deploy/deploy.sh` | clone/pull + venv + migrate + gunicorn |
| `deploy/tezpos-site.service` | systemd (gunicorn `:8001`) |
| `deploy/nginx-tez-pos.uz.conf` | HTTPS nginx |
| `deploy/nginx-tez-pos.uz.http-only.conf` | SSL oldidan HTTP |
| `.env.example` | production env shablon |
