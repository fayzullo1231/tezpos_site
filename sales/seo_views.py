"""SEO: robots.txt va sitemap.xml (Google Search Console uchun)."""
from django.http import HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def robots_txt(_request):
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /accounts/cabinet/\n"
        "Disallow: /login/\n"
        "Disallow: /dashboard/\n"
        "\n"
        "Sitemap: https://tez-pos.uz/sitemap.xml\n"
    )
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


@require_GET
def sitemap_xml(_request):
    # Landing asosiy sahifa — Google indekslash uchun
    body = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://tez-pos.uz/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return HttpResponse(body, content_type="application/xml; charset=utf-8")
