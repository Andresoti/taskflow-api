"""Nested-task CRUD checks against real PostgreSQL and authenticated HTTP requests."""

import unittest

from app.models import Task, TaskStatus
from tests.api_test_case import PostgreSQLApiTestCase


class TaskApiTests(PostgreSQLApiTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.headers = await self.authenticate()
        response = await self.client.post("/projects", json={"name": "Task parent"}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        self.project = response.json()
        self.path = f"/projects/{self.project['id']}/tasks"

    async def create_task(self, title: str = "Synthetic task") -> dict:
        response = await self.client.post(
            self.path, json={"title": title, "description": "Original description"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    async def test_task_creation_requires_owned_parent_and_defaults_to_pending(self) -> None:
        payload = {"title": "  Implement task API  "}
        anonymous = await self.client.post(self.path, json=payload)
        self.assertEqual(anonymous.status_code, 401)
        other = await self.authenticate("other-synthetic@example.com")
        foreign = await self.client.post(self.path, json=payload, headers=other)
        self.assertEqual(foreign.status_code, 404)

        response = await self.client.post(self.path, json=payload, headers=self.headers)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"id", "title", "description", "status", "project_id", "created_at"})
        self.assertEqual(response.json()["title"], "Implement task API")
        self.assertEqual(response.json()["project_id"], self.project["id"])
        self.assertEqual(response.json()["status"], "pending")
        self.assertIsNone(response.json()["description"])
        saved = self.db.get(Task, response.json()["id"])
        assert saved is not None
        self.assertEqual(saved.status, TaskStatus.pending)

    async def test_task_detail_checks_both_parent_and_owner(self) -> None:
        task = await self.create_task()
        owned = await self.client.get(f"{self.path}/{task['id']}", headers=self.headers)
        self.assertEqual(owned.status_code, 200)
        self.assertEqual(owned.json(), task)
        other = await self.authenticate("other-synthetic@example.com")
        foreign = await self.client.get(f"{self.path}/{task['id']}", headers=other)
        self.assertEqual(foreign.status_code, 404)
        for headers in (self.headers, other):
            parent = await self.client.post("/projects", json={"name": "Different parent"}, headers=headers)
            self.assertEqual(parent.status_code, 201)
            wrong_parent = await self.client.get(
                f"/projects/{parent.json()['id']}/tasks/{task['id']}", headers=headers
            )
            self.assertEqual(wrong_parent.status_code, 404)
        missing = await self.client.get(f"{self.path}/{2**31 - 1}", headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        anonymous = await self.client.get(f"{self.path}/{task['id']}")
        self.assertEqual(anonymous.status_code, 401)

    async def test_task_list_is_scoped_to_owned_parent_and_paginated(self) -> None:
        empty = await self.client.get(self.path, headers=self.headers)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json(), [])
        tasks = [await self.create_task(f"Task {index}") for index in range(3)]
        another_parent = await self.client.post("/projects", json={"name": "Other parent"}, headers=self.headers)
        self.assertEqual(another_parent.status_code, 201)
        self.db.add(Task(title="Different project task", project_id=another_parent.json()["id"]))
        self.db.commit()

        full = await self.client.get(self.path, headers=self.headers)
        self.assertEqual([item["id"] for item in full.json()], [item["id"] for item in tasks])
        page = await self.client.get(self.path, params={"limit": 2, "offset": 1}, headers=self.headers)
        self.assertEqual([item["id"] for item in page.json()], [item["id"] for item in tasks[1:]])
        other = await self.authenticate("other-synthetic@example.com")
        foreign = await self.client.get(self.path, headers=other)
        missing = await self.client.get(f"/projects/{2**31 - 1}/tasks", headers=self.headers)
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(foreign.json(), missing.json())
        for parameters in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 2**31}):
            with self.subTest(parameters=parameters):
                invalid = await self.client.get(self.path, params=parameters, headers=self.headers)
                self.assertEqual(invalid.status_code, 422)

    async def test_task_patch_is_partial_and_preserves_parent(self) -> None:
        task = await self.create_task()
        other = await self.authenticate("other-synthetic@example.com")
        denied = await self.client.patch(f"{self.path}/{task['id']}", json={"status": "done"}, headers=other)
        self.assertEqual(denied.status_code, 404)

        progress = await self.client.patch(
            f"{self.path}/{task['id']}", json={"status": "in_progress"}, headers=self.headers
        )
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.json()["status"], "in_progress")
        self.assertEqual(progress.json()["title"], task["title"])
        self.assertEqual(progress.json()["description"], "Original description")
        changed = await self.client.patch(
            f"{self.path}/{task['id']}", json={"title": "  Updated task  ", "description": None}, headers=self.headers
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["title"], "Updated task")
        self.assertIsNone(changed.json()["description"])
        self.assertEqual(changed.json()["status"], "in_progress")
        self.assertEqual(changed.json()["project_id"], self.project["id"])
        self.assertEqual(changed.json()["created_at"], task["created_at"])
        unchanged = await self.client.patch(f"{self.path}/{task['id']}", json={}, headers=self.headers)
        self.assertEqual(unchanged.json(), changed.json())
        for payload in ({"title": None}, {"status": None}, {"title": " "}, {"title": "x" * 201}, {"status": "archived"}, {"project_id": 999}, {"owner_id": 999}, {"description": "x" * 5001}):
            with self.subTest(fields=list(payload)):
                invalid = await self.client.patch(f"{self.path}/{task['id']}", json=payload, headers=self.headers)
                self.assertEqual(invalid.status_code, 422)
        done = await self.client.patch(f"{self.path}/{task['id']}", json={"status": "done"}, headers=self.headers)
        self.assertEqual(done.status_code, 200)
        self.db.expire_all()
        saved = self.db.get(Task, task["id"])
        assert saved is not None
        self.assertEqual(saved.status, TaskStatus.done)

    async def test_task_delete_is_owner_only_and_does_not_delete_parent_or_siblings(self) -> None:
        task = await self.create_task("Delete this task")
        sibling = await self.create_task("Keep this task")
        other = await self.authenticate("other-synthetic@example.com")
        denied = await self.client.delete(f"{self.path}/{task['id']}", headers=other)
        self.assertEqual(denied.status_code, 404)
        another_parent = await self.client.post("/projects", json={"name": "Different parent"}, headers=self.headers)
        self.assertEqual(another_parent.status_code, 201)
        wrong_parent = await self.client.delete(
            f"/projects/{another_parent.json()['id']}/tasks/{task['id']}", headers=self.headers
        )
        self.assertEqual(wrong_parent.status_code, 404)

        deleted = await self.client.delete(f"{self.path}/{task['id']}", headers=self.headers)

        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.content, b"")
        self.db.expire_all()
        self.assertIsNone(self.db.get(Task, task["id"]))
        self.assertIsNotNone(self.db.get(Task, sibling["id"]))
        parent = await self.client.get(f"/projects/{self.project['id']}", headers=self.headers)
        self.assertEqual(parent.status_code, 200)
        repeated = await self.client.delete(f"{self.path}/{task['id']}", headers=self.headers)
        self.assertEqual(repeated.status_code, 404)

    async def test_task_create_rejects_invalid_values_and_relationship_injection(self) -> None:
        payloads = [
            {}, {"title": None}, {"title": " "}, {"title": "x" * 201},
            {"title": "Valid", "status": "archived"}, {"title": "Valid", "status": None},
            {"title": "Valid", "project_id": self.project["id"]},
            {"title": "Valid", "owner_id": 999}, {"title": "Valid", "id": 999},
            {"title": "Valid", "description": "x" * 5001},
        ]
        for index, payload in enumerate(payloads):
            with self.subTest(case=index):
                response = await self.client.post(self.path, json=payload, headers=self.headers)
                self.assertEqual(response.status_code, 422)
        valid = await self.client.post(
            self.path, json={"title": "x" * 200, "description": "x" * 5000, "status": "in_progress"}, headers=self.headers
        )
        self.assertEqual(valid.status_code, 201)
        self.assertEqual(valid.json()["status"], "in_progress")

    async def test_task_list_defaults_to_twenty_results(self) -> None:
        self.db.add_all([Task(title=f"Seed task {index}", project_id=self.project["id"]) for index in range(21)])
        self.db.commit()
        default_page = await self.client.get(self.path, headers=self.headers)
        self.assertEqual(len(default_page.json()), 20)
        full_page = await self.client.get(self.path, params={"limit": 100}, headers=self.headers)
        self.assertEqual(len(full_page.json()), 21)
        exhausted = await self.client.get(self.path, params={"offset": 21}, headers=self.headers)
        self.assertEqual(exhausted.json(), [])

    async def test_all_task_routes_require_authentication(self) -> None:
        task = await self.create_task()
        cases = [
            ("POST", self.path, {"title": "Unauthorized"}),
            ("GET", self.path, None),
            ("GET", f"{self.path}/{task['id']}", None),
            ("PATCH", f"{self.path}/{task['id']}", {"title": "Unauthorized"}),
            ("DELETE", f"{self.path}/{task['id']}", None),
        ]
        for method, path, payload in cases:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, path, json=payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    async def test_every_task_route_hides_foreign_or_missing_parent(self) -> None:
        task = await self.create_task()
        other = await self.authenticate("other-synthetic@example.com")
        cases = [("POST", "", {"title": "Intrusion"}), ("GET", "", None),
                 ("GET", f"/{task['id']}", None), ("PATCH", f"/{task['id']}", {"title": "Intrusion"}),
                 ("DELETE", f"/{task['id']}", None)]
        for method, suffix, payload in cases:
            with self.subTest(method=method, suffix=suffix):
                foreign = await self.client.request(method, self.path + suffix, json=payload, headers=other)
                missing = await self.client.request(
                    method, f"/projects/{2**31 - 1}/tasks{suffix}", json=payload, headers=self.headers
                )
                self.assertEqual(foreign.status_code, 404)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(foreign.json(), missing.json())
        unchanged = await self.client.get(f"{self.path}/{task['id']}", headers=self.headers)
        self.assertEqual(unchanged.json(), task)
        listed = await self.client.get(self.path, headers=self.headers)
        self.assertEqual([item["id"] for item in listed.json()], [task["id"]])

    async def test_task_cannot_be_read_updated_or_deleted_under_another_owned_project(self) -> None:
        task = await self.create_task()
        other = await self.authenticate("other-synthetic@example.com")
        for headers in (self.headers, other):
            parent = await self.client.post("/projects", json={"name": "Different parent"}, headers=headers)
            self.assertEqual(parent.status_code, 201)
            path = f"/projects/{parent.json()['id']}/tasks/{task['id']}"
            for method in ("GET", "PATCH", "DELETE"):
                with self.subTest(method=method, same_owner=headers == self.headers):
                    response = await self.client.request(method, path, json={"title": "Intrusion"}, headers=headers)
                    self.assertEqual(response.status_code, 404)
        unchanged = await self.client.get(f"{self.path}/{task['id']}", headers=self.headers)
        self.assertEqual(unchanged.json(), task)

    async def test_task_and_parent_id_bounds(self) -> None:
        task = await self.create_task()
        for identifier in ("0", "-1", "abc", str(2**31)):
            for method in ("GET", "PATCH", "DELETE"):
                with self.subTest(task_id=identifier, method=method):
                    response = await self.client.request(
                        method, f"{self.path}/{identifier}", json={"title": "Valid"}, headers=self.headers
                    )
                    self.assertEqual(response.status_code, 422)
            for method, suffix in (("POST", ""), ("GET", ""), ("GET", f"/{task['id']}"),
                                   ("PATCH", f"/{task['id']}"), ("DELETE", f"/{task['id']}")):
                with self.subTest(parent_id=identifier, method=method):
                    response = await self.client.request(
                        method, f"/projects/{identifier}/tasks{suffix}", json={"title": "Valid"}, headers=self.headers
                    )
                    self.assertEqual(response.status_code, 422)
        for method in ("GET", "PATCH", "DELETE"):
            with self.subTest(missing_method=method):
                response = await self.client.request(
                    method, f"{self.path}/{2**31 - 1}", json={"title": "Valid"}, headers=self.headers
                )
                self.assertEqual(response.status_code, 404)

    async def test_openapi_marks_every_crud_operation_as_protected(self) -> None:
        response = await self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operations = {
            "/projects": {"post": 201, "get": 200},
            "/projects/{project_id}": {"get": 200, "patch": 200, "delete": 204},
            "/projects/{project_id}/tasks": {"post": 201, "get": 200},
            "/projects/{project_id}/tasks/{task_id}": {"get": 200, "patch": 200, "delete": 204},
        }
        for path, methods in operations.items():
            for method, expected_status in methods.items():
                with self.subTest(path=path, method=method):
                    operation = schema["paths"][path][method]
                    self.assertEqual(operation["security"], [{"OAuth2PasswordBearer": []}])
                    self.assertIn(str(expected_status), operation["responses"])
                    if expected_status == 204:
                        self.assertNotIn("content", operation["responses"]["204"])


    async def test_task_rejects_nul_without_changing_persisted_state(self) -> None:
        task = await self.create_task()
        path = f"{self.path}/{task['id']}"
        for field in ("title", "description"):
            with self.subTest(field=field):
                payload = {"title": "Valid", field: "Invalid\x00text"}
                created = await self.client.post(self.path, json=payload, headers=self.headers)
                self.assertEqual(created.status_code, 422)
                updated = await self.client.patch(path, json={field: "Invalid\x00text"}, headers=self.headers)
                self.assertEqual(updated.status_code, 422)
                current = await self.client.get(path, headers=self.headers)
                self.assertEqual(current.json(), task)
        listed = await self.client.get(self.path, headers=self.headers)
        self.assertEqual([item["id"] for item in listed.json()], [task["id"]])


if __name__ == "__main__":
    unittest.main()
