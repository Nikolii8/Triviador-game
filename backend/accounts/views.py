from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import LoginSerializer, ProfileSerializer, RegisterSerializer, UserSerializer

PROFILE_UPDATE_FIELDS = {'nickname', 'avatar_key'}


class CsrfEnforcedPublicView(APIView):
    """Public endpoint that still requires a valid CSRF token for unsafe methods.

    DRF's SessionAuthentication only checks CSRF for already-authenticated users,
    so anonymous register/login requests would otherwise skip the check.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = [SessionAuthentication]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        SessionAuthentication().enforce_csrf(request)


class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request: Request) -> Response:
        return Response(status=status.HTTP_204_NO_CONTENT)


csrf_view = ensure_csrf_cookie(CsrfView.as_view())


class RegisterView(CsrfEnforcedPublicView):
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(CsrfEnforcedPublicView):
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request, **serializer.validated_data)
        if user is None:
            raise ValidationError({'non_field_errors': ['Invalid username or password.']})

        login(request, user)
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def post(self, request: Request) -> Response:
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)

    def patch(self, request: Request) -> Response:
        # Only profile fields are editable; anything else (is_staff, username, ...) is ignored.
        data = {key: value for key, value in request.data.items() if key in PROFILE_UPDATE_FIELDS}
        if not data:
            raise ValidationError({'non_field_errors': ['Provide nickname and/or avatar_key.']})

        serializer = ProfileSerializer(request.user.profile, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)
