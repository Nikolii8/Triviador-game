from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


def _default_nickname(user):
    nickname = user.username[:30]
    if Profile.objects.filter(nickname=nickname).exists():
        nickname = f'player-{user.pk}'
    return nickname


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance, defaults={'nickname': _default_nickname(instance)})
