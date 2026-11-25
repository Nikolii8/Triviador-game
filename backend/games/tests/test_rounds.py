"""Numeric city capture rounds (M06).

Time never really passes in these tests: `games.rounds.now` is replaced by a fake clock that
the test moves forward. The random question choice is replaced by "first unused question by id".
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase

from games import capture, rounds
from games.initialization import initialize_game
from games.models import Game, Round, RoundAnswer, RoundQuestion
from games.rounds import (
    InvalidRoundInput,
    RoundNotFound,
    RoundStateError,
    create_round,
    evaluate_question,
    round_state,
    start_round,
    submit_answer,
)
from questions.models import Category, NumericQuestion

from .factories import add_player, make_game, make_numeric_round, make_user

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=dt_timezone.utc)


class FakeClock:
    def __init__(self):
        self.moment = T0

    def __call__(self):
        return self.moment

    def at(self, seconds):
        """Move to `seconds` after T0 (fractions allowed)."""
        self.moment = T0 + timedelta(seconds=seconds)


class FirstChoice:
    """Stands in for the random source: always the first (lowest id) candidate."""

    def choice(self, items):
        return items[0]

    def sample(self, items, k):
        return list(items)[:k]


def make_bank(*answers):
    category, _ = Category.objects.get_or_create(name='Числа')
    return [
        NumericQuestion.objects.create(category=category, text=f'Въпрос {i}?', correct_answer=answer)
        for i, answer in enumerate(answers, start=1)
    ]


class RoundTestCase(TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = mock.patch.object(rounds, 'now', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        rng_patcher = mock.patch.object(rounds.random, 'SystemRandom', FirstChoice)
        rng_patcher.start()
        self.addCleanup(rng_patcher.stop)

        self.game = make_game()
        self.a = add_player(self.game, 'ana', 1)
        self.b = add_player(self.game, 'boris', 2)
        self.c = add_player(self.game, 'cveta', 3)
        initialize_game(self.game.pk)  # M05: map, capitals, status in progress
        self.game.refresh_from_db()

    def started_round(self, *bank_answers):
        self.bank = make_bank(*bank_answers)
        round_ = create_round(self.game.pk)
        start_round(round_.pk)
        round_.refresh_from_db()
        return round_

    def answer(self, round_, player, value, at):
        self.clock.at(at)
        return submit_answer(round_.pk, player.pk, value)

    def evaluate(self, round_, at):
        self.clock.at(at)
        return evaluate_question(round_.pk)

    def use_all_picks(self):
        """Make every pending territory pick (M07), so the next round may be created."""
        while (right := capture.pending_right(self.game)) is not None:
            territory = capture.allowed_territories(right.player).first()
            capture.choose_territory(self.game.pk, right.player_id, territory.pk)

    def places(self, round_):
        return [(answer.place, answer.player) for answer in round_.ranking()]


class WorkflowTests(RoundTestCase):
    def test_create_start_and_finish(self):
        bank = make_bank(100)

        round_ = create_round(self.game.pk)

        self.assertEqual((round_.number, round_.status, round_.round_type), (1, Round.PENDING, Round.CITY_CAPTURE))
        self.assertEqual(round_.places_needed, 2)
        self.assertEqual(round_.numeric_question, bank[0])
        first = round_.questions.get()
        self.assertEqual(set(first.participants.all()), {self.a, self.b, self.c})
        self.assertEqual((first.status, first.started_at, first.deadline), (RoundQuestion.PENDING, None, None))
        self.assertIsNone(round_.winner)
        self.assertIsNone(round_.finished_at)

        self.clock.at(5)
        start_round(round_.pk)
        round_.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(round_.status, Round.OPEN)
        self.assertEqual(round_.started_at, T0 + timedelta(seconds=5))
        self.assertEqual(first.deadline, T0 + timedelta(seconds=15))

        self.answer(round_, self.a, 98, at=6)
        self.answer(round_, self.b, '103', at=7)
        self.answer(round_, self.c, 100, at=8)  # last answer evaluates the question

        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.EVALUATED)
        self.assertEqual(round_.winner, self.c)
        self.assertEqual(round_.finished_at, T0 + timedelta(seconds=8))
        self.assertEqual(round_.unresolved_reason, '')
        self.assertEqual(self.places(round_), [(1, self.c), (2, self.a)])
        self.assertEqual(self.game.get_current_round(), round_)

    def test_next_number_is_highest_plus_one_and_questions_are_not_reused(self):
        old_question = make_bank(7)[0]
        make_numeric_round(self.game, number=4, question=old_question, status=Round.EVALUATED)
        make_bank(8)

        round_ = create_round(self.game.pk)

        self.assertEqual(round_.number, 5)
        self.assertNotEqual(round_.numeric_question, old_question)
        self.assertEqual(self.game.get_current_round(), round_)

    def test_only_one_unfinished_round(self):
        make_bank(1, 2)
        create_round(self.game.pk)

        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)
        self.assertEqual(self.game.rounds.count(), 1)

    def test_creation_refused_without_changes(self):
        with self.subTest('no question in the bank'):
            with self.assertRaises(RoundStateError):
                create_round(self.game.pk)
            self.assertFalse(Round.objects.exists())

        make_bank(1)
        with self.subTest('missing game'):
            with self.assertRaises(RoundNotFound):
                create_round(999_999)
        with self.subTest('unknown round type'):
            with self.assertRaises(InvalidRoundInput):
                create_round(self.game.pk, round_type='capital_assignments')
        with self.subTest('game not active'):
            Game.objects.filter(pk=self.game.pk).update(status=Game.WAITING, started_at=None)
            with self.assertRaises(RoundStateError):
                create_round(self.game.pk)
        self.assertFalse(Round.objects.exists())

    def test_every_changing_operation_needs_an_active_game(self):
        round_ = self.started_round(100)
        Game.objects.filter(pk=self.game.pk).update(
            status=Game.FINISHED, finished_at=self.game.started_at + timedelta(seconds=1)
        )

        with self.assertRaises(RoundStateError):
            self.answer(round_, self.a, 100, at=1)
        with self.assertRaises(RoundStateError):
            self.evaluate(round_, at=20)
        self.assertFalse(RoundAnswer.objects.exists())


class RankingTests(RoundTestCase):
    def test_closeness_beats_speed(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 105, at=1)
        self.answer(round_, self.b, 99, at=9)
        self.answer(round_, self.c, 50, at=2)

        self.assertEqual(self.places(round_), [(1, self.b), (2, self.a)])

    def test_equal_closeness_on_both_sides_faster_wins(self):
        round_ = self.started_round(100)
        self.answer(round_, self.b, 105, at=1.25)
        self.answer(round_, self.a, 95, at=1.5)  # same distance, a quarter second later
        self.answer(round_, self.c, 200, at=2)

        self.assertEqual(self.places(round_), [(1, self.b), (2, self.a)])

    def test_order_of_records_does_not_matter(self):
        round_ = self.started_round(100)
        self.answer(round_, self.c, 101, at=3)
        self.answer(round_, self.a, 90, at=1)
        self.answer(round_, self.b, 99, at=2)

        self.assertEqual(self.places(round_), [(1, self.b), (2, self.c)])

    def test_single_answer_gives_only_first_place(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 3, at=1)

        round_ = self.evaluate(round_, at=10)

        self.assertEqual(self.places(round_), [(1, self.a)])
        self.assertEqual(round_.winner, self.a)
        self.assertEqual(round_.unresolved_reason, Round.NOT_ENOUGH_ANSWERS)
        self.assertEqual(round_.questions.count(), 1)


class RefusalTests(RoundTestCase):
    def test_player_of_another_game(self):
        round_ = self.started_round(100)
        other_game = make_game(make_user('other-host'))
        stranger = add_player(other_game, 'stranger', 1)

        with self.assertRaises(RoundStateError):
            self.answer(round_, stranger, 100, at=1)
        with self.assertRaises(RoundNotFound):
            self.clock.at(1)
            submit_answer(round_.pk, 999_999, 100)
        with self.assertRaises(RoundNotFound):
            submit_answer(999_999, self.a.pk, 100)
        self.assertFalse(RoundAnswer.objects.exists())

    def test_second_answer_does_not_replace_the_first(self):
        round_ = self.started_round(100)
        first = self.answer(round_, self.a, 50, at=1)

        with self.assertRaises(RoundStateError):
            self.answer(round_, self.a, 100, at=2)

        self.assertEqual(list(RoundAnswer.objects.all()), [first])
        first.refresh_from_db()
        self.assertEqual(first.numeric_value, 50)

    def test_invalid_input_keeps_the_right_to_answer(self):
        round_ = self.started_round(100)
        for value in ('3.5', 3.5, 3.0, 'abc', '', None, True, '1e3', 2**40, [1]):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRoundInput):
                    self.answer(round_, self.a, value, at=1)
        self.assertFalse(RoundAnswer.objects.exists())

        answer = self.answer(round_, self.a, ' -12 ', at=2)
        self.assertEqual(answer.numeric_value, -12)

    def test_answer_before_start(self):
        make_bank(100)
        round_ = create_round(self.game.pk)

        with self.assertRaises(RoundStateError):
            self.answer(round_, self.a, 100, at=1)
        with self.assertRaises(RoundStateError):
            self.evaluate(round_, at=60)

    def test_start_twice(self):
        round_ = self.started_round(100)
        with self.assertRaises(RoundStateError):
            start_round(round_.pk)

    def test_premature_evaluation(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 100, at=1)

        with self.assertRaises(RoundStateError):
            self.evaluate(round_, at=9.999)
        self.assertEqual(round_.questions.get().status, RoundQuestion.ACTIVE)

    def test_completed_result_is_not_replaced(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 100, at=1)
        round_ = self.evaluate(round_, at=10)
        before = (round_.winner, round_.finished_at, self.places(round_))

        with self.assertRaises(RoundStateError):
            self.evaluate(round_, at=30)
        with self.assertRaises(RoundStateError):
            self.answer(round_, self.b, 100, at=30)

        round_.refresh_from_db()
        self.assertEqual((round_.winner, round_.finished_at, self.places(round_)), before)


class DeadlineTests(RoundTestCase):
    def test_just_before_the_deadline_is_accepted(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 100, at=9.999999)
        self.assertEqual(RoundAnswer.objects.get().submitted_at, T0 + timedelta(seconds=9.999999))

    def test_exactly_at_and_after_the_deadline_is_refused(self):
        round_ = self.started_round(100)
        for at in (10, 10.000001, 60):
            with self.subTest(at=at):
                with self.assertRaises(RoundStateError):
                    self.answer(round_, self.a, 100, at=at)
        self.assertFalse(RoundAnswer.objects.exists())

    def test_evaluation_is_allowed_exactly_at_the_deadline(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 100, at=1)

        round_ = self.evaluate(round_, at=10)

        self.assertEqual(round_.status, Round.EVALUATED)


class FollowUpQuestionTests(RoundTestCase):
    def test_full_tie_for_first_asks_only_the_tied_group(self):
        round_ = self.started_round(100, 50)
        self.answer(round_, self.a, 98, at=2)
        self.answer(round_, self.b, 102, at=2)  # same distance, same moment
        self.answer(round_, self.c, 0, at=3)

        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.OPEN)
        self.assertEqual(self.places(round_), [])
        second = round_.questions.get(sequence=2)
        self.assertEqual(set(second.participants.all()), {self.a, self.b})
        self.assertEqual((second.first_place, second.last_place), (1, 2))
        self.assertEqual(second.numeric_question, self.bank[1])
        # Its own 10 seconds, from when it was actually activated.
        self.assertEqual(second.started_at, T0 + timedelta(seconds=3))
        self.assertEqual(second.deadline, T0 + timedelta(seconds=13))
        self.assertEqual(round_.number, 1)

        with self.assertRaises(RoundStateError):
            self.answer(round_, self.c, 50, at=4)  # not in the tied group

        self.answer(round_, self.b, 50, at=5)
        self.answer(round_, self.a, 40, at=4)

        round_.refresh_from_db()
        self.assertEqual(self.places(round_), [(1, self.b), (2, self.a)])
        self.assertEqual(round_.winner, self.b)

    def test_tie_for_second_keeps_first(self):
        round_ = self.started_round(100, 50)
        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 90, at=2)
        self.answer(round_, self.c, 110, at=2)

        second = round_.questions.get(sequence=2)
        self.assertEqual(set(second.participants.all()), {self.b, self.c})
        self.assertEqual((second.first_place, second.last_place), (2, 2))
        self.assertEqual(self.places(round_), [(1, self.a)])

        self.answer(round_, self.c, 49, at=4)
        round_ = self.evaluate(round_, at=12)  # b never answers

        self.assertEqual(self.places(round_), [(1, self.a), (2, self.c)])
        self.assertEqual(round_.unresolved_reason, '')

    def test_no_answers_asks_the_same_group_again(self):
        round_ = self.started_round(100, 50)

        round_ = self.evaluate(round_, at=10)

        second = round_.questions.get(sequence=2)
        self.assertEqual(set(second.participants.all()), {self.a, self.b, self.c})
        self.assertEqual((second.first_place, second.last_place), (1, 2))
        self.assertEqual(second.deadline, T0 + timedelta(seconds=20))
        self.assertEqual(round_.questions.get(sequence=1).answers.count(), 0)

    def test_lower_place_is_not_promoted_over_a_non_answerer(self):
        round_ = self.started_round(100, 50)
        self.answer(round_, self.a, 99, at=1)
        self.answer(round_, self.b, 101, at=1)
        self.answer(round_, self.c, 0, at=2)  # clearly third

        self.answer(round_, self.a, 50, at=3)
        round_ = self.evaluate(round_, at=12)  # b does not answer the tie-break

        self.assertEqual(self.places(round_), [(1, self.a)])
        self.assertEqual(round_.winner, self.a)
        self.assertEqual(round_.unresolved_reason, Round.NOT_ENOUGH_ANSWERS)

    def test_answers_from_different_questions_are_not_compared(self):
        round_ = self.started_round(100, 50)
        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 90, at=1)
        self.answer(round_, self.c, 110, at=1)
        # The tie-break is far off for c, but b does not answer it: only c can be placed.
        self.answer(round_, self.c, 1000, at=3)
        round_ = self.evaluate(round_, at=11)

        self.assertEqual(self.places(round_), [(1, self.a), (2, self.c)])


class RoundLimitTests(RoundTestCase):
    def test_question_limit_without_any_answer(self):
        round_ = self.started_round(1, 2, 3, 4)

        self.evaluate(round_, at=10)
        self.evaluate(round_, at=20)
        round_ = self.evaluate(round_, at=30)

        self.assertEqual(round_.status, Round.EVALUATED)
        self.assertIsNone(round_.winner)
        self.assertEqual(round_.finished_at, T0 + timedelta(seconds=30))
        self.assertEqual(round_.unresolved_reason, Round.QUESTION_LIMIT)
        self.assertEqual(round_.questions.count(), 3)
        self.assertEqual(round_.number, 1)

    def test_bank_exhausted(self):
        round_ = self.started_round(1, 2)

        self.evaluate(round_, at=10)
        round_ = self.evaluate(round_, at=20)

        self.assertIsNone(round_.winner)
        self.assertEqual(round_.unresolved_reason, Round.BANK_EXHAUSTED)
        self.assertEqual(round_.questions.count(), 2)

    def test_limit_is_the_reason_when_the_bank_is_also_empty(self):
        round_ = self.started_round(1, 2, 3)

        self.evaluate(round_, at=10)
        self.evaluate(round_, at=20)
        round_ = self.evaluate(round_, at=30)

        self.assertEqual(round_.unresolved_reason, Round.QUESTION_LIMIT)

    def test_bank_exhausted_keeps_the_decided_first_place(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 90, at=1)
        self.answer(round_, self.c, 110, at=1)

        round_.refresh_from_db()
        self.assertEqual(round_.winner, self.a)
        self.assertEqual(round_.unresolved_reason, Round.BANK_EXHAUSTED)
        self.assertEqual(self.places(round_), [(1, self.a)])

    def test_winner_decided_on_the_third_question(self):
        round_ = self.started_round(100, 50, 20)
        self.evaluate(round_, at=10)  # nobody answers question 1
        self.answer(round_, self.a, 49, at=11)
        self.answer(round_, self.b, 51, at=11)
        self.answer(round_, self.c, 0, at=12)  # a and b tied, question 3 for them

        self.answer(round_, self.b, 20, at=21)
        self.answer(round_, self.a, 25, at=21)

        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.EVALUATED)
        self.assertEqual(round_.winner, self.b)
        self.assertEqual(round_.unresolved_reason, '')
        self.assertEqual(self.places(round_), [(1, self.b), (2, self.a)])

    def test_second_place_unresolved_at_the_limit(self):
        round_ = self.started_round(100, 50, 20, 7)
        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 90, at=1)
        self.answer(round_, self.c, 110, at=1)  # tie for 2nd
        self.answer(round_, self.b, 49, at=2)
        self.answer(round_, self.c, 51, at=2)  # tie again
        self.answer(round_, self.b, 21, at=3)
        self.answer(round_, self.c, 19, at=3)  # and again on the last allowed question

        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.EVALUATED)
        self.assertEqual(round_.winner, self.a)
        self.assertEqual(round_.unresolved_reason, Round.QUESTION_LIMIT)
        self.assertEqual(self.places(round_), [(1, self.a)])
        self.assertEqual(round_.questions.count(), 3)


class DataTests(RoundTestCase):
    def test_reload_restores_question_deadline_and_ranking(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 97, at=2)

        state = round_state(round_.pk, at=T0 + timedelta(seconds=3.5))
        current = state['current_question']
        self.assertEqual(current['text'], self.bank[0].text)
        self.assertEqual(current['deadline'], T0 + timedelta(seconds=10))
        self.assertEqual(current['remaining_seconds'], 6.5)
        self.assertEqual(current['answered'], [self.a.pk])
        self.assertNotIn('correct_answer', current)

        self.answer(round_, self.b, 100, at=4)
        self.evaluate(round_, at=10)

        state = round_state(round_.pk)
        self.assertIsNone(state['current_question'])
        self.assertEqual(state['ranking'], [{'place': 1, 'player': self.b.pk}, {'place': 2, 'player': self.a.pk}])
        self.assertEqual(state['winner'], self.b.pk)
        self.assertEqual(state['completed_at'], T0 + timedelta(seconds=10))
        self.assertEqual(state['questions'][0]['correct_answer'], 100)

    def test_editing_the_bank_does_not_change_scoring_or_history(self):
        round_ = self.started_round(100)
        NumericQuestion.objects.filter(pk=self.bank[0].pk).update(text='Друг текст', correct_answer=0)

        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 0, at=1)
        self.answer(round_, self.c, 60, at=1)

        round_.refresh_from_db()
        self.assertEqual(self.places(round_), [(1, self.a), (2, self.c)])
        question = round_.questions.get()
        self.assertEqual((question.text, question.correct_answer), ('Въпрос 1?', 100))

    def test_history_of_all_questions_and_answers_is_kept(self):
        round_ = self.started_round(100, 50)
        self.answer(round_, self.a, 98, at=1)
        self.answer(round_, self.b, 102, at=1)
        self.answer(round_, self.c, 0, at=1)
        self.answer(round_, self.a, 50, at=2)
        self.answer(round_, self.b, 40, at=2)

        questions = list(round_.questions.all())
        self.assertEqual([q.status for q in questions], [RoundQuestion.COMPLETED] * 2)
        self.assertEqual([q.answers.count() for q in questions], [3, 2])
        self.assertEqual(RoundAnswer.objects.filter(round=round_).count(), 5)

    def test_unused_check_covers_all_questions_of_the_game(self):
        round_ = self.started_round(100, 50, 20)
        self.evaluate(round_, at=10)
        self.answer(round_, self.a, 50, at=11)
        self.answer(round_, self.b, 1, at=11)
        self.answer(round_, self.c, 2, at=11)  # decided on question 2
        self.use_all_picks()

        # Questions 1 and 2 were both used by round 1, so round 2 can only get question 3 ...
        second_round = create_round(self.game.pk)
        self.assertEqual(second_round.numeric_question, self.bank[2])
        start_round(second_round.pk)
        self.evaluate(second_round, at=40)  # no answers, and no question left for a retry
        # ... and after that the bank is exhausted for this game.
        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)


class ModelValidationTests(RoundTestCase):
    def evaluated_round(self, **fields):
        round_ = self.started_round(100)
        for name, value in {'status': Round.EVALUATED, 'finished_at': T0 + timedelta(seconds=10), **fields}.items():
            setattr(round_, name, value)
        return round_

    def test_completed_without_winner_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            self.evaluated_round().full_clean()

    def test_completed_without_winner_but_with_reason_is_valid(self):
        self.evaluated_round(unresolved_reason=Round.QUESTION_LIMIT).full_clean()

    def test_winner_with_unresolved_second_place_is_valid(self):
        self.evaluated_round(winner=self.a, unresolved_reason=Round.BANK_EXHAUSTED).full_clean()

    def test_winner_and_reason_only_when_completed(self):
        round_ = self.started_round(100)
        for fields in ({'winner': self.a}, {'unresolved_reason': Round.QUESTION_LIMIT}):
            with self.subTest(fields):
                for name, value in fields.items():
                    setattr(round_, name, value)
                with self.assertRaises(ValidationError):
                    round_.full_clean()
                round_.refresh_from_db()


class CommandTests(RoundTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command('numeric_round', *args, stdout=out)
        return out.getvalue()

    def test_command_reports_the_error_kind(self):
        make_bank(100)
        self.run_command('create', str(self.game.pk))
        round_ = self.game.get_current_round()

        with self.assertRaisesMessage(CommandError, '[invalid_state]'):
            self.run_command('answer', str(round_.pk), 'ana', '100')
        self.run_command('start', str(round_.pk))
        with self.assertRaisesMessage(CommandError, '[invalid_input]'):
            self.run_command('answer', str(round_.pk), 'ana', '1.5')
        with self.assertRaisesMessage(CommandError, '[not_found]'):
            self.run_command('answer', str(round_.pk), 'nobody', '1')

        self.run_command('answer', str(round_.pk), 'ana', '100')
        self.run_command('answer', str(round_.pk), 'boris', '90')
        output = self.run_command('answer', str(round_.pk), 'cveta', '1')

        self.assertIn('Place 1: ana', output)
        self.assertIn('Place 2: boris', output)
        self.assertIn('Winner: ana', output)


class CloseExpiredRoundsTests(RoundTestCase):
    """The close_expired_rounds worker command (background tasks exercise)."""

    def run_command(self, *args):
        out = StringIO()
        call_command('close_expired_rounds', *args, stdout=out)
        return out.getvalue()

    def test_finds_expired_and_missed_questions_only(self):
        expired = self.started_round(100).questions.get()
        other_game = make_game(make_user('host2'))
        for order, name in enumerate(('x', 'y', 'z'), start=1):
            add_player(other_game, name, order)
        initialize_game(other_game.pk)
        make_bank(5)
        self.clock.at(30)
        running = create_round(other_game.pk)
        start_round(running.pk)  # deadline T0 + 40 s

        self.assertEqual(list(rounds.expired_questions()), [expired])  # 20 s overdue, still found
        self.clock.at(40)  # exactly at the deadline counts as expired
        self.assertEqual(len(rounds.expired_questions()), 2)

    def test_processes_expired_question_with_the_m06_rules(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 99, at=1)
        self.answer(round_, self.b, 90, at=2)

        self.clock.at(9.9)
        self.assertIn('processed: 0, skipped: 0', self.run_command())
        self.clock.at(25)
        output = self.run_command()

        self.assertIn('expired questions: 1, processed: 1, skipped: 0', output)
        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.EVALUATED)
        self.assertEqual(self.places(round_), [(1, self.a), (2, self.b)])

    def test_expired_question_can_lead_to_a_follow_up(self):
        round_ = self.started_round(100, 50)
        self.clock.at(12)
        self.run_command()  # nobody answered: new question for everyone, its own 10 s

        second = round_.questions.get(sequence=2)
        self.assertEqual(second.status, RoundQuestion.ACTIVE)
        self.assertEqual(second.deadline, T0 + timedelta(seconds=22))
        self.assertIn('processed: 0', self.run_command())  # not expired yet

    def test_running_twice_changes_nothing_more(self):
        round_ = self.started_round(100)
        self.answer(round_, self.a, 100, at=1)
        self.clock.at(10)
        self.run_command()
        round_.refresh_from_db()
        before = (round_.status, round_.winner, round_.finished_at, self.places(round_))

        self.clock.at(50)
        self.assertIn('expired questions: 0', self.run_command())

        round_.refresh_from_db()
        self.assertEqual((round_.status, round_.winner, round_.finished_at, self.places(round_)), before)

    def test_question_closed_by_answers_before_the_deadline_is_not_found(self):
        round_ = self.started_round(100)
        for player in (self.a, self.b, self.c):
            self.answer(round_, player, 100, at=1)

        self.clock.at(30)
        self.assertIn('expired questions: 0', self.run_command())

    def test_stale_evaluation_is_a_no_op(self):
        round_ = self.started_round(100, 50)
        question = round_.questions.get()
        self.answer(round_, self.a, 100, at=1)
        self.clock.at(10)
        evaluate_question(round_.pk, question_id=question.pk)  # the first process
        round_.refresh_from_db()
        finished = round_.finished_at

        # A second process still holding the old "active" copy: the conditional update hits 0 rows.
        with transaction.atomic():
            self.assertFalse(rounds._evaluate(round_, question, T0 + timedelta(seconds=40), FirstChoice()))
        round_.refresh_from_db()
        self.assertEqual(round_.finished_at, finished)
        self.assertEqual(round_.questions.count(), 1)
        with self.assertRaises(RoundStateError):
            evaluate_question(round_.pk, question_id=question.pk)

    def test_loop_stops_cleanly_on_ctrl_c(self):
        with mock.patch('games.management.commands.close_expired_rounds.time.sleep', side_effect=KeyboardInterrupt):
            output = self.run_command('--loop', '--interval', '5')

        self.assertIn('Checking every 5 s', output)
        self.assertIn('expired questions: 0', output)
        self.assertTrue(output.rstrip().endswith('Stopped.'))

    def test_interval_must_be_positive(self):
        with self.assertRaises(CommandError):
            self.run_command('--loop', '--interval', '0')
