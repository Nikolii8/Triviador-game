from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.serializers import RegisterSerializer

User = get_user_model()


class RegisterSerializerTests(TestCase):
    def test_register_serializer_validates_passwords(self):
        data = {
            'username': 'player_one',
            'email': 'player@example.com',
            'nickname': 'MountainKnight',
            'password': 'example-password',
            'password_confirm': 'different-password',
        }

        serializer = RegisterSerializer(data=data)

        self.assertFalse(serializer.is_valid())
        self.assertIn('password_confirm', serializer.errors)

    def test_register_serializer_creates_user_and_profile(self):
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
        self.assertEqual(user.profile.nickname, 'MountainKnight')
