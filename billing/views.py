from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.views import get_tenant_for_user
from .models import Plan


@login_required
def pricing_view(request):
    tenant = get_tenant_for_user(request.user)
    plans = Plan.objects.filter(is_active=True).order_by("monthly_price")
    subscription = getattr(tenant, "subscription", None)
    return render(
        request,
        "billing/pricing.html",
        {
            "tenant": tenant,
            "plans": plans,
            "subscription": subscription,
        },
    )
