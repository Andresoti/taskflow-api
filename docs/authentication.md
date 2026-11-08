# Authentication API

For setup, see the [README](../README.md) and [container runbook](containerization.md). This document covers the authentication contracts and their limits.

## Start locally

Run commands from the repository root with PostgreSQL configured through the existing environment or `.env` file. Apply the migration before using the API. These commands do not initialize a JWT key on disk:

```bash
.venv/bin/python -m alembic upgrade head
export JWT_SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The generated secret is captured in the current shell environment, not printed or written to `.env`. Do not print it, commit it or copy it into notes. Regenerating it invalidates existing tokens. A persistent deployment needs appropriate secret provisioning; this example is only for local use.

The API validates `JWT_SECRET_KEY` at startup and refuses an absent or undersized key. `ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 15 and must be positive. Alembic uses separate database settings and does not require JWT configuration. `.env.example` intentionally leaves the secret blank.

## Contracts

### POST /auth/register

Accept JSON with `email` and `password` only. The shared `EmailInput` schema validates and canonicalizes the email (including Unicode/IDNA normalization), then lowercases it; passwords must contain 8–128 characters. Additional fields are rejected.

Return 201 with `id`, `email` and `created_at`. Passwords and their hashes are never included in this public schema. The stored password hash is Argon2id.

An existing canonical email returns 409. The PostgreSQL unique constraint protects concurrent inserts; rejected inserts are rolled back before the session is reused. This does not migrate manually inserted, differently cased existing addresses.

Validation failures return 422 with `detail` entries containing `type`, `loc` and `msg`. Submitted values and validation context are omitted so error responses do not echo passwords.

### POST /auth/login

Accept an OAuth2 password form (`application/x-www-form-urlencoded`), not a JSON body:

- `username`: the email address; it uses the same `EmailInput` normalization as registration, so Unicode and ASCII IDNA domain forms identify the same account.
- `password`: the original password.

This field name supports the Authorize flow in `/docs`; it does not introduce a separate username column.

Return 200 with `access_token` and `token_type: bearer`, plus `Cache-Control: no-store` and `Pragma: no-cache`.

Wrong passwords and unknown accounts return the same 401 body and `WWW-Authenticate: Bearer`. Both normal failed-login paths verify a password hash; this reduces an obvious account-existence timing difference, not all possible timing differences. Passwords longer than 128 characters are rejected before hashing with the same generic 401 response. Invalid email syntax also returns generic 401. Missing required form fields return 422.

### GET /users/me

Send the access token in the `Authorization` header using the Bearer scheme. The response uses the same public user schema as registration.

The dependency validates the HS256 signature, required `sub`/`iat`/`exp` claims and expiration, checks that the subject is a positive PostgreSQL INTEGER id, and loads that user from the database. A valid signature alone is insufficient: missing/deleted users also receive 401. Invalid tokens receive a generic 401 with a Bearer challenge, not internal decoding errors.

No public user id parameter selects another user's profile. Project/task authorization is documented in [Project and task CRUD](crud.md).

## Reproducible verification

Requirements: project dependencies installed, Docker access, and the `postgres:17` image already available locally. The runner uses `--pull=never` and never downloads an image implicitly.

```bash
.venv/bin/python -B scripts/test_with_postgres.py
```

The runner creates a uniquely labeled PostgreSQL container with no network, no published ports and tmpfs database storage. It communicates through a Unix socket, applies the real migration, runs all tests with Python warnings treated as errors, starts Uvicorn on an ephemeral loopback port, exercises registration/login/profile and project/task CRUD with ownership checks over HTTP, runs `alembic check`, and removes only its owned container.

If Docker creates the container but fails to return its id, cleanup recovers it by the unique name and verifies both the name and ownership label before stopping/removing its inspected id. The temporary directory is removed only after cleanup succeeds; uncertain cleanup preserves it for investigation. Unit tests inject CLI timeouts and ownership mismatches without contacting Docker.

The tests override `get_db` with a PostgreSQL session inside an outer transaction. Session commits use savepoints; each test's outer transaction is rolled back. No development database is migrated or truncated. Test secrets and smoke-test credentials remain in memory.

Running plain `unittest discover` without `TASKFLOW_TEST_DATABASE_URL` intentionally skips the HTTP integration tests; do not describe that run as full integration verification. Use the runner above for the full check.

## Boundaries

JWT secrets belong in private runtime configuration, not source control. The access token expires after 15 minutes by default; there are no refresh tokens or per-token revocation.

Email verification, rate limiting, HTTPS deployment, dependency vulnerability auditing and production abuse protection are not implemented. Password verification is not a guarantee of constant-time authentication.
