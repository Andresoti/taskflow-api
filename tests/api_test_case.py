"""Shared PostgreSQL/ASGI fixture for real HTTP integration tests."""

from collections.abc import Generator
import os
import secrets
import unittest

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


TEST_DATABASE_URL = os.environ.get("TASKFLOW_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "Set TASKFLOW_TEST_DATABASE_URL to disposable PostgreSQL")
class PostgreSQLApiTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_DATABASE_URL is not None
        cls.engine = create_engine(TEST_DATABASE_URL)
        cls.addClassCleanup(cls.engine.dispose)
        if cls.engine.dialect.name != "postgresql":
            raise RuntimeError("HTTP integration tests require PostgreSQL")
        with cls.engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != "taskflow_auth_test":
                raise RuntimeError("Refusing to use a non-test database")
            if connection.scalar(text("SELECT version_num FROM alembic_version")) != "e256c74560c6":
                raise RuntimeError("Apply the real migration before running HTTP integration tests")

        from app.db.session import get_db
        from app.main import app

        cls.app = app
        cls.get_db = staticmethod(get_db)

    async def asyncSetUp(self) -> None:
        app = self.app
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.db = Session(
            bind=self.connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        self.addCleanup(self.db.close)

        def override_database() -> Generator[Session, None, None]:
            yield self.db

        app.dependency_overrides[self.get_db] = override_database
        self.addCleanup(app.dependency_overrides.clear)
        await self.enterAsyncContext(app.router.lifespan_context(app))
        self.client = await self.enterAsyncContext(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://testserver",
            )
        )

    async def authenticate(self, email: str = "synthetic@example.com") -> dict[str, str]:
        """Register/login a synthetic account without persisting test credentials."""
        password = secrets.token_urlsafe(24)
        registered = await self.client.post("/auth/register", json={"email": email, "password": password})
        self.assertEqual(registered.status_code, 201)
        response = await self.client.post("/auth/login", data={"username": email, "password": password})
        self.assertEqual(response.status_code, 200)
        return {"Authorization": "Bearer " + response.json()["access_token"]}
