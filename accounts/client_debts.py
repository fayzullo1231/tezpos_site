"""Mijoz qarzlari + DevSMS shablon."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import devsms
from .auth_views import SESSION_SERVER
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


def _render_sms(
    tpl: DebtSmsTemplate,
    *,
    amount,
    balance=None,
    name: str = "",
    note: str = "",
) -> str:
    shop = (tpl.shop_label or "").strip() or "TezPOS"
    note = (note or "").strip()
    if note and f"-{note}" not in shop:
        shop = f"{shop}-{note}"
    return devsms.build_debt_message(
        shop=shop,
        debt_amount=amount,
        balance=balance if balance is not None else amount,
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
    bal = row.balance()
    ledger = []
    if with_ledger:
        ledger = [_serialize_ledger(x) for x in row.ledger.all()[:limit]]
    return {
        "id": row.pk,
        "name": row.name,
        "phone": row.phone or "",
        "note": row.note or "",
        "balance": float(bal),
        "balance_display": _fmt_money(bal),
        "ledger": ledger,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def _serialize_template(row: DebtSmsTemplate) -> dict:
    preview = _render_sms(row, amount=1456000, name="Mijoz", note="Oziq ovqat")
    return {
        "id": row.pk,
        "title": row.title,
        "shop_label": row.shop_label,
        "body": row.body,
        "is_approved": bool(row.is_approved),
        "updated_display": _fmt_dt(row.updated_at),
        "preview": preview,
        "placeholders": ["{shop}", "{amount}", "{balance}", "{name}", "{note}"],
    }


@login_required
@require_GET
def cabinet_client_debts(request):
    shop = _shop(request)
    detail_id = request.GET.get("id")
    qs = ClientDebtor.objects.filter(shop_key=shop, is_active=True).order_by("name")
    if detail_id:
        try:
            row = qs.get(pk=int(detail_id))
        except (ClientDebtor.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "Topilmadi"}, status=404)
        return JsonResponse(
            {
                "ok": True,
                "debtor": _serialize_debtor(row, with_ledger=True, limit=300),
            }
        )

    rows = list(qs)
    total = Decimal("0")
    out = []
    for r in rows:
        bal = r.balance()
        if bal > 0:
            total += bal
        out.append(_serialize_debtor(r))
    out.sort(key=lambda x: x["balance"], reverse=True)
    return JsonResponse(
        {
            "ok": True,
            "debtors": out,
            "count": len(out),
            "total_debt": float(total),
            "total_display": _fmt_money(total),
        }
    )


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

    # Ixtiyoriy: birinchi qarz summasi
    amount = _dec(body.get("amount"))
    if amount > 0 and not sid:
        tenant = _tenant(request)
        who = tenant.business_name or request.user.username
        ClientDebtorLedger.objects.create(
            debtor=row,
            kind=ClientDebtorLedger.KIND_ADD,
            amount=amount,
            signed_amount=ClientDebtorLedger.sign_for(ClientDebtorLedger.KIND_ADD, amount),
            note=note,
            created_by=str(who)[:180],
        )

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
        # Tasdiqlangan matn: "{shop}:\nQarzdorligingiz : {amount} so‘m.\n…"
        text = _render_sms(
            tpl,
            amount=bal if bal > 0 else amount,
            balance=bal,
            name=debtor.name,
            note=note or debtor.note,
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
    if not shop_label:
        return JsonResponse({"error": "Do‘kon nomi kerak"}, status=400)

    tpl.shop_label = shop_label
    tpl.body = DebtSmsTemplate.DEFAULT_BODY
    tpl.is_approved = True
    tpl.save(update_fields=["shop_label", "body", "is_approved", "updated_at"])

    # Eskiz/DevSMS moderatsiyaga namuna matnni yuborish
    sample = devsms.sample_debt_template(shop_label)
    tpl_res = devsms.submit_template(sample)
    return JsonResponse(
        {
            "ok": True,
            "template": _serialize_template(tpl),
            "moderation": tpl_res,
            "sample_sms": sample,
        }
    )
