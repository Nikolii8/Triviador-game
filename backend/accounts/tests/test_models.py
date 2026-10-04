from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class UserModelTests(TestCase):
    def test_profile_is_created_automatically(self):
        user = User.objects.create_user(username='player_one', email='player@example.com', password='example-password')

        self.assertEqual(user.profile.nickname, 'player_one')
        self.assertEqual(user.profile.avatar_key, 'knight-1')

    def test_default_nickname_falls_back_when_username_is_taken_as_nickname(self):
        first = User.objects.create_user(username='first', email='first@example.com', password='example-password')
        first.profile.nickname = 'second'
        first.profile.save()

        second = User.objects.create_user(username='second', email='second@example.com', password='example-password')

        self.assertEqual(second.profile.nickname, f'player-{second.pk}')
