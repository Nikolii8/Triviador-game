from django.test import TestCase
from django.urls import reverse

from territories.models import Capital, Territory

from .factories import add_player, build_map, make_game, make_user


class TerritoryAdminTests(TestCase):
    def setUp(self):
        admin = make_user('root')
        admin.is_staff = admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)

        self.game = make_game()
        self.other_game = make_game('host2')
        self.alice = add_player(self.game, 'alice', 1)
        self.map = build_map(self.game)
        build_map(self.other_game)
        sredets = self.map['sredets']
        sredets.owner = self.alice
        sredets.save()
        self.capital = Capital.objects.create(territory=sredets, player=self.alice)

    def test_territory_list_search_and_filters(self):
        url = reverse('admin:territories_territory_changelist')
        cases = {
            'all': ({}, 36),
            'search by name': ({'q': 'Sredets'}, 2),
            'search by slug': ({'q': 'zhitno-pole'}, 2),
            'filter by game': ({'game__id__exact': self.game.pk}, 18),
            'filter by owner': ({'owner__id__exact': self.alice.pk}, 1),
        }
        for label, (params, expected) in cases.items():
            with self.subTest(label):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['cl'].result_count, expected)

    def test_capital_list_search_and_filter(self):
        url = reverse('admin:territories_capital_changelist')
        for params in ({'q': 'alice'}, {'q': 'Sredets'}, {'territory__game__id__exact': self.game.pk}):
            with self.subTest(params=params):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['cl'].result_count, 1)

    def test_structure_cannot_be_added_or_deleted(self):
        for model in ('territory', 'capital'):
            with self.subTest(model=model):
                self.assertEqual(self.client.get(reverse(f'admin:territories_{model}_add')).status_code, 403)
        territory = self.map['kukeri']
        delete_url = reverse('admin:territories_territory_delete', args=[territory.pk])
        self.assertEqual(self.client.post(delete_url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(Territory.objects.filter(pk=territory.pk).exists())

    def test_deleting_a_game_removes_its_map(self):
        url = reverse('admin:games_game_delete', args=[self.game.pk])

        response = self.client.post(url, {'post': 'yes'})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Territory.objects.filter(game_id=self.game.pk).exists())
        self.assertFalse(Capital.objects.filter(pk=self.capital.pk).exists())
        self.assertEqual(Territory.objects.filter(game=self.other_game).count(), 18)

    def test_only_score_is_editable_and_still_validated(self):
        territory = self.map['kukeri']
        url = reverse('admin:territories_territory_change', args=[territory.pk])

        response = self.client.post(url, {'score': -5, 'name': 'Hacked', 'owner': self.alice.pk})
        self.assertEqual(response.status_code, 200)  # form re-shown with an error
        territory.refresh_from_db()
        self.assertEqual(territory.score, 0)

        response = self.client.post(url, {'score': 250, 'name': 'Hacked', 'owner': self.alice.pk})
        self.assertEqual(response.status_code, 302)
        territory.refresh_from_db()
        self.assertEqual(territory.score, 250)
        self.assertEqual(territory.name, 'Kukeri')
        self.assertIsNone(territory.owner)

    def test_only_health_is_editable_and_still_validated(self):
        url = reverse('admin:territories_capital_change', args=[self.capital.pk])

        self.assertEqual(self.client.post(url, {'health': 7}).status_code, 200)
        self.capital.refresh_from_db()
        self.assertEqual(self.capital.health, 3)

        self.assertEqual(self.client.post(url, {'health': 1}).status_code, 302)
        self.capital.refresh_from_db()
        self.assertEqual(self.capital.health, 1)
