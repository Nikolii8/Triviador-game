import random
from io import StringIO
from itertools import combinations
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from games.initialization import GameInitializationError, capital_candidates, initialize_game
from games.models import Game, GamePlayer, Round
from territories.map_definition import PROJECT_MAP, MapDefinition, validate_game_map
from territories.models import Adjacency, Capital, Territory

from .factories import add_player, make_choice_round, make_game, make_user


def ring(count):
    territories = tuple((f't{i}', f'T{i}') for i in range(count))
    edges = tuple((f't{i}', f't{(i + 1) % count}') for i in range(count))
    return MapDefinition(territories=territories, adjacencies=edges)


class FixedChoice:
    """Stand-in for random: picks a given capital triple and hands it out in the given order."""

    def __init__(self, triple):
        self.triple = tuple(triple)

    def choice(self, candidates):
        assert tuple(sorted(self.triple)) in candidates, 'triple must be a valid candidate'
        return tuple(sorted(self.triple))

    def sample(self, population, k):
        return list(self.triple)


def waiting_game(player_count=3, name='host'):
    game = make_game(make_user(name))
    players = [add_player(game, f'{name}-p{order}', order) for order in range(1, player_count + 1)]
    return game, players


def snapshot(game):
    """Everything initialization could touch, to prove a refused attempt changed nothing."""
    game.refresh_from_db()
    return {
        'status': game.status,
        'started_at': game.started_at,
        'territories': sorted(Territory.objects.filter(game=game).values_list('slug', 'name', 'owner_id', 'score')),
        'adjacencies': Adjacency.objects.filter(from_territory__game=game).count(),
        'capitals': sorted(Capital.objects.filter(player__game=game).values_list('player_id', 'territory_id', 'health')),
        'players': sorted(GamePlayer.objects.filter(game=game).values_list('pk', 'score')),
    }


class SuccessfulInitializationTests(TestCase):
    def setUp(self):
        self.game, self.players = waiting_game()
        self.players[0].score = 7
        self.players[0].save()
        self.round = make_choice_round(self.game)

    def test_initial_state(self):
        initialize_game(self.game.pk, rng=random.Random(1))

        self.game.refresh_from_db()
        self.assertEqual(self.game.status, Game.IN_PROGRESS)
        self.assertIsNotNone(self.game.started_at)

        territories = Territory.objects.filter(game=self.game)
        self.assertEqual(territories.count(), len(PROJECT_MAP.territories))
        self.assertEqual(set(territories.values_list('slug', 'name')), set(PROJECT_MAP.territories))
        self.assertFalse(territories.exclude(score=0).exists())
        validate_game_map(self.game)  # names, borders, symmetry, no cross-game links

        capitals = Capital.objects.filter(player__game=self.game)
        self.assertEqual(capitals.count(), 3)
        self.assertEqual({capital.player_id for capital in capitals}, {player.pk for player in self.players})
        for capital in capitals:
            self.assertEqual(capital.health, 3)
            self.assertEqual(capital.territory.owner_id, capital.player_id)
            capital.full_clean()

        neutral = territories.filter(owner__isnull=True)
        self.assertEqual(neutral.count(), len(PROJECT_MAP.territories) - 3)
        self.assertEqual(territories.filter(owner__isnull=False).count(), 3)

    def test_player_scores_and_rounds_are_untouched(self):
        rounds_before = list(Round.objects.filter(game=self.game).values())

        initialize_game(self.game.pk)

        self.assertEqual(GamePlayer.objects.get(pk=self.players[0].pk).score, 7)
        self.assertEqual(GamePlayer.objects.get(pk=self.players[1].pk).score, 0)
        self.assertEqual(list(Round.objects.filter(game=self.game).values()), rounds_before)

    def test_map_definition_is_not_modified(self):
        territories, adjacencies = PROJECT_MAP.territories, PROJECT_MAP.adjacencies

        initialize_game(self.game.pk)

        self.assertEqual((PROJECT_MAP.territories, PROJECT_MAP.adjacencies), (territories, adjacencies))


class CapitalChoiceTests(TestCase):
    def test_all_three_capital_pairs_are_non_adjacent(self):
        graph = PROJECT_MAP.neighbors()
        for seed in range(25):
            with self.subTest(seed=seed):
                game, _ = waiting_game(name=f'host{seed}')
                initialize_game(game.pk, rng=random.Random(seed))

                slugs = list(Capital.objects.filter(player__game=game).values_list('territory__slug', flat=True))
                self.assertEqual(len(set(slugs)), 3)
                for a, b in combinations(slugs, 2):
                    self.assertNotIn(b, graph[a])
                    # Same check against the saved records, not just the definition.
                    territory = Territory.objects.get(game=game, slug=a)
                    self.assertFalse(territory.neighbors.filter(slug=b).exists())

    def test_choice_is_controlled_by_the_random_source(self):
        triple = ('pirina', 'kaliakra', 'skali')
        game, players = waiting_game()

        initialize_game(game.pk, rng=FixedChoice(triple))

        for player, slug in zip(players, triple):
            self.assertEqual(Capital.objects.for_player(player).territory.slug, slug)

    def test_assignment_follows_the_random_order_not_the_player_order(self):
        triple = ('skali', 'kaliakra', 'pirina')
        game, players = waiting_game()

        initialize_game(game.pk, rng=FixedChoice(tuple(reversed(triple))))

        self.assertEqual(Capital.objects.for_player(players[0]).territory.slug, 'pirina')
        self.assertEqual(Capital.objects.for_player(players[2]).territory.slug, 'skali')

    def test_same_seed_gives_same_result(self):
        results = []
        for name in ('first', 'second'):
            game, players = waiting_game(name=name)
            initialize_game(game.pk, rng=random.Random(42))
            results.append([Capital.objects.for_player(player).territory.slug for player in players])

        self.assertEqual(results[0], results[1])

    def test_only_valid_triples_are_candidates(self):
        graph = PROJECT_MAP.neighbors()
        candidates = capital_candidates(PROJECT_MAP)

        self.assertTrue(candidates)
        for triple in candidates:
            for a, b in combinations(triple, 2):
                self.assertNotIn(b, graph[a])


class RefusedInitializationTests(TestCase):
    def assert_refused(self, game, message_part, **kwargs):
        before = snapshot(game)
        with self.assertRaises(GameInitializationError) as ctx:
            initialize_game(game.pk, **kwargs)
        self.assertIn(message_part, ' '.join(ctx.exception.messages))
        self.assertEqual(snapshot(game), before)

    def test_missing_game(self):
        with self.assertRaises(GameInitializationError):
            initialize_game(999_999)

    def test_wrong_number_of_players(self):
        for count in (0, 1, 2):
            with self.subTest(players=count):
                game, _ = waiting_game(player_count=count, name=f'host{count}')
                self.assert_refused(game, 'exactly 3 players')

    def test_game_that_is_not_waiting(self):
        for status in (Game.IN_PROGRESS, Game.FINISHED, Game.CANCELLED):
            with self.subTest(status=status):
                game, _ = waiting_game(name=f'host-{status}')
                Game.objects.filter(pk=game.pk).update(status=status)
                self.assert_refused(game, 'Only a waiting game')

    def test_invalid_map_definition(self):
        game, _ = waiting_game()
        self.assert_refused(game, 'map definition is invalid', definition=ring(10))

    def test_map_without_possible_capitals(self):
        # Every territory borders every other one: no valid capital triple at all.
        territories = tuple((f't{i}', f'T{i}') for i in range(9))
        complete = MapDefinition(territories, tuple(combinations([slug for slug, _ in territories], 2)))
        game, _ = waiting_game()

        self.assert_refused(game, 'non-adjacent', definition=complete)
        self.assertFalse(Territory.objects.exists())

    def test_second_attempt_is_refused(self):
        game, _ = waiting_game()
        initialize_game(game.pk, rng=random.Random(3))

        self.assert_refused(game, 'Only a waiting game')
        self.assertEqual(Territory.objects.filter(game=game).count(), len(PROJECT_MAP.territories))
        self.assertEqual(Capital.objects.filter(player__game=game).count(), 3)

    def test_partially_prepared_game_is_not_repaired(self):
        game, players = waiting_game()
        Territory.objects.create(game=game, name='Sredets', slug='sredets', owner=players[0], score=5)

        self.assert_refused(game, 'already has map records')
        self.assertEqual(Territory.objects.filter(game=game).count(), 1)

    def test_failure_during_the_operation_rolls_everything_back(self):
        failures = {
            'adjacency insert fails': mock.patch.object(
                Adjacency.objects, 'bulk_create', side_effect=RuntimeError('disk full')
            ),
            'final check fails': mock.patch(
                'games.initialization.validate_game_map', side_effect=ValidationError('mismatch')
            ),
        }
        for label, failure in failures.items():
            with self.subTest(label):
                game, _ = waiting_game(name=label.split()[0])
                before = snapshot(game)

                with failure, self.assertRaises((RuntimeError, GameInitializationError)):
                    initialize_game(game.pk)

                self.assertEqual(snapshot(game), before)
                self.assertFalse(Territory.objects.filter(game=game).exists())


class IndependentGamesTests(TestCase):
    def test_games_share_structure_but_not_records_or_state(self):
        game_a, _ = waiting_game(name='a')
        game_b, _ = waiting_game(name='b')
        initialize_game(game_a.pk, rng=random.Random(5))
        initialize_game(game_b.pk, rng=random.Random(6))

        for game in (game_a, game_b):
            validate_game_map(game)
        ids_a = set(Territory.objects.filter(game=game_a).values_list('pk', flat=True))
        ids_b = set(Territory.objects.filter(game=game_b).values_list('pk', flat=True))
        self.assertFalse(ids_a & ids_b)

        sredets_a = Territory.objects.get(game=game_a, slug='sredets')
        sredets_a.score = 300
        sredets_a.save()
        self.assertEqual(Territory.objects.get(game=game_b, slug='sredets').score, 0)


class PlayersAfterStartTests(TestCase):
    def setUp(self):
        self.game, self.players = waiting_game()

    def test_creating_a_game_does_not_start_it(self):
        self.assertEqual(self.game.status, Game.WAITING)
        self.assertFalse(Territory.objects.filter(game=self.game).exists())

    def test_players_can_join_and_leave_while_waiting(self):
        self.players[2].delete()
        add_player(self.game, 'replacement', 3)

        self.assertEqual(self.game.players.count(), 3)

    def test_line_up_is_frozen_after_start(self):
        initialize_game(self.game.pk)

        with self.assertRaises(ValidationError):
            self.players[0].delete()
        with self.assertRaises(ValidationError):
            GamePlayer(game=self.game, user=make_user('late'), player_order=3).full_clean()
        self.assertEqual(self.game.players.count(), 3)

    def test_joining_after_start_is_refused_even_on_plain_save(self):
        self.players[2].delete()
        add_player(self.game, 'replacement', 3)
        game_id = self.game.pk
        initialize_game(game_id)

        with self.assertRaises(ValidationError):
            GamePlayer.objects.create(game_id=game_id, user=make_user('late'), player_order=1)

    def test_whole_game_can_still_be_deleted(self):
        initialize_game(self.game.pk)

        self.game.delete()

        self.assertFalse(GamePlayer.objects.exists())
        self.assertFalse(Territory.objects.exists())


class TriggerTests(TestCase):
    """The two ways to run the initialization by hand: the admin action and the management command."""

    def setUp(self):
        self.game, self.players = waiting_game()

    def test_admin_action(self):
        admin_user = make_user('root')
        admin_user.is_staff = admin_user.is_superuser = True
        admin_user.save()
        self.client.force_login(admin_user)
        lonely_game, _ = waiting_game(player_count=1, name='lonely')

        response = self.client.post(
            reverse('admin:games_game_changelist'),
            {'action': 'initialize_selected_games', '_selected_action': [self.game.pk, lonely_game.pk]},
            follow=True,
        )

        messages = [str(message) for message in response.context['messages']]
        self.assertTrue(any('initialized and started' in message for message in messages), messages)
        self.assertTrue(any('exactly 3 players' in message for message in messages), messages)
        self.game.refresh_from_db()
        lonely_game.refresh_from_db()
        self.assertEqual(self.game.status, Game.IN_PROGRESS)
        self.assertEqual(lonely_game.status, Game.WAITING)

    def test_management_command(self):
        out = StringIO()
        call_command('initialize_game', self.game.pk, stdout=out)

        self.assertIn(f'Game #{self.game.pk} initialized', out.getvalue())
        self.assertEqual(out.getvalue().count('capital'), 3)
        with self.assertRaises(CommandError):
            call_command('initialize_game', self.game.pk, stdout=StringIO())
