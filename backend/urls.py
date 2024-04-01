from django.urls import path
from .views import register, get_csrf_token, login_view

urlpatterns = [
    path('csrf_token', get_csrf_token, name='csrf_token'),
    path('register', register, name='register'),
    path('login', login_view, name='login'),
]
