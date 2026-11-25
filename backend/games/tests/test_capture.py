"""Territory capture and scoring after city capture rounds (M07).

Built on the M06 round tests' fake clock and fixed question choice. Scores use the chosen
value-based system: win 150, territory +100 per capture, capture points = territory value after.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from games import capture
from games.capture import allowed_territories, choose_territory, pending_right
from games.errors import InvalidRoundInput, RoundNotFound, RoundStateError
from games.models import CaptureRight, Game, Round, ScoreEntry
from games.rounds import create_round, evaluate_question, start_round
from games.scoring import RANK_BASED, VALUE_BASED
from questions.models import NumericQuestion
from territories.models import Capital, Territory

from .factories import add_player, make_game, make_user
from .test_rounds import RoundTestCase, make_bank


class CaptureTestCase(RoundTestCase):
    def setUp(self):
        super().setUp()
        make_bank(*[100] * 12)
        self.base = 0

    def finish_round(self, first, second=None, others=()):
        """Play a whole round: `first` exact, `second` 10 off, `others` far off; returns the evaluated round."""
        self.base += 100
        self.clock.at(self.base)
        round_ = create_round(self.game.pk)
        start_round(round_.pk)
        if first is not None:
            self.answer(round_, first, 100, at=self.base + 1)
        if second is not None:
            self.answer(round_, second, 90, at=self.base + 2)
        for player in others:
            self.answer(round_, player, 0, at=self.base + 3)
        round_.refresh_from_db()
        at = self.base + 3  # after the last answer; each follow-up question starts there
        while round_.status != Round.EVALUATED:  # unanswered or tied questions: let each one expire
            at += 10
            round_ = self.evaluate(round_, at=at)
        return round_

    def free(self):
        return Territory.objects.filter(game=self.game, owner__isnull=True)

    def pick(self, player, territory=None):
        territory = territory or allowed_territories(player).order_by('pk').first()
        return choose_territory(self.game.pk, player.pk, territory.pk)

    def score(self, player):
        player.refresh_from_db()
        return player.score

    def leave_free(self, count):
        """Give all but `count` free territories to player c (test setup only)."""
        keep = list(self.free().order_by('pk').values_list('pk', flat=True)[:count])
        self.free().exclude(pk__in=keep).update(owner=self.c)


class DistributionTests(CaptureTestCase):
    def test_first_picks_two_then_second_picks_one(self):
        round_ = self.finish_round(self.a, self.b, [self.c])
        self.assertEqual(
            list(round_.capture_rights.values_list('player', 'turn', 'allowed', 'used')),
            [(self.a.pk, 1, 2, 0), (self.b.pk, 2, 1, 0)],
        )
        self.assertEqual(round_.free_territories, 15)

        with self.assertRaises(RoundStateError):
            self.pick(self.b)  # the second waits for the first
        with self.assertRaises(RoundStateError):
            self.pick(self.c)  # the third has no right

        self.pick(self.a)
        self.pick(self.a)
        with self.assertRaises(RoundStateError):
            self.pick(self.a)  # a third pick is over the limit (now it is b's turn)
        self.pick(self.b)
        with self.assertRaises(RoundStateError):
            self.pick(self.b)  # used up

        self.assertIsNone(pending_right(self.game))
        self.assertEqual(Territory.objects.filter(owner=self.a).count(), 3)  # capital + 2
        self.assertEqual(Territory.objects.filter(owner=self.b).count(), 2)  # capital + 1
        self.assertEqual(self.free().count(), 12)
        self.assertEqual(round_.ranking().count(), 2)  # picks do not change the ranking

    def test_last_two_territories_go_to_the_first(self):
        self.leave_free(2)
        round_ = self.finish_round(self.a, self.b, [self.c])

        self.assertEqual(round_.places_needed, 1)  # no question just for second place
        self.assertEqual(list(round_.capture_rights.values_list('player', 'allowed')), [(self.a.pk, 2)])
        self.pick(self.a)
        self.pick(self.a)
        self.assertFalse(self.free().exists())

    def test_last_territory_goes_to_the_first(self):
        self.leave_free(1)
        round_ = self.finish_round(self.a, self.b, [self.c])

        self.assertEqual(list(round_.capture_rights.values_list('player', 'allowed')), [(self.a.pk, 1)])
        self.pick(self.a)
        self.assertIsNone(pending_right(self.game))


class AdjacencyTests(CaptureTestCase):
    def test_neighbor_is_allowed_and_non_neighbor_is_refused(self):
        self.finish_round(self.a, self.b, [self.c])
        capital = Capital.objects.get(player=self.a).territory
        allowed = set(allowed_territories(self.a))
        self.assertEqual(allowed, set(capital.neighbors.filter(owner__isnull=True)))

        far = self.free().exclude(pk__in=[t.pk for t in allowed]).first()
        with self.assertRaises(InvalidRoundInput):
            self.pick(self.a, far)
        self.assertEqual(CaptureRight.objects.get(player=self.a).used, 0)  # the refusal used no pick

        entry = self.pick(self.a, next(iter(allowed)))
        self.assertEqual(entry.territory.owner, self.a)

    def test_second_pick_may_border_the_first(self):
        self.finish_round(self.a, self.b, [self.c])
        before = set(allowed_territories(self.a))
        # A neighbor of the capital that opens up a territory not reachable before.
        first, new = next(
            (t, n)
            for t in sorted(before, key=lambda t: t.pk)
            for n in t.neighbors.filter(owner__isnull=True).order_by('pk')
            if n not in before
        )

        self.pick(self.a, first)

        self.assertIn(new, set(allowed_territories(self.a)))
        self.pick(self.a, new)
        self.assertEqual(Territory.objects.get(pk=new.pk).owner, self.a)

    def test_reachable_in_two_steps_is_not_a_neighbor(self):
        self.finish_round(self.a, self.b, [self.c])
        capital = Capital.objects.get(player=self.a).territory
        two_steps = (
            self.free()
            .filter(neighbors__in=capital.neighbors.all())
            .exclude(pk__in=capital.neighbors.values('pk'))
            .exclude(pk=capital.pk)
            .first()
        )
        with self.assertRaises(InvalidRoundInput):
            self.pick(self.a, two_steps)

    def test_any_free_territory_when_no_free_neighbor(self):
        self.finish_round(self.a, self.b, [self.c])
        capital = Capital.objects.get(player=self.a).territory
        capital.neighbors.filter(owner__isnull=True).update(owner=self.c)  # a is boxed in

        self.assertEqual(set(allowed_territories(self.a)), set(self.free()))
        far = self.free().order_by('-pk').first()
        self.pick(self.a, far)
        self.assertEqual(Territory.objects.get(pk=far.pk).owner, self.a)


class RightTests(CaptureTestCase):
    def test_refusals_change_nothing(self):
        self.finish_round(self.a, self.b, [self.c])
        stranger_game = make_game(make_user('other-host'))
        stranger = add_player(stranger_game, 'stranger', 1)
        target = allowed_territories(self.a).first()
        other_territory = Territory.objects.create(game=stranger_game, name='Elsewhere', slug='elsewhere')

        cases = {
            'player of another game': (lambda: choose_territory(self.game.pk, stranger.pk, target.pk), RoundStateError),
            'missing player': (lambda: choose_territory(self.game.pk, 999_999, target.pk), RoundNotFound),
            'missing territory': (lambda: choose_territory(self.game.pk, self.a.pk, 999_999), RoundNotFound),
            'missing game': (lambda: choose_territory(999_999, self.a.pk, target.pk), RoundNotFound),
            'territory of another game': (
                lambda: choose_territory(self.game.pk, self.a.pk, other_territory.pk),
                InvalidRoundInput,
            ),
            'taken territory (own capital)': (
                lambda: choose_territory(self.game.pk, self.a.pk, Capital.objects.get(player=self.a).territory_id),
                RoundStateError,
            ),
            'wrong order': (lambda: self.pick(self.b), RoundStateError),
        }
        for label, (attempt, error) in cases.items():
            with self.subTest(label):
                with self.assertRaises(error):
                    attempt()
        self.assertFalse(ScoreEntry.objects.filter(kind=ScoreEntry.CAPTURE).exists())
        self.assertEqual(list(CaptureRight.objects.values_list('used', flat=True)), [0, 0])
        self.assertEqual(self.free().count(), 15)

    def test_no_rights_before_any_round(self):
        with self.assertRaises(RoundStateError):
            self.pick(self.a)

    def test_picking_needs_an_active_game(self):
        self.finish_round(self.a, self.b, [self.c])
        Game.objects.filter(pk=self.game.pk).update(status=Game.CANCELLED)
        with self.assertRaises(RoundStateError):
            self.pick(self.a)

    def test_repeated_evaluation_adds_no_rights_or_award(self):
        round_ = self.finish_round(self.a, self.b, [self.c])
        with self.assertRaises(RoundStateError):
            evaluate_question(round_.pk)
        self.assertEqual(round_.capture_rights.count(), 2)
        self.assertEqual(ScoreEntry.objects.filter(kind=ScoreEntry.ROUND_WIN).count(), 1)
        self.assertEqual(self.score(self.a), 150)


class PointsTests(CaptureTestCase):
    def test_win_and_captures_with_before_and_after(self):
        round_ = self.finish_round(self.a, self.b, [self.c])
        win = ScoreEntry.objects.get(kind=ScoreEntry.ROUND_WIN)
        self.assertEqual((win.player, win.round, win.points), (self.a, round_, 150))
        self.assertEqual((win.player_score_before, win.player_score_after), (0, 150))

        first = self.pick(self.a)
        second = self.pick(self.a)
        third = self.pick(self.b)

        # First capture of a territory starts from 0: it becomes 100 and is worth 100 points.
        self.assertEqual((first.territory_score_before, first.territory_score_after, first.points), (0, 100, 100))
        self.assertEqual((first.player_score_before, first.player_score_after), (150, 250))
        self.assertEqual((second.player_score_before, second.player_score_after), (250, 350))
        # The second-placed player also earns capture points (but no win award).
        self.assertEqual((third.player, third.points, third.player_score_before, third.player_score_after),
                         (self.b, 100, 0, 100))
        self.assertEqual(Territory.objects.get(pk=first.territory_id).score, 100)
        self.assertEqual((self.score(self.a), self.score(self.b), self.score(self.c)), (350, 100, 0))
        self.assertIn('territory 0 + 100 = 100', first.calculation)

    def test_round_without_winner_awards_nothing(self):
        round_ = self.finish_round(None)  # nobody answers any of the three questions

        self.assertIsNone(round_.winner)
        self.assertFalse(round_.capture_rights.exists())
        self.assertFalse(ScoreEntry.objects.exists())
        self.assertEqual(self.free().count(), 15)
        self.finish_round(self.b)  # the next round can be played right away

    def test_scores_never_decrease_for_others(self):
        self.finish_round(self.a, self.b, [self.c])
        self.pick(self.a)
        self.pick(self.a)
        self.pick(self.b)
        scores = (self.score(self.a), self.score(self.b))

        self.finish_round(self.c, None, [self.a, self.b])  # c wins round 2
        self.pick(self.c)

        self.assertGreaterEqual(self.score(self.a), scores[0])
        self.assertGreaterEqual(self.score(self.b), scores[1])

    def test_two_scoring_systems_on_the_same_results(self):
        """Round 1: a first, b second; a picks 2, b picks 1. Round 2: c first, second unresolved; c picks 2."""

        def play(system):
            scores = {'a': 0, 'b': 0, 'c': 0}
            for winner, picks in (('a', [('a', 1), ('a', 1), ('b', 2)]), ('c', [('c', 1), ('c', 1)])):
                scores[winner] += system.win()[0]
                for player, turn in picks:
                    scores[player] += system.capture(turn, 0)[0]
            return scores

        self.assertEqual(play(VALUE_BASED), {'a': 350, 'b': 100, 'c': 350})
        self.assertEqual(play(RANK_BASED), {'a': 250, 'b': 60, 'c': 250})


class PhaseTests(CaptureTestCase):
    def test_next_round_waits_for_the_picks(self):
        self.finish_round(self.a, self.b, [self.c])
        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)
        self.pick(self.a)
        self.pick(self.a)
        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)  # b still has a pick
        self.pick(self.b)

        round_ = create_round(self.game.pk)
        self.assertEqual((round_.number, round_.round_type, round_.free_territories), (2, Round.CITY_CAPTURE, 12))

    def test_phase_ends_when_the_map_is_full(self):
        while self.free().exists():
            self.finish_round(self.a, self.b, [self.c])
            while (right := pending_right(self.game)) is not None:
                self.pick(right.player)

        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, Game.IN_PROGRESS)  # a full map does not end the game
        self.assertEqual(self.game.rounds.count(), 5)  # 15 free territories, 3 per round
        self.assertEqual(ScoreEntry.objects.filter(kind=ScoreEntry.CAPTURE).count(), 15)

    def test_empty_bank_blocks_the_round_until_refilled(self):
        self.finish_round(self.a, self.b, [self.c])
        self.pick(self.a)
        self.pick(self.a)
        self.pick(self.b)
        used = list(self.game.rounds.values_list('numeric_question_id', flat=True))
        NumericQuestion.objects.exclude(pk__in=used).delete()
        with self.assertRaises(RoundStateError):
            create_round(self.game.pk)
        self.assertEqual(self.game.rounds.count(), 1)

        make_bank(42)
        round_ = create_round(self.game.pk)
        self.assertNotIn(round_.numeric_question_id, used)


class AtomicityTests(CaptureTestCase):
    def test_failure_during_a_pick_changes_nothing(self):
        self.finish_round(self.a, self.b, [self.c])
        target = allowed_territories(self.a).first()

        with mock.patch.object(ScoreEntry.objects, 'create', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                self.pick(self.a, target)

        target.refresh_from_db()
        self.assertIsNone(target.owner)
        self.assertEqual(target.score, 0)
        self.assertEqual(CaptureRight.objects.get(player=self.a).used, 0)
        self.assertEqual(self.score(self.a), 150)  # only the win

    def test_failure_while_recording_the_result_keeps_the_round_open(self):
        round_ = create_round(self.game.pk)
        start_round(round_.pk)
        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 90, at=2)

        with mock.patch.object(capture.CaptureRight.objects, 'create', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.answer(round_, self.c, 0, at=3)

        round_.refresh_from_db()
        self.assertEqual(round_.status, Round.OPEN)
        self.assertIsNone(round_.winner)
        self.assertFalse(ScoreEntry.objects.exists())
        self.assertEqual(self.score(self.a), 0)


class IncompleteRankingTests(CaptureTestCase):
    def test_unresolved_second_keeps_firsts_rights_and_leaves_territories(self):
        # a exact; b and c tie for second on every question until the bank runs out.
        round_ = create_round(self.game.pk)
        start_round(round_.pk)
        self.answer(round_, self.a, 100, at=1)
        self.answer(round_, self.b, 90, at=2)
        self.answer(round_, self.c, 110, at=2)
        for at in (3, 4):
            self.answer(round_, self.b, 90, at=at)
            self.answer(round_, self.c, 110, at=at)
        round_.refresh_from_db()

        self.assertEqual((round_.winner, round_.unresolved_reason), (self.a, Round.QUESTION_LIMIT))
        self.assertEqual(list(round_.capture_rights.values_list('player', 'allowed')), [(self.a.pk, 2)])
        self.pick(self.a)
        self.pick(self.a)
        self.assertEqual(self.free().count(), 13)  # the would-be second pick stays for the next round
        self.assertEqual(create_round(self.game.pk).free_territories, 13)

    def test_only_first_answered(self):
        round_ = self.finish_round(self.a)
        self.assertEqual(round_.unresolved_reason, Round.NOT_ENOUGH_ANSWERS)
        self.assertEqual(list(round_.capture_rights.values_list('player', 'allowed')), [(self.a.pk, 2)])


class CommandTests(CaptureTestCase):
    def test_capture_command(self):
        self.finish_round(self.a, self.b, [self.c])
        out = StringIO()
        call_command('capture', 'show', str(self.game.pk), stdout=out)
        self.assertIn("ana's turn", out.getvalue())

        target = allowed_territories(self.a).first()
        call_command('capture', 'pick', str(self.game.pk), 'ana', target.slug, stdout=out)
        self.assertIn(f'ana took {target.name}: 150 + 100 = 250', out.getvalue())
        with self.assertRaisesMessage(CommandError, '[invalid_state]'):
            call_command('capture', 'pick', str(self.game.pk), 'cveta', target.slug, stdout=out)
