"""Propose project commands from a folder (PLAN §15). Reads files only; never runs anything."""

import json
import logging
import re
import tomllib
from pathlib import Path

from clickachu.api.protocol import ProjectCommand, ProjectProposal

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 512 * 1024
MAX_MAKE_TARGETS = 25

# Scripts that start something long-running and so get a guessed port.
_SERVER_SCRIPTS = re.compile(r"^(dev|start|serve|server|preview|watch|storybook)([:\-_].*)?$")
_PORT_FLAG = re.compile(r"(?:--port[=\s]+|\s-p[=\s]+|\bPORT=)(\d{2,5})\b")
_PORT_URL = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})\b")
# Dev-server defaults by tool, checked against the script body.
_TOOL_PORTS = (
    ("vite", 5173),
    ("next", 3000),
    ("nuxt", 3000),
    ("react-scripts", 3000),
    ("astro", 4321),
    ("storybook", 6006),
    ("webpack serve", 8080),
    ("webpack-dev-server", 8080),
    ("remix", 3000),
)


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            log.info("skipping oversized %s", path)
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _valid_port(value: str) -> int | None:
    port = int(value)
    return port if 1 <= port <= 65535 else None


def guess_port(script_name: str, body: str) -> int | None:
    if not _SERVER_SCRIPTS.match(script_name):
        return None
    for pattern in (_PORT_FLAG, _PORT_URL):
        if m := pattern.search(body):
            return _valid_port(m[1])
    lowered = body.lower()
    for tool, port in _TOOL_PORTS:
        if re.search(rf"(?<![\w-]){re.escape(tool)}(?![\w-])", lowered):
            return port
    return None


def js_package_manager(folder: Path) -> str:
    if (folder / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (folder / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _from_package_json(folder: Path) -> list[ProjectCommand]:
    text = _read(folder / "package.json")
    if text is None:
        return []
    try:
        scripts = json.loads(text).get("scripts")
    except (json.JSONDecodeError, AttributeError):
        log.info("unparseable package.json in %s", folder)
        return []
    if not isinstance(scripts, dict):
        return []
    pm = js_package_manager(folder)
    prefix = {"npm": "npm run", "pnpm": "pnpm run", "yarn": "yarn"}[pm]
    out = []
    for name, body in scripts.items():
        if not isinstance(name, str) or not isinstance(body, str) or not name.strip():
            continue
        out.append(
            ProjectCommand(
                name=name,
                cmd=f"{prefix} {name}",
                cwd=str(folder),
                port=guess_port(name, body),
            )
        )
    return out


def _from_pyproject(folder: Path) -> list[ProjectCommand]:
    text = _read(folder / "pyproject.toml")
    if text is None:
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        log.info("unparseable pyproject.toml in %s", folder)
        return []
    if (folder / "poetry.lock").exists() or "poetry" in data.get("tool", {}):
        runner = "poetry run"
    elif (folder / "uv.lock").exists() or "uv" in data.get("tool", {}):
        runner = "uv run"
    else:
        runner = ""
    names: list[str] = []
    names += list(data.get("project", {}).get("scripts", {}))
    names += list(data.get("tool", {}).get("poetry", {}).get("scripts", {}))
    seen: set[str] = set()
    out = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        out.append(ProjectCommand(name=name, cmd=f"{runner} {name}".strip(), cwd=str(folder)))
    return out


_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_PORT_MAPPING = re.compile(r"""^\s*-\s*["']?(?:[\d.]+:)?(\d{2,5}):\d{2,5}(?:/\w+)?["']?\s*$""")


def compose_services(text: str) -> list[tuple[str, int | None]]:
    """Service names (and first published host port) via indentation; no YAML dependency."""
    services: list[tuple[str, int | None]] = []
    in_services = False
    service_indent: int | None = None
    in_ports = False
    ports_indent = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            in_services = stripped == "services:"
            service_indent = None
            in_ports = False
            continue
        if not in_services:
            continue
        if service_indent is None:
            service_indent = indent
        if indent == service_indent and stripped.endswith(":"):
            services.append((stripped[:-1].strip("\"'"), None))
            in_ports = False
        elif indent > (service_indent or 0) and stripped == "ports:":
            in_ports, ports_indent = True, indent
        elif in_ports and indent > ports_indent:
            m = _PORT_MAPPING.match(line)
            if m and services and services[-1][1] is None:
                services[-1] = (services[-1][0], _valid_port(m[1]))
        elif indent <= ports_indent:
            in_ports = False
    return services


def _from_compose(folder: Path) -> list[ProjectCommand]:
    for filename in _COMPOSE_NAMES:
        text = _read(folder / filename)
        if text is None:
            continue
        services = compose_services(text)
        if not services:
            return []
        first_port = next((p for _, p in services if p), None)
        out = [
            ProjectCommand(
                name="docker compose up", cmd="docker compose up", cwd=str(folder), port=first_port
            )
        ]
        out += [
            ProjectCommand(
                name=f"docker: {name}", cmd=f"docker compose up {name}", cwd=str(folder), port=port
            )
            for name, port in services
        ]
        return out
    return []


_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?![=:])")


def _from_makefile(folder: Path) -> list[ProjectCommand]:
    for filename in ("Makefile", "makefile", "GNUmakefile"):
        text = _read(folder / filename)
        if text is None:
            continue
        targets: dict[str, None] = {}
        for line in text.splitlines():
            if m := _MAKE_TARGET.match(line):
                targets.setdefault(m[1])
        return [
            ProjectCommand(name=f"make {t}", cmd=f"make {t}", cwd=str(folder))
            for t in list(targets)[:MAX_MAKE_TARGETS]
        ]
    return []


def detect(path: Path) -> ProjectProposal:
    """Raises NotADirectoryError if `path` isn't a folder."""
    folder = path.expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(str(path))
    commands: list[ProjectCommand] = []
    for source in (_from_package_json, _from_pyproject, _from_compose, _from_makefile):
        commands.extend(source(folder))
    return ProjectProposal(name=folder.name, path=str(folder), commands=commands)
