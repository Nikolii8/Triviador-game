from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

CSRF_URL = '/api/auth/csrf/'
REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
LOGOUT_URL = '/api/auth/logout/'
ME_URL = '/api/auth/me/'

PASSWORD = 'example-password'


def make_user(username='player_one', email='player@example.com', nickname='MountainKnight'):
    user = User.objects.create_user(username=username, email=email, password=PASSWORD)
    user.profile.nickname = nickname
    user.profile.save()
    return user


class AuthAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    def csrf_token(self):
        response = self.client.get(CSRF_URL)
        self.assertEqual(response.status_code, 204)
        return self.client.cookies['csrftoken'].value

    def post(self, url, data=None):
        return self.client.post(url, data or {}, format='json', HTTP_X_CSRFTOKEN=self.csrf_token())

    def patch(self, url, data):
        return self.client.patch(url, data, format='json', HTTP_X_CSRFTOKEN=self.csrf_token())

    def login(self, username='player_one', password=PASSWORD):
        return self.post(LOGIN_URL, {'username': username, 'password': password})

    def assert_me_denied(self):
        response = self.client.get(ME_URL)
        self.assertIn(response.status_code, (401, 403))
        self.assertIn('errors', response.data)


class RegistrationTests(AuthAPITestCase):
    valid_payload = {
        'username': 'player_one',
        'email': 'player@example.com',
        'nickname': 'MountainKnight',
        'password': PASSWORD,
        'password_confirm': PASSWORD,
    }

    def test_successful_registration(self):
        response = self.post(REGISTER_URL, self.valid_payload)

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username='player_one')
        self.assertEqual(user.profile.nickname, 'MountainKnight')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(
            response.data,
            {
                'id': user.id,
                'username': 'player_one',
                'email': 'player@example.com',
                'profile': {'nickname': 'MountainKnight', 'avatar_key': 'knight-1'},
            },
        )
        self.assertNotIn('password', response.data)
        self.assertNotIn('password_confirm', response.data)

    def test_invalid_registration(self):
        make_user(username='taken_user', email='taken@example.com', nickname='TakenNick')

        cases = [
            ('username', {'username': 'taken_user'}),
            ('email', {'email': 'taken@example.com'}),
            ('nickname', {'nickname': 'TakenNick'}),
            ('password_confirm', {'password_confirm': 'different-password'}),
        ]
        for field, override in cases:
            with self.subTest(field=field):
                response = self.post(REGISTER_URL, {**self.valid_payload, **override})

                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data['errors'])
                self.assertEqual(User.objects.count(), 1)

    def test_username_matching_existing_nickname_does_not_break_registration(self):
        make_user(username='someone', email='someone@example.com', nickname='player_one')

        response = self.post(REGISTER_URL, self.valid_payload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['profile']['nickname'], 'MountainKnight')


class LoginTests(AuthAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()

    def test_successful_login_creates_session(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user.id)
        self.assertNotIn('password', response.data)
        self.assertIn('sessionid', self.client.cookies)

        me_response = self.client.get(ME_URL)
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data['username'], 'player_one')

    def test_failed_login_with_wrong_password(self):
        response = self.login(password='wrong-password')

        self.assertEqual(response.status_code, 400)
        self.assertIn('non_field_errors', response.data['errors'])
        self.assertNotIn('sessionid', self.client.cookies)
        self.assert_me_denied()


class MePermissionTests(AuthAPITestCase):
    def test_anonymous_access_is_denied(self):
        self.assert_me_denied()

        response = self.client.patch(ME_URL, {'nickname': 'Hacker'}, format='json')
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_user_sees_only_own_data(self):
        make_user(username='other', email='other@example.com', nickname='OtherKnight')
        user = make_user()
        self.client.force_login(user)

        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], user.id)
        self.assertEqual(response.data['profile']['nickname'], 'MountainKnight')


class ProfileUpdateTests(AuthAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(nickname='OldKnight')
        self.client.force_login(self.user)

    def test_update_nickname_and_avatar(self):
        response = self.patch(ME_URL, {'nickname': 'NewKnight', 'avatar_key': 'knight-3'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['profile'], {'nickname': 'NewKnight', 'avatar_key': 'knight-3'})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.nickname, 'NewKnight')
        self.assertEqual(self.user.profile.avatar_key, 'knight-3')

    def test_protected_fields_cannot_be_changed(self):
        response = self.patch(
            ME_URL,
            {'nickname': 'NewKnight', 'is_staff': True, 'is_superuser': True, 'username': 'hijacked'},
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.username, 'player_one')

    def test_invalid_profile_values_are_rejected(self):
        make_user(username='other', email='other@example.com', nickname='TakenNick')

        cases = [
            ('nickname', {'nickname': 'TakenNick'}),
            ('avatar_key', {'avatar_key': 'dragon-9'}),
        ]
        for field, payload in cases:
            with self.subTest(field=field):
                response = self.patch(ME_URL, payload)

                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data['errors'])


class LogoutTests(AuthAPITestCase):
    def test_logout_ends_session(self):
        make_user()
        self.assertEqual(self.login().status_code, 200)

        response = self.post(LOGOUT_URL)

        self.assertEqual(response.status_code, 204)
        self.assert_me_denied()


class CsrfTests(AuthAPITestCase):
    def test_unsafe_request_requires_csrf_token(self):
        user = make_user(nickname='OldKnight')
        self.client.force_login(user)

        without_token = self.client.patch(ME_URL, {'nickname': 'NewKnight'}, format='json')
        self.assertEqual(without_token.status_code, 403)
        self.assertIn('errors', without_token.data)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.nickname, 'OldKnight')

        with_token = self.patch(ME_URL, {'nickname': 'NewKnight'})
        self.assertEqual(with_token.status_code, 200)

    def test_anonymous_login_requires_csrf_token(self):
        make_user()

        without_token = self.client.post(
            LOGIN_URL, {'username': 'player_one', 'password': PASSWORD}, format='json'
        )
        self.assertEqual(without_token.status_code, 403)
        self.assertNotIn('sessionid', self.client.cookies)

        self.assertEqual(self.login().status_code, 200)
