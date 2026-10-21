from django.contrib.auth import get_user_model
from django.test import TestCase

from games.models import Game, GamePlayer, WaitingRoom
from games.waiting_room import form_waiting_room_game


class FormWaitingRoomGameTests(TestCase):
    def test_five_waiting_users_create_game_and_leave_two_waiting(self):
        User = get_user_model()
        for number in range(5):
            user = User.objects.create_user(
                username=f'player{number}',
                email=f'player{number}@example.com',
            )
            WaitingRoom.objects.create(user=user)

        game = form_waiting_room_game()

        self.assertEqual(Game.objects.count(), 1)
        self.assertEqual(game.status, Game.WAITING)
        self.assertEqual(game.players.count(), 3)
        self.assertEqual(game.players.values('user').distinct().count(), 3)
        self.assertEqual(
            set(game.players.values_list('color', flat=True)),
            {'red', 'blue', 'yellow'},
        )
        self.assertFalse(game.players.exclude(score=0).exists())
        self.assertEqual(WaitingRoom.objects.count(), 2)
        self.assertEqual(game.rounds.count(), 0)
