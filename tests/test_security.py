"""Tests for password and access-token helpers without database access."""

import base64
from datetime import datetime, timedelta, timezone
import json
import secrets
import unittest

import jwt

from app.core import security


class PasswordSecurityTests(unittest.TestCase):
    def test_password_hash_uses_argon2id_and_verifies_original_password(self) -> None:
        password = "synthetic-password-for-unit-tests"
        password_hash = security.hash_password(password)

        self.assertNotEqual(password_hash, password)
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertTrue(security.verify_password(password, password_hash))

    def test_password_verification_rejects_wrong_password(self) -> None:
        password_hash = security.hash_password("synthetic-correct-password")

        self.assertFalse(security.verify_password("synthetic-wrong-password", password_hash))

    def test_hashing_same_password_uses_distinct_salts(self) -> None:
        password = "synthetic-password-for-unit-tests"

        self.assertNotEqual(security.hash_password(password), security.hash_password(password))


class AccessTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret_key = secrets.token_urlsafe(32)

    def test_access_token_has_subject_and_fifteen_minute_lifetime(self) -> None:
        issued_after = int(datetime.now(timezone.utc).timestamp())
        token = security.create_access_token("42", secret_key=self.secret_key)
        claims = jwt.decode(token, self.secret_key, algorithms=["HS256"])

        self.assertEqual(claims["sub"], "42")
        self.assertGreaterEqual(claims["iat"], issued_after)
        self.assertEqual(claims["exp"] - claims["iat"], timedelta(minutes=15).total_seconds())

    def test_decode_access_token_returns_verified_subject(self) -> None:
        token = security.create_access_token("42", secret_key=self.secret_key)

        claims = security.decode_access_token(token, secret_key=self.secret_key)

        self.assertEqual(claims["sub"], "42")

    def test_decode_rejects_expired_token(self) -> None:
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {"sub": "42", "iat": now - timedelta(minutes=2), "exp": now - timedelta(minutes=1)},
            self.secret_key,
            algorithm="HS256",
        )

        with self.assertRaises(jwt.ExpiredSignatureError):
            security.decode_access_token(token, secret_key=self.secret_key)

    def test_decode_rejects_tampered_payload(self) -> None:
        token = security.create_access_token("42", secret_key=self.secret_key)
        header, _, signature = token.split(".")
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "99"}).encode()).rstrip(b"=").decode()
        tampered_token = ".".join((header, payload, signature))

        with self.assertRaises(jwt.InvalidSignatureError):
            security.decode_access_token(tampered_token, secret_key=self.secret_key)

    def test_decode_rejects_wrong_signing_key(self) -> None:
        token = security.create_access_token("42", secret_key=self.secret_key)

        with self.assertRaises(jwt.InvalidSignatureError):
            security.decode_access_token(token, secret_key=secrets.token_urlsafe(32))

    def test_decode_rejects_unsigned_token(self) -> None:
        token = jwt.encode({"sub": "42"}, key="", algorithm="none")

        with self.assertRaises(jwt.InvalidAlgorithmError):
            security.decode_access_token(token, secret_key=self.secret_key)

    def test_decode_rejects_unapproved_signing_algorithm(self) -> None:
        token = jwt.encode({"sub": "42"}, secrets.token_urlsafe(64), algorithm="HS512")

        with self.assertRaises(jwt.InvalidAlgorithmError):
            security.decode_access_token(token, secret_key=self.secret_key)

    def test_decode_rejects_malformed_token(self) -> None:
        with self.assertRaises(jwt.DecodeError):
            security.decode_access_token("not-a-jwt", secret_key=self.secret_key)

    def test_creation_rejects_blank_subject(self) -> None:
        for subject in ("", " ", "\t"):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                security.create_access_token(subject, secret_key=self.secret_key)

    def test_creation_rejects_nonpositive_lifetime(self) -> None:
        for lifetime in (timedelta(0), timedelta(seconds=-1)):
            with self.subTest(lifetime=lifetime), self.assertRaises(ValueError):
                security.create_access_token("42", secret_key=self.secret_key, expires_delta=lifetime)

    def test_creation_supports_custom_positive_lifetime(self) -> None:
        token = security.create_access_token(
            "42", secret_key=self.secret_key, expires_delta=timedelta(minutes=5)
        )
        claims = security.decode_access_token(token, secret_key=self.secret_key)

        self.assertEqual(claims["exp"] - claims["iat"], timedelta(minutes=5).total_seconds())

    def test_helpers_reject_short_signing_keys(self) -> None:
        valid_token = security.create_access_token("42", secret_key=self.secret_key)
        for secret_key in ("", "short", "x" * 31):
            with self.subTest(operation="create", key_bytes=len(secret_key.encode())):
                with self.assertRaises(ValueError):
                    security.create_access_token("42", secret_key=secret_key)
            with self.subTest(operation="decode", key_bytes=len(secret_key.encode())):
                with self.assertRaises(ValueError):
                    security.decode_access_token(valid_token, secret_key=secret_key)

    def test_decode_requires_subject_issued_at_and_expiration(self) -> None:
        now = datetime.now(timezone.utc)
        for missing_claim in ("sub", "iat", "exp"):
            with self.subTest(missing_claim=missing_claim):
                claims = {"sub": "42", "iat": now, "exp": now + timedelta(minutes=1)}
                del claims[missing_claim]
                token = jwt.encode(claims, self.secret_key, algorithm="HS256")

                with self.assertRaises(jwt.MissingRequiredClaimError):
                    security.decode_access_token(token, secret_key=self.secret_key)


if __name__ == "__main__":
    unittest.main()
