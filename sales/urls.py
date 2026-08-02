from django.urls import path

from .views import sales_page_view

app_name = "sales"

urlpatterns = [
    path("", sales_page_view, name="page"),
]
