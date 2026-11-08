# Local API containers and Compose

Start with the [README](../README.md) for a first run. This runbook covers configuration, persistence and troubleshooting for the [authentication](authentication.md) and [project/task CRUD](crud.md) services. The setup is intended for local use, not production deployment.

## Services and startup order

- `db`: PostgreSQL 17 with a named `postgres_data` volume and readiness check.
- `migrate`: the application image running `alembic upgrade head` once, after PostgreSQL reports healthy. It must exit successfully.
- `api`: the same image running Uvicorn, after the database is healthy and migrations succeed.

The migration container normally appears as `Exited (0)`; it is not a failed server. A migration error prevents a fresh API container from starting. Do not bypass this with `--no-deps` for normal startup.

The image runs as UID/GID `10001:10001`. API/migration containers use a read-only root filesystem, writable temporary `/tmp`, no Linux capabilities and `no-new-privileges`. The database uses its official image's existing user/storage behavior, not the application restrictions.

The API healthcheck requests `/openapi.json`. It checks HTTP availability after application startup, not continued database connectivity or every business operation. Database readiness and migration completion are separate startup gates; they are not automatic recovery policies. No restart loop or hot reload is enabled.

## First local startup

Requirements: Docker Engine, Docker Compose v2 or newer supporting successful-completion dependencies, and access to download the official Python/PostgreSQL images and existing Python dependencies.

1. From the repository root, create `.env` from `.env.example` only if `.env` does not already exist. Never overwrite an existing configuration.
2. Configure `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` for your local database. The example password is only a development placeholder, not a production credential.
3. Supply a cryptographically random `JWT_SECRET_KEY` of at least 32 UTF-8 bytes through your local environment or untracked `.env`. Do not share it, add it to the image, or paste it into notes. An empty key deliberately prevents API startup.
4. Optionally set `API_PORT` (default 8000) and `POSTGRES_PORT` (default 5432). Both bind to `127.0.0.1` only. A value of 0 requests a free host port.
5. Run:

```bash
docker compose up --build --detach --wait --wait-timeout 90 api
docker compose ps --all
docker compose port api 8000
```

With the default API port, open `http://127.0.0.1:8000/docs`. You can use registration, OAuth2 form login and Authorize to exercise the protected routes. If port 8000 is occupied, set another `API_PORT`; do not stop an unrelated service.

The command above can create/recreate this project's containers and apply migrations to its database. During implementation it was exercised only under a unique disposable project, never against the existing development database.

## Database configuration: host versus container

`DATABASE_URL` remains for Python/Alembic commands run on the host. Its host is normally `localhost`, with the published PostgreSQL port and URL-encoded credentials. If that host port changes, update the host-side URL accordingly.

Compose intentionally does not forward that URL to application containers: their `localhost` would refer to themselves. Instead it passes the three `POSTGRES_*` components and `TASKFLOW_DATABASE_HOST=db`. The image entrypoint uses SQLAlchemy `URL.create` to construct a correctly escaped URL at runtime before replacing itself with Alembic or Uvicorn using `exec`.

This preserves reserved password characters such as `@`, `:`, `/`, `%` and `$` without asking the user to manually encode the container URL. Values still need correct quoting at their source: single-quote literal values in a Compose `.env` file when they contain `$`, or supply them as already-set shell environment variables. Never print the resolved configuration to share it: `docker compose config` can expose credentials.

Only `api` receives the JWT key and access-token lifetime. Neither `db` nor `migrate` receives JWT settings. Alembic can therefore migrate the database without authentication configuration.

## Existing development containers and persistence

The fixed `container_name: taskflow-db` was removed. Compose-generated names let independent projects coexist; use `docker compose ps` or `docker compose exec db ...`, rather than relying on a container's display name.

The volume key remains `postgres_data`, mounted at PostgreSQL 17's `/var/lib/postgresql/data`. Compose normally prefixes the volume name with its project name. Keep the same Compose project name if you intend to reuse existing project data; changing `-p` selects a different volume and can look like data loss even though the old volume still exists.

Before the first normal startup against old data, inspect which project and volume the old database belongs to and arrange a backup. A differently named/unmanaged old container may still occupy port 5432. Do not delete it or its volume to resolve a conflict. Starting an existing volume with different `POSTGRES_*` values does not reset its stored users, passwords or database contents.

To stop the stack and retain data:

```bash
docker compose down
```

To start it again:

```bash
docker compose up --detach --wait --wait-timeout 90 api
```

Do not add `--volumes` to routine shutdown. It destroys the project's database volume. The verification runner uses that flag only for its uniquely named, ownership-checked disposable test volume.

To rebuild after code changes, stop the API first so an older running process cannot continue serving if a new migration fails:

```bash
docker compose stop api
docker compose up --build --detach --wait --wait-timeout 90 api
```

Startup dependencies do not automatically stop an already-running old API when a later migration command fails. For manual migration commands, stop the API first and check exit status. Never downgrade the development database automatically.

## Image contents and limits

The Dockerfile copies only requirements, application code, Alembic files and the small entrypoint. `.dockerignore` uses an allowlist: real `.env`, Git history, local `.venv`, tests, scripts, documentation and generated Python caches stay out of the build context/image.

No secret is passed as a Docker build argument. Runtime environment values are still inspectable by users with Docker access; this is not a production secret store.

The base is the official `python:3.14-slim-bookworm` tag. Direct requirements are version-pinned, but transitive dependencies and the base tag are not fully locked; rebuilding later can resolve different compatible packages. A successful build or `pip check` is not a vulnerability audit.

## Reproducible verification

Run both commands from the repository root:

```bash
.venv/bin/python -B scripts/test_with_postgres.py
.venv/bin/python -B scripts/test_with_compose.py
```

The first runs the complete unit/HTTP integration suite against disposable PostgreSQL over a Unix socket, followed by a real host Uvicorn smoke and migration drift check.

The second builds the image and exercises the actual Compose topology using a unique project, explicit ownership labels, generated synthetic credentials, free loopback ports and its own named volume. It never loads the development `.env`. Its bridge network is separate from other Compose projects but is not a network-disabled sandbox.

It checks:

- Non-root API execution with a read-only root filesystem.
- Migration success before serving and absence of JWT settings in the migration container.
- Actual HTTP registration/login/profile/docs and project/task CRUD with two users, ownership denial, wrong-parent protection and cascade deletion.
- User, project and task persistence after database and API containers are removed/recreated while their volume is retained.
- `alembic check` from the migration image.
- A deliberately failed migration (exit 23) preventing API startup.
- An empty JWT key preventing the API from serving.
- Cleanup by project and owner labels, followed by verification that owned containers, network and test volume are gone. The built image/cache are retained locally; there is no global Docker prune.

If cleanup cannot establish ownership or remove a resource, it fails rather than deleting a broader target. The temporary runner configuration is retained on cleanup failure. It contains isolation metadata, not the generated credentials.

## Troubleshooting and scope

- Migration exited nonzero: inspect its local logs, correct configuration or the actual migration issue, then retry startup. Do not bypass dependencies or run a destructive downgrade.
- API exits with JWT validation error: supply a valid local secret; do not weaken validation or hardcode a fallback.
- Database healthcheck fails: check credentials, volume provenance, initialization and port conflicts. Do not reset a volume as a routine fix.
- After recreating PostgreSQL, recreate/restart the API as well; this slice does not add connection-pool recovery policies for arbitrary database outages.

HTTPS deployment, CI/CD, sharing, frontend, rate limiting, production secret management and dependency vulnerability auditing are outside this setup. The [README](../README.md) describes the supported scope and the commands needed to run and test it.
