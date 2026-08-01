"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
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
from django.contrib.auth import views as auth_views
from django.urls import include, path

from projects import auth2fa

urlpatterns = [
    path('admin/', admin.site.urls),
    # Kirish — Telegram 2FA bilan (profilda chat_id bo'lsa kod so'raladi)
    path('login/', auth2fa.kirish, name='login'),
    path('login/kod/', auth2fa.kirish_kod, name='login_kod'),
    path('telegram/hook/<str:secret>/', auth2fa.telegram_hook, name='telegram_hook'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('ombor/', include('ombor.urls')),
    path('', include('projects.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
