# Project and task CRUD

For setup, see the [README](../README.md). These routes use the [authentication API](authentication.md) and the existing project/task models.

## Routes

All operations require a Bearer JWT and an existing authenticated user.

| Method | Path | Success |
| --- | --- | --- |
| POST | /projects | 201, created project |
| GET | /projects | 200, project array |
| GET | /projects/{project_id} | 200, project |
| PATCH | /projects/{project_id} | 200, updated project |
| DELETE | /projects/{project_id} | 204, no body |
| POST | /projects/{project_id}/tasks | 201, created task |
| GET | /projects/{project_id}/tasks | 200, task array |
| GET | /projects/{project_id}/tasks/{task_id} | 200, task |
| PATCH | /projects/{project_id}/tasks/{task_id} | 200, updated task |
| DELETE | /projects/{project_id}/tasks/{task_id} | 204, no body |

There are no unscoped `/tasks/{id}` routes. Use the nested project path for every task operation.

## Ownership and errors

Authentication identifies the caller; it does not authorize arbitrary resource ids.

- Project detail/update/delete use a dependency selecting both project id and authenticated owner id.
- Project listing always filters by authenticated owner id.
- Task operations first resolve an owned project. Task detail/update/delete additionally select both task id and that project's id.
- A task under a different project is not accessible, even when the caller owns both projects.
- A foreign project and a nonexistent project return the same 404 body. A task missing from an authorized project returns 404.
- A valid owned project with no tasks returns an empty array; a foreign or missing parent does not return an empty array.
- No input schema permits `owner_id`, `project_id`, `id` or `created_at`. The server assigns ownership and relationships from the authenticated user and URL.
- Missing/invalid credentials return 401 with a Bearer challenge. Invalid field values, unknown JSON fields, malformed/out-of-range ids and invalid pagination return 422.

Ids must be positive PostgreSQL INTEGER values. No owner-transfer or task-reparenting endpoint is provided.

## Inputs and public output

Project creation accepts a required `name` and optional `description`:

```json
{"name": "Portfolio API", "description": "Project description"}
```

Task creation accepts a required `title`, optional `description` and optional `status`:

```json
{"title": "Implement CRUD", "status": "pending"}
```

Names/titles are stripped of surrounding whitespace and must not be blank. Maximum lengths are 100 characters for a project name, 200 for a task title, and 5000 for a description. Descriptions default to null. Writable names, titles and descriptions reject U+0000 (NUL) with 422 before persistence because PostgreSQL character columns cannot store it.

Task status is one of `pending`, `in_progress`, `done`; creation defaults to `pending`. There is no state-machine transition policy beyond validating these enum values.

Project output contains `id`, `name`, `description`, `owner_id`, `created_at`. Task output contains `id`, `title`, `description`, `status`, `project_id`, `created_at`. Relationships and user password fields are not serialized.

## Partial updates

PATCH only changes fields that were supplied (`model_dump(exclude_unset=True)`). It does not replace the whole resource.

- Omitting a field preserves its stored value.
- `{"description": null}` clears the description.
- Explicit null for project name, task title or task status is rejected with 422.
- Empty PATCH `{}` is a documented no-op returning the current resource.
- Extra fields are rejected rather than silently ignored.

Creation and mutation commit explicitly. No optimistic locking is provided; concurrent changes to the same field are last-writer-wins. There is no automatic conflict-retry policy.

## Lists and pagination

Both collection endpoints return arrays ordered by ascending id:

- `limit`: default 20, minimum 1, maximum 100.
- `offset`: default 0, minimum 0, maximum 2147483647.

For example, `GET /projects?limit=20&offset=20`. There is no total count, arbitrary sorting or filtering in this slice. Offset pagination is bounded and predictable but is not a snapshot across concurrent writes.

## Deletion

Deleting an owned project removes its tasks through the existing ORM/database cascades. Deleting a task leaves its parent and siblings intact. Both successful delete operations return an empty 204 response; attempting to delete an already-missing resource returns 404.

Deletion is destructive. No soft-delete, undo, restore or project sharing is implemented. Do not retry a create operation blindly after an ambiguous timeout: POST does not implement idempotency keys.

## Verification

From the repository root, with Docker and the locally available `postgres:17` image:

```bash
.venv/bin/python -B scripts/test_with_postgres.py
```

The runner uses disposable PostgreSQL, real migrations, real hashing/JWT helpers and HTTP integration tests, followed by an actual Uvicorn smoke test. The CRUD smoke exercises both project/task lifecycles, two users, cross-user denial, wrong-parent denial and a direct PostgreSQL assertion that cascade deletion left no child tasks. It never uses the development database.

The shared HTTP fixture in `tests/api_test_case.py` rolls back an outer transaction after each test, while allowing route-level commits through savepoints. Running only plain unittest without the explicit disposable database URL skips the HTTP cases and is not a full verification run.

## Remaining boundaries

The API can run with PostgreSQL through [Docker Compose](containerization.md). It remains a local portfolio project: production rate limiting, HTTPS, vulnerability auditing and operational policies are not implemented.
