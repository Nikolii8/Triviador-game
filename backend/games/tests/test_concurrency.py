"""Concurrent initialization attempts, using real separate database connections.

TransactionTestCase is needed so each thread sees committed data. On SQLite the
attempts are serialized by the database write lock (transaction_mode IMMEDIATE);
on PostgreSQL by select_for_update() on the game row. See README for the limits.
"""

import threading

from django.db import connection
from django.test import TransactionTestCase

from games.initialization import GameInitializationError, initialize_game
from games.models import Game, GamePlayer
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
            lambda: GamePlayer.objects.create(game=self.game, user=make_user('racer'), player_order=2),
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
