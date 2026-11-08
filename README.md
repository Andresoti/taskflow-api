# TaskFlow API

TaskFlow is a REST API for managing projects and their tasks. Users register, sign in and work with their own data. Projects are private: knowing another project's ID does not grant access to it.

Built with Python 3.14, FastAPI, SQLAlchemy, PostgreSQL 17 and Alembic. Passwords are hashed with Argon2id; protected routes use short-lived JWT access tokens.

## Run with Docker

You need Docker Engine and Docker Compose v2 or newer. The first build downloads the Python image and dependencies; startup also needs the `postgres:17` image. The shell commands below use Bash.

From the repository root, copy the example only if `.env` does not already exist:

```bash
test -f .env || cp .env.example .env
```

Review `.env` before starting. Its sample database password is for disposable local development only. Use a different local password if you plan to keep data, and never reuse credentials from another service. If you already have a database volume, changing `POSTGRES_PASSWORD` in `.env` does not change the password stored in that database.

Build the image, generate a key for this shell session and start the stack:

```bash
docker compose build
export JWT_SECRET_KEY="$(docker run --rm --network none --entrypoint python taskflow-api:local -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose up --detach --wait --wait-timeout 90 api
```

Open [Swagger UI](http://127.0.0.1:8000/docs). Register through `POST /auth/register`, then use **Authorize** with your email in the `username` field and your password. Login uses an OAuth2 form, not JSON.

Compose starts PostgreSQL, waits for it to become healthy, runs `alembic upgrade head`, then starts Uvicorn. The `migrate` container exits with code 0 when its work is done. A failed migration prevents a new API container from starting.

Both published ports bind to `127.0.0.1`. Defaults are 8000 for the API and 5432 for PostgreSQL. Set `API_PORT` or `POSTGRES_PORT` if either is occupied. To find the API's published port:

```bash
docker compose port api 8000
```

Stop the stack without removing its data:

```bash
docker compose down
```

Do not add `--volumes` unless you intend to delete the database. Keep the same Compose project name when restarting if you want to use the same volume.

The generated JWT key stays in the current shell. Keep using that shell to restart the stack. Generating a new key invalidates existing tokens; accounts and projects remain in PostgreSQL. For a stable local key, configure it privately in the untracked `.env` instead.

For existing containers, migration failures and rebuilds, see [the container runbook](docs/containerization.md). Stop the API before rebuilding for a schema update: startup dependencies do not stop an older API that is already running.

## API

Registration and login are public. Every other route below requires `Authorization: Bearer <access_token>`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/register` | Create an account |
| POST | `/auth/login` | Obtain an access token |
| GET | `/users/me` | Read the current user |
| POST, GET | `/projects` | Create or list your projects |
| GET, PATCH, DELETE | `/projects/{project_id}` | Read, update or delete a project |
| POST, GET | `/projects/{project_id}/tasks` | Create or list tasks in a project |
| GET, PATCH, DELETE | `/projects/{project_id}/tasks/{task_id}` | Read, update or delete a task |

A few requests against a fresh local database:

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"local-test-password"}'

curl --fail-with-body -sS http://127.0.0.1:8000/auth/login \
  --data-urlencode 'username=dev@example.com' \
  --data-urlencode 'password=local-test-password'
```

Set `TOKEN` to the `access_token` returned by login, then create a project:

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/projects \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Weekend project","description":"A small backlog"}'
```

Use the returned project ID when creating tasks. Task status is `pending`, `in_progress` or `done`; new tasks default to `pending`.

Lists use `limit` and `offset`, ordered by ascending ID. The default limit is 20 and the maximum is 100. `PATCH` changes only supplied fields: omitting `description` preserves it, while sending `null` clears it. Unknown input fields are rejected. Deleting a project also deletes its tasks.

A resource belonging to another user returns 404. A task must also belong to the project in the URL, even if you own both projects. See [CRUD contracts](docs/crud.md) and [authentication details](docs/authentication.md) for validation, responses and token behavior.

## Tests

The full integration runner uses disposable PostgreSQL, not SQLite or your development database. It requires Linux with Docker access, Python 3.14 and the project's dependencies:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
docker pull postgres:17
.venv/bin/python -B scripts/test_with_postgres.py
```

It applies real migrations, runs the unit and HTTP integration tests with warnings treated as errors, exercises Uvicorn over HTTP and checks for schema drift. Running plain `unittest discover` without the disposable database configuration skips the HTTP integration cases; use the runner for a full check.

To verify the actual container stack, including persistence after container recreation and startup failure handling:

```bash
.venv/bin/python -B scripts/test_with_compose.py
```

This builds the image and creates a separate Compose project with generated test credentials, free loopback ports and its own volume. Both runners clean up their test resources. The Compose runner retains the built image. Neither runner uses the development `.env` credentials or resets development data.

## Working on the API

You can also run Python on the host while PostgreSQL runs in Docker. After installing the dependencies above, start only the database and set `DATABASE_URL` in your local environment or `.env` to match its published port and credentials. URL-encode reserved characters in the host-side URL.

```bash
docker compose up --detach --wait db
.venv/bin/python -m alembic upgrade head
export JWT_SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Containers do not use the host's `DATABASE_URL`: the entrypoint constructs their URL with the Compose hostname `db` and the `POSTGRES_*` settings.

## Code layout and decisions

- `app/routers/` handles HTTP requests; `app/schemas/` defines accepted input and public output.
- `app/core/` holds configuration, password/token helpers and authentication/ownership dependencies.
- `app/models/` and `app/db/` contain SQLAlchemy models and request-scoped sessions. `alembic/` tracks schema changes.
- `tests/` covers authentication, ownership, validation, partial updates, pagination, cascades and container configuration. `scripts/` contains the isolated integration runners.

Ownership checks live in shared dependencies so detail, update and delete routes apply the same rules. Task routes are nested under projects to check both ownership and the parent relationship. The database's unique constraint handles duplicate registration, including concurrent inserts.

The application image runs as a non-root user. Compose makes its root filesystem read-only and gives it temporary scratch space. Migrations run as a separate service rather than inside each web worker. These choices keep the local setup small without mixing schema changes into request handling.

## Scope

This is a backend portfolio project, not a hosted service. It has no frontend, project sharing, refresh tokens, password reset or email verification. Writes do not use optimistic locking or creation idempotency keys.

Before exposing it publicly, add HTTPS, login rate limiting, secret provisioning, backups and a dependency vulnerability review. The base image tag and transitive Python dependencies are not fully locked. The API healthcheck checks HTTP availability, not continued database connectivity.
