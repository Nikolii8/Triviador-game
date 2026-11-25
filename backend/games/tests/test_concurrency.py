"""Concurrent initialization attempts, using real separate database connections.

TransactionTestCase is needed so each thread sees committed data. On SQLite the
attempts are serialized by the database write lock (transaction_mode IMMEDIATE);
on PostgreSQL by select_for_update() on the game row. See README for the limits.
"""

import threading

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from games.initialization import GameInitializationError, initialize_game
from games.models import Game, GamePlayer, RoundAnswer
from games.rounds import RoundStateError, create_round, start_round, submit_answer
from questions.models import Category, NumericQuestion
from territories.map_definition import PROJECT_MAP
from territories.models import Capital, Territory

from .factories import add_player, make_game, make_user


def run_simultaneously(*targets):
    """Start every target at the same moment in its own thread; return what each returned or raised."""
    barrier = threading.Barrier(len(targets))
    results = [None] * len(targets)

    def worker(index, target):
        try:
            barrier.wait()
            results[index] = target()
        except Exception as exc:  # collected and inspected by the test
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, target)) for i, target in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


class ConcurrentInitializationTests(TransactionTestCase):
    def setUp(self):
        self.game = make_game(make_user('host'))
        self.players = [add_player(self.game, f'p{order}', order) for order in range(1, 4)]

    def test_two_simultaneous_starts_create_one_map(self):
        results = run_simultaneously(lambda: initialize_game(self.game.pk), lambda: initialize_game(self.game.pk))

        successes = [result for result in results if isinstance(result, Game)]
        refusals = [result for result in results if isinstance(result, GameInitializationError)]
        self.assertEqual((len(successes), len(refusals)), (1, 1), results)

        self.assertEqual(Territory.objects.filter(game=self.game).count(), len(PROJECT_MAP.territories))
        self.assertEqual(Capital.objects.filter(player__game=self.game).count(), 3)
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, Game.IN_PROGRESS)

    def test_join_racing_with_start_never_breaks_three_players(self):
        self.players[2].delete()  # leave a free seat, then race a join against a start
        add_player(self.game, 'p3-again', 3)
        self.players[1].delete()

        results = run_simultaneously(
            lambda: initialize_game(self.game.pk),
            lambda: GamePlayer.objects.create(
                game=self.game, user=make_user('racer'), player_order=2, color='blue'
            ),
        )

        self.game.refresh_from_db()
        player_count = GamePlayer.objects.filter(game=self.game).count()
        if self.game.status == Game.IN_PROGRESS:
            # The join went first, so the start saw exactly three players.
            self.assertEqual(player_count, 3, results)
            self.assertEqual(Capital.objects.filter(player__game=self.game).count(), 3)
        else:
            # The start went first and refused (only two players); the join then succeeded.
            self.assertIsInstance(results[0], GameInitializationError)
            self.assertEqual(player_count, 3)
            self.assertFalse(Territory.objects.filter(game=self.game).exists())


class ConcurrentAnswerTests(TransactionTestCase):
    """M06 upgrade: two simultaneous answers from one player to one question."""

    def test_only_one_answer_is_accepted(self):
        game = make_game(make_user('host'))
        players = [add_player(game, f'p{order}', order) for order in (1, 2, 3)]
        Game.objects.filter(pk=game.pk).update(status=Game.IN_PROGRESS, started_at=timezone.now())
        category = Category.objects.create(name='Числа')
        NumericQuestion.objects.create(category=category, text='Колко?', correct_answer=10)
        round_ = create_round(game.pk)
        start_round(round_.pk)

        results = run_simultaneously(
            lambda: submit_answer(round_.pk, players[0].pk, 10),
            lambda: submit_answer(round_.pk, players[0].pk, 11),
        )

        accepted = [result for result in results if isinstance(result, RoundAnswer)]
        refused = [result for result in results if isinstance(result, RoundStateError)]
        self.assertEqual((len(accepted), len(refused)), (1, 1), results)
        stored = RoundAnswer.objects.get(round=round_, player=players[0])
        self.assertEqual(stored.numeric_value, accepted[0].numeric_value)
