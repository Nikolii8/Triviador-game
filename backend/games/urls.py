from django.urls import path

from .views import join_waiting_room, leave_waiting_room

urlpatterns = [
    path('waiting-room/', join_waiting_room, name='waiting-room-join'),
    path('waiting-room/leave/', leave_waiting_room, name='waiting-room-leave'),
]
