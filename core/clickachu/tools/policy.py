"""Tool execution policy engine (PLAN §3 Hard Rule 2, §13.2)."""

import os
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from clickachu.api.protocol import DEFAULT_SENSITIVE_DOMAINS, AllowedRoot
from clickachu.db.conn import Database
from clickachu.tools.base import Tier, Tool

LogDecision = Literal["auto", "approved", "always", "denied", "blocked"]

_SEP_SPLIT = re.compile(r"[\\/]+")


class PolicyAction(StrEnum):
    AUTO = "auto"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reason: str = ""
    log_decision: LogDecision = "auto"


class TrustStore:
    """Persists 'Always' tool approvals in `trusted_actions` (PLAN §8, §13.2)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def is_trusted(self, tool: str, arg_key: str) -> bool:
        row = (
            self._db.conn()
            .execute(
                "SELECT 1 FROM trusted_actions WHERE tool = ? AND arg_key = ?",
                (tool, arg_key),
            )
            .fetchone()
        )
        return row is not None

    def add(self, tool: str, arg_key: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        conn = self._db.conn()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO trusted_actions (tool, arg_key, created_at)"
                " VALUES (?, ?, ?)",
                (tool, arg_key, now),
            )

    def pairs(self) -> set[tuple[str, str]]:
        rows = self._db.conn().execute("SELECT tool, arg_key FROM trusted_actions").fetchall()
        return {(r["tool"], r["arg_key"]) for r in rows}


def normalise_safe_path(raw: str) -> Path | None:
    """Resolve a local path safely; return None on UNC, device prefixes, relative paths or `..`."""
    if not isinstance(raw, str) or "\x00" in raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    # Reject UNC (\\server\share, //server/share) and Win32 device paths (\\?\..., \\.\...).
    if stripped.startswith(("\\\\", "//")):
        return None
    # Reject any raw `..` traversal segment before resolution.
    if ".." in _SEP_SPLIT.split(stripped):
        return None
    drive, tail = os.path.splitdrive(stripped)
    if not drive or drive.startswith(("\\\\", "//")) or not tail.startswith(("\\", "/")):
        return None
    try:
        resolved = Path(stripped).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if str(resolved).startswith(("\\\\", "//")):
        return None
    return resolved


def resolve_safe_path(raw: str, roots: Sequence[AllowedRoot]) -> Path | None:
    """Resolve a path safely, including relative paths matching an allowed root or single file."""
    if not isinstance(raw, str) or "\x00" in raw:
        return None
    stripped = raw.strip()
    if not stripped or stripped.startswith(("\\\\", "//")):
        return None
    segments = [s for s in _SEP_SPLIT.split(stripped) if s and s != "."]
    if ".." in segments:
        return None

    # If it has a drive or device prefix:
    drive, tail = os.path.splitdrive(stripped)
    if drive:
        if drive.startswith(("\\\\", "//")) or not tail.startswith(("\\", "/")):
            return None
        try:
            resolved = Path(stripped).resolve()
            if not str(resolved).startswith(("\\\\", "//")):
                return resolved
        except (OSError, RuntimeError, ValueError):
            return None
        return None

    # It is a relative path.
    norm_segments = [s.lower().replace("ondrive", "onedrive") for s in segments]

    # 1. Match prefixes of segments against suffixes of allowed roots (longest suffix first)
    # Sort roots so writable and more specific roots are prioritized
    sorted_roots = sorted(roots, key=lambda r: (not r.can_write, -len(r.path)))

    best_match: tuple[int, Path] | None = None
    for root in sorted_roots:
        root_path = normalise_safe_path(root.path)
        if root_path is None:
            continue
        try:
            root_res = root_path.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        root_parts = [p.lower() for p in root_res.parts if p and p not in ("\\", "/")]

        max_k = min(len(norm_segments) - 1 if len(norm_segments) > 1 else 1, len(root_parts))
        for k in range(max_k, 0, -1):
            prefix = norm_segments[:k]
            if root_parts[-k:] == prefix:
                remainder = segments[k:]
                candidate = root_res.joinpath(*remainder) if remainder else root_res
                try:
                    cand_res = candidate.resolve()
                    if Path(os.path.normcase(str(cand_res))).is_relative_to(
                        Path(os.path.normcase(str(root_res)))
                    ):
                        if best_match is None or k > best_match[0]:
                            best_match = (k, cand_res)
                            break
                except (OSError, RuntimeError, ValueError):
                    continue

    if best_match is not None:
        return best_match[1]

    # 2. Single filename (e.g. 'recipe.txt'): resolve inside first writable root (or first root)
    if len(segments) == 1 and roots:
        writable = [r for r in roots if r.can_write]
        preferred = writable[0] if writable else roots[0]
        root_path = normalise_safe_path(preferred.path)
        if root_path is not None:
            candidate = root_path / segments[0]
            try:
                cand_res = candidate.resolve()
                root_res = root_path.resolve()
                if Path(os.path.normcase(str(cand_res))).is_relative_to(
                    Path(os.path.normcase(str(root_res)))
                ):
                    return cand_res
            except (OSError, RuntimeError, ValueError):
                pass

    return None


def resolve_tool_paths(
    tool: Tool[Any], args: BaseModel, roots: Sequence[AllowedRoot]
) -> None:
    """Resolve any relative paths in tool arguments to canonical paths under allowed roots."""
    del tool
    for field_name in ("path", "src", "dst", "root"):
        if hasattr(args, field_name):
            val = getattr(args, field_name)
            if isinstance(val, str) and val.strip():
                resolved = resolve_safe_path(val, roots)
                if resolved is not None:
                    setattr(args, field_name, str(resolved))
    if hasattr(args, "paths"):
        val = args.paths
        if isinstance(val, list):
            new_paths: list[str] = []
            for item in val:
                if isinstance(item, str) and item.strip():
                    resolved = resolve_safe_path(item, roots)
                    new_paths.append(str(resolved) if resolved is not None else item)
                else:
                    new_paths.append(item)
            args.paths = new_paths


def _allowed_roots_hint(roots: Sequence[AllowedRoot]) -> str:
    if not roots:
        return " No allowed folders are configured in Settings. Please add one in Settings."
    entries = [f"{r.path} ({'read/write' if r.can_write else 'read-only'})" for r in roots]
    return f" Allowed folders: {', '.join(entries)}"


def check_path_allowed(
    raw_path: str, roots: Sequence[AllowedRoot], *, require_write: bool
) -> str | None:
    """Return None when `raw_path` is permitted under `roots`, or a denial reason string."""
    resolved = resolve_safe_path(raw_path, roots)
    if resolved is None:
        hint = _allowed_roots_hint(roots)
        return f"invalid or unsafe path {raw_path!r} (outside allowed folders).{hint}"
    target_norm = Path(os.path.normcase(str(resolved)))

    matching: list[tuple[int, AllowedRoot]] = []
    for root in roots:
        root_resolved = normalise_safe_path(root.path)
        if root_resolved is None:
            continue
        root_norm = Path(os.path.normcase(str(root_resolved)))
        if target_norm.is_relative_to(root_norm):
            matching.append((len(str(root_norm)), root))

    if not matching:
        return f"path {raw_path!r} is outside allowed folders.{_allowed_roots_hint(roots)}"

    if require_write:
        # Use the most specific matching root to decide write permission.
        matching.sort(key=lambda item: item[0], reverse=True)
        best_root = matching[0][1]
        if not best_root.can_write:
            return (
                f"path {raw_path!r} is in a read-only folder ({best_root.path}, can_write=0). "
                "Enable 'can write' for this folder in Settings to allow saving or modifying files."
            )

    return None


def extract_hostname(url_or_domain: str) -> str:
    text = url_or_domain.strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"https://{text}")
    return (parsed.hostname or "").lower().rstrip(".")


def is_sensitive_domain(url_or_domain: str, patterns: Sequence[str]) -> bool:
    host = extract_hostname(url_or_domain)
    if not host:
        return False
    for raw_pat in patterns:
        pat = raw_pat.strip().lower().lstrip("*.").rstrip(".")
        if not pat:
            continue
        if host == pat or host.endswith(f".{pat}"):
            return True
    return False


def _is_trusted(
    tool_name: str,
    trust_key: str | None,
    trusted: TrustStore | Collection[tuple[str, str]] | Collection[str] | None,
) -> bool:
    if trusted is None or trust_key is None:
        return False
    if isinstance(trusted, TrustStore):
        return trusted.is_trusted(tool_name, trust_key) or trusted.is_trusted(tool_name, "*")
    return (
        (tool_name, trust_key) in trusted
        or (tool_name, "*") in trusted
        or trust_key in trusted
    )


def decide(
    tool: Tool[Any],
    args: BaseModel,
    *,
    roots: Sequence[AllowedRoot] = (),
    trusted: TrustStore | Collection[tuple[str, str]] | Collection[str] | None = None,
    sensitive_domains: Sequence[str] = DEFAULT_SENSITIVE_DOMAINS,
) -> PolicyDecision:
    """Decide AUTO / ASK / DENY from the tool's declared tier and arguments (PLAN §13.2)."""
    if tool.tier == Tier.BLOCKED:
        return PolicyDecision(
            action=PolicyAction.DENY,
            reason=f"tool {tool.name!r} is blocked",
            log_decision="blocked",
        )

    for read_path in tool.read_paths(args):
        if err := check_path_allowed(read_path, roots, require_write=False):
            return PolicyDecision(action=PolicyAction.DENY, reason=err, log_decision="blocked")

    for write_path in tool.write_paths(args):
        if err := check_path_allowed(write_path, roots, require_write=True):
            return PolicyDecision(action=PolicyAction.DENY, reason=err, log_decision="blocked")

    for url in tool.target_urls(args):
        if is_sensitive_domain(url, sensitive_domains):
            host = extract_hostname(url)
            return PolicyDecision(
                action=PolicyAction.ASK,
                reason=f"sensitive domain ({host})",
                log_decision="approved",
            )

    if tool.tier in (Tier.READ, Tier.LOW):
        return PolicyDecision(action=PolicyAction.AUTO, log_decision="auto")

    if tool.tier == Tier.CONFIRM:
        key = tool.trust_key(args)
        if _is_trusted(tool.name, key, trusted):
            return PolicyDecision(
                action=PolicyAction.AUTO,
                reason="trusted action",
                log_decision="always",
            )
        return PolicyDecision(
            action=PolicyAction.ASK,
            reason="confirmation required",
            log_decision="approved",
        )

    return PolicyDecision(
        action=PolicyAction.DENY,
        reason=f"unknown tier {tool.tier!r}",
        log_decision="blocked",
    )
