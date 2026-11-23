"""
URL configuration for rosterchief project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
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
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from api.api import api
from api.legacy.urls import api as legacy_public_api
from controlpanel.api.urls import api as platform_api
from rosterchief.health import health

urlpatterns = [
    path('healthz', health, name='health'),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('_allauth/', include('club.headless')),
    path('_allauth/', include('allauth.headless.urls')),
    path('api/v1/', legacy_public_api.urls),
    path('api/v1/', api.urls),
    path('api/platform/v1/', platform_api.urls),
    path('', include('chat.urls')),  # chat attachments (streamed after a permission check)
    path('', include('members.urls')),  # private member photos (streamed after a permission check)
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
