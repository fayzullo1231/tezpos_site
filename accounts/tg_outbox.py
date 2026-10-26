"""
Telegram yuborish navbati — fayl orqali.

Gunicorn yangi Telethon client ochmasin: session debt_group_listener da band.
Xabar var/tg-outbox/pending ga yoziladi, listener o‘zi yuboradi.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)


def _outbox_candidates() -> list[Path]:
    return [
        Path("/tmp/tezpos-tg-outbox"),
        Path(settings.BASE_DIR) / "var" / "tg-outbox",
    ]


def outbox_root() -> Path:
    last: OSError | None = None
    for root in _outbox_candidates():
        try:
            (root / "pending").mkdir(parents=True, exist_ok=True)
            (root / "fail").mkdir(parents=True, exist_ok=True)
            return root
        except OSError as exc:
            last = exc
    raise RuntimeError(f"Telegram navbat papkasi ochilmadi: {last}")


def write_job(job: dict[str, Any]) -> Path:
    root = outbox_root()
    pending = root / "pending"
    name = f"{time.time_ns()}_{uuid.uuid4().hex[:8]}.json"
    dest = pending / name
    tmp = dest.with_suffix(".tmp")
    payload = dict(job)
    payload.setdefault("created", time.time())
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dest)
    logger.info(
        "Telegram navbat: type=%s shop=%s file=%s",
        payload.get("type"),
        payload.get("shop"),
        dest.name,
    )
    return dest


def _shop_match(shop: str, allowed: set[str]) -> bool:
    if not shop:
        return False
    if shop in allowed:
        return True
    for a in allowed:
        if not a:
            continue
        if shop.startswith(a) or a.startswith(shop):
            return True
    return False


def take_jobs(shops: set[str]) -> list[tuple[Path, dict[str, Any]]]:
    """Shu sessiondagi do‘konlar uchun pending joblarni oladi."""
    close_old_connections()
    allowed = {str(s or "").strip().lower() for s in shops if s}
    out: list[tuple[Path, dict[str, Any]]] = []
    roots: list[Path] = []
    try:
        roots.append(outbox_root())
    except Exception:
        pass
    for extra in _outbox_candidates():
        if extra not in roots:
            roots.append(extra)
    for root in roots:
        pending = root / "pending"
        if not pending.is_dir():
            continue
        for path in sorted(pending.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Telegram navbat buzilgan: %s", path.name)
                continue
            shop = str(data.get("shop") or "").strip().lower()
            if shop and not _shop_match(shop, allowed):
                logger.warning(
                    "Telegram navbat shop mos emas file=%s shop=%s allowed=%s",
                    path.name,
                    shop,
                    ",".join(sorted(allowed)),
                )
                continue
            if not shop and allowed:
                continue
            proc = path.with_suffix(".run")
            try:
                path.replace(proc)
            except OSError:
                continue
            out.append((proc, data))
    return out


def mark_done(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def mark_fail(path: Path, error: str = "") -> None:
    root = outbox_root()
    dest = root / "fail" / (path.stem + ".json")
    try:
        if error:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            data["error"] = (error or "")[:300]
            dest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            path.unlink(missing_ok=True)
            return
        path.replace(dest)
    except OSError:
        logger.warning("Telegram navbat fail yozilmadi: %s", path)
