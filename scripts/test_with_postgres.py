"""Run all tests and a real HTTP smoke test using disposable PostgreSQL 17."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[1]
OWNER_LABEL = "taskflow.api-test-owner"


def run_command(arguments: list[str], *, environment: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        arguments, cwd=ROOT, env=environment, text=True, capture_output=True,
        check=True, timeout=60,
    )
    return completed.stdout.strip()


def smoke_crud(client: httpx.Client, headers: dict[str, str], database_url: str) -> None:
    """Exercise ownership and CRUD over real HTTP using two synthetic accounts."""
    def checked(response: httpx.Response, expected: int) -> httpx.Response:
        if response.status_code != expected:
            raise AssertionError(
                f"HTTP {response.request.method} {response.request.url.path}: expected {expected}, got {response.status_code}"
            )
        return response

    project = checked(client.post("/projects", json={"name": "HTTP project", "description": "Original"}, headers=headers), 201).json()
    project_path = f"/projects/{project['id']}"
    checked(client.get(project_path, headers=headers), 200)
    listed = checked(client.get("/projects", params={"limit": 1}, headers=headers), 200).json()
    if [item["id"] for item in listed] != [project["id"]]:
        raise AssertionError("HTTP project list did not contain the owned project")
    updated = checked(client.patch(project_path, json={"name": "Updated HTTP project", "description": None}, headers=headers), 200).json()
    if updated["name"] != "Updated HTTP project" or updated["description"] is not None:
        raise AssertionError("HTTP project PATCH did not apply the requested changes")

    task_path = project_path + "/tasks"
    task = checked(client.post(task_path, json={"title": "HTTP task", "description": "Original"}, headers=headers), 201).json()
    resource_path = f"{task_path}/{task['id']}"
    checked(client.get(resource_path, headers=headers), 200)
    tasks = checked(client.get(task_path, headers=headers), 200).json()
    if [item["id"] for item in tasks] != [task["id"]]:
        raise AssertionError("HTTP task list did not contain the owned task")
    updated_task = checked(client.patch(resource_path, json={"status": "done", "description": None}, headers=headers), 200).json()
    if updated_task["status"] != "done" or updated_task["description"] is not None:
        raise AssertionError("HTTP task PATCH did not persist the expected values")
    deleted = checked(client.delete(resource_path, headers=headers), 204)
    if deleted.content:
        raise AssertionError("HTTP 204 task deletion unexpectedly returned a body")
    checked(client.get(resource_path, headers=headers), 404)
    cascade_task = checked(client.post(task_path, json={"title": "Cascade task"}, headers=headers), 201).json()
    checked(client.post(task_path, json={"title": "Second cascade task"}, headers=headers), 201)

    other_email = f"other-{uuid.uuid4().hex}@example.com"
    other_password = secrets.token_urlsafe(24)
    checked(client.post("/auth/register", json={"email": other_email, "password": other_password}), 201)
    other_login = checked(client.post("/auth/login", data={"username": other_email, "password": other_password}), 200)
    other_headers = {"Authorization": "Bearer " + other_login.json()["access_token"]}
    other_project = checked(client.post("/projects", json={"name": "Other HTTP project"}, headers=other_headers), 201).json()
    other_task_path = f"/projects/{other_project['id']}/tasks"
    other_task = checked(client.post(other_task_path, json={"title": "Other task"}, headers=other_headers), 201).json()
    for method in ("GET", "PATCH", "DELETE"):
        checked(client.request(method, project_path, json={"name": "Intrusion"}, headers=other_headers), 404)
        checked(client.request(method, f"{task_path}/{cascade_task['id']}", json={"title": "Intrusion"}, headers=other_headers), 404)
        checked(client.request(method, f"{other_task_path}/{cascade_task['id']}", json={"title": "Intrusion"}, headers=other_headers), 404)
    checked(client.get(task_path, headers=other_headers), 404)
    checked(client.post(task_path, json={"title": "Intrusion"}, headers=other_headers), 404)
    other_list = checked(client.get("/projects", headers=other_headers), 200).json()
    if [item["id"] for item in other_list] != [other_project["id"]]:
        raise AssertionError("HTTP project list leaked another user's resources")

    deleted_project = checked(client.delete(project_path, headers=headers), 204)
    if deleted_project.content:
        raise AssertionError("HTTP 204 project deletion unexpectedly returned a body")
    checked(client.get(project_path, headers=headers), 404)
    checked(client.get(f"{other_task_path}/{other_task['id']}", headers=other_headers), 200)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            remaining = connection.scalar(text("SELECT count(*) FROM tasks WHERE project_id = :project_id"), {"project_id": project["id"]})
            if remaining != 0:
                raise AssertionError("HTTP project deletion left child tasks in PostgreSQL")
    finally:
        engine.dispose()
    checked(client.delete(f"/projects/{other_project['id']}", headers=other_headers), 204)
    print("Real HTTP CRUD smoke: projects+tasks=PASS; cross-user and wrong-parent=404; cascade=PASS", flush=True)


def smoke_http(environment: dict[str, str], workspace: Path) -> None:
    """Exercise Uvicorn over a loopback socket without printing credentials."""
    with socket.socket() as listener, (workspace / "uvicorn.log").open("w") as output:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "uvicorn", "app.main:app", "--fd", str(listener.fileno()),
             "--no-access-log"],
            cwd=ROOT, env=environment, pass_fds=(listener.fileno(),), stdout=output, stderr=output,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Uvicorn exited before becoming ready")
                    try:
                        if client.get("/openapi.json").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Uvicorn did not become ready")
                    time.sleep(0.1)
                email = f"smoke-{uuid.uuid4().hex}@example.com"
                password = secrets.token_urlsafe(24)
                registered = client.post("/auth/register", json={"email": email, "password": password})
                if registered.status_code != 201:
                    raise AssertionError(f"HTTP registration returned {registered.status_code}")
                login = client.post("/auth/login", data={"username": email, "password": password})
                if login.status_code != 200:
                    raise AssertionError(f"HTTP login returned {login.status_code}")
                profile = client.get(
                    "/users/me", headers={"Authorization": "Bearer " + login.json()["access_token"]}
                )
                if profile.status_code != 200 or profile.json() != registered.json():
                    raise AssertionError("HTTP profile did not match the registered user")
                if client.get("/users/me").status_code != 401:
                    raise AssertionError("HTTP profile accepted a request without credentials")
                if client.get("/docs").status_code != 200:
                    raise AssertionError("Interactive documentation is not served")
                print("Real HTTP smoke: register=201, login=200, me=200, unauthenticated=401, docs=200", flush=True)
                smoke_crud(
                    client,
                    {"Authorization": "Bearer " + login.json()["access_token"]},
                    environment["DATABASE_URL"],
                )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def cleanup_test_container(container_id: str | None, name: str, owner: str) -> None:
    """Recover a lost id by name, then verify ownership before stopping anything."""
    if container_id is None:
        matches = run_command([
            "docker", "ps", "--all", "--quiet", "--filter", "name=^/" + name + "$",
        ])
        if not matches:
            return
    target = container_id or name
    inspection = json.loads(run_command(["docker", "inspect", target]))[0]
    if inspection["Config"]["Labels"].get(OWNER_LABEL) != owner or inspection["Name"] != "/" + name:
        raise RuntimeError("Refusing cleanup: test container ownership mismatch")
    verified_id = inspection["Id"]
    # Graceful shutdown removes socket files before the temporary directory is deleted.
    run_command(["docker", "stop", "--time", "5", verified_id])
    run_command(["docker", "rm", verified_id])
    remaining = run_command(["docker", "ps", "--all", "--quiet", "--filter", "id=" + verified_id])
    if remaining:
        raise RuntimeError("Test container still exists after cleanup")


def main() -> None:
    owner = uuid.uuid4().hex
    name = "taskflow-api-test-" + owner[:12]
    container_id = None
    temporary_directory = tempfile.TemporaryDirectory(
        prefix="taskflow-api-tests-", dir=os.environ.get("TMPDIR"), delete=False
    )
    with temporary_directory as folder:
        workspace = Path(folder)
        socket_directory = workspace / "socket"
        socket_directory.mkdir(mode=0o777)
        socket_directory.chmod(0o777)
        try:
            container_id = run_command([
                "docker", "run", "--detach", "--pull=never", "--name", name,
                "--label", OWNER_LABEL + "=" + owner, "--network", "none",
                "--tmpfs", "/var/lib/postgresql/data:rw,nosuid",
                "--mount", f"type=bind,src={socket_directory},dst=/var/run/postgresql",
                "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
                "--env", "POSTGRES_DB=taskflow_auth_test",
                "postgres:17", "postgres", "-c", "listen_addresses=",
            ])
            inspection = json.loads(run_command(["docker", "inspect", container_id]))[0]
            if inspection["HostConfig"]["NetworkMode"] != "none" or inspection["HostConfig"]["PortBindings"]:
                raise RuntimeError("Test PostgreSQL must not have network access or published ports")
            if any(mount["Type"] == "volume" for mount in inspection["Mounts"]):
                raise RuntimeError("Test PostgreSQL must not use persistent volumes")
            url = URL.create(
                "postgresql+psycopg2", username="postgres", database="taskflow_auth_test",
                query={"host": str(socket_directory)},
            ).render_as_string(hide_password=False)
            environment = {
                **os.environ, "DATABASE_URL": url, "TASKFLOW_TEST_DATABASE_URL": url,
                "JWT_SECRET_KEY": secrets.token_urlsafe(32), "ACCESS_TOKEN_EXPIRE_MINUTES": "15",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT),
            }
            engine = create_engine(url, connect_args={"connect_timeout": 2})
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        logs = run_command(["docker", "logs", container_id])
                        if "PostgreSQL init process complete; ready for start up." not in logs:
                            raise RuntimeError("Initialization still in progress")
                        with engine.connect() as connection:
                            version = connection.scalar(text("SHOW server_version"))
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Disposable PostgreSQL did not become ready") from None
                        time.sleep(0.25)
                print(f"Disposable PostgreSQL: {version}; network=none; published ports=none", flush=True)
            finally:
                engine.dispose()
            migration_environment = {key: value for key, value in environment.items() if key != "JWT_SECRET_KEY"}
            run_command([sys.executable, "-B", "-m", "alembic", "upgrade", "head"], environment=migration_environment)
            print("Real migrations without JWT configuration: PASS", flush=True)
            subprocess.run(
                [sys.executable, "-B", "-W", "error", "-m", "unittest", "discover", "-s", "tests", "-v"],
                cwd=ROOT, env=environment, check=True, timeout=120,
            )
            smoke_http(environment, workspace)
            print(run_command([sys.executable, "-B", "-m", "alembic", "check"], environment=migration_environment), flush=True)
        finally:
            cleanup_test_container(container_id, name, owner)
            temporary_directory.cleanup()
            print("Cleanup: no owned PostgreSQL test container remains; no persistent test volume created", flush=True)


if __name__ == "__main__":
    main()
