"""Database-level integrity: these rules hold even when model validation is bypassed."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from games.models import Game, GamePlayer, Round, RoundAnswer

from .factories import (
    add_player,
    make_choice_question,
    make_choice_round,
    make_game,
    make_numeric_question,
    make_numeric_round,
    make_user,
)


class ConstraintTestCase(TestCase):
    def assert_integrity_error(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()


class GameConstraintTests(ConstraintTestCase):
    def test_invalid_status_is_rejected(self):
        self.assert_integrity_error(lambda: make_game(status='paused'))

    def test_finished_at_cannot_be_before_started_at(self):
        now = timezone.now()
        self.assert_integrity_error(lambda: make_game(started_at=now, finished_at=now - timedelta(seconds=1)))


class GamePlayerConstraintTests(ConstraintTestCase):
    def setUp(self):
        self.game = make_game()
        self.alice = add_player(self.game, 'alice', 1)

    def test_user_joins_a_game_only_once(self):
        self.assert_integrity_error(
            lambda: GamePlayer.objects.create(game=self.game, user=self.alice.user, player_order=2)
        )

    def test_player_order_is_unique_per_game(self):
        self.assert_integrity_error(lambda: add_player(self.game, 'bob', 1))

    def test_player_order_must_be_in_range(self):
        for order in (0, 4):
            with self.subTest(order=order):
                self.assert_integrity_error(lambda: add_player(self.game, f'player{order}', order))

    def test_same_order_is_allowed_in_another_game(self):
        add_player(make_game(make_user('host2')), 'bob', 1)

        self.assertEqual(GamePlayer.objects.filter(player_order=1).count(), 2)


class RoundConstraintTests(ConstraintTestCase):
    def setUp(self):
        self.game = make_game()

    def test_round_number_is_unique_per_game(self):
        make_choice_round(self.game, number=1)

        self.assert_integrity_error(lambda: make_numeric_round(self.game, number=1))

    def test_round_number_must_be_positive(self):
        self.assert_integrity_error(lambda: make_choice_round(self.game, number=0))

    def test_invalid_status_is_rejected(self):
        self.assert_integrity_error(lambda: make_choice_round(self.game, status='paused'))

    def test_round_has_exactly_one_question(self):
        cases = {
            'no question': {'question_type': Round.CHOICE},
            'both questions': {
                'choice_question': make_choice_question(),
                'numeric_question': make_numeric_question(),
            },
        }
        for label, fields in cases.items():
            with self.subTest(label):
                self.assert_integrity_error(lambda: Round.objects.create(game=self.game, number=1, **fields))

    def test_question_type_cannot_drift_when_save_is_bypassed(self):
        round_ = make_choice_round(self.game)
        rounds = Round.objects.filter(pk=round_.pk)
        numeric_question = make_numeric_question()
        cases = {
            'type changed alone': {'question_type': Round.NUMERIC},
            'question changed alone': {'choice_question': None, 'numeric_question': numeric_question},
            'unknown type': {'question_type': 'essay'},
        }
        for label, changes in cases.items():
            with self.subTest(label):
                self.assert_integrity_error(lambda: rounds.update(**changes))
        round_.refresh_from_db()
        self.assertEqual(round_.question_type, Round.CHOICE)

    def test_finished_at_cannot_be_before_started_at(self):
        now = timezone.now()
        self.assert_integrity_error(
            lambda: make_choice_round(self.game, started_at=now, finished_at=now - timedelta(seconds=1))
        )


class RoundAnswerConstraintTests(ConstraintTestCase):
    def setUp(self):
        game = make_game()
        self.player = add_player(game, 'alice', 1)
        self.round = make_choice_round(game)
        self.option = self.round.choice_question.answer_options.first()

    def test_one_answer_per_player_per_round(self):
        RoundAnswer.objects.create(round=self.round, player=self.player, selected_option=self.option)

        self.assert_integrity_error(
            lambda: RoundAnswer.objects.create(round=self.round, player=self.player, selected_option=self.option)
        )

    def test_answer_cannot_hold_both_option_and_number(self):
        self.assert_integrity_error(
            lambda: RoundAnswer.objects.create(
                round=self.round, player=self.player, selected_option=self.option, numeric_value=5
            )
        )
