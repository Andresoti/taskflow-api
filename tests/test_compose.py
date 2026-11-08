"""Validate the resolved Compose configuration without starting containers."""

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComposeConfigurationTests(unittest.TestCase):
    def resolve(self) -> dict:
        environment = {
            **os.environ, "POSTGRES_USER": "configuration_test",
            "POSTGRES_PASSWORD": "configuration_test", "POSTGRES_DB": "configuration_test",
            "JWT_SECRET_KEY": "synthetic-configuration-key-at-least-32-bytes",
        }
        result = subprocess.run(
            ["docker", "compose", "--env-file", os.devnull, "-f", str(ROOT / "docker-compose.yml"), "config", "--format", "json"],
            cwd=ROOT, env=environment, check=True, capture_output=True, text=True, timeout=30,
        )
        return json.loads(result.stdout)

    def test_startup_orders_health_then_migrations_then_api(self) -> None:
        services = self.resolve()["services"]
        self.assertEqual(services["migrate"]["depends_on"]["db"]["condition"], "service_healthy")
        self.assertEqual(services["api"]["depends_on"]["migrate"]["condition"], "service_completed_successfully")
        self.assertEqual(services["api"]["depends_on"]["db"]["condition"], "service_healthy")
        self.assertEqual(services["migrate"]["command"], ["alembic", "upgrade", "head"])
        self.assertTrue(services["migrate"]["healthcheck"]["disable"])
        self.assertEqual(services["api"]["image"], services["migrate"]["image"])
        self.assertEqual(services["migrate"]["restart"], "no")

    def test_environment_separates_host_and_container_settings_and_jwt(self) -> None:
        services = self.resolve()["services"]
        for name in ("api", "migrate"):
            self.assertEqual(services[name]["environment"]["TASKFLOW_DATABASE_HOST"], "db")
            self.assertNotIn("DATABASE_URL", services[name]["environment"])
            self.assertNotIn("env_file", services[name])
            self.assertNotIn("container_name", services[name])
        self.assertNotIn("JWT_SECRET_KEY", services["migrate"]["environment"])
        self.assertNotIn("JWT_SECRET_KEY", services["db"]["environment"])
        self.assertIn("JWT_SECRET_KEY", services["api"]["environment"])
        self.assertNotIn("container_name", services["db"])

    def test_runtime_is_restricted_and_api_is_loopback_only(self) -> None:
        config = self.resolve()
        for name in ("api", "migrate"):
            service = config["services"][name]
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", service["security_opt"])
            self.assertNotIn("volumes", service)
        ports = config["services"]["api"]["ports"]
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
        self.assertEqual(ports[0]["target"], 8000)
        volumes = config["services"]["db"]["volumes"]
        self.assertEqual(volumes[0]["type"], "volume")
        self.assertEqual(volumes[0]["source"], "postgres_data")
        self.assertEqual(volumes[0]["target"], "/var/lib/postgresql/data")

    def test_postgres_is_published_only_on_ipv4_loopback(self) -> None:
        # Dummy values keep this check independent of local credentials and .env.
        environment = {
            **os.environ,
            "POSTGRES_USER": "configuration_test",
            "POSTGRES_PASSWORD": "configuration_test",
            "POSTGRES_DB": "configuration_test",
            "POSTGRES_PORT": "5432",
        }
        result = subprocess.run(
            [
                "docker", "compose", "--env-file", os.devnull,
                "-f", str(ROOT / "docker-compose.yml"),
                "config", "--format", "json",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        ports = json.loads(result.stdout)["services"]["db"]["ports"]

        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0].get("host_ip"), "127.0.0.1")
        self.assertEqual(ports[0]["target"], 5432)
        self.assertEqual(str(ports[0]["published"]), "5432")
        self.assertEqual(ports[0]["protocol"], "tcp")


if __name__ == "__main__":
    unittest.main()
