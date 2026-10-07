from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from games.models import Game
from territories.models import Adjacency, Capital, Territory

from .factories import add_player, build_map, make_game


class IntegrityTestCase(TestCase):
    def assert_integrity_error(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()


class TerritoryTests(IntegrityTestCase):
    def setUp(self):
        self.game = make_game()

    def test_territory_defaults(self):
        territory = Territory.objects.create(game=self.game, name='Sredets', slug='sredets')

        territory.full_clean()
        self.assertEqual(territory.score, 0)
        self.assertIsNone(territory.owner)
        self.assertIn(territory, self.game.territories.all())

    def test_name_and_slug_are_unique_within_a_game(self):
        Territory.objects.create(game=self.game, name='Sredets', slug='sredets')
        cases = {
            'name': {'name': 'Sredets', 'slug': 'other'},
            'slug': {'name': 'Other', 'slug': 'sredets'},
        }
        for label, fields in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValidationError):
                    Territory(game=self.game, **fields).full_clean()
                self.assert_integrity_error(lambda: Territory.objects.create(game=self.game, **fields))

    def test_same_name_and_slug_allowed_in_another_game(self):
        Territory.objects.create(game=self.game, name='Sredets', slug='sredets')

        Territory.objects.create(game=make_game('host2'), name='Sredets', slug='sredets')

        self.assertEqual(Territory.objects.filter(slug='sredets').count(), 2)

    def test_negative_score_is_rejected(self):
        with self.assertRaises(ValidationError):
            Territory(game=self.game, name='Sredets', slug='sredets', score=-1).full_clean()
        self.assert_integrity_error(
            lambda: Territory.objects.create(game=self.game, name='Sredets', slug='sredets', score=-1)
        )

    def test_owner_must_be_from_the_same_game(self):
        outsider = add_player(make_game('host2'), 'outsider', 1)

        with self.assertRaises(ValidationError) as ctx:
            Territory(game=self.game, name='Sredets', slug='sredets', owner=outsider).full_clean()
        self.assertIn('owner', ctx.exception.message_dict)

    def test_owned_territory(self):
        player = add_player(self.game, 'alice', 1)

        territory = Territory.objects.create(game=self.game, name='Sredets', slug='sredets', owner=player)

        territory.full_clean()
        self.assertEqual(list(player.territories.all()), [territory])


class AdjacencyTests(IntegrityTestCase):
    def setUp(self):
        self.game = make_game()
        self.sredets = Territory.objects.create(game=self.game, name='Sredets', slug='sredets')
        self.kukeri = Territory.objects.create(game=self.game, name='Kukeri', slug='kukeri')

    def test_neighbors_are_symmetric(self):
        self.sredets.neighbors.add(self.kukeri)

        self.assertEqual(list(self.sredets.neighbors.all()), [self.kukeri])
        self.assertEqual(list(self.kukeri.neighbors.all()), [self.sredets])

    def test_adding_the_same_border_twice_keeps_one_logical_link(self):
        self.sredets.neighbors.add(self.kukeri)
        self.kukeri.neighbors.add(self.sredets)

        self.assertEqual(Adjacency.objects.count(), 2)  # one row per direction
        self.assertEqual(self.sredets.neighbors.count(), 1)

    # neighbors.add() runs inside an atomic block without a savepoint, so inside a test
    # transaction the failing call needs its own atomic() to keep the test usable afterwards.
    def test_self_reference_is_rejected(self):
        with self.assertRaises(ValidationError), transaction.atomic():
            self.sredets.neighbors.add(self.sredets)
        self.assert_integrity_error(
            lambda: Adjacency.objects.create(from_territory=self.sredets, to_territory=self.sredets)
        )

    def test_neighbor_from_another_game_is_rejected(self):
        foreign = Territory.objects.create(game=make_game('host2'), name='Kukeri', slug='kukeri')

        with self.assertRaises(ValidationError), transaction.atomic():
            self.sredets.neighbors.add(foreign)
        with self.assertRaises(ValidationError):
            Adjacency(from_territory=self.sredets, to_territory=foreign).full_clean()
        self.assertFalse(Adjacency.objects.exists())

    def test_duplicate_directed_row_is_rejected_by_the_database(self):
        Adjacency.objects.create(from_territory=self.sredets, to_territory=self.kukeri)

        self.assert_integrity_error(
            lambda: Adjacency.objects.create(from_territory=self.sredets, to_territory=self.kukeri)
        )


class CapitalTests(IntegrityTestCase):
    def setUp(self):
        self.game = make_game()
        self.alice = add_player(self.game, 'alice', 1)
        self.bob = add_player(self.game, 'bob', 2)
        self.sredets = Territory.objects.create(game=self.game, name='Sredets', slug='sredets', owner=self.alice)
        self.kukeri = Territory.objects.create(game=self.game, name='Kukeri', slug='kukeri', owner=self.alice)

    def test_capital_defaults_and_access(self):
        capital = Capital.objects.create(territory=self.sredets, player=self.alice)

        capital.full_clean()
        self.assertEqual(capital.health, 3)
        self.assertEqual(capital.game, self.game)
        self.assertFalse(capital.is_destroyed)
        self.assertEqual(self.alice.capital, capital)
        self.assertEqual(self.sredets.capital, capital)
        self.assertEqual(Capital.objects.for_player(self.alice), capital)

    def test_missing_capital_is_none(self):
        self.assertIsNone(Capital.objects.for_player(self.bob))
        self.assertFalse(hasattr(self.bob, 'capital'))

    def test_health_range(self):
        for health in (0, 1, 2, 3):
            with self.subTest(health=health):
                Capital(territory=self.sredets, player=self.alice, health=health).full_clean()

        capital = Capital(territory=self.sredets, player=self.alice, health=0)
        self.assertTrue(capital.is_destroyed)

        with self.assertRaises(ValidationError):
            Capital(territory=self.sredets, player=self.alice, health=4).full_clean()
        self.assert_integrity_error(
            lambda: Capital.objects.create(territory=self.sredets, player=self.alice, health=4)
        )

    def test_destroyed_capital_is_kept(self):
        capital = Capital.objects.create(territory=self.sredets, player=self.alice)

        capital.health = 0
        capital.full_clean()
        capital.save()

        self.assertTrue(Capital.objects.filter(pk=capital.pk, health=0).exists())

    def test_one_capital_per_player_and_per_territory(self):
        Capital.objects.create(territory=self.sredets, player=self.alice)
        self.kukeri.owner = self.bob
        self.kukeri.save()

        cases = {
            'second capital for player': lambda: Capital.objects.create(territory=self.kukeri, player=self.alice),
            'second capital on territory': lambda: Capital.objects.create(territory=self.sredets, player=self.bob),
        }
        for label, create in cases.items():
            with self.subTest(label):
                self.assert_integrity_error(create)

        with self.assertRaises(ValidationError):
            Capital(territory=self.kukeri, player=self.alice).full_clean()

    def test_capital_must_be_owned_by_its_player(self):
        with self.assertRaises(ValidationError) as ctx:
            Capital(territory=self.sredets, player=self.bob).full_clean()
        self.assertIn('territory', ctx.exception.message_dict)

        unowned = Territory.objects.create(game=self.game, name='Ezera', slug='ezera')
        with self.assertRaises(ValidationError):
            Capital(territory=unowned, player=self.bob).full_clean()

    def test_capital_cannot_cross_games(self):
        other_game = make_game('host2')
        outsider = add_player(other_game, 'outsider', 1)

        with self.assertRaises(ValidationError):
            Capital(territory=self.sredets, player=outsider).full_clean()

    def test_plain_save_skips_model_validation(self):
        # Documents the split between database constraints and application validation:
        # save() does not call full_clean(), so cross-game ownership is only caught by validation.
        outsider = add_player(make_game('host2'), 'outsider', 1)
        capital = Capital.objects.create(territory=self.sredets, player=outsider)

        with self.assertRaises(ValidationError):
            capital.full_clean()


class AccessTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.alice = add_player(self.game, 'alice', 1)
        self.territories = build_map(self.game)

    def test_territories_of_a_game(self):
        build_map(make_game('host2'))

        self.assertEqual(self.game.territories.count(), 18)
        self.assertEqual(Territory.objects.for_game(self.game).count(), 18)

    def test_owned_territories_and_capital_of_a_player(self):
        owned = [self.territories['sredets'], self.territories['kukeri']]
        for territory in owned:
            territory.owner = self.alice
            territory.save()
        Capital.objects.create(territory=self.territories['sredets'], player=self.alice)

        self.assertEqual(set(Territory.objects.owned_by(self.alice)), set(owned))
        self.assertEqual(set(self.alice.territories.all()), set(owned))
        self.assertEqual(Capital.objects.for_player(self.alice).territory, self.territories['sredets'])

    def test_creating_a_game_does_not_initialize_the_map(self):
        game = Game.objects.create(created_by=self.alice.user)

        self.assertEqual(game.status, Game.WAITING)
        self.assertFalse(game.territories.exists())
        self.assertFalse(Capital.objects.filter(territory__game=game).exists())
