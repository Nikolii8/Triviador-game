"""Battles (M08): choice question, numeric follow-up and the consequences for the target.

Built on the M06 round tests' fake clock and fixed question choice. The map is filled in the
test setup (free territories shared out by id), standing in for the M07 conquest phase.
"""

from datetime import timedelta
from unittest import mock

from games import capture
from games.battles import attackable_territories, create_battle
from games.errors import BattleTargetChanged, InvalidRoundInput, RoundError, RoundNotFound, RoundStateError
from games.models import Game, Round, RoundAnswer, RoundQuestion, ScoreEntry
from games.rounds import create_round, evaluate_question, round_state, start_round
from questions.models import AnswerOption, Category, ChoiceQuestion, NumericQuestion
from territories.models import Capital, Territory

from .factories import add_player, make_game, make_user
from .test_rounds import T0, RoundTestCase, make_bank


def make_choice_bank(count, start_pk=None):
    category, _ = Category.objects.get_or_create(name='Избор')
    questions = []
    for i in range(count):
        extra = {'pk': start_pk + i} if start_pk is not None else {}
        question = ChoiceQuestion.objects.create(category=category, text=f'Избор {i + 1}?', **extra)
        for j in range(4):
            AnswerOption.objects.create(question=question, text=f'Отговор {j + 1}', is_correct=j == 0)
        questions.append(question)
    return questions


class BattleTestCase(RoundTestCase):
    def setUp(self):
        super().setUp()
        free = list(Territory.objects.filter(game=self.game, owner__isnull=True).order_by('pk'))
        for index, territory in enumerate(free):
            territory.owner = (self.a, self.b, self.c)[index % 3]
            territory.save(update_fields=['owner'])
        self.base = 0

    def target_of(self, defender, attacker=None, capital=False):
        """A territory of `defender` that `attacker` (default a) may attack."""
        attacker = attacker or self.a
        candidates = attackable_territories(attacker).filter(owner=defender)
        capitals = Capital.objects.values('territory')
        if capital:
            territory = Capital.objects.get(player=defender).territory
            if not candidates.filter(pk=territory.pk).exists():
                # Test setup: give the attacker a neighbor of the defender's capital.
                neighbor = territory.neighbors.exclude(pk__in=capitals).first()
                Territory.objects.filter(pk=neighbor.pk).update(owner=attacker)
            return territory
        territory = candidates.exclude(pk__in=capitals).order_by('pk').first()
        if territory is None:
            # Test setup: hand the attacker a territory next to one of the defender's.
            border = Territory.objects.filter(owner=defender).exclude(pk__in=capitals).order_by('pk')
            for own in border:
                neighbor = own.neighbors.exclude(owner=defender).exclude(pk__in=capitals).first()
                if neighbor:
                    Territory.objects.filter(pk=neighbor.pk).update(owner=attacker)
                    return own
        return territory

    def battle(self, defender=None, capital=False, choice=1, numeric=0, attacker=None):
        """Create and start a battle with `choice` choice and `numeric` numeric questions in the bank."""
        self.choice_bank = make_choice_bank(choice) if choice else []
        self.numeric_bank = make_bank(*[100] * numeric) if numeric else []
        target = self.target_of(defender or self.b, attacker, capital)
        self.base += 100
        self.clock.at(self.base)
        round_ = create_battle(self.game.pk, (attacker or self.a).pk, target.pk)
        start_round(round_.pk)
        round_.refresh_from_db()
        return round_

    def step(self, round_, sequence=None):
        questions = round_.questions.order_by('-sequence')
        return questions.get(sequence=sequence) if sequence else questions.first()

    def right(self, round_):
        return self.step(round_).options.get(is_correct=True).option_id

    def wrong(self, round_):
        return self.step(round_).options.filter(is_correct=False).first().option_id

    def at(self, seconds):
        return self.base + seconds

    def score(self, player):
        player.refresh_from_db()
        return player.score


class TargetTests(BattleTestCase):
    def test_refused_targets_create_nothing(self):
        make_choice_bank(1)
        capitals = Capital.objects.values('territory')
        own = Territory.objects.filter(owner=self.a).exclude(pk__in=capitals).first()
        far = (
            Territory.objects.filter(game=self.game)
            .exclude(owner=self.a)
            .exclude(pk__in=attackable_territories(self.a).values('pk'))
            .first()
        )
        other_game = make_game(make_user('other-host'))
        elsewhere = Territory.objects.create(game=other_game, name='Elsewhere', slug='elsewhere')
        target = self.target_of(self.b)

        cases = {
            'own territory': (lambda: create_battle(self.game.pk, self.a.pk, own.pk), InvalidRoundInput),
            'not adjacent': (lambda: create_battle(self.game.pk, self.a.pk, far.pk), InvalidRoundInput),
            'other game': (lambda: create_battle(self.game.pk, self.a.pk, elsewhere.pk), InvalidRoundInput),
            'wrong defender': (
                lambda: create_battle(self.game.pk, self.a.pk, target.pk, defender_id=self.c.pk),
                InvalidRoundInput,
            ),
            'missing territory': (lambda: create_battle(self.game.pk, self.a.pk, 999_999), RoundNotFound),
            'attacker of another game': (
                lambda: create_battle(self.game.pk, add_player(other_game, 'x', 1).pk, target.pk),
                RoundStateError,
            ),
        }
        for label, (attempt, error) in cases.items():
            with self.subTest(label):
                with self.assertRaises(error):
                    attempt()
        self.assertFalse(Round.objects.filter(game=self.game).exists())

        round_ = create_battle(self.game.pk, self.a.pk, target.pk, defender_id=self.b.pk)
        self.assertEqual((round_.attacker, round_.defender, round_.target), (self.a, self.b, target))
        self.assertEqual((round_.round_type, round_.places_needed, round_.status), (Round.BATTLE, 1, Round.PENDING))

    def test_free_territory_and_unfinished_map(self):
        make_choice_bank(1)
        target = self.target_of(self.b)
        Territory.objects.filter(pk=target.pk).update(owner=None)

        with self.assertRaises(RoundError):
            create_battle(self.game.pk, self.a.pk, target.pk)  # free target, map not full
        self.assertFalse(Round.objects.exists())

    def test_capital_makes_a_capital_attack(self):
        make_choice_bank(1)
        capital = self.target_of(self.b, capital=True)
        round_ = create_battle(self.game.pk, self.a.pk, capital.pk)
        self.assertEqual(round_.round_type, Round.CAPITAL_ATTACK)

    def test_one_round_at_a_time_and_no_city_capture_on_a_full_map(self):
        round_ = self.battle()
        with self.assertRaises(RoundStateError):
            create_battle(self.game.pk, self.c.pk, self.target_of(self.b, self.c).pk)
        self.assertEqual(Round.objects.count(), 1)
        self.assertEqual(round_.questions.get().kind, RoundQuestion.CHOICE)
        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)

    def test_no_choice_question_refuses_without_changes(self):
        target = self.target_of(self.b)
        make_bank(100)  # only numeric questions
        category, _ = Category.objects.get_or_create(name='Избор')
        broken = ChoiceQuestion.objects.create(category=category, text='Само три опции?')
        for j in range(3):
            AnswerOption.objects.create(question=broken, text=str(j), is_correct=j == 0)

        with self.assertRaises(RoundStateError):
            create_battle(self.game.pk, self.a.pk, target.pk)
        self.assertFalse(Round.objects.exists())


class AnswerTests(BattleTestCase):
    def test_option_must_be_one_of_this_questions_options(self):
        round_ = self.battle(choice=2)
        other_question = self.choice_bank[1]  # existing, but not the one asked
        foreign = other_question.answer_options.get(is_correct=True)

        for value in (foreign.pk, 999_999, 'abc', 1.5):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRoundInput):
                    self.answer(round_, self.a, value, at=self.at(1))
        self.assertFalse(RoundAnswer.objects.exists())

        answer = self.answer(round_, self.a, self.right(round_), at=self.at(2))  # the right is kept
        self.assertTrue(answer.is_correct)
        self.assertEqual(answer.selected_option.question, round_.choice_question)

    def test_third_player_cannot_answer(self):
        round_ = self.battle()
        with self.assertRaises(RoundStateError):
            self.answer(round_, self.c, self.right(round_), at=self.at(1))

    def test_numeric_step_takes_a_number_not_an_option(self):
        round_ = self.battle(numeric=1)
        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        self.answer(round_, self.b, self.right(round_), at=self.at(2))
        with self.assertRaises(InvalidRoundInput):
            self.answer(round_, self.a, '99.5', at=self.at(3))
        self.answer(round_, self.a, 99, at=self.at(3))


class ResultTests(BattleTestCase):
    def test_one_correct_answer_wins(self):
        round_ = self.battle()
        self.answer(round_, self.a, self.wrong(round_), at=self.at(1))  # faster, but wrong
        self.answer(round_, self.b, self.right(round_), at=self.at(5))

        round_.refresh_from_db()
        self.assertEqual((round_.status, round_.winner), (Round.EVALUATED, self.b))
        self.assertEqual(round_.questions.count(), 1)

    def test_two_correct_answers_lead_to_a_numeric_question(self):
        round_ = self.battle(numeric=1)
        self.answer(round_, self.b, self.right(round_), at=self.at(1))  # faster, but speed does not count
        self.answer(round_, self.a, self.right(round_), at=self.at(4))

        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.OPEN)
        numeric = self.step(round_)
        self.assertEqual((numeric.sequence, numeric.kind), (2, RoundQuestion.NUMERIC))
        self.assertEqual(numeric.numeric_question, self.numeric_bank[0])
        self.assertIsNone(numeric.choice_question)
        self.assertEqual(set(numeric.participants.all()), {self.a, self.b})
        self.assertEqual(numeric.started_at, T0 + timedelta(seconds=self.at(4)))
        self.assertEqual(numeric.deadline, T0 + timedelta(seconds=self.at(14)))

        self.answer(round_, self.a, 98, at=self.at(5))
        self.answer(round_, self.b, 90, at=self.at(6))
        round_.refresh_from_db()
        self.assertEqual(round_.winner, self.a)

    def test_no_correct_answer_leads_to_another_choice_question(self):
        round_ = self.battle(choice=2)
        self.answer(round_, self.a, self.wrong(round_), at=self.at(1))
        round_ = self.evaluate(round_, at=self.at(10))  # b did not answer: a wrong answer is no better

        follow_up = self.step(round_)
        self.assertEqual((follow_up.sequence, follow_up.kind), (2, RoundQuestion.CHOICE))
        self.assertEqual(follow_up.choice_question, self.choice_bank[1])
        self.assertEqual(follow_up.deadline, T0 + timedelta(seconds=self.at(20)))
        self.assertIsNone(round_.winner)

    def test_numeric_tie_asks_numeric_again_never_choice(self):
        round_ = self.battle(choice=3, numeric=2)
        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        self.answer(round_, self.b, self.right(round_), at=self.at(1))
        self.answer(round_, self.a, 95, at=self.at(2))
        self.answer(round_, self.b, 105, at=self.at(2))  # same distance, same moment

        third = self.step(round_)
        self.assertEqual((third.sequence, third.kind), (3, RoundQuestion.NUMERIC))
        self.answer(round_, self.b, 100, at=self.at(3))
        self.answer(round_, self.a, 0, at=self.at(4))

        round_.refresh_from_db()
        self.assertEqual((round_.winner, round_.unresolved_reason), (self.b, ''))  # decided on the 3rd
        self.assertEqual(round_.questions.count(), 3)

    def test_numeric_without_answers_stays_numeric_for_the_same_pair(self):
        round_ = self.battle(choice=3, numeric=2)
        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        self.answer(round_, self.b, self.right(round_), at=self.at(1))
        round_ = self.evaluate(round_, at=self.at(11))

        third = self.step(round_)
        self.assertEqual(third.kind, RoundQuestion.NUMERIC)
        self.assertEqual(set(third.participants.all()), {self.a, self.b})

    def test_limit_of_three_questions(self):
        round_ = self.battle(choice=4)
        self.evaluate(round_, at=self.at(10))
        self.evaluate(round_, at=self.at(20))
        round_ = self.evaluate(round_, at=self.at(30))

        self.assertEqual((round_.status, round_.winner), (Round.EVALUATED, None))
        self.assertEqual(round_.unresolved_reason, Round.QUESTION_LIMIT)
        self.assertEqual(round_.questions.count(), 3)
        self.assertFalse(ScoreEntry.objects.exists())

    def test_bank_exhausted_and_limit_precedence(self):
        round_ = self.battle(numeric=0)
        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        self.answer(round_, self.b, self.right(round_), at=self.at(1))  # needs a numeric one: none left

        round_.refresh_from_db()
        self.assertEqual((round_.winner, round_.unresolved_reason), (None, Round.BANK_EXHAUSTED))

        round_ = self.battle(choice=3, numeric=0)
        self.evaluate(round_, at=self.at(10))
        self.evaluate(round_, at=self.at(20))
        round_ = self.evaluate(round_, at=self.at(30))  # a 4th would be needed and the bank is empty too
        self.assertEqual(round_.unresolved_reason, Round.QUESTION_LIMIT)


class ConsequenceTests(BattleTestCase):
    def test_successful_attack_takes_the_territory(self):
        target = self.target_of(self.b)
        Territory.objects.filter(pk=target.pk).update(score=100)  # already worth something
        defender_before = self.score(self.b)
        round_ = self.battle()

        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        self.answer(round_, self.b, self.wrong(round_), at=self.at(2))

        target.refresh_from_db()
        self.assertEqual(target.owner, self.a)
        self.assertEqual(target.score, 200)  # 100 + 100: grows, never reset
        conquest = ScoreEntry.objects.get(kind=ScoreEntry.CONQUEST)
        self.assertEqual((conquest.territory_score_before, conquest.territory_score_after, conquest.points),
                         (100, 200, 200))
        self.assertEqual(self.score(self.a), 150 + 200)
        self.assertEqual(self.score(self.b), defender_before)

    def test_successful_defence_keeps_the_territory(self):
        target = self.target_of(self.b)
        round_ = self.battle()

        self.answer(round_, self.b, self.right(round_), at=self.at(1))
        self.answer(round_, self.a, self.wrong(round_), at=self.at(2))

        target.refresh_from_db()
        self.assertEqual((target.owner, target.score), (self.b, 0))
        self.assertEqual(self.score(self.b), 150)
        self.assertEqual(self.score(self.a), 0)
        self.assertFalse(ScoreEntry.objects.filter(kind=ScoreEntry.CONQUEST).exists())

    def test_no_winner_changes_nothing(self):
        target = self.target_of(self.b)
        round_ = self.battle(choice=1)
        round_ = self.evaluate(round_, at=self.at(10))  # nobody answers, no choice question left

        self.assertEqual(round_.unresolved_reason, Round.BANK_EXHAUSTED)
        target.refresh_from_db()
        self.assertEqual(target.owner, self.b)
        self.assertFalse(ScoreEntry.objects.exists())

    def test_capital_attack_awards_without_touching_the_capital(self):
        capital = Capital.objects.get(player=self.b)
        round_ = self.battle(capital=True)
        self.assertEqual(round_.round_type, Round.CAPITAL_ATTACK)

        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        self.answer(round_, self.b, self.wrong(round_), at=self.at(2))

        round_.refresh_from_db()
        self.assertEqual(round_.winner, self.a)
        self.assertEqual(self.score(self.a), 150)
        capital.refresh_from_db()
        capital.territory.refresh_from_db()
        self.assertEqual((capital.player, capital.health, capital.territory.owner), (self.b, 3, self.b))
        self.assertEqual(capital.territory.score, 0)


class IntegrityTests(BattleTestCase):
    def test_repeated_evaluation_repeats_nothing(self):
        round_ = self.battle()
        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        round_ = self.evaluate(round_, at=self.at(10))

        with self.assertRaises(RoundStateError):
            self.evaluate(round_, at=self.at(20))
        self.assertEqual(ScoreEntry.objects.filter(kind=ScoreEntry.ROUND_WIN).count(), 1)
        self.assertEqual(ScoreEntry.objects.filter(kind=ScoreEntry.CONQUEST).count(), 1)
        self.assertEqual(self.score(self.a), 250)

    def test_changed_target_owner_is_refused_without_result(self):
        target = self.target_of(self.b)
        round_ = self.battle()
        self.answer(round_, self.a, self.right(round_), at=self.at(1))
        Territory.objects.filter(pk=target.pk).update(owner=self.c)  # changed behind the battle's back

        self.answer(round_, self.b, self.wrong(round_), at=self.at(2))  # accepted and kept
        with self.assertRaises(BattleTargetChanged):
            self.evaluate(round_, at=self.at(10))

        round_.refresh_from_db()
        self.assertEqual((round_.status, round_.winner), (Round.OPEN, None))
        self.assertEqual(RoundAnswer.objects.filter(round=round_).count(), 2)
        self.assertFalse(ScoreEntry.objects.exists())
        self.assertEqual(Territory.objects.get(pk=target.pk).owner, self.c)  # no other target is taken

    def test_failure_while_applying_rolls_everything_back(self):
        target = self.target_of(self.b)
        round_ = self.battle()
        self.answer(round_, self.a, self.right(round_), at=self.at(1))

        with mock.patch.object(capture, 'credit', side_effect=[mock.DEFAULT, RuntimeError('disk full')]):
            with self.assertRaises(RuntimeError):
                self.evaluate(round_, at=self.at(10))

        round_.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual((round_.status, round_.winner), (Round.OPEN, None))
        self.assertEqual((target.owner, target.score), (self.b, 0))
        self.assertFalse(ScoreEntry.objects.exists())
        self.assertEqual(self.score(self.a), 0)

    def test_game_must_be_active(self):
        round_ = self.battle()
        Game.objects.filter(pk=self.game.pk).update(status=Game.CANCELLED)
        with self.assertRaises(RoundStateError):
            self.answer(round_, self.a, self.right(round_), at=self.at(1))


class DataTests(BattleTestCase):
    def test_choice_then_numeric_is_rebuilt_from_the_records(self):
        round_ = self.battle(numeric=1)
        first_right = self.right(round_)
        self.answer(round_, self.a, first_right, at=self.at(1))
        self.answer(round_, self.b, first_right, at=self.at(2))
        self.answer(round_, self.a, 101, at=self.at(3))
        self.answer(round_, self.b, 80, at=self.at(4))
        # Editing the bank afterwards changes neither the history nor the result.
        AnswerOption.objects.filter(pk=first_right).update(text='Променено', is_correct=False)
        NumericQuestion.objects.filter(pk=self.numeric_bank[0].pk).update(correct_answer=80)

        state = round_state(round_.pk)

        choice, numeric = state['questions']
        self.assertEqual((choice['kind'], choice['bank_question']), ('choice', self.choice_bank[0].pk))
        self.assertEqual(choice['correct_answer'], first_right)
        self.assertEqual([o['text'] for o in choice['options']][0], 'Отговор 1')
        self.assertEqual([(a['player'], a['value'], a['is_correct']) for a in choice['answers']],
                         [(self.a.pk, first_right, True), (self.b.pk, first_right, True)])
        self.assertEqual((numeric['kind'], numeric['bank_question'], numeric['correct_answer']),
                         ('numeric', self.numeric_bank[0].pk, 100))
        self.assertEqual([(a['player'], a['value']) for a in numeric['answers']], [(self.a.pk, 101), (self.b.pk, 80)])
        self.assertEqual(state['winner'], self.a.pk)
        self.assertTrue(RoundAnswer.objects.get(round=round_, player=self.a, question__sequence=1).is_correct)

    def test_same_id_in_both_question_models_stays_unambiguous(self):
        choice = make_choice_bank(1, start_pk=777)[0]
        category, _ = Category.objects.get_or_create(name='Числа')
        numeric = NumericQuestion.objects.create(pk=777, category=category, text='Колко?', correct_answer=100)
        self.choice_bank, self.numeric_bank = [choice], [numeric]
        target = self.target_of(self.b)
        round_ = create_battle(self.game.pk, self.a.pk, target.pk)
        start_round(round_.pk)
        right = round_.questions.get().options.get(is_correct=True).option_id

        # Choice #777 being used does not count as numeric #777 being used: the step can still be asked.
        self.answer(round_, self.a, right, at=1)
        self.answer(round_, self.b, right, at=1)

        first, second = round_.questions.order_by('sequence')
        self.assertEqual((first.kind, first.choice_question_id, first.numeric_question_id), ('choice', 777, None))
        self.assertEqual((second.kind, second.numeric_question_id, second.choice_question_id), ('numeric', 777, None))
        self.assertEqual(second.correct_answer, 100)
        self.assertEqual(first.options.count(), 4)
        self.assertEqual(second.options.count(), 0)
