from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from games.models import MAX_PLAYERS, Game, GamePlayer, Round, RoundAnswer

from .factories import (
    add_player,
    make_choice_question,
    make_choice_round,
    make_game,
    make_numeric_question,
    make_numeric_round,
    make_user,
)


class GameTests(TestCase):
    def test_new_game_defaults(self):
        creator = make_user('creator')

        game = make_game(creator)

        game.full_clean()
        self.assertEqual(game.status, Game.WAITING)
        self.assertIsNotNone(game.created_at)
        self.assertIsNone(game.started_at)
        self.assertIsNone(game.finished_at)
        self.assertIn(game, creator.created_games.all())

    def test_status_choices(self):
        self.assertEqual(
            [value for value, _ in Game.STATUS_CHOICES],
            ['waiting', 'in_progress', 'finished', 'cancelled'],
        )
        with self.assertRaises(ValidationError):
            Game(created_by=make_user('someone'), status='paused').full_clean()

    def test_status_requires_matching_timestamps(self):
        creator = make_user('creator')
        now = timezone.now()
        invalid = [
            {'status': Game.IN_PROGRESS},
            {'status': Game.FINISHED, 'started_at': now},
        ]
        for fields in invalid:
            with self.subTest(**fields):
                with self.assertRaises(ValidationError):
                    Game(created_by=creator, **fields).full_clean()

        Game(created_by=creator, status=Game.FINISHED, started_at=now, finished_at=now + timedelta(minutes=5)).full_clean()

    def test_games_are_ordered_newest_first(self):
        creator = make_user('creator')
        first = make_game(creator)
        second = make_game(creator)

        self.assertEqual(list(Game.objects.all()), [second, first])

    def test_creator_with_games_cannot_be_deleted(self):
        creator = make_user('creator')
        make_game(creator)

        with self.assertRaises(ProtectedError):
            creator.delete()

    def test_deleting_game_deletes_players_rounds_and_answers(self):
        game = make_game()
        player = add_player(game, 'alice', 1)
        round_ = make_numeric_round(game)
        RoundAnswer.objects.create(round=round_, player=player, numeric_value=1990)

        game.delete()

        self.assertFalse(GamePlayer.objects.exists())
        self.assertFalse(Round.objects.exists())
        self.assertFalse(RoundAnswer.objects.exists())


class GamePlayerTests(TestCase):
    def setUp(self):
        self.game = make_game()

    def test_player_defaults_and_relations(self):
        player = add_player(self.game, 'alice', 1)

        player.full_clean()
        self.assertEqual(player.score, 0)
        self.assertTrue(player.is_active)
        self.assertIsNotNone(player.joined_at)
        self.assertIn(player, self.game.players.all())
        self.assertIn(player, player.user.game_players.all())

    def test_players_are_ordered_by_player_order(self):
        third = add_player(self.game, 'carol', 3)
        first = add_player(self.game, 'alice', 1)
        second = add_player(self.game, 'bob', 2)

        self.assertEqual(list(self.game.players.all()), [first, second, third])

    def test_game_accepts_at_most_max_players(self):
        for order in range(1, MAX_PLAYERS + 1):
            add_player(self.game, f'player{order}', order)

        extra = GamePlayer(game=self.game, user=make_user('late'), player_order=MAX_PLAYERS)
        with self.assertRaises(ValidationError):
            extra.full_clean()

    def test_duplicate_user_or_order_is_rejected_by_validation(self):
        alice = add_player(self.game, 'alice', 1)
        cases = {
            'same user': GamePlayer(game=self.game, user=alice.user, player_order=2),
            'same order': GamePlayer(game=self.game, user=make_user('bob'), player_order=1),
        }
        for label, player in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    player.full_clean()

    def test_same_user_can_join_different_games(self):
        alice = add_player(self.game, 'alice', 1)
        other_game = make_game(alice.user)

        GamePlayer.objects.create(game=other_game, user=alice.user, player_order=1)

        self.assertEqual(alice.user.game_players.count(), 2)

    def test_user_in_a_game_cannot_be_deleted(self):
        player = add_player(self.game, 'alice', 1)

        with self.assertRaises(ProtectedError):
            player.user.delete()


class RoundTests(TestCase):
    def setUp(self):
        self.game = make_game()

    def test_choice_round(self):
        round_ = make_choice_round(self.game)

        round_.full_clean()
        self.assertEqual(round_.status, Round.PENDING)
        self.assertEqual(round_.question_type, Round.CHOICE)
        self.assertEqual(round_.question, round_.choice_question)
        self.assertIn(round_, self.game.rounds.all())
        self.assertIn(round_, round_.choice_question.rounds.all())

    def test_numeric_round(self):
        round_ = make_numeric_round(self.game)

        round_.full_clean()
        self.assertEqual(round_.question_type, Round.NUMERIC)
        self.assertEqual(round_.question, round_.numeric_question)
        self.assertIn(round_, round_.numeric_question.rounds.all())

    def test_round_lifecycle_statuses(self):
        self.assertEqual(
            [value for value, _ in Round.ROUND_STATUS_CHOICES],
            ['pending', 'open', 'closed', 'evaluated'],
        )
        round_ = make_choice_round(self.game)
        for status in (Round.OPEN, Round.CLOSED, Round.EVALUATED):
            round_.status = status
            round_.full_clean()
            round_.save()
        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.EVALUATED)

    def test_rounds_are_ordered_by_number(self):
        third = make_numeric_round(self.game, number=3)
        first = make_choice_round(self.game, number=1)
        second = make_numeric_round(self.game, number=2)

        self.assertEqual(list(self.game.rounds.all()), [first, second, third])

    def test_question_type_is_derived_from_the_question(self):
        # A wrong type passed by hand is corrected on save.
        round_ = Round.objects.create(
            game=self.game, number=1, question_type=Round.NUMERIC, choice_question=make_choice_question()
        )
        round_.refresh_from_db()
        self.assertEqual(round_.question_type, Round.CHOICE)

        round_.choice_question = None
        round_.numeric_question = make_numeric_question()
        round_.save(update_fields=['choice_question', 'numeric_question'])

        round_.refresh_from_db()
        self.assertEqual(round_.question_type, Round.NUMERIC)
        self.assertEqual(round_.question, round_.numeric_question)

    def test_full_clean_sets_question_type_on_new_round(self):
        round_ = Round(game=self.game, number=1, numeric_question=make_numeric_question())

        round_.full_clean()

        self.assertEqual(round_.question_type, Round.NUMERIC)

    def test_round_needs_exactly_one_question(self):
        cases = {
            'no question': Round(game=self.game, number=1),
            'both questions': Round(
                game=self.game,
                number=1,
                choice_question=make_choice_question(),
                numeric_question=make_numeric_question(),
            ),
        }
        for label, round_ in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    round_.full_clean()

    def test_question_used_in_round_cannot_be_deleted(self):
        round_ = make_choice_round(self.game)

        with self.assertRaises(ProtectedError):
            round_.choice_question.delete()


class RoundAnswerTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.alice = add_player(self.game, 'alice', 1)
        self.bob = add_player(self.game, 'bob', 2)
        self.choice_round = make_choice_round(self.game, number=1)
        self.numeric_round = make_numeric_round(self.game, number=2)

    def test_choice_answer(self):
        option = self.choice_round.choice_question.answer_options.get(is_correct=True)

        answer = RoundAnswer.objects.create(
            round=self.choice_round,
            player=self.alice,
            selected_option=option,
            is_correct=True,
            points_awarded=100,
            submitted_at=timezone.now(),
        )

        answer.full_clean()
        self.assertIn(answer, self.choice_round.answers.all())
        self.assertIn(answer, self.alice.answers.all())

    def test_numeric_answer_defaults(self):
        answer = RoundAnswer.objects.create(round=self.numeric_round, player=self.alice, numeric_value=1990)

        answer.full_clean()
        self.assertIsNone(answer.is_correct)
        self.assertEqual(answer.points_awarded, 0)
        self.assertIsNone(answer.submitted_at)

    def test_player_has_answers_across_rounds(self):
        RoundAnswer.objects.create(round=self.choice_round, player=self.alice)
        RoundAnswer.objects.create(round=self.numeric_round, player=self.alice, numeric_value=1989)
        RoundAnswer.objects.create(round=self.numeric_round, player=self.bob, numeric_value=2000)

        self.assertEqual(self.alice.answers.count(), 2)
        self.assertEqual(
            [answer.player for answer in self.numeric_round.answers.all()],
            [self.alice, self.bob],
        )

    def test_invalid_answers(self):
        other_question = make_choice_question('Кой е най-големият океан?')
        foreign_option = other_question.answer_options.first()
        own_option = self.choice_round.choice_question.answer_options.first()
        outsider = add_player(make_game(make_user('host2')), 'outsider', 1)
        RoundAnswer.objects.create(round=self.numeric_round, player=self.bob, numeric_value=1)

        cases = {
            'number in choice round': RoundAnswer(round=self.choice_round, player=self.alice, numeric_value=5),
            'option from another question': RoundAnswer(
                round=self.choice_round, player=self.alice, selected_option=foreign_option
            ),
            'option in numeric round': RoundAnswer(
                round=self.numeric_round, player=self.alice, selected_option=own_option
            ),
            'player from another game': RoundAnswer(round=self.numeric_round, player=outsider, numeric_value=1),
            'both answer kinds': RoundAnswer(
                round=self.choice_round, player=self.alice, selected_option=own_option, numeric_value=5
            ),
            'second answer by same player': RoundAnswer(round=self.numeric_round, player=self.bob, numeric_value=2),
        }

        for label, answer in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    answer.full_clean()

    def test_selected_option_cannot_be_deleted(self):
        option = self.choice_round.choice_question.answer_options.first()
        RoundAnswer.objects.create(round=self.choice_round, player=self.alice, selected_option=option)

        with self.assertRaises(ProtectedError):
            option.delete()
