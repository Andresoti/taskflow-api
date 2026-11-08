"""Construct container database settings, then replace this process with its command."""

import os
import sys

from sqlalchemy.engine import URL


def configure_database() -> None:
    host = os.environ.get("TASKFLOW_DATABASE_HOST")
    if not host:
        return
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing container database settings: " + ", ".join(missing))
    # URL.create handles reserved characters without embedding credentials in Compose.
    os.environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg2", host=host, port=5432,
        username=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    ).render_as_string(hide_password=False)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("A container command is required")
    configure_database()
    # exec preserves signals and exit status for Uvicorn and the one-shot migration.
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
