from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Profile, User


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    extra = 0


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = [ProfileInline]
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('username', 'email', 'password1', 'password2')}),
    )
    list_display = ('username', 'email', 'is_staff')


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'nickname', 'avatar_key')
    search_fields = ('user__username', 'nickname')
