"""Mijoz qarzlari + DevSMS shablon."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import time

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import devsms, tezpos_api
from .auth_views import SESSION_DISPLAY, SESSION_SERVER
from .models import ClientDebtor, ClientDebtorLedger, DebtSmsTemplate, TenantProfile


def _shop(request) -> str:
    server = (request.session.get(SESSION_SERVER) or "").strip()
    if server:
        return server.lower()
    return f"user:{request.user.pk}"


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value).replace(" ", "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, TypeError, ValueError):
        try:
            return Decimal(str(default)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0.00")


def _fmt_money(n) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.0f}".replace(",", " ")


def _fmt_dt(dt) -> str:
    if not dt:
        return ""
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")


def _tenant(request):
    tenant, _ = TenantProfile.objects.get_or_create(
        user=request.user,
        defaults={"business_name": request.user.get_full_name() or request.user.username},
    )
    return tenant


def _get_or_create_template(shop: str, tenant: TenantProfile | None = None) -> DebtSmsTemplate:
    row = DebtSmsTemplate.objects.filter(shop_key=shop).first()
    if row:
        return row
    shop_label = (tenant.business_name if tenant else "") or "TezPOS"
    return DebtSmsTemplate.objects.create(
        shop_key=shop,
        title=f"{shop_label} qarzdorlik",
        shop_label=shop_label,
        body=DebtSmsTemplate.DEFAULT_BODY,
        is_approved=True,
    )


def _split_shop_branch(label: str) -> tuple[str, str]:
    """'Kulol Optom - Oziq ovqat' yoki 'Kulol Optom-Oziq ovqat'."""
    text = (label or "").strip()
    if not text:
        return "Kulol Optom", "Oziq ovqat"
    if " - " in text:
        shop, branch = text.split(" - ", 1)
        return shop.strip() or "Kulol Optom", branch.strip()
    if "-" in text[1:]:
        shop, branch = text.split("-", 1)
        return shop.strip() or "Kulol Optom", branch.strip()
    return text, ""


def _canonical_shop_label(label: str) -> str:
    shop, branch = _split_shop_branch(label)
    return f"{shop} - {branch}" if branch else shop


def _resolve_sms_shop_branch(
    tpl: DebtSmsTemplate,
    *,
    note: str = "",
    request=None,
) -> tuple[str, str]:
    """Saqlangan do'kon nomi birinchi; bo'sh bo'lsa sessiyadan."""
    label = (tpl.shop_label or "").strip()
    if not label and request is not None:
        display = (request.session.get(SESSION_DISPLAY) or "").strip()
        uname = request.user.username or ""
        shop = uname.split(":", 1)[-1] if ":" in uname else (uname or "admin")
        label = f"{shop} - {display}" if display else shop
    shop, branch = _split_shop_branch(label)
    note = (note or "").strip()
    if note and not branch and note.lower() not in shop.lower():
        branch = note
    return shop, branch


def _render_sms(
    tpl: DebtSmsTemplate,
    *,
    amount,
    balance=None,
    name: str = "",
    note: str = "",
    check_link: str = "",
    request=None,
) -> str:
    shop, branch = _resolve_sms_shop_branch(tpl, note=note, request=request)
    return devsms.build_client_debt_message(
        shop=shop,
        branch=branch,
        transaction_amount=amount,
        balance=balance if balance is not None else amount,
        check_link=check_link or devsms.DEFAULT_CLIENT_CHECK,
    )


def _serialize_ledger(row: ClientDebtorLedger) -> dict:
    labels = dict(ClientDebtorLedger.KIND_CHOICES)
    return {
        "id": row.pk,
        "kind": row.kind,
        "kind_label": labels.get(row.kind, row.kind),
        "amount": float(row.amount),
        "amount_display": _fmt_money(row.amount),
        "signed_amount": float(row.signed_amount),
        "note": row.note or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "created_display": _fmt_dt(row.created_at),
        "sms_sent": bool(row.sms_sent),
        "tone": "red" if row.kind == ClientDebtorLedger.KIND_ADD else "green",
    }


def _serialize_debtor(row: ClientDebtor, *, with_ledger=False, limit=80) -> dict:
    bal = getattr(row, "bal", None)
    if bal is None:
        bal = row.balance()
    ledger = []
    if with_ledger:
        ledger = [_serialize_ledger(x) for x in row.ledger.all()[:limit]]
    return {
        "id": row.pk,
        "name": row.name,
        "phone": row.phone or "",
        "note": row.note or "",
        "balance": float(bal or 0),
        "balance_display": _fmt_money(bal or 0),
        "ledger": ledger,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def _debtors_qs(shop: str):
    return (
        ClientDebtor.objects.filter(shop_key=shop, is_active=True)
        .annotate(
            bal=Coalesce(
                Sum("ledger__signed_amount"),
                Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            )
        )
        .order_by("name")
    )


def _list_payload(shop: str) -> dict:
    rows = list(_debtors_qs(shop))
    total = Decimal("0")
    out = []
    for r in rows:
        item = _serialize_debtor(r)
        if item["balance"] > 0:
            total += Decimal(str(item["balance"]))
        out.append(item)
    out.sort(key=lambda x: x["balance"], reverse=True)
    return {
        "ok": True,
        "debtors": out,
        "count": len(out),
        "total_debt": float(total),
        "total_display": _fmt_money(total),
    }


def _apply_balance_target(row: ClientDebtor, target: Decimal, *, who: str, note: str = "") -> None:
    cur = row.balance()
    diff = (target - cur).quantize(Decimal("0.01"))
    if diff == 0:
        return
    kind = ClientDebtorLedger.KIND_ADD if diff > 0 else ClientDebtorLedger.KIND_SUB
    amt = abs(diff)
    ClientDebtorLedger.objects.create(
        debtor=row,
        kind=kind,
        amount=amt,
        signed_amount=ClientDebtorLedger.sign_for(kind, amt),
        note=note or "Qarz tahrirlandi",
        created_by=str(who)[:180],
    )


def _serialize_template(row: DebtSmsTemplate) -> dict:
    preview = _render_sms(row, amount=865000, balance=2140500, name="Mijoz")
    preview_credit = _render_sms(
        row,
        amount=0,
        balance=-2140500,
        name="Mijoz",
    )
    return {
        "id": row.pk,
        "title": row.title,
        "shop_label": row.shop_label,
        "body": row.body,
        "is_approved": bool(row.is_approved),
        "updated_display": _fmt_dt(row.updated_at),
        "preview": preview,
        "preview_credit": preview_credit,
        "placeholders": ["{shop}", "{amount}", "{balance}", "{check_link}"],
    }


@login_required
@require_GET
def cabinet_client_debts(request):
    shop = _shop(request)
    detail_id = request.GET.get("id")
    if detail_id:
        try:
            row = ClientDebtor.objects.get(
                pk=int(detail_id), shop_key=shop, is_active=True
            )
        except (ClientDebtor.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "Topilmadi"}, status=404)
        return JsonResponse(
            {
                "ok": True,
                "debtor": _serialize_debtor(row, with_ledger=True, limit=300),
            }
        )
    return JsonResponse(_list_payload(shop))


@login_required
@require_POST
def cabinet_client_debtor_save(request):
    shop = _shop(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    name = str(body.get("name") or "").strip()[:180]
    if not name:
        return JsonResponse({"error": "Mijoz ismi majburiy"}, status=400)
    phone = str(body.get("phone") or "").strip()[:40]
    note = str(body.get("note") or "").strip()[:180]
    sid = body.get("id")

    if sid:
        try:
            row = ClientDebtor.objects.get(pk=int(sid), shop_key=shop, is_active=True)
        except (ClientDebtor.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "Topilmadi"}, status=404)
        row.name = name
        row.phone = phone
        row.note = note
        row.save(update_fields=["name", "phone", "note", "updated_at"])
    else:
        row = ClientDebtor.objects.create(
            shop_key=shop, name=name, phone=phone, note=note
        )

    tenant = _tenant(request)
    who = tenant.business_name or request.user.username

    # Ixtiyoriy: birinchi qarz summasi
    amount = _dec(body.get("amount"))
    if amount > 0 and not sid:
        ClientDebtorLedger.objects.create(
            debtor=row,
            kind=ClientDebtorLedger.KIND_ADD,
            amount=amount,
            signed_amount=ClientDebtorLedger.sign_for(ClientDebtorLedger.KIND_ADD, amount),
            note=note,
            created_by=str(who)[:180],
        )

    if "debt" in body or "balance" in body:
        target = _dec(body.get("debt", body.get("balance")))
        _apply_balance_target(row, target, who=who)

    return JsonResponse({"ok": True, "debtor": _serialize_debtor(row, with_ledger=True)})


@login_required
@require_POST
def cabinet_client_debtor_delete(request):
    shop = _shop(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
        row = ClientDebtor.objects.get(
            pk=int(body.get("id")), shop_key=shop, is_active=True
        )
    except (json.JSONDecodeError, ClientDebtor.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "Topilmadi"}, status=404)
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def cabinet_client_debt_adjust(request):
    """Qarz qo‘shish yoki ayirish (ixtiyoriy SMS)."""
    shop = _shop(request)
    tenant = _tenant(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    kind = str(body.get("kind") or "add").strip().lower()
    if kind not in (ClientDebtorLedger.KIND_ADD, ClientDebtorLedger.KIND_SUB):
        return JsonResponse({"error": "Amal noto‘g‘ri"}, status=400)
    amount = _dec(body.get("amount"))
    if amount <= 0:
        return JsonResponse({"error": "Summa 0 dan katta bo‘lishi kerak"}, status=400)

    try:
        debtor = ClientDebtor.objects.get(
            pk=int(body.get("debtor_id")), shop_key=shop, is_active=True
        )
    except (ClientDebtor.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "Mijoz topilmadi"}, status=404)

    note = str(body.get("note") or "").strip()[:255]
    send_sms = bool(body.get("send_sms"))
    who = tenant.business_name or request.user.username

    with transaction.atomic():
        entry = ClientDebtorLedger.objects.create(
            debtor=debtor,
            kind=kind,
            amount=amount,
            signed_amount=ClientDebtorLedger.sign_for(kind, amount),
            note=note or debtor.note,
            created_by=str(who)[:180],
        )
        bal = debtor.balance()

    sms_res = None
    if send_sms and debtor.phone:
        tpl = _get_or_create_template(shop, tenant)
        text = _render_sms(
            tpl,
            amount=amount,
            balance=bal,
            name=debtor.name,
            note=note or debtor.note,
            request=request,
        )
        sms_res = devsms.send_dev_sms(phone=debtor.phone, message=text)
        if sms_res.get("ok"):
            entry.sms_sent = True
            entry.save(update_fields=["sms_sent"])

    return JsonResponse(
        {
            "ok": True,
            "entry": _serialize_ledger(entry),
            "debtor": _serialize_debtor(debtor, with_ledger=True, limit=120),
            "sms": sms_res,
        }
    )


@login_required
@require_GET
def cabinet_sms_template(request):
    shop = _shop(request)
    tenant = _tenant(request)
    tpl = _get_or_create_template(shop, tenant)
    return JsonResponse({"ok": True, "template": _serialize_template(tpl)})


@login_required
@require_POST
def cabinet_sms_template_save(request):
    shop = _shop(request)
    tenant = _tenant(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    tpl = _get_or_create_template(shop, tenant)
    shop_label = str(body.get("shop_label") or "").strip()[:180]
    if shop_label:
        tpl.shop_label = _canonical_shop_label(shop_label)
    tpl.body = DebtSmsTemplate.DEFAULT_BODY
    tpl.is_approved = True
    tpl.save(update_fields=["shop_label", "body", "is_approved", "updated_at"])

    label = tpl.shop_label or shop_label
    sample = devsms.sample_debt_template(label)
    credit_sample = devsms.sample_client_credit_template(label)
    # Moderatsiya API sekin — saqlashni bloklamaslik
    tpl_res = devsms.submit_template(sample, quick=True)
    credit_tpl_res = devsms.submit_template(credit_sample, quick=True)
    return JsonResponse(
        {
            "ok": True,
            "saved": True,
            "template": _serialize_template(tpl),
            "moderation": tpl_res,
            "moderation_credit": credit_tpl_res,
            "sample_sms": sample,
            "sample_sms_credit": credit_sample,
        }
    )


_SHOP_CACHE: dict[str, tuple[float, str]] = {}


def _cors(resp: JsonResponse) -> JsonResponse:
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, X-Server-Name, X-TezPOS-Token, X-TezPOS-Server"
    )
    resp["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _token_shop(request) -> str:
    auth = request.META.get("HTTP_AUTHORIZATION") or ""
    token = ""
    if auth.lower().startswith("token "):
        token = auth[6:].strip()
    elif auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    token = (
        token
        or (request.META.get("HTTP_X_TEZPOS_TOKEN") or "").strip()
    )
    server = (
        (request.META.get("HTTP_X_SERVER_NAME") or "")
        or (request.META.get("HTTP_X_TEZPOS_SERVER") or "")
        or (request.GET.get("server") or "")
    ).strip().lower()
    if not token:
        raise PermissionError("Token yo‘q")
    cache_key = token[:48]
    hit = _SHOP_CACHE.get(cache_key)
    if hit and hit[0] > time.time():
        return hit[1]
    shop = server
    try:
        me = tezpos_api.api_request(
            "GET",
            "/api/auth/me/",
            token=token,
            server_name=server,
            timeout=5,
        )
        if isinstance(me, dict):
            shop = str(
                me.get("server_name")
                or (me.get("tenant") or {}).get("server_name")
                or server
            ).strip().lower()
    except Exception:
        if not server:
            raise
        shop = server
    if not shop:
        raise PermissionError("Server topilmadi")
    _SHOP_CACHE[cache_key] = (time.time() + 300, shop)
    return shop


def _json_body(request) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


@csrf_exempt
@require_http_methods(["GET", "OPTIONS"])
def api_client_debts(request):
    if request.method == "OPTIONS":
        return _cors(JsonResponse({"ok": True}))
    try:
        shop = _token_shop(request)
    except PermissionError as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=401))
    except tezpos_api.TezPosApiError as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=exc.status or 401))
    detail_id = request.GET.get("id")
    if detail_id:
        try:
            row = ClientDebtor.objects.get(
                pk=int(detail_id), shop_key=shop, is_active=True
            )
        except (ClientDebtor.DoesNotExist, TypeError, ValueError):
            return _cors(JsonResponse({"error": "Topilmadi"}, status=404))
        return _cors(
            JsonResponse(
                {
                    "ok": True,
                    "debtor": _serialize_debtor(row, with_ledger=True, limit=200),
                }
            )
        )
    return _cors(JsonResponse(_list_payload(shop)))


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def api_client_debtor_save(request):
    if request.method == "OPTIONS":
        return _cors(JsonResponse({"ok": True}))
    try:
        shop = _token_shop(request)
    except PermissionError as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=401))
    body = _json_body(request)
    name = str(body.get("name") or "").strip()[:180]
    if not name:
        return _cors(JsonResponse({"error": "Mijoz ismi majburiy"}, status=400))
    phone = str(body.get("phone") or "").strip()[:40]
    note = str(body.get("note") or "").strip()[:180]
    sid = body.get("id")
    who = shop
    if sid:
        try:
            row = ClientDebtor.objects.get(pk=int(sid), shop_key=shop, is_active=True)
        except (ClientDebtor.DoesNotExist, TypeError, ValueError):
            return _cors(JsonResponse({"error": "Topilmadi"}, status=404))
        row.name = name
        row.phone = phone
        row.note = note
        row.save(update_fields=["name", "phone", "note", "updated_at"])
    else:
        row = ClientDebtor.objects.create(
            shop_key=shop, name=name, phone=phone, note=note
        )
    amount = _dec(body.get("amount"))
    if amount > 0 and not sid:
        ClientDebtorLedger.objects.create(
            debtor=row,
            kind=ClientDebtorLedger.KIND_ADD,
            amount=amount,
            signed_amount=ClientDebtorLedger.sign_for(
                ClientDebtorLedger.KIND_ADD, amount
            ),
            note=note,
            created_by=who[:180],
        )
    if "debt" in body or "balance" in body:
        _apply_balance_target(
            row, _dec(body.get("debt", body.get("balance"))), who=who
        )
    return _cors(
        JsonResponse({"ok": True, "debtor": _serialize_debtor(row, with_ledger=True)})
    )


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def api_client_debt_adjust(request):
    if request.method == "OPTIONS":
        return _cors(JsonResponse({"ok": True}))
    try:
        shop = _token_shop(request)
    except PermissionError as exc:
        return _cors(JsonResponse({"error": str(exc)}, status=401))
    body = _json_body(request)
    kind = str(body.get("kind") or "add").strip().lower()
    if kind not in (ClientDebtorLedger.KIND_ADD, ClientDebtorLedger.KIND_SUB):
        return _cors(JsonResponse({"error": "Amal noto‘g‘ri"}, status=400))
    amount = _dec(body.get("amount"))
    if amount <= 0:
        return _cors(JsonResponse({"error": "Summa 0 dan katta bo‘lishi kerak"}, status=400))
    try:
        debtor = ClientDebtor.objects.get(
            pk=int(body.get("debtor_id") or body.get("id")),
            shop_key=shop,
            is_active=True,
        )
    except (ClientDebtor.DoesNotExist, TypeError, ValueError):
        return _cors(JsonResponse({"error": "Mijoz topilmadi"}, status=404))
    note = str(body.get("note") or "").strip()[:255]
    send_sms = bool(body.get("send_sms"))
    with transaction.atomic():
        entry = ClientDebtorLedger.objects.create(
            debtor=debtor,
            kind=kind,
            amount=amount,
            signed_amount=ClientDebtorLedger.sign_for(kind, amount),
            note=note or debtor.note,
            created_by=shop[:180],
        )
        bal = debtor.balance()
    sms_res = None
    if send_sms and debtor.phone:
        tpl = _get_or_create_template(shop)
        check_link = f"https://tez-pos.uz/check/debt/{shop}/{entry.pk}/"
        text = _render_sms(
            tpl,
            amount=amount,
            balance=bal,
            name=debtor.name,
            note=note or debtor.note,
            check_link=check_link,
            request=request,
        )
        sms_res = devsms.send_dev_sms(phone=debtor.phone, message=text)
        if sms_res.get("ok"):
            entry.sms_sent = True
            entry.save(update_fields=["sms_sent"])
    return _cors(
        JsonResponse(
            {
                "ok": True,
                "entry": _serialize_ledger(entry),
                "debtor": _serialize_debtor(debtor, with_ledger=True, limit=80),
                "sms": sms_res,
                "check_url": f"https://tez-pos.uz/check/debt/{shop}/{entry.pk}/",
            }
        )
    )


@require_GET
def public_client_debt_check(request, shop, pk):
    from sales.public_check import _logo_url

    shop = str(shop or "").strip().lower()
    logo = _logo_url(request)
    try:
        entry = ClientDebtorLedger.objects.select_related("debtor").get(
            pk=int(pk), debtor__shop_key=shop
        )
    except (ClientDebtorLedger.DoesNotExist, TypeError, ValueError):
        return render(
            request,
            "sales/public_check.html",
            {
                "logo_url": logo,
                "title": "Chek topilmadi",
                "store_name": "TezPOS",
                "subtitle": "Elektron chek",
                "kind": "not_found",
                "empty_title": "Chek topilmadi",
                "empty_detail": shop,
                "meta_rows": [],
                "items": [],
                "is_payment": False,
                "show_debt": False,
            },
            status=404,
        )
    debtor = entry.debtor
    bal = debtor.balance()
    add = entry.kind == ClientDebtorLedger.KIND_ADD
    return render(
        request,
        "sales/public_check.html",
        {
            "logo_url": logo,
            "title": f"{debtor.name} — qarz",
            "store_name": shop,
            "subtitle": "Qarzga " + ("qo'shildi" if add else "to'landi"),
            "kind": "payment",
            "is_payment": True,
            "show_debt": True,
            "meta_rows": [
                {"label": "Sana", "value": _fmt_dt(entry.created_at)},
                {"label": "Mijoz", "value": debtor.name},
                {"label": "Telefon", "value": debtor.phone or "—"},
                {"label": "Turi", "value": "Qarz qo'shish" if add else "Qarz to'lovi"},
            ],
            "items": [],
            "total": "",
            "paid": "",
            "debt_amount": _fmt_money(entry.amount if add else -entry.amount),
            "debt_balance": _fmt_money(bal),
        },
    )
