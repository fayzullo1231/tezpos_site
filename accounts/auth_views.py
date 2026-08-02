"""TezPOS API orqali login / logout."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login as django_login, logout as django_logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import TenantProfile
from . import tezpos_api


User = get_user_model()

SESSION_TOKEN = "tezpos_token"
SESSION_SERVER = "tezpos_server_name"
SESSION_DISPLAY = "tezpos_display_name"
SESSION_ROLE = "tezpos_role"


def session_has_tezpos(request) -> bool:
    return bool(request.session.get(SESSION_TOKEN) and request.session.get(SESSION_SERVER))


def clear_tezpos_session(request) -> None:
    for key in (SESSION_TOKEN, SESSION_SERVER, SESSION_DISPLAY, SESSION_ROLE):
        request.session.pop(key, None)


def store_tezpos_session(request, *, token: str, server_name: str, user_payload: dict) -> None:
    request.session[SESSION_TOKEN] = token
    request.session[SESSION_SERVER] = server_name
    request.session[SESSION_DISPLAY] = (
        user_payload.get("display_name")
        or server_name
        or user_payload.get("username")
        or ""
    )
    request.session[SESSION_ROLE] = user_payload.get("role") or ""
    request.session.modified = True


def ensure_local_user(server_name: str, username: str, first_name: str = "") -> User:
    local_username = f"{server_name.strip().lower()}:{username.strip().lower()}"
    user, created = User.objects.get_or_create(
        username=local_username,
        defaults={
            "first_name": (first_name or username)[:150],
            "is_staff": False,
            "is_superuser": False,
        },
    )
    if not created and first_name and user.first_name != first_name:
        user.first_name = first_name[:150]
        user.save(update_fields=["first_name"])
    if created or not user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])
    TenantProfile.objects.update_or_create(
        user=user,
        defaults={"business_name": server_name},
    )
    return user


@csrf_exempt
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def tezpos_login(request):
    if request.user.is_authenticated and session_has_tezpos(request):
        return redirect("accounts:cabinet")

    # Session cookie CSRF uchun darhol yozilsin
    request.session.setdefault("_tezpos_csrf_boot", True)
    request.session.modified = True

    next_url = request.GET.get("next") or request.POST.get("next") or reverse("accounts:cabinet")
    ctx = {
        "default_server": request.session.get(SESSION_SERVER, ""),
        "default_login": "",
        "api_base": tezpos_api.normalize_api_base(),
        "next": next_url,
        "login_errors": [],
    }

    if request.method != "POST":
        return render(request, "registration/login.html", ctx)

    server_name = (request.POST.get("server_name") or "").strip()
    login_name = (request.POST.get("username") or request.POST.get("login") or "").strip()
    password = request.POST.get("password") or ""
    ctx["default_server"] = server_name
    ctx["default_login"] = login_name
    ctx["next"] = next_url

    errors = []
    if not server_name:
        errors.append("Server nomini kiriting (TezPOS dagi server nomi).")
    if not login_name:
        errors.append("Foydalanuvchi nomini kiriting.")
    if not password:
        errors.append("Parolni kiriting.")
    if errors:
        ctx["login_errors"] = errors
        return render(request, "registration/login.html", ctx)

    try:
        data = tezpos_api.login(server_name, login_name, password)
    except tezpos_api.TezPosApiError as exc:
        ctx["login_errors"] = [str(exc)]
        return render(request, "registration/login.html", ctx)

    user_payload = data.get("user") or {}
    token = data.get("token") or user_payload.get("api_token") or ""
    resolved_server = data.get("server_name") or user_payload.get("server_name") or server_name
    if not token:
        ctx["login_errors"] = ["TezPOS javobida token yo'q."]
        return render(request, "registration/login.html", ctx)

    local_user = ensure_local_user(
        resolved_server,
        user_payload.get("username") or login_name,
        first_name=user_payload.get("first_name") or "",
    )
    TenantProfile.objects.filter(user=local_user).update(
        business_name=user_payload.get("display_name") or resolved_server
    )
    django_login(request, local_user, backend="django.contrib.auth.backends.ModelBackend")
    store_tezpos_session(
        request,
        token=token,
        server_name=resolved_server,
        user_payload=user_payload,
    )
    messages.success(request, f"Xush kelibsiz, {user_payload.get('username') or login_name}!")
    return redirect(next_url)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def tezpos_logout(request):
    clear_tezpos_session(request)
    django_logout(request)
    return redirect("landing")
