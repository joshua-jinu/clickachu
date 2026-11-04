"""Project detection on fixture folders, profile CRUD, routes and window-title matching (§15)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clickachu.api.protocol import ProjectCommand, ProjectCreate, ProjectUpdate
from clickachu.context.selection import FocusedElement
from clickachu.db.conn import Database
from clickachu.projects.detect import compose_services, detect, guess_port
from clickachu.projects.profiles import ProjectNameTaken, ProjectNotFound, ProjectStore
from clickachu.projects.roots import RootExists, RootNotFound, RootStore
from tests.conftest import AUTH, BASE_URL, AppFactory
from tests.fake_win32 import FakeWin32


def by_name(folder: Path) -> dict[str, ProjectCommand]:
    return {c.name: c for c in detect(folder).commands}


def make_node_project(root: Path, lockfile: str | None) -> Path:
    folder = root / "TeamFuse"
    folder.mkdir()
    scripts = {
        "dev": "vite",
        "start": "node server.js --port 4000",
        "build": "tsc && vite build",
        "serve": "http-server -p 9090",
        "test": "vitest",
    }
    (folder / "package.json").write_text(json.dumps({"name": "teamfuse", "scripts": scripts}))
    if lockfile:
        (folder / lockfile).write_text("")
    return folder


@pytest.mark.parametrize(
    ("lockfile", "dev_cmd", "build_cmd"),
    [
        ("pnpm-lock.yaml", "pnpm run dev", "pnpm run build"),
        ("yarn.lock", "yarn dev", "yarn build"),
        ("package-lock.json", "npm run dev", "npm run build"),
        (None, "npm run dev", "npm run build"),
    ],
)
def test_detect_package_json(
    tmp_path: Path, lockfile: str | None, dev_cmd: str, build_cmd: str
) -> None:
    folder = make_node_project(tmp_path, lockfile)
    proposal = detect(folder)
    cmds = by_name(folder)
    assert proposal.name == "TeamFuse" and proposal.path == str(folder.resolve())
    assert cmds["dev"].cmd == dev_cmd and cmds["build"].cmd == build_cmd
    assert cmds["dev"].port == 5173  # vite default
    assert cmds["start"].port == 4000  # --port flag wins
    assert cmds["serve"].port == 9090  # -p flag
    assert cmds["build"].port is None and cmds["test"].port is None
    assert all(c.cwd == str(folder.resolve()) and c.trusted is False for c in proposal.commands)


def test_detect_docker_compose(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        """\
version: "3.9"
services:
  web:
    image: nginx # front
    ports:
      - "8080:80"
  db:
    image: postgres
    ports:
      - 127.0.0.1:5433:5432
  worker:
    build: .
volumes:
  data: {}
"""
    )
    cmds = by_name(tmp_path)
    assert cmds["docker compose up"].cmd == "docker compose up"
    assert cmds["docker compose up"].port == 8080
    assert (cmds["docker: web"].cmd, cmds["docker: web"].port) == ("docker compose up web", 8080)
    assert cmds["docker: db"].port == 5433
    assert cmds["docker: worker"].port is None
    assert "docker: data" not in cmds


def test_compose_services_ignores_other_top_level_keys() -> None:
    text = (
        "name: x\nnetworks:\n  front:\n    driver: bridge\n"
        "services:\n  api:\n    ports:\n      - '3000:3000'\n"
    )
    assert compose_services(text) == [("api", 3000)]


def test_detect_pyproject_scripts(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.scripts]\nserve = "x.main:run"\nmigrate = "x.db:migrate"\n'
    )
    assert by_name(tmp_path)["serve"].cmd == "serve"
    (tmp_path / "uv.lock").write_text("")
    assert by_name(tmp_path)["serve"].cmd == "uv run serve"
    assert by_name(tmp_path)["migrate"].cmd == "uv run migrate"


def test_detect_makefile_targets(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(
        ".PHONY: build test\n"
        "CC := gcc\n"
        "build: deps\n\t$(CC) main.c\n"
        "test:\n\t./run\n"
        "%.o: %.c\n\t$(CC) -c $<\n"
        "deps:\n"
        "build:\n"  # duplicate target
    )
    assert list(by_name(tmp_path)) == ["make build", "make test", "make deps"]
    assert by_name(tmp_path)["make build"].cmd == "make build"


def test_detect_empty_and_broken_inputs(tmp_path: Path) -> None:
    assert detect(tmp_path).commands == []
    (tmp_path / "package.json").write_text("{not json")
    (tmp_path / "pyproject.toml").write_text("= nope")
    assert detect(tmp_path).commands == []
    with pytest.raises(NotADirectoryError):
        detect(tmp_path / "missing")


def test_guess_port() -> None:
    assert guess_port("dev", "next dev") == 3000
    assert guess_port("dev", "vite --port 3001") == 3001
    assert guess_port("dev", "PORT=4200 node app.js") == 4200
    assert guess_port("start", "node app.js") is None
    assert guess_port("build", "vite build") is None
    assert guess_port("dev", "vite --port 99999") is None


# ---------------------------------------------------------------- profiles


@pytest.fixture
def projects(db: Database) -> ProjectStore:
    return ProjectStore(db)


def create(name: str, path: str = "C:\\dev\\x") -> ProjectCreate:
    cmd = ProjectCommand(name="dev", cmd="pnpm dev", cwd=path, port=5173)
    return ProjectCreate(name=name, path=path, commands=[cmd], env={"A": "1"})


def test_profile_crud(projects: ProjectStore) -> None:
    p = projects.create(create("TeamFuse", "C:\\dev\\TeamFuse"))
    assert p.approved_at and p.commands[0].port == 5173 and p.commands[0].trusted is False
    assert p.env == {"A": "1"}
    assert [x.id for x in projects.list()] == [p.id]

    updated = projects.update(p.id, ProjectUpdate(commands=[], name="TF"))
    assert updated.name == "TF" and updated.commands == [] and updated.path == p.path
    projects.delete(p.id)
    with pytest.raises(ProjectNotFound):
        projects.get(p.id)


def test_profile_names_are_unique(projects: ProjectStore) -> None:
    projects.create(create("A"))
    with pytest.raises(ProjectNameTaken):
        projects.create(create("A"))
    b = projects.create(create("B"))
    with pytest.raises(ProjectNameTaken):
        projects.update(b.id, ProjectUpdate(name="A"))


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("worker.ts - TeamFuse - Visual Studio Code", "TeamFuse"),
        ("worker.ts — TeamFuse — Visual Studio Code", "TeamFuse"),
        ("teamfuse", "TeamFuse"),
        ("C:\\dev\\TeamFuse: npm run dev", "TeamFuse"),
        ("TeamFuse: Dashboard - Google Chrome", "TeamFuse"),
        ("worker.ts - TeamFuse2 - Visual Studio Code", None),
        ("Untitled - Notepad", None),
        ("", None),
    ],
)
def test_match_titles(projects: ProjectStore, title: str, expected: str | None) -> None:
    projects.create(create("TeamFuse", "C:\\dev\\TeamFuse"))
    projects.create(create("Other", "C:\\dev\\other"))
    match = projects.match(title, "Code.exe")
    assert (match.name if match else None) == expected


def test_match_prefers_exact_segment_then_longest(projects: ProjectStore) -> None:
    projects.create(create("api", "C:\\dev\\api"))
    projects.create(create("TeamFuse", "C:\\dev\\TeamFuse"))
    # "api" appears as a word, but the whole-segment TeamFuse match is stronger.
    match = projects.match("api.ts - TeamFuse - Visual Studio Code", "Code.exe")
    assert match is not None and match.name == "TeamFuse"


def test_match_uses_folder_name_when_profile_renamed(projects: ProjectStore) -> None:
    projects.create(create("Fuse", "C:\\dev\\TeamFuse"))
    match = projects.match("worker.ts - TeamFuse - Visual Studio Code", "Code.exe")
    assert match is not None and match.name == "Fuse"


# ---------------------------------------------------------------- allowed roots


def test_roots_crud(db: Database, tmp_path: Path) -> None:
    roots = RootStore(db)
    r = roots.add(str(tmp_path / "a" / ".." / "work"), can_write=False)
    assert r.path == str(tmp_path / "work") and not r.can_write
    with pytest.raises(RootExists):
        roots.add(str(tmp_path / "WORK"), can_write=True)
    assert roots.set_write(r.id, True).can_write
    roots.remove(r.id)
    assert roots.list() == []
    with pytest.raises(RootNotFound):
        roots.remove(r.id)


# ---------------------------------------------------------------- HTTP + capture


def test_project_routes_and_path_memory(make_app: AppFactory, tmp_path: Path) -> None:
    folder = make_node_project(tmp_path, "pnpm-lock.yaml")
    with TestClient(make_app(), base_url=BASE_URL, headers=AUTH) as c:
        proposal = c.post("/projects/detect", json={"path": str(folder)}).json()
        assert proposal["name"] == "TeamFuse"
        assert {x["cmd"] for x in proposal["commands"]} >= {"pnpm run dev"}
        assert c.get("/projects").json() == []  # detect doesn't save
        assert c.post("/projects/detect", json={"path": str(tmp_path / "nope")}).status_code == 400

        created = c.post("/projects", json=proposal).json()
        assert created["approved_at"] and created["commands"][0]["trusted"] is False
        assert c.post("/projects", json=proposal).status_code == 409

        memories = c.get("/memories", params={"scope_type": "project"}).json()
        assert [(m["scope_key"], m["content"]) for m in memories] == [
            ("TeamFuse", f"Project TeamFuse lives at {proposal['path']}")
        ]
        assert len(c.get("/memories").json()) == 1

        new_path = str(tmp_path / "moved")
        patched = c.patch(
            f"/projects/{created['id']}", json={"path": new_path, "name": "TF"}
        ).json()
        assert (patched["name"], patched["path"]) == ("TF", new_path)
        memories = c.get("/memories").json()
        assert [(m["scope_key"], m["content"]) for m in memories] == [
            ("TF", f"Project TF lives at {new_path}")
        ]
        assert c.patch("/projects/999", json={"name": "x"}).status_code == 404

        assert c.delete(f"/projects/{created['id']}").status_code == 204
        assert c.get("/projects").json() == []
        assert c.delete(f"/projects/{created['id']}").status_code == 404


def test_project_memory_skipped_when_memory_off(make_app: AppFactory, tmp_path: Path) -> None:
    with TestClient(make_app(), base_url=BASE_URL, headers=AUTH) as c:
        c.put("/settings", json={"memory_enabled": False})
        body = {"name": "P", "path": str(tmp_path), "commands": []}
        assert c.post("/projects", json=body).status_code == 201
        assert c.get("/memories").json() == []


def test_allowed_root_routes(make_app: AppFactory, tmp_path: Path) -> None:
    with TestClient(make_app(), base_url=BASE_URL, headers=AUTH) as c:
        root = c.post("/allowed-roots", json={"path": str(tmp_path), "can_write": True}).json()
        assert root["can_write"] is True
        assert c.post("/allowed-roots", json={"path": str(tmp_path)}).status_code == 409
        assert (
            c.patch(f"/allowed-roots/{root['id']}", json={"can_write": False}).json()["can_write"]
            is False
        )
        assert [r["id"] for r in c.get("/allowed-roots").json()] == [root["id"]]
        assert c.delete(f"/allowed-roots/{root['id']}").status_code == 204
        assert c.delete(f"/allowed-roots/{root['id']}").status_code == 404


def test_log_route_empty_then_filtered(make_app: AppFactory, db: Database) -> None:
    with TestClient(make_app(), base_url=BASE_URL, headers=AUTH) as c:
        assert c.get("/log").json() == []
        conn = db.conn()
        conn.execute(
            "INSERT INTO action_log (run_id, step_id, tool, args_json, tier, decision, ok,"
            " started_at) VALUES ('r1', 's1', 'open_app', '{}', 'low', 'auto', 1, 't')"
        )
        conn.commit()
        rows = c.get("/log", params={"run_id": "r1"}).json()
        assert [(r["tool"], r["ok"], r["verified"]) for r in rows] == [("open_app", True, None)]
        assert c.get("/log", params={"run_id": "other"}).json() == []


def test_capture_shows_matching_project(
    make_app: AppFactory, win32: FakeWin32, tmp_path: Path
) -> None:
    win32.process = "Code.exe"
    win32.title = "worker.ts - TeamFuse - Visual Studio Code"
    win32.focus = FocusedElement("monaco-editor", "selected text")
    with TestClient(make_app(), base_url=BASE_URL, headers=AUTH) as c:
        assert c.post("/context/capture").json()["project"] is None
        created = c.post(
            "/projects", json={"name": "TeamFuse", "path": str(tmp_path / "TeamFuse")}
        ).json()
        snap = c.post("/context/capture").json()
        assert snap["app"] == "VS Code"
        assert snap["project"] == {"id": created["id"], "name": "TeamFuse"}
