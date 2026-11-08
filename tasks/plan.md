# Authentication endpoint implementation plan

## Scope and contracts

Connect the existing password/JWT helpers to three endpoints. Do not implement project/task CRUD, refresh tokens, deployment, or modify development data.

- `POST /auth/register`: JSON email/password; return 201 with id/email/created_at, never password fields. Treat email as case-insensitive. Return 409 for an existing email and 422 for invalid input. The database unique constraint remains the final concurrency guard.
- `POST /auth/login`: OAuth2 password form; `username` contains the email. Return an access_token/token_type pair. Unknown email and incorrect password return the same 401 response with a Bearer challenge. Do not echo credentials or allow caching of successful token responses.
- `GET /users/me`: accept a Bearer JWT, validate it and load the existing user by its integer id. Return only the public user schema. Missing/invalid/expired tokens, malformed subject ids and deleted users return 401.

## Decisions and trust boundaries

- Validate request bodies/forms at the HTTP boundary; use separate input and output schemas. Strip submitted values from validation error responses to avoid echoing passwords.
- Keep authentication settings separate from database settings so Alembic does not require a JWT key. Load and validate the mandatory secret at API startup, with no default. Do not write a real key to .env, source files, notes or test artifacts.
- Use existing HS256 helpers, UTC and the configured positive lifetime (default 15 minutes). A token is not proof that the user still exists; query the database on protected requests.
- Preserve synchronous SQLAlchemy sessions and existing model/migration structure. Commit registration explicitly and roll back a rejected insert.
- Use a dummy password hash for unknown accounts to exercise password verification on both failed-login paths. This is not a substitute for future rate limiting.

## Ordered slices and acceptance checks

1. Authentication settings: reject missing/short keys and nonpositive lifetime; accept a valid configuration; migration commands still work without a JWT secret.
2. Registration: test the real HTTP request, persisted Argon2 hash, public response, duplicate/invalid input, and transaction recovery in isolated PostgreSQL.
3. Login: test valid credentials, wrong password and unknown account; decode returned JWT and verify its subject/lifetime and no-store headers.
4. Current user: test register → login → me, missing/invalid/expired tokens, unknown/deleted users and invalid subject ids; verify OpenAPI OAuth2 wiring.
5. Verification: run all unit/integration tests with warnings treated as errors, migration/schema checks and an actual local HTTP smoke test; obtain a read-only independent review and prepare a Spanish Obsidian draft for approval.

## Test isolation

Use a unique disposable PostgreSQL 17 container with network disabled, no published ports and tmpfs database storage. Connect through a scratch-directory Unix socket. Apply the real Alembic migration. Integration tests use an explicit test database URL, override get_db and roll back an outer transaction after each test. Never truncate or migrate the development database. Remove only the owned test container after checking its identity/label.

## Limits

Local educational/portfolio stage, not a production deployment. No persistent production secret will be provisioned. Rate limiting, HTTPS deployment, revocation, email verification and dependency vulnerability auditing remain outside this slice and must not be represented as completed.

# Project and nested-task CRUD plan — 2026-11-07

The authentication slice above is completed and retained as history. The next authorized slice implements projects and nested tasks only; no schema changes or new dependencies are planned.

## API contracts

- Projects: POST /projects (201), GET /projects (200), GET /projects/{project_id} (200), PATCH /projects/{project_id} (200), DELETE /projects/{project_id} (204 with no response body).
- Tasks: the same operations under /projects/{project_id}/tasks and /projects/{project_id}/tasks/{task_id}. A task must belong to the project in the URL.
- Every route requires the existing authenticated user. Query projects with both id and owner_id. Query tasks under the already-authorized project. Return the same 404 for nonexistent and foreign resources; never accept owner_id/project_id/id from request bodies.
- Lists return arrays ordered by id, with limit=20 (1–100) and offset=0 (0–2147483647). Do not add filtering, sharing or ordering features.
- Project names: trimmed nonblank strings up to 100 characters. Task titles: trimmed nonblank strings up to 200 characters. Descriptions: nullable strings up to 5000 characters. Task status: pending/in_progress/done, default pending on creation.
- PATCH uses exclude_unset: omitted fields stay unchanged, description=null clears it, and explicit null for name/title/status is rejected. Extra fields are forbidden. Empty PATCH is a documented no-op.
- Deleting an owned project also removes its tasks using the existing model/database cascades. Repeat deletion of a missing resource returns 404.

## Ordered implementation and verification

1. Extract the existing PostgreSQL HTTP fixture without changing existing authentication test methods; rerun the 41-test baseline.
2. Build project create, read/list, patch and delete in individual RED → GREEN slices; cover unauthenticated callers, ownership injection, foreign users, invalid input, pagination and clearing descriptions.
3. Build task creation, read/list, patch and delete in the same way; additionally cover wrong parent projects, status validation, forbidden reparenting and cascade deletion.
4. Extend the real Uvicorn smoke test with two users, project/task lifecycles, cross-user denial and project cascade deletion. Run the complete PostgreSQL-backed suite, migration drift check, syntax and pip checks. Verify original code/data protection and cleanup.
5. Obtain scoped read-only independent review; resolve in-scope findings; prepare a Spanish Obsidian guide and wait for approval before saving it.

## Trust boundaries and limits

JWT authentication is not resource authorization. Resource ids, project ids and JSON fields are all untrusted. Use parameterized SQLAlchemy expressions and server-derived ownership, never a client-provided owner or relationship. Validate ids against positive PostgreSQL INTEGER bounds. Data in these tests is synthetic and lives only in disposable PostgreSQL.

Do not touch development data, real secrets, committed history or unrelated code. Do not overwrite the already-completed authentication work. No frontend, deployment, migrations, refresh tokens, new infrastructure or production-hardening claim is authorized by this slice.

# API containerization and Compose startup — 2026-11-07

The CRUD slice above is complete. The user authorized local API containerization and Compose startup, not deployment, publication or modification of development data.

## Decisions

- Use the official Python 3.14 slim Debian image, matching the existing Python minor version. Keep existing dependencies; no OS package installation or application architecture changes.
- Build one non-root application image. Separate the one-shot `migrate` service from `api`; order db healthy -> migrations successful -> API. Do not run concurrent migrations in every web worker.
- Build the container database URL with SQLAlchemy URL.create from explicit PostgreSQL components. Inside Compose the database host is `db`, not localhost; preserve the host DATABASE_URL and real .env unchanged. Never put JWT secrets into the image or migrate service.
- Keep database and API host ports on loopback, configurable for isolated tests. Remove the fixed database container name so unique Compose projects cannot collide. Keep the existing postgres_data volume key and PostgreSQL 17 data path.
- Explicitly copy only runtime source/config into the image; exclude secrets, local virtualenv, Git, notes and tests from the build context. Use a read-only application root filesystem, temporary scratch space, dropped capabilities and no-new-privileges.

## Slices and acceptance checks

1. Add entrypoint tests first: reserved URL characters survive parsing; component validation is fail-closed without leaking values; command execution replaces the wrapper. Build the image and verify its non-root user, imports and build exclusions.
2. Add resolved Compose checks first: service startup conditions, environment boundaries, loopback binding, named persistence and no fixed container names. Preserve the existing loopback regression.
3. Execute a unique isolated Compose project with generated synthetic credentials, ephemeral loopback ports and its own labeled volume. Verify actual HTTP registration/login/profile, project/task lifecycle and cross-user denial. Recreate the database and API without deleting the volume, then verify records still exist. Test migrations failing prevent API startup, and invalid JWT configuration prevents serving.
4. Run the full PostgreSQL suite plus real Compose verification; confirm development container, real .env and unrelated files unchanged. Verify cleanup only touches resources carrying this test's project and ownership labels. Keep the built image locally, never prune Docker globally.
5. Read-only independent review, English repository runbook and Spanish Obsidian draft for approval. Do not save notes until approved.

## Risks and limits

Docker base/dependency downloads require network access but no paid services. Direct dependencies are pinned; the base tag and transitive dependencies are not a complete reproducible lock. Migration failure must block API startup; never downgrade a real database automatically. Local HTTP is loopback-only, not a production TLS deployment. Docker environment variables are visible to users with Docker access, so this is not a production secret-management design.
