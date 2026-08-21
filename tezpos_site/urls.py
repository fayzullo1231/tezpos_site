"""
URL configuration for tezpos_site project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from accounts.auth_views import tezpos_login, tezpos_logout
from sales.public_check import PublicReceiptCheckView
from sales.seo_views import google_site_verification, robots_txt, sitemap_xml
from sales.views import sales_page_view
from tezpos_site.health import health

urlpatterns = [
    path('admin/', admin.site.urls),
    path("health/", health, name="health"),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),
    path(
        "googlee15cabc4e6a68bf3.html",
        google_site_verification,
        name="google_site_verification",
    ),
    path("", sales_page_view, name="landing"),
    path(
        "check/<str:server_name>/<str:ref>/",
        PublicReceiptCheckView.as_view(),
        name="public-receipt-check",
    ),
    path(
        "check/<str:server_name>/<str:ref>",
        PublicReceiptCheckView.as_view(),
        name="public-receipt-check-noslash",
    ),
    path("accounts/", include("accounts.urls")),
    path("sales/", include("sales.urls")),
    path("billing/", include("billing.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("login/", tezpos_login, name="login"),
    path("logout/", tezpos_logout, name="logout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Production: nginx media ni beradi; fallback sifatida DEBUG=0 da ham media kerak bo'lsa:
    from django.views.static import serve
    from django.urls import re_path

    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
