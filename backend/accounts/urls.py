from django.urls import path

from .views import csrf_view, login_view, logout_view, MeView, RegisterView

urlpatterns = [
    path('csrf/', csrf_view, name='csrf'),
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('me/', MeView.as_view(), name='me'),
]
