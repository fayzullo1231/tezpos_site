"""TezPOS backend API client for shaxsiy kabinet."""
from __future__ import annotations

import gzip
import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlparse

from django.conf import settings


class TezPosApiError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


# Production TezPOS backend (Contabo). 127.0.0.1 / localhost hech qachon ishlatilmasin.
TEZPOS_BACKEND_DEFAULT = "http://13.140.146.78:8000"


def normalize_api_base(url: str | None = None) -> str:
    raw = (url or getattr(settings, "TEZPOS_API_URL", "") or "").strip()
    # .env dan kelgan qo‘shtirnoq / CRLF
    raw = raw.strip().strip('"').strip("'").replace("\r", "").strip()
    if not raw:
        raw = TEZPOS_BACKEND_DEFAULT
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw
    raw = raw.rstrip("/")
    # 127/localhost — sayt o‘zi emas, Contabo API
    lowered = raw.lower()
    if (
        "127.0.0.1" in lowered
        or "localhost" in lowered
        or "0.0.0.0" in lowered
    ):
        return TEZPOS_BACKEND_DEFAULT
    return raw


def _parse_error(body: bytes, status: int) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    # Django HTML 404/500 — foydalanuvchiga xom HTML chiqarmaslik
    lowered = text[:200].lower()
    if "<!doctype" in lowered or "<html" in lowered:
        if status == 404:
            return (
                f"API topilmadi (HTTP 404). TEZPOS_API_URL noto‘g‘ri yoki backendda "
                f"marshrut yo‘q. Hozirgi: {normalize_api_base()}"
            )
        return f"TezPOS API xato (HTTP {status}). Manzil: {normalize_api_base()}"
    try:
        data = json.loads(text)
    except Exception:
        # Qisqa matn; uzun HTML emas
        clean = " ".join(text.split())
        if len(clean) > 180:
            clean = clean[:177] + "..."
        return clean or f"HTTP {status}"
    if isinstance(data, dict):
        for key in ("detail", "login", "password", "server_name", "non_field_errors"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return str(val[0])
            if isinstance(val, str) and val.strip():
                return val
        for val in data.values():
            if isinstance(val, list) and val:
                return str(val[0])
            if isinstance(val, str) and val.strip():
                return val
    return text[:180] or f"HTTP {status}"


_tls = threading.local()


def _http_conn(base: str) -> tuple[http.client.HTTPConnection | http.client.HTTPSConnection, str]:
    """Keep-alive ulanish — har so‘rovda yangi TCP ochmaslik (WAN da muhim)."""
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    key = f"{parsed.scheme}://{host}:{port}"
    pool = getattr(_tls, "pool", None)
    if pool is None:
        pool = {}
        _tls.pool = pool
    conn = pool.get(key)
    if conn is None:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=30)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=30)
        pool[key] = conn
    return conn, key


def _decode_body(raw: bytes, headers: http.client.HTTPMessage) -> bytes:
    enc = (headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            return gzip.decompress(raw)
        except Exception:
            return raw
    return raw


def api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    server_name: str | None = None,
    body: dict | list | None = None,
    query: dict | None = None,
    timeout: float = 20,
) -> Any:
    base = normalize_api_base()
    path_q = path if path.startswith("/") else f"/{path}"
    if query:
        qs = urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None and v != ""},
            doseq=True,
        )
        if qs:
            path_q = f"{path_q}?{qs}"

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Connection": "keep-alive",
        "User-Agent": "TezPOS-Site-Cabinet/1.1",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Token {token}"
    if server_name:
        headers["X-Server-Name"] = server_name

    conn, pool_key = _http_conn(base)
    conn.timeout = timeout
    try:
        conn.request(method.upper(), path_q, body=data, headers=headers)
        resp = conn.getresponse()
        raw = _decode_body(resp.read(), resp.headers)
        status = resp.status
        ctype = (resp.getheader("Content-Type") or "").lower()
    except (TimeoutError, socket.timeout) as exc:
        # Ulanishni yangilash
        try:
            conn.close()
        except Exception:
            pass
        getattr(_tls, "pool", {}).pop(pool_key, None)
        raise TezPosApiError(
            f"TezPOS javob bermadi (timeout {timeout:.0f}s). Server: {normalize_api_base()}"
        ) from exc
    except (http.client.HTTPException, OSError) as exc:
        try:
            conn.close()
        except Exception:
            pass
        getattr(_tls, "pool", {}).pop(pool_key, None)
        # Bir marta urllib orqali qayta urinish
        return _api_request_urllib(
            method,
            path,
            token=token,
            server_name=server_name,
            body=body,
            query=query,
            timeout=timeout,
        )

    if status >= 400:
        raise TezPosApiError(_parse_error(raw, status), status=status, payload=raw)

    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "text/html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raise TezPosApiError(
            f"API HTML qaytardi (JSON emas). TEZPOS_API_URL={base} sayt emas, "
            f"TezPOS backend bo‘lishi kerak. Path: {path}",
            status=502,
            payload=raw[:200],
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TezPosApiError(
            f"API JSON emas. TEZPOS_API_URL={base}, path={path}",
            status=502,
            payload=raw[:200],
        ) from exc


def _api_request_urllib(
    method: str,
    path: str,
    *,
    token: str | None = None,
    server_name: str | None = None,
    body: dict | list | None = None,
    query: dict | None = None,
    timeout: float = 20,
) -> Any:
    """Fallback — keep-alive ishlamasa."""
    base = normalize_api_base()
    url = f"{base}{path}"
    if query:
        qs = urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None and v != ""},
            doseq=True,
        )
        if qs:
            url = f"{url}?{qs}"

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "User-Agent": "TezPOS-Site-Cabinet/1.1",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Token {token}"
    if server_name:
        headers["X-Server-Name"] = server_name

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            if not raw:
                return None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            text = raw.decode("utf-8", errors="replace")
            if "text/html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
                raise TezPosApiError(
                    f"API HTML qaytardi (JSON emas). TEZPOS_API_URL={base} sayt emas, "
                    f"TezPOS backend bo‘lishi kerak. Path: {path}",
                    status=502,
                    payload=raw[:200],
                )
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise TezPosApiError(
                    f"API JSON emas. TEZPOS_API_URL={base}, path={path}",
                    status=502,
                    payload=raw[:200],
                ) from exc
    except TezPosApiError:
        raise
    except urllib.error.HTTPError as exc:
        err_body = exc.read() if hasattr(exc, "read") else b""
        raise TezPosApiError(_parse_error(err_body, exc.code), status=exc.code, payload=err_body) from exc
    except TimeoutError as exc:
        raise TezPosApiError(
            f"TezPOS javob bermadi (timeout). Server: {normalize_api_base()}"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
            raise TezPosApiError(
                f"TezPOS javob bermadi (timeout). Server: {normalize_api_base()}"
            ) from exc
        raise TezPosApiError(
            f"TezPOS serverga ulanib bo'lmadi ({normalize_api_base()}). "
            f"Backend ishlayotganini tekshiring."
        ) from exc
    except OSError as exc:
        raise TezPosApiError(
            f"TezPOS serverga ulanib bo'lmadi ({normalize_api_base()}): {exc}"
        ) from exc


def login(server_name: str, login: str, password: str) -> dict:
    return api_request(
        "POST",
        "/api/auth/login/",
        body={
            "server_name": server_name.strip(),
            "login": login.strip(),
            "password": password,
        },
        timeout=15,
    )


def get_me(token: str, server_name: str) -> dict:
    return api_request("GET", "/api/auth/me/", token=token, server_name=server_name, timeout=10)


def get_products(
    token: str,
    server_name: str,
    *,
    max_pages: int = 80,
    timeout: float = 12,
    page_size: int = 100,
    all_at_once: bool = False,
    info: dict | None = None,
) -> list:
    """Barcha mahsulotlar. count yo‘q bo‘lsa ham oxirgi sahifagacha o‘qiydi."""
    results: list = []
    seen: set[str] = set()
    total = 0
    last_short = False

    def _rows(payload) -> list:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return list(
                payload.get("results")
                or payload.get("items")
                or payload.get("products")
                or payload.get("data")
                or []
            )
        return []

    def _count(payload) -> int:
        if not isinstance(payload, dict):
            return 0
        try:
            return int(payload.get("count") or payload.get("total") or 0)
        except (TypeError, ValueError):
            return 0

    def _add(chunk: list) -> None:
        for row in chunk:
            if not isinstance(row, dict):
                results.append(row)
                continue
            pid = str(row.get("id") or "")
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            results.append(row)

    def _fetch(query: dict, wait: float):
        return api_request(
            "GET",
            "/api/catalog/products/",
            token=token,
            server_name=server_name,
            query=query,
            timeout=wait,
        )

    def _done(n: int, page_len: int) -> bool:
        nonlocal last_short
        if page_len <= 0:
            last_short = True
            return True
        if total and n >= total:
            last_short = True
            return True
        # To‘liq sahifa — yana bor. Qisqa sahifa — oxiri.
        if page_len < size:
            last_short = True
            return True
        return False

    if all_at_once:
        try:
            data = _fetch({"all": "true"}, max(timeout, 60))
            rows = _rows(data)
            cnt = _count(data)
            if rows and (not cnt or len(rows) >= cnt):
                if info is not None:
                    info["total"] = cnt or len(rows)
                    info["complete"] = True
                return rows
            if rows:
                _add(rows)
                total = cnt
        except TezPosApiError as exc:
            if exc.status in (401, 403):
                raise

    size = max(20, min(int(page_size or 100), 200))
    data = None
    last_err: TezPosApiError | None = None
    first_len = 0
    if not results:
        for query in (
            {"page": "1", "page_size": str(size)},
            {"page": "1"},
        ):
            try:
                data = _fetch(query, timeout)
                chunk = _rows(data)
                if chunk:
                    _add(chunk)
                    total = _count(data)
                    first_len = len(chunk)
                    size = max(size, first_len)
                    break
            except TezPosApiError as exc:
                last_err = exc
                if exc.status in (401, 403):
                    raise
                continue
    else:
        first_len = len(results)

    if not results:
        if last_err:
            raise last_err
        if info is not None:
            info["total"] = 0
            info["complete"] = True
        return results

    page = 2
    deadline = time.time() + 80.0
    if _done(len(results), first_len) and (not total or len(results) >= total):
        page = max_pages + 1
    while page <= max_pages and time.time() < deadline:
        if total and len(results) >= total:
            last_short = True
            break
        try:
            page_data = _fetch({"page": str(page), "page_size": str(size)}, timeout)
        except TezPosApiError:
            break
        chunk = _rows(page_data)
        if not chunk:
            last_short = True
            break
        before = len(results)
        _add(chunk)
        if isinstance(page_data, dict):
            total = _count(page_data) or total
        if _done(len(results), len(chunk)):
            break
        if len(results) == before:
            last_short = True
            break
        page += 1

    if info is not None:
        info["total"] = total or len(results)
        info["complete"] = bool(
            last_short or (total and len(results) >= total) or page > max_pages
        )
    return results


def get_sales(
    token: str,
    server_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    timeout: float = 18,
    max_pages: int = 3,
) -> list:
    """Sotuvlar ro'yxati. all=true bilan pagination o'chiriladi."""
    data = api_request(
        "GET",
        "/api/sales/",
        token=token,
        server_name=server_name,
        query={
            "all": "true",
            "date_from": date_from,
            "date_to": date_to,
        },
        timeout=timeout,
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        results = list(data.get("results") or [])
        # Ba'zi sozlamalarda all=true ishlamasa — sahifalab yig'ish
        next_url = data.get("next")
        page = 2
        while next_url and page <= max_pages:
            page_data = api_request(
                "GET",
                "/api/sales/",
                token=token,
                server_name=server_name,
                query={
                    "page": page,
                    "date_from": date_from,
                    "date_to": date_to,
                },
                timeout=min(timeout, 15),
            )
            if isinstance(page_data, list):
                results.extend(page_data)
                break
            if not isinstance(page_data, dict):
                break
            chunk = page_data.get("results") or []
            results.extend(chunk)
            next_url = page_data.get("next")
            if not chunk:
                break
            page += 1
        return results
    return []


def get_sales_for_day(token: str, server_name: str, day: str) -> list:
    """Bitta kun uchun sotuvlar (tez)."""
    return get_sales(token, server_name, date_from=day, date_to=day, timeout=12, max_pages=1)


def get_sale(token: str, server_name: str, sale_id: str) -> dict:
    return api_request(
        "GET",
        f"/api/sales/{sale_id}/",
        token=token,
        server_name=server_name,
        timeout=5,
    )


def get_price_lists(token: str, server_name: str) -> list:
    data = api_request(
        "GET",
        "/api/catalog/price-lists/",
        token=token,
        server_name=server_name,
        timeout=12,
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get("results") or data.get("items") or [])
    return []


SHIFT_LIST_PATHS = (
    "/api/shifts/",
    "/api/accounts/shifts/",
    "/api/v1/shifts/",
    "/api/cash-shifts/",
    "/api/cash_shifts/",
    "/api/pos/shifts/",
    "/api/sales/shifts/",
    "/api/cash-sessions/",
    "/api/cash_sessions/",
)

_SHIFT_PATH_OK: str | None = None


def get_shifts(
    token: str,
    server_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    timeout: float = 12,
    max_pages: int = 2,
) -> list:
    """TezPOS smenalari. Ishlaydigan endpointni eslab qoladi."""
    global _SHIFT_PATH_OK
    paths = list(SHIFT_LIST_PATHS)
    if _SHIFT_PATH_OK and _SHIFT_PATH_OK in paths:
        paths = [_SHIFT_PATH_OK] + [p for p in paths if p != _SHIFT_PATH_OK]

    for path in paths:
        probe_timeout = timeout if path == _SHIFT_PATH_OK else min(timeout, 5.0)
        try:
            data = api_request(
                "GET",
                path,
                token=token,
                server_name=server_name,
                query={
                    "all": "true",
                    "date_from": date_from,
                    "date_to": date_to,
                },
                timeout=probe_timeout,
            )
        except TezPosApiError as exc:
            if exc.status in (404, 405):
                continue
            if exc.status in (401, 403):
                raise
            continue
        rows: list = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = list(
                data.get("results")
                or data.get("items")
                or data.get("shifts")
                or data.get("data")
                or []
            )
            next_url = data.get("next")
            page = 2
            while next_url and page <= max_pages:
                try:
                    page_data = api_request(
                        "GET",
                        path,
                        token=token,
                        server_name=server_name,
                        query={
                            "page": page,
                            "date_from": date_from,
                            "date_to": date_to,
                        },
                        timeout=probe_timeout,
                    )
                except TezPosApiError:
                    break
                if isinstance(page_data, list):
                    rows.extend(page_data)
                    break
                if not isinstance(page_data, dict):
                    break
                chunk = (
                    page_data.get("results")
                    or page_data.get("items")
                    or page_data.get("shifts")
                    or []
                )
                rows.extend(chunk)
                next_url = page_data.get("next")
                if not chunk:
                    break
                page += 1
        # 200 OK — yo‘l ishlaydi (bo‘sh ro‘yxat ham OK)
        _SHIFT_PATH_OK = path
        return [r for r in rows if isinstance(r, dict)]
    return []


def get_shift(token: str, server_name: str, shift_id: str) -> dict:
    """Bitta smena detali тАФ mavjud yo'llarni sinaydi."""
    sid = str(shift_id).strip()
    for path in (
        f"/api/shifts/{sid}/",
        f"/api/cash-shifts/{sid}/",
        f"/api/cash_shifts/{sid}/",
        f"/api/pos/shifts/{sid}/",
        f"/api/sales/shifts/{sid}/",
    ):
        try:
            data = api_request(
                "GET", path, token=token, server_name=server_name, timeout=12
            )
            if isinstance(data, dict):
                return data
        except TezPosApiError as exc:
            if exc.status in (404, 405):
                continue
            if exc.status in (401, 403):
                raise
            continue
    return {}


def get_daily_stats(token: str, server_name: str) -> dict:
    return api_request(
        "GET",
        "/api/sales/stats/daily/",
        token=token,
        server_name=server_name,
    ) or {}


def get_top_products(
    token: str,
    server_name: str,
    days: int = 30,
    limit: int = 100,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    query: dict = {"limit": limit}
    if date_from and date_to:
        query["date_from"] = date_from
        query["date_to"] = date_to
        query["from"] = date_from
        query["to"] = date_to
    else:
        query["days"] = days
    try:
        return (
            api_request(
                "GET",
                "/api/sales/stats/top-products/",
                token=token,
                server_name=server_name,
                query=query,
                timeout=30,
            )
            or {}
        )
    except TezPosApiError as exc:
        # Eski backendda endpoint bo'lmasligi mumkin
        if exc.status in (404, 405):
            return {"days": days, "items": []}
        raise


def get_customers(token: str, server_name: str) -> list:
    data = api_request(
        "GET",
        "/api/sales/customers/",
        token=token,
        server_name=server_name,
        query={"all": "true"},
        timeout=60,
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or []
    return []


def pay_customer_debt(
    token: str,
    server_name: str,
    customer_id: str,
    amount,
    *,
    payment_type: str = "cash",
    note: str = "",
    check_base_url: str | None = None,
) -> dict:
    body: dict = {
        "amount": str(amount),
        "payment_type": payment_type or "cash",
        "note": note or "",
        "send_sms": True,
        "sms": True,
        "notify": True,
        "notify_sms": True,
    }
    # Localhost SMS chekiga yaroqsiz тАФ faqat ochiq domen
    if check_base_url and "127.0.0.1" not in check_base_url and "localhost" not in check_base_url:
        body["check_base_url"] = check_base_url
        body["public_check_base"] = check_base_url
        body["site_url"] = check_base_url
    return api_request(
        "POST",
        f"/api/sales/customers/{customer_id}/pay-debt/",
        token=token,
        server_name=server_name,
        body=body,
        timeout=45,
    )


def send_sales_sms(
    token: str,
    server_name: str,
    *,
    phone: str,
    message: str,
    customer_id: str | None = None,
) -> dict:
    """
    DevSMS orqali SMS тАФ TezPOS /api/sales/sms/ endpointi.
    Backend maydon nomlari farq qilishi mumkin, shuning uchun bir necha variant sinanadi.
    """
    phone = (phone or "").strip()
    message = (message or "").strip()
    if not phone:
        return {"ok": False, "error": "Mijozda telefon raqam yoтАШq тАФ SMS yuborib boтАШlmaydi."}
    if not message:
        return {"ok": False, "error": "SMS matni boтАШsh."}

    payloads: list[dict] = [
        {"phone": phone, "message": message},
        {"phone": phone, "text": message},
        {"to": phone, "message": message},
        {"phone_number": phone, "message": message},
    ]
    if customer_id:
        payloads.extend(
            [
                {"customer_id": customer_id, "message": message, "phone": phone},
                {"customer": customer_id, "phone": phone, "message": message},
                {"customer_id": customer_id, "text": message},
            ]
        )

    last_err = "SMS yuborilmadi"
    for body in payloads:
        try:
            data = api_request(
                "POST",
                "/api/sales/sms/",
                token=token,
                server_name=server_name,
                body=body,
                timeout=30,
            )
            if isinstance(data, dict) and data.get("ok") is False:
                last_err = str(data.get("error") or data.get("detail") or last_err)
                continue
            return {"ok": True, "result": data, "payload_used": body}
        except TezPosApiError as exc:
            # 404/405 тАФ endpoint yo'q; boshqa payloadlar foydasiz
            if exc.status in (404, 405):
                return {"ok": False, "error": "Backendda SMS endpoint topilmadi (/api/sales/sms/)."}
            # 400 тАФ noto'g'ri maydon, keyingi variant
            detail = str(exc)
            if isinstance(exc.payload, (bytes, bytearray)):
                try:
                    payload = json.loads(exc.payload.decode("utf-8", errors="replace"))
                    if isinstance(payload, dict):
                        detail = str(
                            payload.get("detail")
                            or payload.get("error")
                            or payload.get("message")
                            or detail
                        )
                except Exception:
                    pass
            last_err = detail
            if exc.status in (401, 403):
                return {"ok": False, "error": detail}
            continue
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            continue
    return {"ok": False, "error": last_err}


def create_product(token: str, server_name: str, payload: dict) -> dict:
    return api_request(
        "POST",
        "/api/catalog/products/",
        token=token,
        server_name=server_name,
        body=payload,
        timeout=60,
    )


def update_product(token: str, server_name: str, product_id: str, payload: dict) -> dict:
    return api_request(
        "PATCH",
        f"/api/catalog/products/{product_id}/",
        token=token,
        server_name=server_name,
        body=payload,
        timeout=60,
    )
