"""Taminotchilar — mahalliy CRUD, qarz/to‘lov tarixi, mahsulot biriktirish."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .auth_views import session_has_tezpos
from .models import Supplier, SupplierLedger, SupplierProduct


def _label_shop_key(request) -> str:
    from .auth_views import SESSION_SERVER

    server = (request.session.get(SESSION_SERVER) or "").strip()
    if server:
        return server.lower()
    return f"user:{request.user.pk}"


def get_tenant_for_user(user):
    from .models import TenantProfile

    tenant, _ = TenantProfile.objects.get_or_create(
        user=user, defaults={"business_name": user.get_full_name() or user.username}
    )
    return tenant


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


def _fmt_money(n: Decimal | float | int) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:,.0f}".replace(",", " ")


def _fmt_dt(dt) -> str:
    if not dt:
        return ""
    local = timezone.localtime(dt)
    return local.strftime("%d.%m.%Y %H:%M")


def _shop(request) -> str:
    return _label_shop_key(request)


def _serialize_product(row: SupplierProduct) -> dict:
    return {
        "id": row.pk,
        "product_id": row.product_id or "",
        "name": row.product_name,
    }


def _serialize_ledger(row: SupplierLedger) -> dict:
    labels = dict(SupplierLedger.KIND_CHOICES)
    if row.kind == SupplierLedger.KIND_WE_OWE:
        tone = "red"
    elif row.kind == SupplierLedger.KIND_THEY_OWE:
        tone = "green"
    elif row.kind == SupplierLedger.KIND_WE_PAY:
        tone = "blue"
    else:
        tone = "teal"
    return {
        "id": row.pk,
        "supplier_id": row.supplier_id,
        "supplier_name": getattr(row.supplier, "name", "") or "",
        "kind": row.kind,
        "kind_label": labels.get(row.kind, row.kind),
        "amount": float(row.amount),
        "amount_display": _fmt_money(row.amount),
        "signed_amount": float(row.signed_amount),
        "note": row.note or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "created_display": _fmt_dt(row.created_at),
        "tone": tone,
    }


def _serialize_supplier(
    row: Supplier, *, with_products=True, with_ledger=False, ledger_limit=40
) -> dict:
    bal = row.balance()
    we_owe = bal if bal > 0 else Decimal("0")
    they_owe = (-bal) if bal < 0 else Decimal("0")
    products = []
    if with_products:
        products = [_serialize_product(p) for p in row.products.all()]
    ledger = []
    if with_ledger:
        qs = row.ledger.all()[:ledger_limit]
        ledger = [_serialize_ledger(x) for x in qs]
    return {
        "id": row.pk,
        "name": row.name,
        "phone": row.phone or "",
        "note": row.note or "",
        "balance": float(bal),
        "balance_display": _fmt_money(abs(bal)),
        "we_owe": float(we_owe),
        "they_owe": float(they_owe),
        "status": "we_owe" if bal > 0 else ("they_owe" if bal < 0 else "clear"),
        "status_label": (
            "Biz qarzdamiz"
            if bal > 0
            else ("Taminotchi qarz" if bal < 0 else "Qarz yo‘q")
        ),
        "products": products,
        "products_count": len(products),
        "ledger": ledger,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def _summary_for_shop(shop_key: str) -> dict:
    suppliers = list(Supplier.objects.filter(shop_key=shop_key, is_active=True))
    we_owe = Decimal("0")
    they_owe = Decimal("0")
    for s in suppliers:
        bal = s.balance()
        if bal > 0:
            we_owe += bal
        elif bal < 0:
            they_owe += -bal
    return {
        "suppliers_count": len(suppliers),
        "we_owe": float(we_owe),
        "they_owe": float(they_owe),
        "we_owe_display": _fmt_money(we_owe),
        "they_owe_display": _fmt_money(they_owe),
        "net": float(we_owe - they_owe),
    }


@login_required
@require_GET
def cabinet_suppliers(request):
    if not session_has_tezpos(request) and not request.user.is_authenticated:
        return JsonResponse({"error": "auth"}, status=401)
    shop = _shop(request)
    detail_id = request.GET.get("id")
    qs = (
        Supplier.objects.filter(shop_key=shop, is_active=True)
        .prefetch_related(
            Prefetch("products", queryset=SupplierProduct.objects.all()),
        )
        .order_by("name")
    )
    if detail_id:
        try:
            row = qs.get(pk=int(detail_id))
        except (Supplier.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "Topilmadi"}, status=404)
        return JsonResponse(
            {
                "ok": True,
                "supplier": _serialize_supplier(
                    row, with_products=True, with_ledger=True, ledger_limit=1000
                ),
                "summary": _summary_for_shop(shop),
            }
        )

    rows = [_serialize_supplier(s, with_products=True, with_ledger=False) for s in qs]
    return JsonResponse(
        {
            "ok": True,
            "suppliers": rows,
            "count": len(rows),
            "summary": _summary_for_shop(shop),
        }
    )


@login_required
@require_GET
def cabinet_suppliers_summary(request):
    """Bosh sahifa KPI + so‘nggi harakatlar."""
    shop = _shop(request)
    summary = _summary_for_shop(shop)
    recent = (
        SupplierLedger.objects.filter(supplier__shop_key=shop)
        .select_related("supplier")
        .order_by("-created_at", "-id")[:60]
    )
    return JsonResponse(
        {
            "ok": True,
            "summary": summary,
            "recent": [_serialize_ledger(x) for x in recent],
        }
    )


@login_required
@require_GET
def cabinet_suppliers_history(request):
    """Barcha eski qarz/to‘lov yozuvlari (to‘langanlar ham)."""
    shop = _shop(request)
    supplier_id = request.GET.get("supplier_id")
    kind = str(request.GET.get("kind") or "").strip()
    try:
        limit = max(1, min(5000, int(request.GET.get("limit") or 2000)))
    except (TypeError, ValueError):
        limit = 2000
    qs = (
        SupplierLedger.objects.filter(supplier__shop_key=shop)
        .select_related("supplier")
        .order_by("-created_at", "-id")
    )
    if supplier_id:
        try:
            qs = qs.filter(supplier_id=int(supplier_id))
        except (TypeError, ValueError):
            pass
    if kind in {c[0] for c in SupplierLedger.KIND_CHOICES}:
        qs = qs.filter(kind=kind)
    elif kind == "debt":
        qs = qs.filter(
            kind__in=[SupplierLedger.KIND_WE_OWE, SupplierLedger.KIND_THEY_OWE]
        )
    elif kind == "pay":
        qs = qs.filter(
            kind__in=[SupplierLedger.KIND_WE_PAY, SupplierLedger.KIND_THEY_PAY]
        )
    rows = [_serialize_ledger(x) for x in qs[:limit]]
    return JsonResponse({"ok": True, "entries": rows, "count": len(rows)})


@login_required
@require_GET
def cabinet_suppliers_export(request):
    """Tanlangan (yoki barcha) taminotchilar qarzlari — Excel."""
    from io import BytesIO

    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    shop = _shop(request)
    ids_raw = str(request.GET.get("ids") or "").strip()
    id_list: list[int] = []
    if ids_raw:
        for part in ids_raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                id_list.append(int(part))
            except ValueError:
                continue

    suppliers_qs = Supplier.objects.filter(shop_key=shop, is_active=True).order_by("name")
    if id_list:
        suppliers_qs = suppliers_qs.filter(pk__in=id_list)
    suppliers = list(suppliers_qs)
    supplier_ids = [s.pk for s in suppliers]

    ledger_qs = (
        SupplierLedger.objects.filter(supplier_id__in=supplier_ids)
        .select_related("supplier")
        .order_by("supplier__name", "-created_at", "-id")
        if supplier_ids
        else SupplierLedger.objects.none()
    )

    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "Jami"
    ws_sum.append(
        ["#", "Taminotchi", "Telefon", "Holat", "Biz qarz", "Ular qarz", "Mahsulotlar"]
    )
    for cell in ws_sum[1]:
        cell.font = Font(bold=True)

    for i, s in enumerate(suppliers, start=1):
        bal = s.balance()
        we = float(bal) if bal > 0 else 0.0
        they = float(-bal) if bal < 0 else 0.0
        status = (
            "Biz qarzdamiz" if bal > 0 else ("Taminotchi qarz" if bal < 0 else "Qarz yo‘q")
        )
        products = ", ".join(p.product_name for p in s.products.all()[:40])
        ws_sum.append(
            [
                i,
                s.name,
                s.phone or "",
                status,
                we,
                they,
                products,
            ]
        )

    ws_led = wb.create_sheet("Tarix")
    ws_led.append(
        [
            "#",
            "Sana",
            "Soat",
            "Taminotchi",
            "Amal",
            "Summa",
            "Izoh",
            "Kim yozgan",
        ]
    )
    for cell in ws_led[1]:
        cell.font = Font(bold=True)

    labels = dict(SupplierLedger.KIND_CHOICES)
    for i, row in enumerate(ledger_qs, start=1):
        local = timezone.localtime(row.created_at) if row.created_at else None
        ws_led.append(
            [
                i,
                local.strftime("%d.%m.%Y") if local else "",
                local.strftime("%H:%M") if local else "",
                row.supplier.name if row.supplier_id else "",
                labels.get(row.kind, row.kind),
                float(row.amount),
                row.note or "",
                row.created_by or "",
            ]
        )

    for ws in (ws_sum, ws_led):
        ws.column_dimensions["A"].width = 6
        ws.column_dimensions["B"].width = 16
        ws.column_dimensions["C"].width = 14
        ws.column_dimensions["D"].width = 22
        ws.column_dimensions["E"].width = 14
        ws.column_dimensions["F"].width = 14
        ws.column_dimensions["G"].width = 36
        if ws is ws_led:
            ws.column_dimensions["H"].width = 18

    for col in ("E", "F"):
        for cell in ws_sum[col][1:]:
            cell.number_format = "#,##0.##"
            cell.alignment = Alignment(horizontal="right")
    for cell in ws_led["F"][1:]:
        cell.number_format = "#,##0.##"
        cell.alignment = Alignment(horizontal="right")

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
    fname = f"taminotchilar_{stamp}.xlsx"
    response = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{fname}"'
    return response


@login_required
@require_POST
def cabinet_supplier_save(request):
    shop = _shop(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    name = str(body.get("name") or "").strip()[:180]
    if not name:
        return JsonResponse({"error": "Taminotchi nomi majburiy"}, status=400)
    phone = str(body.get("phone") or "").strip()[:40]
    note = str(body.get("note") or "").strip()[:255]
    sid = body.get("id")

    if sid:
        try:
            row = Supplier.objects.get(pk=int(sid), shop_key=shop, is_active=True)
        except (Supplier.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"error": "Topilmadi"}, status=404)
        if (
            Supplier.objects.filter(shop_key=shop, name__iexact=name, is_active=True)
            .exclude(pk=row.pk)
            .exists()
        ):
            return JsonResponse({"error": "Bunday nom allaqachon bor"}, status=400)
        row.name = name
        row.phone = phone
        row.note = note
        row.save(update_fields=["name", "phone", "note", "updated_at"])
    else:
        if Supplier.objects.filter(
            shop_key=shop, name__iexact=name, is_active=True
        ).exists():
            return JsonResponse({"error": "Bunday nom allaqachon bor"}, status=400)
        row = Supplier.objects.create(
            shop_key=shop, name=name, phone=phone, note=note
        )

    return JsonResponse(
        {"ok": True, "supplier": _serialize_supplier(row, with_products=True)}
    )


@login_required
@require_POST
def cabinet_supplier_delete(request):
    shop = _shop(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)
    try:
        row = Supplier.objects.get(
            pk=int(body.get("id")), shop_key=shop, is_active=True
        )
    except (Supplier.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "Topilmadi"}, status=404)
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def cabinet_supplier_product(request):
    """Mahsulot biriktirish / olib tashlash (ixtiyoriy)."""
    shop = _shop(request)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    action = str(body.get("action") or "add").strip().lower()
    try:
        supplier = Supplier.objects.get(
            pk=int(body.get("supplier_id")), shop_key=shop, is_active=True
        )
    except (Supplier.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "Taminotchi topilmadi"}, status=404)

    if action == "remove":
        try:
            SupplierProduct.objects.filter(
                supplier=supplier, pk=int(body.get("id"))
            ).delete()
        except (TypeError, ValueError):
            return JsonResponse({"error": "ID noto‘g‘ri"}, status=400)
        return JsonResponse(
            {
                "ok": True,
                "supplier": _serialize_supplier(supplier, with_products=True),
            }
        )

    product_name = str(body.get("product_name") or body.get("name") or "").strip()[
        :220
    ]
    product_id = str(body.get("product_id") or "").strip()[:64]
    if not product_name:
        return JsonResponse({"error": "Mahsulot nomi kerak"}, status=400)

    if product_id and SupplierProduct.objects.filter(
        supplier=supplier, product_id=product_id
    ).exists():
        return JsonResponse(
            {"error": "Bu mahsulot allaqachon biriktirilgan"}, status=400
        )
    if SupplierProduct.objects.filter(
        supplier=supplier, product_name__iexact=product_name
    ).exists():
        return JsonResponse(
            {"error": "Bu mahsulot allaqachon biriktirilgan"}, status=400
        )

    SupplierProduct.objects.create(
        supplier=supplier,
        product_id=product_id,
        product_name=product_name,
    )
    return JsonResponse(
        {"ok": True, "supplier": _serialize_supplier(supplier, with_products=True)}
    )


@login_required
@require_POST
def cabinet_supplier_ledger(request):
    """Qarz yozish yoki to‘lov."""
    shop = _shop(request)
    tenant = get_tenant_for_user(request.user)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Noto‘g‘ri JSON"}, status=400)

    kind = str(body.get("kind") or "").strip()
    allowed = {c[0] for c in SupplierLedger.KIND_CHOICES}
    if kind not in allowed:
        return JsonResponse({"error": "Amal turi noto‘g‘ri"}, status=400)

    amount = _dec(body.get("amount"))
    if amount <= 0:
        return JsonResponse({"error": "Summa 0 dan katta bo‘lishi kerak"}, status=400)

    try:
        supplier = Supplier.objects.get(
            pk=int(body.get("supplier_id")), shop_key=shop, is_active=True
        )
    except (Supplier.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "Taminotchi topilmadi"}, status=404)

    note = str(body.get("note") or "").strip()[:255]
    who = (
        (tenant.business_name if tenant else "")
        or getattr(request.user, "get_full_name", lambda: "")()
        or request.user.username
    )

    with transaction.atomic():
        entry = SupplierLedger.objects.create(
            supplier=supplier,
            kind=kind,
            amount=amount,
            signed_amount=SupplierLedger.sign_for(kind, amount),
            note=note,
            created_by=str(who)[:180],
            created_at=timezone.now(),
        )

    return JsonResponse(
        {
            "ok": True,
            "entry": _serialize_ledger(entry),
            "supplier": _serialize_supplier(
                supplier, with_products=True, with_ledger=True, ledger_limit=1000
            ),
            "summary": _summary_for_shop(shop),
        }
    )
