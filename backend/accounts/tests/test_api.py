from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class AuthAPITests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    def _get_csrf_token(self):
        response = self.client.get('/api/auth/csrf/')
        self.assertEqual(response.status_code, 204)
        return self.client.cookies['csrftoken'].value

    def test_registration_success_creates_user_and_profile(self):
        csrf = self._get_csrf_token()
        response = self.client.post(
            '/api/auth/register/',
            {
                'username': 'player_one',
                'email': 'player@example.com',
                'nickname': 'MountainKnight',
                'password': 'example-password',
                'password_confirm': 'example-password',
            },
            format='json',
            HTTP_X_CSRFTOKEN=csrf,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(User.objects.filter(username='player_one').exists())
        self.assertEqual(response.data['profile']['nickname'], 'MountainKnight')
        self.assertNotIn('password', response.data)

    def test_login_and_me_require_authenticated_session(self):
        user = User.objects.create_user(
            username='player_one',
            email='player@example.com',
            password='example-password',
        )
        user.profile.nickname = 'MountainKnight'
        user.profile.save()

        csrf = self._get_csrf_token()
        login_response = self.client.post(
            '/api/auth/login/',
            {'username': 'player_one', 'password': 'example-password'},
            format='json',
            HTTP_X_CSRFTOKEN=csrf,
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertIn('sessionid', self.client.cookies)

        me_response = self.client.get('/api/auth/me/')
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data['username'], 'player_one')

    def test_profile_update_only_allows_profile_fields(self):
        user = User.objects.create_user(
            username='player_one',
            email='player@example.com',
            password='example-password',
        )
        user.profile.nickname = 'OldKnight'
        user.profile.save()
        self.client.force_login(user)

        response = self.client.patch(
            '/api/auth/me/',
            {'nickname': 'NewKnight', 'avatar_key': 'knight-3'},
            format='json',
            HTTP_X_CSRFTOKEN=self._get_csrf_token(),
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile.nickname, 'NewKnight')
        self.assertEqual(user.profile.avatar_key, 'knight-3')

    def test_logout_invalidates_session(self):
        user = User.objects.create_user(
            username='player_one',
            email='player@example.com',
            password='example-password',
        )
        self.client.force_login(user)
        csrf = self._get_csrf_token()

        response = self.client.post('/api/auth/logout/', HTTP_X_CSRFTOKEN=csrf)

        self.assertEqual(response.status_code, 204)
        me_response = self.client.get('/api/auth/me/')
        self.assertIn(me_response.status_code, (401, 403))
