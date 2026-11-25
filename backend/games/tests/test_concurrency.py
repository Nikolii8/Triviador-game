"""Concurrent initialization attempts, using real separate database connections.

TransactionTestCase is needed so each thread sees committed data. On SQLite the
attempts are serialized by the database write lock (transaction_mode IMMEDIATE);
on PostgreSQL by select_for_update() on the game row. See README for the limits.
"""

import threading
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from games.initialization import GameInitializationError, initialize_game
from games.capture import allowed_territories, choose_territory
from games.models import CaptureRight, Game, GamePlayer, RoundAnswer, ScoreEntry
from games import rounds
from games.rounds import RoundStateError, create_round, evaluate_question, start_round, submit_answer
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
        initialize_game(game.pk)
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


class ConcurrentCloseExpiredTests(TransactionTestCase):
    """Two copies of close_expired_rounds evaluating the same expired question at once."""

    def test_question_is_evaluated_once(self):
        game = make_game(make_user('host'))
        players = [add_player(game, f'p{order}', order) for order in (1, 2, 3)]
        initialize_game(game.pk)
        category = Category.objects.create(name='Числа')
        NumericQuestion.objects.create(category=category, text='Колко?', correct_answer=10)
        NumericQuestion.objects.create(category=category, text='А сега?', correct_answer=20)
        round_ = create_round(game.pk)
        start_round(round_.pk)
        submit_answer(round_.pk, players[0].pk, 10)
        question = round_.questions.get()

        later = timezone.now() + timedelta(seconds=30)
        with mock.patch.object(rounds, 'now', lambda: later):
            results = run_simultaneously(
                lambda: evaluate_question(round_.pk, question_id=question.pk),
                lambda: evaluate_question(round_.pk, question_id=question.pk),
            )

        refused = [result for result in results if isinstance(result, RoundStateError)]
        self.assertEqual(len(refused), 1, results)
        round_.refresh_from_db()
        self.assertEqual(round_.status, round_.EVALUATED)
        self.assertEqual(round_.questions.count(), 1)
        self.assertEqual(RoundAnswer.objects.filter(round=round_, place__isnull=False).count(), 1)


class ConcurrentCaptureTests(TransactionTestCase):
    """M07 upgrade: simultaneous picks of one free territory and of the last capture right."""

    def setUp(self):
        self.game = make_game(make_user('host'))
        self.players = [add_player(self.game, f'p{order}', order) for order in (1, 2, 3)]
        initialize_game(self.game.pk)
        category = Category.objects.create(name='Числа')
        NumericQuestion.objects.create(category=category, text='Колко?', correct_answer=10)
        round_ = create_round(self.game.pk)
        start_round(round_.pk)
        first, second, third = self.players
        submit_answer(round_.pk, first.pk, 10)
        submit_answer(round_.pk, second.pk, 12)
        submit_answer(round_.pk, third.pk, 50)  # first: 2 picks, then second: 1 pick

    def test_one_free_territory_is_taken_once(self):
        first = self.players[0]
        target = allowed_territories(first).first()

        results = run_simultaneously(
            lambda: choose_territory(self.game.pk, first.pk, target.pk),
            lambda: choose_territory(self.game.pk, first.pk, target.pk),
        )

        self.assertEqual(sum(isinstance(r, ScoreEntry) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, RoundStateError) for r in results), 1, results)
        self.assertEqual(ScoreEntry.objects.filter(territory=target).count(), 1)
        self.assertEqual(CaptureRight.objects.get(player=first).used, 1)

    def test_last_right_is_used_once(self):
        first, second, third = self.players
        for _ in range(2):
            choose_territory(self.game.pk, first.pk, allowed_territories(first).first().pk)
        # Capitals are random, so make both targets valid regardless of the map: give the second's
        # free neighbors to the third (test setup), which lets the second pick any free territory.
        free = Territory.objects.filter(game=self.game, owner__isnull=True)
        free.filter(neighbors__owner=second).update(owner=third)
        targets = list(free.order_by('pk')[:2])
        self.assertEqual(set(allowed_territories(second)), set(free))

        results = run_simultaneously(
            lambda: choose_territory(self.game.pk, second.pk, targets[0].pk),
            lambda: choose_territory(self.game.pk, second.pk, targets[1].pk),
        )

        self.assertEqual(sum(isinstance(r, ScoreEntry) for r in results), 1, results)
        self.assertEqual(CaptureRight.objects.get(player=second).used, 1)
        self.assertEqual(Territory.objects.filter(owner=second).count(), 2)  # capital + exactly one pick
