from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .models import Game, GamePlayer, WaitingRoom
from .waiting_room import form_waiting_room_game


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([permissions.IsAuthenticated])
def join_waiting_room(request):
    user = request.user

    if WaitingRoom.objects.filter(user=user).exists():
        return Response(
            {'detail': 'You already have a waiting room request.'},
            status=status.HTTP_409_CONFLICT,
        )

    if GamePlayer.objects.filter(
        user=user,
        game__status__in=[Game.WAITING, Game.IN_PROGRESS],
    ).exists():
        return Response(
            {'detail': 'You are already in a waiting or active game.'},
            status=status.HTTP_409_CONFLICT,
        )

    WaitingRoom.objects.create(user=user)
    game = form_waiting_room_game()

    return Response(
        {
            'waiting': game is None,
            'game_id': game.pk if game is not None else None,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@authentication_classes([SessionAuthentication])
@permission_classes([permissions.IsAuthenticated])
def leave_waiting_room(request):
    WaitingRoom.objects.filter(user=request.user).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
