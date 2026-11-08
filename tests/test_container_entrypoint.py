"""Validate the container-only database bridge without touching local settings."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]


class ContainerEntrypointTests(unittest.TestCase):
    def load_entrypoint(self):
        spec = importlib.util.spec_from_file_location("container_entrypoint", ROOT / "docker" / "entrypoint.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_url_components_override_host_url_and_preserve_reserved_characters(self) -> None:
        module = self.load_entrypoint()
        environment = {
            "TASKFLOW_DATABASE_HOST": "db", "POSTGRES_USER": "synthetic@user",
            "POSTGRES_PASSWORD": "synthetic:@/#?%$ value", "POSTGRES_DB": "taskflow_test",
            "DATABASE_URL": "postgresql://wrong@localhost/wrong",
        }
        with patch.dict(os.environ, environment, clear=True):
            module.configure_database()
            url = make_url(os.environ["DATABASE_URL"])
            self.assertEqual(url.host, "db")
            self.assertEqual(url.port, 5432)
            self.assertEqual(url.username, environment["POSTGRES_USER"])
            self.assertEqual(url.password, environment["POSTGRES_PASSWORD"])
            self.assertEqual(url.database, environment["POSTGRES_DB"])

    def test_without_container_host_existing_database_url_is_preserved(self) -> None:
        module = self.load_entrypoint()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://synthetic@localhost/test"}, clear=True):
            module.configure_database()
            self.assertEqual(os.environ["DATABASE_URL"], "postgresql://synthetic@localhost/test")

    def test_missing_components_fail_without_echoing_values(self) -> None:
        module = self.load_entrypoint()
        for field in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
            with self.subTest(field=field):
                environment = {"TASKFLOW_DATABASE_HOST": "db", "POSTGRES_USER": "synthetic", "POSTGRES_PASSWORD": "never-echo-this", "POSTGRES_DB": "test"}
                environment.pop(field)
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(SystemExit) as error:
                        module.configure_database()
                    self.assertIn(field, str(error.exception))
                    self.assertNotIn("never-echo-this", str(error.exception))

    def test_main_replaces_wrapper_with_requested_command(self) -> None:
        module = self.load_entrypoint()
        with patch.object(module, "configure_database") as configure, patch.object(module.os, "execvp") as execute, patch.object(module.sys, "argv", ["entrypoint.py", "alembic", "upgrade", "head"]):
            module.main()
        configure.assert_called_once_with()
        execute.assert_called_once_with("alembic", ["alembic", "upgrade", "head"])

    def test_missing_command_fails_clearly(self) -> None:
        module = self.load_entrypoint()
        with patch.object(module.sys, "argv", ["entrypoint.py"]):
            with self.assertRaises(SystemExit):
                module.main()


if __name__ == "__main__":
    unittest.main()
