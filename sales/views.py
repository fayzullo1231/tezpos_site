from django.shortcuts import render

from accounts.auth_views import session_has_tezpos
from accounts.views import get_tenant_for_user
from billing.models import Plan


def sales_page_view(request):
    tenant = None
    if request.user.is_authenticated and session_has_tezpos(request):
        tenant = get_tenant_for_user(request.user)

    # Marketing landing — static demo raqamlar olib tashlandi
    highlights = []
    plans = Plan.objects.filter(is_active=True).order_by("monthly_price")[:3]
    return render(
        request,
        "sales/sales_page.html",
        {
            "tenant": tenant,
            "sales": [],
            "highlights": highlights,
            "plans": plans,
        },
    )
