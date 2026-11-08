"""Offline regression checks for the initial PostgreSQL migration."""

import importlib.util
import io
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "alembic"
    / "versions"
    / "e256c74560c6_create_users_projects_and_tasks_tables.py"
)


def render_migration_sql(direction: str) -> str:
    """Render the real migration without loading settings or opening a connection."""
    spec = importlib.util.spec_from_file_location("initial_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load migration: {MIGRATION_PATH}")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        getattr(migration, direction)()
    return output.getvalue()


class InitialMigrationTests(unittest.TestCase):
    def test_downgrade_drops_enum_after_dependent_table(self) -> None:
        sql = render_migration_sql("downgrade")

        self.assertIn("DROP TYPE task_status;", sql)
        self.assertLess(sql.index("DROP TABLE tasks;"), sql.index("DROP TYPE task_status;"))
        self.assertNotIn("CASCADE", sql)


if __name__ == "__main__":
    unittest.main()
