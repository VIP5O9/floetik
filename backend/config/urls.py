from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from corpus.api import api

admin.site.site_header = "Floetik — Espace de publication"
admin.site.site_title = "Floetik"
admin.site.index_title = "Corpus"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
