from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class UserModelTests(TestCase):
    def test_user_profile_is_created_automatically(self):
        user = User.objects.create_user(username='player_one', email='player@example.com', password='example-password')

        self.assertTrue(hasattr(user, 'profile'))
        self.assertEqual(user.profile.nickname, 'player_one')
