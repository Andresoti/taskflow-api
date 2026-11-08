"""Build and verify the actual Compose stack without using development data."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import uuid

import httpx
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[1]
OWNER_LABEL = "taskflow.compose-test-owner"


def run(arguments: list[str], environment: dict[str, str] | None = None, *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Do not echo rendered Compose configuration, command output or credentials.
        raise RuntimeError(f"Container verification command failed (exit {result.returncode})")
    return result


def resources(project: str) -> list[tuple[str, str]]:
    found = []
    for kind in ("container", "network", "volume"):
        arguments = ["docker", kind, "ls"]
        if kind == "container":
            arguments.append("--all")
        field = "{{.Name}}" if kind == "volume" else "{{.ID}}"
        result = run(arguments + ["--filter", "label=com.docker.compose.project=" + project, "--format", field])
        found.extend((kind, identifier) for identifier in result.stdout.split())
    return found


def validate_labels(labels: dict[str, str], project: str, owner: str) -> None:
    if labels.get("com.docker.compose.project") != project or labels.get(OWNER_LABEL) != owner:
        raise RuntimeError("Refusing cleanup: Compose resource ownership mismatch")


def cleanup(command: list[str], environment: dict[str, str], project: str, owner: str) -> None:
    for kind, identifier in resources(project):
        data = json.loads(run(["docker", kind, "inspect", identifier]).stdout)[0]
        labels = data["Config"]["Labels"] if kind == "container" else data["Labels"]
        validate_labels(labels or {}, project, owner)
    run(command + ["down", "--volumes", "--timeout", "5"], environment)
    if resources(project):
        raise RuntimeError("Owned Compose resources remain after cleanup")
    print("Compose cleanup: owned containers, network and test volume removed", flush=True)


def service_state(command: list[str], environment: dict[str, str], service: str) -> dict:
    identifier = run(command + ["ps", "--all", "--quiet", service], environment).stdout.strip()
    if not identifier:
        return {}
    return json.loads(run(["docker", "container", "inspect", identifier]).stdout)[0]


def published_port(command: list[str], environment: dict[str, str], service: str, port: int) -> int:
    address = run(command + ["port", service, str(port)], environment).stdout.strip()
    host, published = address.rsplit(":", 1)
    if host != "127.0.0.1":
        raise AssertionError(f"Test stack published an unexpected address: {address!r}")
    return int(published)


def verify_http(command: list[str], environment: dict[str, str]) -> dict:
    # Import only when the real smoke runs; unit tests never create containers.
    sys.path.insert(0, str(ROOT))
    from scripts.test_with_postgres import smoke_crud

    api_port = published_port(command, environment, "api", 8000)
    db_port = published_port(command, environment, "db", 5432)
    database_url = URL.create(
        "postgresql+psycopg2", host="127.0.0.1", port=db_port,
        username=environment["POSTGRES_USER"], password=environment["POSTGRES_PASSWORD"],
        database=environment["POSTGRES_DB"],
    ).render_as_string(hide_password=False)
    with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=10, trust_env=False) as client:
        email = "container-" + uuid.uuid4().hex + "@example.com"
        password = secrets.token_urlsafe(24)
        registered = client.post("/auth/register", json={"email": email, "password": password})
        if registered.status_code != 201:
            raise AssertionError("Container registration did not return 201")
        login = client.post("/auth/login", data={"username": email, "password": password})
        if login.status_code != 200:
            raise AssertionError("Container login did not return 200")
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        profile = client.get("/users/me", headers=headers)
        if profile.status_code != 200 or profile.json() != registered.json():
            raise AssertionError("Container profile mismatch")
        if client.get("/users/me").status_code != 401 or client.get("/docs").status_code != 200:
            raise AssertionError("Container authentication or docs check failed")
        smoke_crud(client, headers, database_url)
        project = client.post("/projects", json={"name": "Persistence sentinel"}, headers=headers)
        if project.status_code != 201:
            raise AssertionError("Could not create persistence sentinel")
        task = client.post(f"/projects/{project.json()['id']}/tasks", json={"title": "Survive recreation"}, headers=headers)
        if task.status_code != 201:
            raise AssertionError("Could not create task persistence sentinel")
        print("Container HTTP: register/login/me/docs and CRUD passed", flush=True)
        return {"email": email, "password": password, "project": project.json(), "task": task.json()}


def verify_persistence(command: list[str], environment: dict[str, str], sentinel: dict) -> None:
    port = published_port(command, environment, "api", 8000)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
        login = client.post("/auth/login", data={"username": sentinel["email"], "password": sentinel["password"]})
        if login.status_code != 200:
            raise AssertionError("User did not survive container recreation")
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        path = f"/projects/{sentinel['project']['id']}"
        project = client.get(path, headers=headers)
        task = client.get(f"{path}/tasks/{sentinel['task']['id']}", headers=headers)
        if project.status_code != 200 or project.json() != sentinel["project"]:
            raise AssertionError("Project changed or disappeared across recreation")
        if task.status_code != 200 or task.json() != sentinel["task"]:
            raise AssertionError("Task changed or disappeared across recreation")
    print("Persistence: user, project and task survived database/API recreation", flush=True)


def main() -> None:
    owner = uuid.uuid4().hex
    project = "taskflow-test-" + owner
    environment = {
        **os.environ, "POSTGRES_USER": "taskflow_test", "POSTGRES_DB": "taskflow_compose_test",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24) + ":@/#?%$",
        "JWT_SECRET_KEY": secrets.token_urlsafe(32), "ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "POSTGRES_PORT": "0", "API_PORT": "0", "COMPOSE_IGNORE_ORPHANS": "true",
    }
    directory = tempfile.TemporaryDirectory(prefix="taskflow-compose-", dir=os.environ.get("TMPDIR"), delete=False)
    workspace = Path(directory.name)
    override_path = workspace / "isolation.json"
    labels = {OWNER_LABEL: owner}
    override = {
        "services": {name: {"labels": labels} for name in ("api", "migrate", "db")},
        "volumes": {"postgres_data": {"name": project + "_postgres_data", "labels": labels}},
        "networks": {"default": {"labels": labels}},
    }
    override_path.write_text(json.dumps(override))
    command = ["docker", "compose", "--project-name", project, "--env-file", os.devnull,
               "-f", str(ROOT / "docker-compose.yml"), "-f", str(override_path)]
    try:
        run(["docker", "build", "--tag", "taskflow-api:local", "."], timeout=600)
        print("Container image: built successfully", flush=True)
        run(command + ["up", "--detach", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "60", "api"], environment)
        api = service_state(command, environment, "api")
        if api["Config"]["User"] != "10001:10001" or not api["HostConfig"]["ReadonlyRootfs"]:
            raise AssertionError("API must run non-root with read-only root filesystem")
        migration = service_state(command, environment, "migrate")
        if migration["State"]["ExitCode"] != 0 or migration["State"]["Status"] != "exited":
            raise AssertionError("Migration must exit successfully before API startup")
        if any(value.startswith("JWT_SECRET_KEY=") for value in migration["Config"]["Env"]):
            raise AssertionError("Migration container unexpectedly received JWT configuration")
        sentinel = verify_http(command, environment)
        old_db = service_state(command, environment, "db")["Id"]
        old_api = api["Id"]
        run(command + ["down", "--timeout", "5"], environment)
        run(command + ["up", "--detach", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "60", "api"], environment)
        if service_state(command, environment, "db")["Id"] == old_db or service_state(command, environment, "api")["Id"] == old_api:
            raise AssertionError("Persistence test did not recreate both containers")
        verify_persistence(command, environment, sentinel)
        check = run(command + ["run", "--rm", "--no-deps", "migrate", "alembic", "check"], environment)
        if "No new upgrade operations detected" not in check.stdout + check.stderr:
            raise AssertionError("Container migration drift check did not pass")
        print("Container Alembic check: no schema drift", flush=True)

        run(command + ["down", "--timeout", "5"], environment)
        override["services"]["migrate"]["command"] = ["python", "-c", "raise SystemExit(23)"]
        override_path.write_text(json.dumps(override))
        failed = run(command + ["up", "--detach", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "45", "api"], environment, check=False)
        migration = service_state(command, environment, "migrate")
        api = service_state(command, environment, "api")
        if failed.returncode == 0 or migration["State"]["ExitCode"] != 23 or api.get("State", {}).get("Running", False):
            raise AssertionError("Failed migrations did not block API startup")
        print("Startup gate: migration exit 23 prevented API startup", flush=True)

        run(command + ["down", "--timeout", "5"], environment)
        del override["services"]["migrate"]["command"]
        override_path.write_text(json.dumps(override))
        environment["JWT_SECRET_KEY"] = ""
        failed = run(command + ["up", "--detach", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "45", "api"], environment, check=False)
        api = service_state(command, environment, "api")
        if failed.returncode == 0 or api["State"]["Running"] or api["State"]["ExitCode"] == 0:
            raise AssertionError("API served without a valid JWT secret")
        print("Startup gate: missing JWT secret prevented API from serving", flush=True)
    finally:
        cleanup(command, environment, project, owner)
        directory.cleanup()


if __name__ == "__main__":
    main()
