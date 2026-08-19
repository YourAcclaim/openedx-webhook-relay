"""Minimal urlconf so Django admin views can be exercised in tests."""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
