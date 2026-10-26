"""Gunicorn → local listener (127.0.0.1:18765). Session listenerda ochiq qoladi."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

LISTENER_URL = "http://127.0.0.1:18765/send"


def send_via_listener(job: dict[str, Any], timeout: float = 40) -> dict[str, Any]:
    raw = json.dumps(job, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(
        LISTENER_URL,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body else {}
            if not isinstance(data, dict):
                return {"ok": False, "error": "listener javobi noto‘g‘ri"}
            return data
    except urllib.error.HTTPError as exc:
        err = ""
        try:
            err = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            err = str(exc)
        try:
            data = json.loads(err) if err else {}
            if isinstance(data, dict) and (data.get("error") or "ok" in data):
                return data
        except Exception:
            pass
        return {"ok": False, "error": err or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "offline": True}
