from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import Territory


@receiver(m2m_changed, sender=Territory.neighbors.through)
def reject_invalid_neighbors(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Guard territory.neighbors.add(): no self-links and no links into another game.

    The database cannot express "same game" across two rows, so this is the
    application-level check for the usual way of adding neighbors.
    """
    if action != 'pre_add' or not pk_set:
        return
    if instance.pk in pk_set:
        raise ValidationError('A territory cannot be its own neighbor.')
    if model.objects.filter(pk__in=pk_set).exclude(game_id=instance.game_id).exists():
        raise ValidationError('Neighbors must belong to the same game.')
