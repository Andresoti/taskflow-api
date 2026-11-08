"""Authentication configuration checks that never read the real .env file."""

import os
import secrets
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.core.auth_config import AuthSettings


class AuthenticationConfigurationTests(unittest.TestCase):
    def test_secret_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                AuthSettings(_env_file=None)

    def test_rejects_secret_shorter_than_thirty_two_utf8_bytes(self) -> None:
        with self.assertRaises(ValidationError):
            AuthSettings(_env_file=None, jwt_secret_key="synthetic-short-key")

    def test_valid_configuration_uses_fifteen_minute_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = AuthSettings(_env_file=None, jwt_secret_key=secrets.token_urlsafe(32))

        self.assertEqual(settings.access_token_expire_minutes, 15)

    def test_rejects_nonpositive_token_lifetime(self) -> None:
        for minutes in (0, -1):
            with self.subTest(minutes=minutes), self.assertRaises(ValidationError):
                AuthSettings(
                    _env_file=None,
                    jwt_secret_key=secrets.token_urlsafe(32),
                    access_token_expire_minutes=minutes,
                )

    def test_secret_is_redacted_from_configuration_representation(self) -> None:
        secret = secrets.token_urlsafe(32)
        settings = AuthSettings(_env_file=None, jwt_secret_key=secret)

        self.assertNotIn(secret, repr(settings))



if __name__ == "__main__":
    unittest.main()
