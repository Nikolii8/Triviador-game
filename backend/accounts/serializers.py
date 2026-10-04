from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import Profile

User = get_user_model()


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['nickname', 'avatar_key']


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'profile']


class RegisterSerializer(serializers.ModelSerializer):
    nickname = serializers.CharField(max_length=30)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'nickname', 'password', 'password_confirm']

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': ['Passwords do not match.']})

        if User.objects.filter(username=attrs['username']).exists():
            raise serializers.ValidationError({'username': ['A user with that username already exists.']})

        if User.objects.filter(email=attrs['email']).exists():
            raise serializers.ValidationError({'email': ['User with this email already exists.']})

        if Profile.objects.filter(nickname=attrs['nickname']).exists():
            raise serializers.ValidationError({'nickname': ['This nickname is already taken.']})

        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data.pop('password_confirm')
        nickname = validated_data.pop('nickname')

        with transaction.atomic():
            user = User.objects.create_user(
                password=password,
                **validated_data,
            )
            user.profile.nickname = nickname
            user.profile.save(update_fields=['nickname'])
            return user

    def to_representation(self, instance):
        return UserSerializer(instance).data
