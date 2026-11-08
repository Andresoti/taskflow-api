"""PostgreSQL-backed project CRUD and resource-ownership checks."""

import unittest

from app.models import Project, Task
from tests.api_test_case import PostgreSQLApiTestCase


class ProjectApiTests(PostgreSQLApiTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.headers = await self.authenticate()
        profile = await self.client.get("/users/me", headers=self.headers)
        self.assertEqual(profile.status_code, 200)
        self.owner_id = profile.json()["id"]

    async def test_project_creation_requires_auth_and_uses_authenticated_owner(self) -> None:
        payload = {"name": "  Portfolio API  ", "description": "Synthetic project"}
        anonymous = await self.client.post("/projects", json=payload)
        self.assertEqual(anonymous.status_code, 401)

        response = await self.client.post("/projects", json=payload, headers=self.headers)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"id", "name", "description", "owner_id", "created_at"})
        self.assertEqual(response.json()["name"], "Portfolio API")
        self.assertEqual(response.json()["owner_id"], self.owner_id)
        saved = self.db.get(Project, response.json()["id"])
        assert saved is not None
        self.assertEqual(saved.description, payload["description"])
        self.assertEqual(saved.owner_id, self.owner_id)

    async def create_project(self, name: str = "Synthetic project", *, headers=None) -> dict:
        response = await self.client.post(
            "/projects", json={"name": name, "description": "Original description"},
            headers=self.headers if headers is None else headers,
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    async def test_project_detail_is_visible_only_to_its_owner(self) -> None:
        project = await self.create_project()
        other = await self.authenticate("other-synthetic@example.com")

        owned = await self.client.get(f"/projects/{project['id']}", headers=self.headers)
        self.assertEqual(owned.status_code, 200)
        self.assertEqual(owned.json(), project)
        foreign = await self.client.get(f"/projects/{project['id']}", headers=other)
        missing = await self.client.get(f"/projects/{2**31 - 1}", headers=self.headers)
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(foreign.json(), missing.json())
        anonymous = await self.client.get(f"/projects/{project['id']}")
        self.assertEqual(anonymous.status_code, 401)

    async def test_project_list_is_owner_scoped_ordered_and_paginated(self) -> None:
        projects = [await self.create_project(f"Project {index}") for index in range(3)]
        other = await self.authenticate("other-synthetic@example.com")
        foreign = await self.create_project("Foreign project", headers=other)

        response = await self.client.get("/projects", params={"limit": 2, "offset": 1}, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [item["id"] for item in projects[1:]])
        other_list = await self.client.get("/projects", headers=other)
        self.assertEqual([item["id"] for item in other_list.json()], [foreign["id"]])
        anonymous = await self.client.get("/projects")
        self.assertEqual(anonymous.status_code, 401)
        for parameters in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 2**31}):
            with self.subTest(parameters=parameters):
                invalid = await self.client.get("/projects", params=parameters, headers=self.headers)
                self.assertEqual(invalid.status_code, 422)

    async def test_project_patch_preserves_omitted_fields_and_allows_clearing_description(self) -> None:
        project = await self.create_project()
        other = await self.authenticate("other-synthetic@example.com")
        foreign = await self.client.patch(
            f"/projects/{project['id']}", json={"name": "Intrusion"}, headers=other
        )
        self.assertEqual(foreign.status_code, 404)

        updated = await self.client.patch(
            f"/projects/{project['id']}", json={"name": "  Updated project  "}, headers=self.headers
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "Updated project")
        self.assertEqual(updated.json()["description"], "Original description")
        self.assertEqual(updated.json()["owner_id"], self.owner_id)
        self.assertEqual(updated.json()["created_at"], project["created_at"])
        unchanged = await self.client.patch(f"/projects/{project['id']}", json={}, headers=self.headers)
        self.assertEqual(unchanged.json(), updated.json())
        cleared = await self.client.patch(
            f"/projects/{project['id']}", json={"description": None}, headers=self.headers
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["description"])
        for payload in ({"name": None}, {"name": " "}, {"name": "x" * 101}, {"owner_id": 999}, {"description": "x" * 5001}):
            with self.subTest(fields=list(payload)):
                invalid = await self.client.patch(f"/projects/{project['id']}", json=payload, headers=self.headers)
                self.assertEqual(invalid.status_code, 422)

    async def test_project_delete_is_owner_only_and_cascades_only_its_tasks(self) -> None:
        project = await self.create_project()
        other = await self.authenticate("other-synthetic@example.com")
        foreign_project = await self.create_project("Foreign project", headers=other)
        owned_task = Task(title="Owned task", project_id=project["id"])
        foreign_task = Task(title="Foreign task", project_id=foreign_project["id"])
        self.db.add_all([owned_task, foreign_task])
        self.db.commit()
        owned_task_id, foreign_task_id = owned_task.id, foreign_task.id

        denied = await self.client.delete(f"/projects/{project['id']}", headers=other)
        self.assertEqual(denied.status_code, 404)
        anonymous = await self.client.delete(f"/projects/{project['id']}")
        self.assertEqual(anonymous.status_code, 401)
        deleted = await self.client.delete(f"/projects/{project['id']}", headers=self.headers)

        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.content, b"")
        self.db.expire_all()
        self.assertIsNone(self.db.get(Project, project["id"]))
        self.assertIsNone(self.db.get(Task, owned_task_id))
        self.assertIsNotNone(self.db.get(Project, foreign_project["id"]))
        self.assertIsNotNone(self.db.get(Task, foreign_task_id))
        repeated = await self.client.delete(f"/projects/{project['id']}", headers=self.headers)
        self.assertEqual(repeated.status_code, 404)

    async def test_project_create_rejects_invalid_or_server_owned_fields(self) -> None:
        payloads = [
            {}, {"name": " "}, {"name": None}, {"name": "x" * 101},
            {"name": "Valid", "owner_id": self.owner_id}, {"name": "Valid", "id": 999},
            {"name": "Valid", "description": "x" * 5001},
        ]
        for index, payload in enumerate(payloads):
            with self.subTest(case=index):
                response = await self.client.post("/projects", json=payload, headers=self.headers)
                self.assertEqual(response.status_code, 422)
        valid = await self.client.post("/projects", json={"name": "Valid"}, headers=self.headers)
        self.assertEqual(valid.status_code, 201)
        self.assertIsNone(valid.json()["description"])

    async def test_project_list_is_empty_when_absent_and_defaults_to_twenty_results(self) -> None:
        empty = await self.client.get("/projects", headers=self.headers)
        self.assertEqual(empty.json(), [])
        self.db.add_all([Project(name=f"Seed project {index}", owner_id=self.owner_id) for index in range(21)])
        self.db.commit()

        default_page = await self.client.get("/projects", headers=self.headers)
        self.assertEqual(len(default_page.json()), 20)
        full_page = await self.client.get("/projects", params={"limit": 100}, headers=self.headers)
        self.assertEqual(len(full_page.json()), 21)
        exhausted = await self.client.get("/projects", params={"offset": 21}, headers=self.headers)
        self.assertEqual(exhausted.json(), [])

    async def test_all_project_routes_require_authentication(self) -> None:
        project = await self.create_project()
        cases = [
            ("POST", "/projects", {"name": "Unauthorized"}),
            ("GET", "/projects", None),
            ("GET", f"/projects/{project['id']}", None),
            ("PATCH", f"/projects/{project['id']}", {"name": "Unauthorized"}),
            ("DELETE", f"/projects/{project['id']}", None),
        ]
        for method, path, payload in cases:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, path, json=payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    async def test_project_id_bounds_and_missing_resource_errors(self) -> None:
        for identifier in ("0", "-1", "abc", str(2**31)):
            for method in ("GET", "PATCH", "DELETE"):
                with self.subTest(identifier=identifier, method=method):
                    response = await self.client.request(
                        method, f"/projects/{identifier}", json={"name": "Valid"}, headers=self.headers
                    )
                    self.assertEqual(response.status_code, 422)
        for method in ("GET", "PATCH", "DELETE"):
            with self.subTest(missing_method=method):
                response = await self.client.request(
                    method, f"/projects/{2**31 - 1}", json={"name": "Valid"}, headers=self.headers
                )
                self.assertEqual(response.status_code, 404)


    async def test_project_rejects_nul_without_changing_persisted_state(self) -> None:
        project = await self.create_project()
        path = f"/projects/{project['id']}"
        for field in ("name", "description"):
            with self.subTest(field=field):
                payload = {"name": "Valid", field: "Invalid\x00text"}
                created = await self.client.post("/projects", json=payload, headers=self.headers)
                self.assertEqual(created.status_code, 422)
                updated = await self.client.patch(path, json={field: "Invalid\x00text"}, headers=self.headers)
                self.assertEqual(updated.status_code, 422)
                current = await self.client.get(path, headers=self.headers)
                self.assertEqual(current.json(), project)
        listed = await self.client.get("/projects", headers=self.headers)
        self.assertEqual([item["id"] for item in listed.json()], [project["id"]])


if __name__ == "__main__":
    unittest.main()
