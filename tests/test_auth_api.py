"""HTTP integration tests; require an explicitly supplied disposable PostgreSQL URL."""

from datetime import datetime, timedelta, timezone
import os
import secrets
import unittest
from unittest.mock import patch

import jwt
from pydantic import ValidationError
from sqlalchemy import select

from app.core.security import decode_access_token, verify_password
from app.models import User
from tests.api_test_case import PostgreSQLApiTestCase


class AuthenticationApiTests(PostgreSQLApiTestCase):
    async def test_registration_persists_hash_and_returns_only_public_fields(self) -> None:
        response = await self.client.post(
            "/auth/register",
            json={"email": "Synthetic@Example.com", "password": "synthetic-password-for-tests"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"id", "email", "created_at"})
        self.assertEqual(response.json()["email"], "synthetic@example.com")
        user = self.db.scalar(select(User).where(User.email == "synthetic@example.com"))
        self.assertIsNotNone(user)
        assert user is not None
        self.assertTrue(user.hashed_password.startswith("$argon2id$"))
        self.assertTrue(verify_password("synthetic-password-for-tests", user.hashed_password))

    async def test_duplicate_registration_returns_conflict_and_session_recovers(self) -> None:
        payload = {"email": "synthetic@example.com", "password": "synthetic-password-for-tests"}
        first = await self.client.post("/auth/register", json=payload)
        self.assertEqual(first.status_code, 201)

        duplicate = await self.client.post(
            "/auth/register", json={**payload, "email": "SYNTHETIC@EXAMPLE.COM"}
        )

        self.assertEqual(duplicate.status_code, 409)
        recovered = await self.client.post(
            "/auth/register", json={**payload, "email": "another-synthetic@example.com"}
        )
        self.assertEqual(recovered.status_code, 201)

    async def test_registration_validation_does_not_echo_submitted_password(self) -> None:
        response = await self.client.post(
            "/auth/register", json={"email": "synthetic@example.com", "password": "tiny"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn("tiny", response.text)
        for error in response.json()["detail"]:
            self.assertNotIn("input", error)

    async def test_registration_rejects_invalid_email_password_and_extra_fields(self) -> None:
        valid = {"email": "synthetic@example.com", "password": "synthetic-password-for-tests"}
        invalid_payloads = [
            {**valid, "email": "not-an-email"},
            {**valid, "password": "x" * 129},
            {**valid, "id": 99},
            {"email": valid["email"]},
            {**valid, "password": ["invalid-type"]},
        ]
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(case=index):
                response = await self.client.post("/auth/register", json=payload)
                self.assertEqual(response.status_code, 422)

    async def test_login_returns_signed_token_for_registered_user(self) -> None:
        registered = await self.client.post(
            "/auth/register",
            json={"email": "synthetic@example.com", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(registered.status_code, 201)

        response = await self.client.post(
            "/auth/login",
            data={"username": "SYNTHETIC@EXAMPLE.COM", "password": "synthetic-password-for-tests"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"access_token", "token_type"})
        self.assertEqual(response.json()["token_type"], "bearer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")
        claims = decode_access_token(response.json()["access_token"], secret_key=os.environ["JWT_SECRET_KEY"])
        self.assertEqual(claims["sub"], str(registered.json()["id"]))

    async def test_wrong_password_and_unknown_account_return_same_unauthorized_response(self) -> None:
        registered = await self.client.post(
            "/auth/register",
            json={"email": "synthetic@example.com", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(registered.status_code, 201)
        wrong_password = await self.client.post(
            "/auth/login", data={"username": "synthetic@example.com", "password": "wrong-password"}
        )
        unknown_account = await self.client.post(
            "/auth/login", data={"username": "unknown@example.com", "password": "wrong-password"}
        )

        for response in (wrong_password, unknown_account):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
            self.assertNotIn("wrong-password", response.text)
        self.assertEqual(wrong_password.json(), unknown_account.json())

    async def test_login_requires_form_fields_without_echoing_password(self) -> None:
        response = await self.client.post("/auth/login", data={"password": "synthetic-password"})

        self.assertEqual(response.status_code, 422)
        self.assertNotIn("synthetic-password", response.text)

    async def test_registered_user_can_log_in_and_read_own_profile(self) -> None:
        registered = await self.client.post(
            "/auth/register",
            json={"email": "synthetic@example.com", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(registered.status_code, 201)
        login = await self.client.post(
            "/auth/login",
            data={"username": "synthetic@example.com", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(login.status_code, 200)
        response = await self.client.get(
            "/users/me", headers={"Authorization": "Bearer " + login.json()["access_token"]}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), registered.json())
        self.assertNotIn("hashed_password", response.json())

    async def test_profile_requires_bearer_credentials(self) -> None:
        for headers in ({}, {"Authorization": "Basic synthetic"}):
            with self.subTest(headers_present=bool(headers)):
                response = await self.client.get("/users/me", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    async def test_profile_rejects_malformed_expired_tampered_and_unsigned_tokens(self) -> None:
        now = datetime.now(timezone.utc)
        claims = {"sub": "1", "iat": now, "exp": now + timedelta(minutes=1)}
        expired = {**claims, "iat": now - timedelta(minutes=2), "exp": now - timedelta(minutes=1)}
        tokens = [
            "not-a-token",
            jwt.encode(expired, os.environ["JWT_SECRET_KEY"], algorithm="HS256"),
            jwt.encode(claims, secrets.token_urlsafe(32), algorithm="HS256"),
            jwt.encode(claims, key="", algorithm="none"),
        ]
        for index, token in enumerate(tokens):
            with self.subTest(case=index):
                response = await self.client.get("/users/me", headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    async def test_profile_rejects_invalid_or_nonexistent_subject(self) -> None:
        now = datetime.now(timezone.utc)
        for subject in ("", "0", "-1", "abc", "1.5", "01", str(2**31), str(2**31 - 1)):
            with self.subTest(subject=subject):
                token = jwt.encode(
                    {"sub": subject, "iat": now, "exp": now + timedelta(minutes=1)},
                    os.environ["JWT_SECRET_KEY"],
                    algorithm="HS256",
                )
                response = await self.client.get("/users/me", headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 401)

    async def test_deleted_user_cannot_reuse_valid_token(self) -> None:
        registered = await self.client.post(
            "/auth/register",
            json={"email": "synthetic@example.com", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(registered.status_code, 201)
        login = await self.client.post(
            "/auth/login",
            data={"username": "synthetic@example.com", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(login.status_code, 200)
        user = self.db.get(User, registered.json()["id"])
        assert user is not None
        self.db.delete(user)
        self.db.commit()

        response = await self.client.get(
            "/users/me", headers={"Authorization": "Bearer " + login.json()["access_token"]}
        )

        self.assertEqual(response.status_code, 401)

    async def test_openapi_describes_form_login_and_protected_profile(self) -> None:
        response = await self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()

        scheme = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]
        self.assertEqual(scheme["flows"]["password"]["tokenUrl"], "auth/login")
        self.assertEqual(schema["paths"]["/users/me"]["get"]["security"], [{"OAuth2PasswordBearer": []}])
        content = schema["paths"]["/auth/login"]["post"]["requestBody"]["content"]
        self.assertIn("application/x-www-form-urlencoded", content)
        docs = await self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)

    async def test_startup_refuses_empty_jwt_secret(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET_KEY": ""}):
            with self.assertRaises(ValidationError):
                async with self.app.router.lifespan_context(self.app):
                    pass

    async def test_login_accepts_same_international_email_forms_as_registration(self) -> None:
        registered = await self.client.post(
            "/auth/register",
            json={"email": "synthetic@xn--bcher-kva.de", "password": "synthetic-password-for-tests"},
        )
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(registered.json()["email"], "synthetic@bücher.de")

        for email in ("synthetic@xn--bcher-kva.de", "synthetic@bücher.de"):
            with self.subTest(email_form=email):
                response = await self.client.post(
                    "/auth/login", data={"username": email, "password": "synthetic-password-for-tests"}
                )
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
