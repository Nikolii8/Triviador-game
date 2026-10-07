from django.core.exceptions import ValidationError
from django.test import TestCase

from games.models import Game
from territories.map_definition import PROJECT_MAP, validate_game_map
from territories.models import Adjacency, Capital, Territory

from .factories import add_player, build_map, make_game


class GameMapConformanceTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.territories = build_map(self.game)

    def assert_mismatch(self, message_part):
        with self.assertRaises(ValidationError) as ctx:
            validate_game_map(self.game)
        self.assertTrue(
            any(message_part in message for message in ctx.exception.messages),
            ctx.exception.messages,
        )

    def test_records_built_from_the_definition_conform(self):
        validate_game_map(self.game)

    def test_validation_is_read_only(self):
        before = (Territory.objects.count(), Adjacency.objects.count(), Capital.objects.count())

        validate_game_map(self.game)

        self.assertEqual(before, (Territory.objects.count(), Adjacency.objects.count(), Capital.objects.count()))
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, Game.WAITING)

    def test_missing_territory(self):
        self.territories['pirina'].delete()

        self.assert_mismatch('do not match the map')

    def test_renamed_territory(self):
        territory = self.territories['pirina']
        territory.name = 'Pirin'
        territory.save()

        self.assert_mismatch('do not match the map')

    def test_same_count_but_wrong_border(self):
        # Swap one border for another: counts stay the same, the graph is wrong.
        self.territories['skali'].neighbors.remove(self.territories['dunaviya'])
        self.territories['skali'].neighbors.add(self.territories['kaliakra'])

        self.assertEqual(Adjacency.objects.count(), 2 * len(PROJECT_MAP.adjacencies))
        self.assert_mismatch('Adjacencies do not match')

    def test_one_directional_border(self):
        Adjacency.objects.filter(
            from_territory=self.territories['skali'], to_territory=self.territories['dunaviya']
        ).delete()

        self.assert_mismatch('not symmetric')

    def test_border_into_another_game(self):
        other = build_map(make_game('host2'))
        Adjacency.objects.create(from_territory=self.territories['skali'], to_territory=other['kaliakra'])

        self.assert_mismatch('another game')

    def test_owner_from_another_game(self):
        outsider = add_player(make_game('host2'), 'outsider', 1)
        Territory.objects.filter(pk=self.territories['skali'].pk).update(owner=outsider)

        self.assert_mismatch('player from another game')

    def test_empty_game_does_not_conform(self):
        self.game = make_game('host2')

        self.assert_mismatch('do not match the map')


class GameIndependenceTests(TestCase):
    def test_games_share_the_definition_but_not_the_state(self):
        game_a, game_b = make_game('host-a'), make_game('host-b')
        map_a, map_b = build_map(game_a), build_map(game_b)
        alice = add_player(game_a, 'alice', 1)

        sredets_a = map_a['sredets']
        sredets_a.owner = alice
        sredets_a.score = 400
        sredets_a.save()

        sredets_b = map_b['sredets']
        sredets_b.refresh_from_db()
        self.assertIsNone(sredets_b.owner)
        self.assertEqual(sredets_b.score, 0)
        self.assertNotEqual(sredets_a.pk, sredets_b.pk)

        for game in (game_a, game_b):
            validate_game_map(game)
            names = set(game.territories.values_list('slug', 'name'))
            self.assertEqual(names, set(PROJECT_MAP.territories))

        self.assertFalse(
            Adjacency.objects.filter(from_territory__game=game_a, to_territory__game=game_b).exists()
        )
        self.assertFalse(
            Adjacency.objects.filter(from_territory__game=game_b, to_territory__game=game_a).exists()
        )
