"""Ownership checks for destructive cleanup in the isolated Compose runner."""

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ComposeRunnerTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location("compose_runner", ROOT / "scripts/test_with_compose.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cleanup_requires_both_project_and_owner_labels(self) -> None:
        module = self.module()
        labels = {"com.docker.compose.project": "taskflow-test-unique", module.OWNER_LABEL: "unique"}
        module.validate_labels(labels, "taskflow-test-unique", "unique")
        for missing in labels:
            with self.subTest(missing=missing):
                invalid = {key: value for key, value in labels.items() if key != missing}
                with self.assertRaises(RuntimeError):
                    module.validate_labels(invalid, "taskflow-test-unique", "unique")
        with self.assertRaises(RuntimeError):
            module.validate_labels(labels, "another-project", "unique")

    def test_cleanup_refuses_all_deletion_if_any_resource_is_not_owned(self) -> None:
        module = self.module()
        with patch.object(module, "resources", return_value=[("container", "unexpected")]), patch.object(module, "run", return_value=subprocess.CompletedProcess([], 0, '[{"Config":{"Labels":{}}}]', "")) as command:
            with self.assertRaises(RuntimeError):
                module.cleanup(["docker", "compose"], {}, "taskflow-test-unique", "unique")
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0], ["docker", "container", "inspect", "unexpected"])

    def test_cleanup_checks_removal_after_compose_down(self) -> None:
        module = self.module()
        with patch.object(module, "resources", side_effect=[[], [("volume", "leftover")]]), patch.object(module, "run") as command:
            with self.assertRaises(RuntimeError):
                module.cleanup(["docker", "compose"], {}, "taskflow-test-unique", "unique")
        self.assertEqual(command.call_count, 1)
        self.assertIn("--volumes", command.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
