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
            conn = http.client.HTTPSConnection(host, port, timeout=20)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=20)
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


def _server_slug(server_name: str) -> str:
    return "".join(ch for ch in (server_name or "").strip().lower() if ch.isalnum() or ch in "_-")


def _product_rows(payload) -> list:
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


def catalog_has_more(
    *,
    actual: int,
    requested: int = 100,
    has_next: bool = False,
    total: int = 0,
    loaded: int = 0,
) -> bool:
    """DRF ko‘pincha 20 ta qaytaradi — bu oxiri emas."""
    if actual <= 0:
        return False
    if has_next:
        return True
    if total and loaded < total:
        return True
    req = max(1, int(requested or 20))
    if actual >= req:
        return True
    if actual >= 20:
        return True
    return False


def get_product_count(token: str, server_name: str, timeout: float = 12) -> int:
    """TezPOS desktop: GET /api/catalog/products/count/"""
    try:
        data = api_request(
            "GET",
            "/api/catalog/products/count/",
            token=token,
            server_name=server_name,
            timeout=timeout,
        )
        if isinstance(data, dict):
            return int(data.get("count") or data.get("total") or 0)
        if isinstance(data, (int, float)):
            return int(data)
    except (TezPosApiError, TypeError, ValueError):
        pass
    return 0


def get_all_products(
    token: str,
    server_name: str,
    *,
    info: dict | None = None,
    timeout: float = 18,
) -> list:
    """Desktop TezPOS bilan bir xil: /{server}/product/, keyin ?all=true, keyin sahifa."""
    count = get_product_count(token, server_name)
    if info is not None:
        info["total"] = count
        info["complete"] = False
        info["source"] = ""

    def _enough(rows: list) -> bool:
        if not rows:
            return False
        if count and len(rows) >= max(1, min(count, int(count * 0.9))):
            return True
        # count yo‘q/200 — 200 ta sahifa to‘liq katalog emas
        if not count:
            return len(rows) >= 400
        return False

    slug = _server_slug(server_name)
    if slug:
        try:
            data = api_request(
                "GET",
                f"/{slug}/product/",
                token=token,
                server_name=server_name,
                timeout=timeout,
            )
            rows = data if isinstance(data, list) else _product_rows(data)
            if _enough(rows):
                if info is not None:
                    info["total"] = max(count, len(rows))
                    info["complete"] = True
                    info["source"] = "tenant"
                return rows
        except TezPosApiError as exc:
            if exc.status in (401, 403):
                raise

    try:
        data = api_request(
            "GET",
            "/api/catalog/products/",
            token=token,
            server_name=server_name,
            query={"all": "true"},
            timeout=timeout,
        )
        rows = data if isinstance(data, list) else _product_rows(data)
        if _enough(rows):
            if info is not None:
                info["total"] = max(count, len(rows))
                info["complete"] = True
                info["source"] = "all"
            return rows
    except TezPosApiError as exc:
        if exc.status in (401, 403):
            raise

    meta: dict = {}
    rows = get_products(
        token,
        server_name,
        max_pages=120,
        timeout=12,
        page_size=100,
        all_at_once=False,
        info=meta,
    )
    if info is not None:
        info["total"] = max(count, int(meta.get("total") or 0), len(rows or []))
        info["complete"] = bool(meta.get("complete")) or (count and len(rows or []) >= count)
        info["source"] = "pages"
    return rows or []


_CATALOG_SNAP: dict[str, tuple[float, list]] = {}
_CATALOG_SNAP_LOCK = threading.Lock()


def get_catalog_snapshot(token: str, server_name: str, timeout: float = 22) -> list:
    """Desktop kabi to‘liq katalog (tenant / all=true). 200 ta sahifa keshga yozilmaydi."""
    key = f"{_server_slug(server_name)}|{(token or '')[-20:]}"
    now = time.time()
    with _CATALOG_SNAP_LOCK:
        hit = _CATALOG_SNAP.get(key)
        if hit and now - hit[0] < 120 and len(hit[1]) > 200:
            return hit[1]

    count = get_product_count(token, server_name, timeout=min(8.0, timeout)) or 0

    def _ok(rows: list) -> bool:
        n = len(rows or [])
        if n <= 200:
            return False
        if count and n >= max(1, int(count * 0.85)):
            return True
        return n >= 400

    def _save(rows: list) -> list:
        with _CATALOG_SNAP_LOCK:
            _CATALOG_SNAP[key] = (time.time(), rows)
        return rows

    slug = _server_slug(server_name)
    if slug:
        try:
            data = api_request(
                "GET",
                f"/{slug}/product/",
                token=token,
                server_name=server_name,
                timeout=timeout,
            )
            rows = data if isinstance(data, list) else _product_rows(data)
            if _ok(rows):
                return _save(rows)
        except TezPosApiError as exc:
            if exc.status in (401, 403):
                raise

    try:
        data = api_request(
            "GET",
            "/api/catalog/products/",
            token=token,
            server_name=server_name,
            query={"all": "true"},
            timeout=timeout,
        )
        rows = data if isinstance(data, list) else _product_rows(data)
        if _ok(rows):
            return _save(rows)
    except TezPosApiError as exc:
        if exc.status in (401, 403):
            raise
    return []


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

    def _maybe_drop_capped_total(loaded: int, page_len: int) -> None:
        """TezPOS count=100/200 da kesiladi — to‘liq sahifada totalga ishonmaymiz."""
        nonlocal total
        if page_len >= size and total and total <= loaded and total % 50 == 0:
            total = 0

    def _done(n: int, page_len: int) -> bool:
        nonlocal last_short
        del n
        if page_len <= 0:
            last_short = True
            return True
        # To‘liq sahifa (20 yoki 100) — yana bor. Qisqa — oxiri.
        if page_len >= size:
            return False
        last_short = True
        return True

    if all_at_once:
        try:
            data = _fetch({"all": "true"}, timeout)
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
                    if 0 < first_len < size:
                        size = first_len
                    else:
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
    _maybe_drop_capped_total(len(results), first_len)
    if _done(len(results), first_len) and (not total or len(results) >= total):
        page = max_pages + 1
    while page <= max_pages and time.time() < deadline:
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
        _maybe_drop_capped_total(len(results), len(chunk))
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


def get_products_page(
    token: str,
    server_name: str,
    *,
    page: int = 1,
    page_size: int = 100,
    timeout: float = 45,
    retries: int = 3,
) -> dict:
    """Bitta katalog sahifasi — timeout bo‘lsa qayta urinib, to‘liq ro‘yxat yig‘iladi."""
    page = max(1, int(page or 1))
    size = max(20, min(int(page_size or 100), 100))
    offset = (page - 1) * size

    try:
        snap = get_catalog_snapshot(token, server_name, timeout=min(22.0, max(timeout, 12.0)))
    except (TezPosApiError, TimeoutError, OSError, socket.timeout):
        snap = []
    if len(snap) > 200:
        start = (page - 1) * size
        chunk = snap[start : start + size]
        return {
            "rows": chunk,
            "total": len(snap),
            "has_more": start + len(chunk) < len(snap),
            "page": page,
            "page_size": len(chunk) or size,
            "catalog_count": len(snap),
        }

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

    query = {
        "page": str(page),
        "page_size": str(size),
        "limit": str(size),
        "offset": str(offset),
    }
    data = None
    last_err: TezPosApiError | None = None
    for attempt in range(max(1, int(retries or 1))):
        try:
            data = api_request(
                "GET",
                "/api/catalog/products/",
                token=token,
                server_name=server_name,
                query=query,
                timeout=timeout,
            )
            last_err = None
            break
        except TezPosApiError as exc:
            last_err = exc
            if exc.status in (401, 403):
                raise
            if attempt + 1 < retries:
                time.sleep(0.4 * (attempt + 1))
                continue
    if data is None and last_err:
        raise last_err
    rows = _rows(data)
    total = _count(data)
    nxt = data.get("next") if isinstance(data, dict) else None
    actual = len(rows)
    if 0 < actual < size:
        size = actual
    loaded = (page - 1) * size + actual
    if actual >= size and total and total <= loaded and total % 50 == 0:
        total = 0
    has_more = catalog_has_more(
        actual=actual,
        requested=size,
        has_next=bool(nxt),
        total=total,
        loaded=loaded,
    )
    if actual == 0:
        has_more = False
    return {
        "rows": rows,
        "total": total,
        "has_more": has_more,
        "page": page,
        "page_size": actual if actual else size,
        "catalog_count": total,
    }


def _sale_rows(payload) -> list:
    """Desktop / DRF sotuv javobi: list yoki {results|items|sales}."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "items", "sales", "data"):
        val = payload.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            nested = val.get("results") or val.get("items") or val.get("sales")
            if isinstance(nested, list):
                return [r for r in nested if isinstance(r, dict)]
    return []


def _sale_total_hint(payload) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get("count") or payload.get("total") or 0)
    except (TypeError, ValueError):
        return 0


def get_sales(
    token: str,
    server_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    timeout: float = 18,
    max_pages: int = 60,
    try_all: bool = True,
) -> list:
    """Desktop kabi: /api/sales/?all=true&date_from=&date_to=, so‘ng sahifa.

    DRF 20 ta qaytarib next bermasa ham davom etadi. all=true list bo‘lsa — to‘liq.
    """
    max_pages = max(1, int(max_pages))
    collected: list = []
    seen: set[str] = set()

    def _absorb(rows: list) -> int:
        n = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("id") or row.get("uuid") or "").strip()
            if sid:
                if sid in seen:
                    continue
                seen.add(sid)
            collected.append(row)
            n += 1
        return n

    start_page = 1
    use_all = bool(try_all and (date_from or date_to))
    if use_all:
        try:
            data = api_request(
                "GET",
                "/api/sales/",
                token=token,
                server_name=server_name,
                query={
                    "all": "true",
                    "include_items": "true",
                    "with_items": "true",
                    "date_from": date_from,
                    "date_to": date_to,
                },
                timeout=timeout,
            )
        except TezPosApiError:
            data = None
        if isinstance(data, list):
            _absorb(data)
            return collected
        if isinstance(data, dict):
            chunk = _sale_rows(data)
            _absorb(chunk)
            has_next = bool(data.get("next"))
            total = _sale_total_hint(data)
            # 20 ta DRF sahifa emas: all=true to‘liq list
            complete = (not has_next) and (
                len(chunk) != 20 or (total > 0 and len(collected) >= total)
            )
            if complete or not catalog_has_more(
                actual=len(chunk),
                requested=max(len(chunk), 20),
                has_next=has_next,
                total=total,
                loaded=len(collected),
            ):
                return collected
            start_page = 2

    page = start_page
    while page <= max_pages:
        try:
            data = api_request(
                "GET",
                "/api/sales/",
                token=token,
                server_name=server_name,
                query={
                    "page": str(page),
                    "page_size": "100",
                    "include_items": "true",
                    "with_items": "true",
                    "date_from": date_from,
                    "date_to": date_to,
                },
                timeout=timeout if page == start_page else min(timeout, 12),
            )
        except TezPosApiError:
            break
        if isinstance(data, list):
            _absorb(data)
            break
        if not isinstance(data, dict):
            break
        chunk = _sale_rows(data)
        if not chunk:
            break
        added = _absorb(chunk)
        if added <= 0:
            break
        if not catalog_has_more(
            actual=len(chunk),
            requested=100,
            has_next=bool(data.get("next")),
            total=_sale_total_hint(data),
            loaded=len(collected),
        ):
            break
        page += 1
    return collected


def get_sales_for_day(token: str, server_name: str, day: str) -> list:
    """Bitta kun — barcha cheklar (desktop all=true)."""
    return get_sales(
        token,
        server_name,
        date_from=day,
        date_to=day,
        timeout=18,
        max_pages=80,
        try_all=True,
    )


def get_sale(token: str, server_name: str, sale_id: str) -> dict:
    return api_request(
        "GET",
        f"/api/sales/{sale_id}/",
        token=token,
        server_name=server_name,
        timeout=5,
    )


def get_price_lists(token: str, server_name: str) -> list:
    last: list = []
    for query in ({"all": "true"}, None):
        try:
            data = api_request(
                "GET",
                "/api/catalog/price-lists/",
                token=token,
                server_name=server_name,
                query=query,
                timeout=12,
            )
        except TezPosApiError:
            continue
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = list(data.get("results") or data.get("items") or [])
        else:
            rows = []
        if len(rows) > len(last):
            last = rows
        if len(rows) > 1:
            return rows
    return last


# Desktop TezPOS: /api/auth/shift/current/ va /api/auth/shift/history/
SHIFT_LIST_PATHS = (
    "/api/auth/shift/history/",
    "/api/auth/shifts/",
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


def _shift_rows(payload) -> list:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    shift = payload.get("shift")
    if isinstance(shift, dict) and (shift.get("id") or shift.get("opened_at")):
        return [shift]
    for key in ("results", "items", "shifts", "history", "data"):
        val = payload.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
        if isinstance(val, dict):
            inner = val.get("shift")
            if isinstance(inner, dict):
                return [inner]
            nested = val.get("results") or val.get("items")
            if isinstance(nested, list):
                return [r for r in nested if isinstance(r, dict)]
    return []


def merge_shift_summary(shift: dict, summary: dict | None) -> dict:
    """Desktop /api/auth/shift/current/ dagi summary ni smena obyektiga qo‘shadi."""
    if not isinstance(shift, dict):
        return {}
    if not isinstance(summary, dict) or not summary:
        return shift
    if shift.get("sales_count") in (None, "", 0) and summary.get("sales_count") is not None:
        shift["sales_count"] = summary.get("sales_count")
    if not shift.get("sales_total") and summary.get("sales_total") is not None:
        shift["sales_total"] = summary.get("sales_total")
    if not shift.get("total_sales") and summary.get("sales_total") is not None:
        shift["total_sales"] = summary.get("sales_total")
    return shift


def get_current_shift(token: str, server_name: str, timeout: float = 8) -> dict | None:
    """Desktop bilan bir xil: ochiq smena yoki None."""
    try:
        data = api_request(
            "GET",
            "/api/auth/shift/current/",
            token=token,
            server_name=server_name,
            timeout=timeout,
        )
    except TezPosApiError as exc:
        if exc.status in (401, 403):
            raise
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else None
    shift = data.get("shift")
    if isinstance(shift, dict) and (shift.get("id") or shift.get("opened_at")):
        return merge_shift_summary(shift, summary)
    if data.get("id") and (data.get("opened_at") or data.get("status")):
        return merge_shift_summary(data, summary)
    return None


def get_shifts(
    token: str,
    server_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    timeout: float = 12,
    max_pages: int = 20,
) -> list:
    """Haqiqiy TezPOS smenalari. Bo‘sh /api/shifts/ ni 'topildi' deb eshlamaydi."""
    global _SHIFT_PATH_OK
    by_id: dict[str, dict] = {}
    order: list[str] = []
    max_pages = max(1, int(max_pages))

    def _add(rows: list) -> None:
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or raw.get("uuid") or "").strip()
            if not sid:
                opened = str(raw.get("opened_at") or raw.get("created_at") or "")
                sid = opened or str(len(by_id))
            if sid not in by_id:
                order.append(sid)
            by_id[sid] = raw

    current = get_current_shift(token, server_name, timeout=min(timeout, 8))
    if current:
        _add([current])

    paths = list(SHIFT_LIST_PATHS)
    if _SHIFT_PATH_OK and _SHIFT_PATH_OK in paths:
        paths = [_SHIFT_PATH_OK] + [p for p in paths if p != _SHIFT_PATH_OK]
    desktop_paths = {"/api/auth/shift/history/", "/api/auth/shifts/"}

    for path in paths:
        if path == _SHIFT_PATH_OK or path in desktop_paths:
            probe_timeout = timeout
        else:
            probe_timeout = min(timeout, 2.2)
        query = {
            "all": "true",
            "date_from": date_from,
            "date_to": date_to,
        }
        try:
            data = api_request(
                "GET",
                path,
                token=token,
                server_name=server_name,
                query=query,
                timeout=probe_timeout,
            )
        except TezPosApiError as exc:
            if exc.status in (404, 405):
                continue
            if exc.status in (401, 403):
                raise
            continue
        rows = _shift_rows(data)
        # all=true to‘liq list — sahifa yo‘q
        if isinstance(data, list):
            if not rows:
                continue
            _SHIFT_PATH_OK = path
            _add(rows)
            break
        last_n = len(rows)
        has_next = bool(data.get("next")) if isinstance(data, dict) else False
        total = _sale_total_hint(data) if isinstance(data, dict) else 0
        page = 2
        need_pages = catalog_has_more(
            actual=last_n,
            requested=20,
            has_next=has_next,
            total=total,
            loaded=len(rows),
        )
        if last_n > 20 and not has_next:
            need_pages = False
        while need_pages and page <= max_pages:
            try:
                page_data = api_request(
                    "GET",
                    path,
                    token=token,
                    server_name=server_name,
                    query={
                        "page": page,
                        "all": "true",
                        "date_from": date_from,
                        "date_to": date_to,
                    },
                    timeout=probe_timeout,
                )
            except TezPosApiError:
                break
            chunk = _shift_rows(page_data)
            if not chunk:
                break
            rows.extend(chunk)
            last_n = len(chunk)
            has_next = bool(page_data.get("next")) if isinstance(page_data, dict) else False
            if isinstance(page_data, dict):
                total = max(total, _sale_total_hint(page_data))
            need_pages = catalog_has_more(
                actual=last_n,
                requested=20,
                has_next=has_next,
                total=total,
                loaded=len(rows),
            )
            page += 1
        if not rows:
            # Bo‘sh 200 — bu yo‘l smena API emas (masalan /api/shifts/)
            continue
        _SHIFT_PATH_OK = path
        _add(rows)
        break

    return [by_id[sid] for sid in order]


def get_shift(token: str, server_name: str, shift_id: str) -> dict:
    """Bitta smena detali тАФ mavjud yo'llarni sinaydi."""
    sid = str(shift_id).strip()
    for path in (
        f"/api/auth/shift/{sid}/",
        f"/api/auth/shifts/{sid}/",
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
