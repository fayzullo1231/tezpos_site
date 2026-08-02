from django.urls import path

from .views import pricing_view

app_name = "billing"

urlpatterns = [
    path("pricing/", pricing_view, name="pricing"),
]
