from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.serializers import RegisterSerializer

User = get_user_model()


class RegisterSerializerTests(TestCase):
    def test_creates_user_with_hashed_password_and_profile(self):
        data = {
            'username': 'player_one',
            'email': 'player@example.com',
            'nickname': 'MountainKnight',
            'password': 'example-password',
            'password_confirm': 'example-password',
        }

        serializer = RegisterSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()

        self.assertNotEqual(user.password, 'example-password')
        self.assertTrue(user.check_password('example-password'))
        self.assertEqual(user.profile.nickname, 'MountainKnight')
        self.assertNotIn('password', serializer.data)
        self.assertNotIn('password_confirm', serializer.data)

    def test_rejects_weak_password(self):
        data = {
            'username': 'player_one',
            'email': 'player@example.com',
            'nickname': 'MountainKnight',
            'password': '123',
            'password_confirm': '123',
        }

        serializer = RegisterSerializer(data=data)

        self.assertFalse(serializer.is_valid())
        self.assertIn('password', serializer.errors)
        self.assertFalse(User.objects.exists())
