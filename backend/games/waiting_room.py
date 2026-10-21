import random
from django.db import transaction
from .models import Game, GamePlayer, PLAYER_COLORS, WaitingRoom

def form_waiting_room_game():
    active_statuses = [Game.WAITING, Game.IN_PROGRESS]
    waiting_entries = list(
        WaitingRoom.objects.exclude(
            user_id__in=GamePlayer.objects.filter(
                game__status__in=active_statuses,
            ).values('user_id')
        )
    )

    if len(waiting_entries) < 3:
        return None

    selected_entries = random.sample(waiting_entries, 3)

    with transaction.atomic():
        game = Game.objects.create(created_by=selected_entries[0].user)

        for order, entry in enumerate(selected_entries, start=1):
            color = PLAYER_COLORS[order - 1][0]
            GamePlayer.objects.create(
                game=game,
                user=entry.user,
                player_order=order,
                color=color,
                score=0,
            )

        WaitingRoom.objects.filter(
            pk__in=[entry.pk for entry in selected_entries],
        ).delete()

    return game