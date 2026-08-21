"""SEO: robots.txt, sitemap.xml, Google Search Console HTML tasdiqlash."""
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

# Loyiha ildizidagi Google HTML tasdiqlash fayli (Search Console)
_GOOGLE_VERIFY_NAME = "googlee15cabc4e6a68bf3.html"


@require_GET
def robots_txt(_request):
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /accounts/cabinet/\n"
        "Disallow: /login/\n"
        "Disallow: /logout/\n"
        "Disallow: /dashboard/\n"
        "Disallow: /health/\n"
        "\n"
        "Sitemap: https://tez-pos.uz/sitemap.xml\n"
    )
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


@require_GET
def sitemap_xml(_request):
    body = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://tez-pos.uz/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://tez-pos.uz/login/</loc>
    <changefreq>monthly</changefreq>
    <priority>0.4</priority>
  </url>
</urlset>
"""
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


@require_GET
def google_site_verification(_request):
    """Google Search Console HTML fayl usuli — https://tez-pos.uz/<fayl>"""
    path = Path(settings.BASE_DIR) / _GOOGLE_VERIFY_NAME
    if not path.is_file():
        raise Http404("Verification file not found")
    return HttpResponse(
        path.read_text(encoding="utf-8"),
        content_type="text/html; charset=utf-8",
    )
