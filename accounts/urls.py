from django.urls import path

from .views import cabinet_view

app_name = "accounts"

urlpatterns = [
    path("cabinet/", cabinet_view, name="cabinet"),
]
