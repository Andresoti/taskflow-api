"""Fault-injection checks for the test runner's Docker lifecycle boundary."""

import json
from pathlib import Path
import subprocess
from typing import Any
import unittest
from unittest.mock import patch

from scripts import test_with_postgres as runner


class PostgresRunnerCleanupTests(unittest.TestCase):
    def test_creation_timeout_recovers_and_removes_container_by_owned_name(self) -> None:
        state: dict[str, Any] = {"removed": False}
        container_id = "a" * 64

        def simulated_docker(arguments: list[str], **_kwargs) -> str:
            operation = arguments[1]
            if operation == "run":
                state["name"] = arguments[arguments.index("--name") + 1]
                state["label"] = arguments[arguments.index("--label") + 1]
                mount = arguments[arguments.index("--mount") + 1]
                state["workspace"] = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0]).parent
                raise subprocess.TimeoutExpired("docker run", 60)
            if operation == "ps":
                return "" if state["removed"] else container_id
            if operation == "inspect":
                key, value = state["label"].split("=", 1)
                return json.dumps([{
                    "Id": container_id,
                    "Name": "/" + state["name"],
                    "Config": {"Labels": {key: value}},
                }])
            if operation == "stop":
                self.assertEqual(arguments[-1], container_id)
                return container_id
            if operation == "rm":
                self.assertEqual(arguments[-1], container_id)
                state["removed"] = True
                return container_id
            raise AssertionError("Unexpected Docker operation")

        with patch.object(runner, "run_command", side_effect=simulated_docker):
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.main()

        self.assertTrue(state["removed"], "A created container must be removed even when its id was lost")
        self.assertFalse(state["workspace"].exists())

    def test_cleanup_refuses_mismatched_owner_or_name(self) -> None:
        owner = "b" * 32
        name = "taskflow-api-test-" + owner[:12]
        for actual_owner, actual_name in (("another-owner", "/" + name), (owner, "/another-container")):
            with self.subTest(owner_matches=actual_owner == owner, name_matches=actual_name == "/" + name):
                inspection = json.dumps([{
                    "Id": "a" * 64,
                    "Name": actual_name,
                    "Config": {"Labels": {runner.OWNER_LABEL: actual_owner}},
                }])
                with patch.object(runner, "run_command", return_value=inspection) as command:
                    with self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
                        runner.cleanup_test_container("a" * 64, name, owner)
                    command.assert_called_once_with(["docker", "inspect", "a" * 64])

    def test_cleanup_skips_stop_and_remove_when_creation_never_happened(self) -> None:
        owner = "b" * 32
        name = "taskflow-api-test-" + owner[:12]
        with patch.object(runner, "run_command", return_value="") as command:
            runner.cleanup_test_container(None, name, owner)

        command.assert_called_once_with([
            "docker", "ps", "--all", "--quiet", "--filter", "name=^/" + name + "$",
        ])


if __name__ == "__main__":
    unittest.main()
