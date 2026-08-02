"""TezPOS backend API client for shaxsiy kabinet."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings


class TezPosApiError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def normalize_api_base(url: str | None = None) -> str:
    raw = (url or getattr(settings, "TEZPOS_API_URL", "") or "").strip()
    if not raw:
        raw = "http://127.0.0.1:8000"
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw
    return raw.rstrip("/")


def _parse_error(body: bytes, status: int) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except Exception:
        return text.strip() or f"HTTP {status}"
    if isinstance(data, dict):
        for key in ("detail", "login", "password", "server_name", "non_field_errors"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return str(val[0])
            if isinstance(val, str) and val.strip():
                return val
        # first list/string error
        for val in data.values():
            if isinstance(val, list) and val:
                return str(val[0])
            if isinstance(val, str) and val.strip():
                return val
    return text.strip() or f"HTTP {status}"


def api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    server_name: str | None = None,
    body: dict | list | None = None,
    query: dict | None = None,
    timeout: float = 45,
) -> Any:
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
        "User-Agent": "TezPOS-Site-Cabinet/1.0",
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
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
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
    )


def get_me(token: str, server_name: str) -> dict:
    return api_request("GET", "/api/auth/me/", token=token, server_name=server_name)


def get_products(token: str, server_name: str) -> list:
    data = api_request(
        "GET",
        "/api/catalog/products/",
        token=token,
        server_name=server_name,
        query={"all": "true"},
        timeout=120,
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        results = list(data.get("results") or [])
        next_url = data.get("next")
        page = 2
        while next_url and page <= 60:
            page_data = api_request(
                "GET",
                "/api/catalog/products/",
                token=token,
                server_name=server_name,
                query={"page": page},
                timeout=60,
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


def get_sales(
    token: str,
    server_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    timeout: float = 60,
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
        while next_url and page <= 40:
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
                timeout=timeout,
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
    return get_sales(token, server_name, date_from=day, date_to=day, timeout=45)


def get_sale(token: str, server_name: str, sale_id: str) -> dict:
    return api_request(
        "GET",
        f"/api/sales/{sale_id}/",
        token=token,
        server_name=server_name,
        timeout=8,
    )


def get_daily_stats(token: str, server_name: str) -> dict:
    return api_request(
        "GET",
        "/api/sales/stats/daily/",
        token=token,
        server_name=server_name,
    ) or {}


def get_top_products(token: str, server_name: str, days: int = 30, limit: int = 100) -> dict:
    try:
        return (
            api_request(
                "GET",
                "/api/sales/stats/top-products/",
                token=token,
                server_name=server_name,
                query={"days": days, "limit": limit},
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
