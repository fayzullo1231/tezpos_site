"""Tez health-check — DB/Telegram/API chaqirmaydi."""
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health(_request):
    return JsonResponse({"ok": True, "service": "tezpos-site"})
