# Authentication implementation checklist

- [x] Validate separate authentication settings without breaking Alembic.
- [x] Implement and test registration against disposable PostgreSQL.
- [x] Implement and test OAuth2 form login with generic failures.
- [x] Implement and test GET /users/me and OpenAPI security wiring.
- [x] Run complete checks and a real HTTP smoke test; verify cleanup.
- [x] Review the scoped authentication change independently.
- [x] Present and, if approved, save the Spanish Obsidian guide.

# Project and task CRUD checklist

- [x] Extract the PostgreSQL HTTP test fixture while preserving authentication coverage.
- [x] Implement and verify project create, list/detail, patch and delete with ownership.
- [x] Implement and verify nested task create, list/detail, patch and delete.
- [x] Verify cross-user denial, wrong-parent protection, validation, pagination and cascades.
- [x] Extend and run the real HTTP smoke test and all PostgreSQL-backed checks.
- [x] Obtain independent scoped review and resolve in-scope findings (NUL validation regression reproduced as HTTP 500, fixed to 422; 64 tests and real HTTP smoke pass).
- [x] Present and, if approved, save the next Spanish Obsidian guide (04-crud-proyectos-y-tareas.md approved and saved; vault index, log and project status updated).

# API containerization checklist

- [x] Test and implement safe container database configuration and process entrypoint.
- [x] Build a non-root image without secrets or unrelated files.
- [x] Test and implement db -> migrate -> api Compose dependencies and loopback ports.
- [x] Verify real container HTTP behavior, persistence across recreation and startup failure gates in an isolated project.
- [x] Run full regressions, verify protected state and remove only owned test resources (75 tests passed, real host/container HTTP and Compose persistence/failure checks passed).
- [x] Obtain read-only independent review and finish the English runbook (no blocking findings; inherited-port test issue reproduced/fixed, full 75-test suite rerun; docs/containerization.md completed).
- [x] Save the Spanish Obsidian guide and update vault navigation/state after explicit draft approval (05-docker-y-arranque-con-compose.md approved and saved on 2026-11-08; index, log and project status updated).

# Documentation and local delivery

- [x] Write the English README and reconcile the existing runbooks with the delivered API.
- [x] Follow the README from a clean source copy with a fresh virtualenv; verify HTTP examples, full PostgreSQL suite and Compose persistence/failure gates (75 tests passed, documented quickstart/curl/shutdown commands passed).
- [x] Check documentation links, inspect the exact commit payload for secrets/unrelated work and complete scoped read-only review (minor login error-contract wording corrected; staged runtime/config/tests and README match the verified clean copy).

Delivery is a local commit only, authorized by the user. Confirm its hash, committed contents and final working-tree state in the delivery report after Git succeeds. GitHub publication and the live demonstration are deferred.
