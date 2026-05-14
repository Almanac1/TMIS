from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameModelBackend(ModelBackend):
    """
    Authenticate against email first (case-insensitive), with username fallback.
    """

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        login_identifier = (email or username or "").strip()
        if not login_identifier or password is None:
            return None

        user_model = get_user_model()
        email_matches = user_model._default_manager.filter(email__iexact=login_identifier)
        if email_matches.count() == 1:
            user = email_matches.first()
            if user and user.check_password(password) and self.user_can_authenticate(user):
                return user
            return None
        if email_matches.count() > 1:
            # Avoid authenticating ambiguous duplicate emails.
            return None

        username_matches = user_model._default_manager.filter(
            Q(username__iexact=login_identifier)
        )
        if username_matches.count() != 1:
            return None

        user = username_matches.first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
