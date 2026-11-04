"""Table-driven policy engine tests (PLAN §13.2, §18): ≥25 cases incl. path escapes."""

from collections.abc import Sequence
from typing import ClassVar

import pytest
from pydantic import BaseModel

from clickachu.api.protocol import DEFAULT_SENSITIVE_DOMAINS, AllowedRoot
from clickachu.db.conn import Database
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult
from clickachu.tools.policy import LogDecision, PolicyAction, TrustStore, decide


class PathArgs(BaseModel):
    path: str = ""
    src: str = ""
    dst: str = ""
    paths: list[str] = []
    url: str = ""
    key: str = "default:cmd"


def _make_tool(
    name: str,
    tier: Tier,
    *,
    writes: bool = False,
    with_trust_key: bool = True,
) -> Tool[PathArgs]:
    class _DummyTool(Tool[PathArgs]):
        Args: ClassVar[type[BaseModel]] = PathArgs

        def run(self, args: PathArgs, ctx: ToolContext) -> ToolResult:
            del args, ctx
            return ToolResult(ok=True)

        def trust_key(self, args: PathArgs) -> str | None:
            return args.key if with_trust_key else None

    _DummyTool.name = name
    _DummyTool.description = f"Dummy {name}"
    _DummyTool.tier = tier
    _DummyTool.writes_files = writes
    return _DummyTool()


RO_ROOT = AllowedRoot(id=1, path=r"C:\AllowedRead", can_write=False)
RW_ROOT = AllowedRoot(id=2, path=r"C:\AllowedWrite", can_write=True)
NESTED_RW_ROOT = AllowedRoot(id=3, path=r"C:\AllowedRead\WritableSub", can_write=True)
NESTED_RO_ROOT = AllowedRoot(id=4, path=r"C:\AllowedWrite\ReadOnlySub", can_write=False)

STD_ROOTS: tuple[AllowedRoot, ...] = (RO_ROOT, RW_ROOT, NESTED_RW_ROOT, NESTED_RO_ROOT)


POLICY_CASES: list[
    tuple[
        str,
        Tool[PathArgs],
        PathArgs,
        Sequence[AllowedRoot],
        set[tuple[str, str]],
        Sequence[str],
        PolicyAction,
        LogDecision,
    ]
] = [
    # ---- Tier basics (no paths, no URLs) ----
    (
        "read_tier_auto",
        _make_tool("read_basic", Tier.READ),
        PathArgs(),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
    (
        "low_tier_auto",
        _make_tool("low_basic", Tier.LOW),
        PathArgs(),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
    (
        "confirm_tier_untrusted_asks",
        _make_tool("confirm_basic", Tier.CONFIRM),
        PathArgs(key="proj:dev"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "confirm_tier_trusted_auto",
        _make_tool("confirm_basic", Tier.CONFIRM),
        PathArgs(key="proj:dev"),
        STD_ROOTS,
        {("confirm_basic", "proj:dev")},
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "always",
    ),
    (
        "confirm_tier_different_key_still_asks",
        _make_tool("confirm_basic", Tier.CONFIRM),
        PathArgs(key="proj:prod"),
        STD_ROOTS,
        {("confirm_basic", "proj:dev")},
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "confirm_tier_no_trust_key_asks",
        _make_tool("confirm_nokey", Tier.CONFIRM, with_trust_key=False),
        PathArgs(),
        STD_ROOTS,
        {("confirm_nokey", "default:cmd")},
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "blocked_tier_always_denied",
        _make_tool("shell_exec", Tier.BLOCKED),
        PathArgs(),
        STD_ROOTS,
        {("shell_exec", "default:cmd")},
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    # ---- Read paths & normalisation ----
    (
        "read_inside_ro_root",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\AllowedRead\docs\resume.pdf"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
    (
        "read_inside_rw_root",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\AllowedWrite\notes.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
    (
        "read_case_insensitive_and_forward_slashes",
        _make_tool("read_file", Tier.READ),
        PathArgs(path="c:/allowedread/Sub/Resume.PDF"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
    (
        "read_outside_roots_denied",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\Windows\System32\drivers\etc\hosts"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "read_empty_roots_denied",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\AllowedRead\file.txt"),
        (),
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    # ---- Path escapes, UNC, relative, prefix collision ----
    (
        "escape_dotdot_out_of_root",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\AllowedRead\..\Secret\passwords.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_dotdot_even_staying_inside_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\AllowedRead\sub\..\file.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_dotdot_forward_slash",
        _make_tool("read_file", Tier.READ),
        PathArgs(path="C:/AllowedRead/../../Windows/win.ini"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_prefix_collision_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"C:\AllowedReadEvil\secret.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_unc_backslash_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"\\fileserver\share\resume.pdf"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_unc_forwardslash_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path="//fileserver/share/resume.pdf"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_device_path_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"\\?\C:\AllowedRead\file.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_relative_path_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path=r"docs\resume.pdf"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_drive_relative_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path="C:resume.pdf"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "escape_null_byte_rejected",
        _make_tool("read_file", Tier.READ),
        PathArgs(path="C:\\AllowedRead\\ok.txt\x00.exe"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    # ---- Write paths & can_write checks ----
    (
        "write_in_ro_root_denied",
        _make_tool("write_file", Tier.CONFIRM, writes=True),
        PathArgs(path=r"C:\AllowedRead\out.txt"),
        STD_ROOTS,
        {("write_file", "default:cmd")},
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "write_in_rw_root_asks_confirm",
        _make_tool("write_file", Tier.CONFIRM, writes=True),
        PathArgs(path=r"C:\AllowedWrite\out.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "write_in_nested_rw_inside_ro_allowed",
        _make_tool("write_file", Tier.CONFIRM, writes=True),
        PathArgs(path=r"C:\AllowedRead\WritableSub\out.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "write_in_nested_ro_inside_rw_denied",
        _make_tool("write_file", Tier.CONFIRM, writes=True),
        PathArgs(path=r"C:\AllowedWrite\ReadOnlySub\out.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "move_file_dst_in_ro_denied",
        _make_tool("move_file", Tier.CONFIRM, writes=True),
        PathArgs(src=r"C:\AllowedWrite\a.txt", dst=r"C:\AllowedRead\a.txt"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    (
        "trash_file_one_outside_root_denied",
        _make_tool("trash_file", Tier.CONFIRM, writes=True),
        PathArgs(paths=[r"C:\AllowedWrite\a.txt", r"C:\Outside\b.txt"]),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.DENY,
        "blocked",
    ),
    # ---- Sensitive domains (§13.2) ----
    (
        "low_browser_non_sensitive_auto",
        _make_tool("browser_open", Tier.LOW),
        PathArgs(url="https://docs.python.org/3/library/pathlib.html"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
    (
        "low_browser_sensitive_exact_domain_asks",
        _make_tool("browser_open", Tier.LOW),
        PathArgs(url="https://paypal.com/myaccount"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "low_browser_sensitive_subdomain_asks",
        _make_tool("browser_open", Tier.LOW),
        PathArgs(url="https://www.checkout.stripe.com/pay"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.ASK,
        "approved",
    ),
    (
        "low_browser_custom_sensitive_domain_from_settings_asks",
        _make_tool("browser_open", Tier.LOW),
        PathArgs(url="https://internal.corp.example/admin"),
        STD_ROOTS,
        set(),
        ["corp.example"],
        PolicyAction.ASK,
        "approved",
    ),
    (
        "low_browser_lookalike_domain_not_falsely_flagged",
        _make_tool("browser_open", Tier.LOW),
        PathArgs(url="https://notpaypal.com/info"),
        STD_ROOTS,
        set(),
        DEFAULT_SENSITIVE_DOMAINS,
        PolicyAction.AUTO,
        "auto",
    ),
]


@pytest.mark.parametrize(
    ("case_id", "tool", "args", "roots", "trusted", "domains", "expected_action", "expected_log"),
    POLICY_CASES,
    ids=[c[0] for c in POLICY_CASES],
)
def test_policy_decide_table(
    case_id: str,
    tool: Tool[PathArgs],
    args: PathArgs,
    roots: Sequence[AllowedRoot],
    trusted: set[tuple[str, str]],
    domains: Sequence[str],
    expected_action: PolicyAction,
    expected_log: LogDecision,
) -> None:
    del case_id
    result = decide(tool, args, roots=roots, trusted=trusted, sensitive_domains=domains)
    assert result.action == expected_action
    assert result.log_decision == expected_log


def test_trust_store_roundtrip_with_policy(db: Database) -> None:
    store = TrustStore(db)
    tool = _make_tool("run_project_command", Tier.CONFIRM)
    args = PathArgs(key="TeamFuse:dev")

    assert decide(tool, args, trusted=store).action == PolicyAction.ASK
    store.add("run_project_command", "TeamFuse:dev")
    decision = decide(tool, args, trusted=store)
    assert decision.action == PolicyAction.AUTO
    assert decision.log_decision == "always"


def test_resolve_safe_path_and_tool_paths(tmp_path) -> None:
    from clickachu.tools.files import WriteFileArgs, WriteFileTool
    from clickachu.tools.policy import check_path_allowed, resolve_safe_path, resolve_tool_paths

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    roots = [AllowedRoot(id=1, path=str(desktop), can_write=True)]

    # Relative path matching folder name
    res1 = resolve_safe_path("Desktop/recipe.txt", roots)
    assert res1 == (desktop / "recipe.txt").resolve()

    # Nested root sub-path matching (e.g. OneDrive/Desktop) and common typo tolerance (OnDrive)
    onedrive_desktop = tmp_path / "OneDrive" / "Desktop"
    onedrive_desktop.mkdir(parents=True)
    od_roots = [AllowedRoot(id=2, path=str(onedrive_desktop), can_write=True)]
    res_od = resolve_safe_path(r"OneDrive\Desktop\recipe.txt", od_roots)
    assert res_od == (onedrive_desktop / "recipe.txt").resolve()
    res_od_typo = resolve_safe_path("OnDrive/Desktop/recipe.txt", od_roots)
    assert res_od_typo == (onedrive_desktop / "recipe.txt").resolve()

    # Bare filename
    res2 = resolve_safe_path("recipe.txt", roots)
    assert res2 == (desktop / "recipe.txt").resolve()

    # Traversal attempt rejected
    assert resolve_safe_path("../secret.txt", roots) is None

    # resolve_tool_paths updates args in-place
    tool = WriteFileTool()
    args = WriteFileArgs(path="Desktop/my-recipe.txt", content="Delicious cookies")
    resolve_tool_paths(tool, args, roots)
    assert args.path == str((desktop / "my-recipe.txt").resolve())

    # Denial reason includes allowed folder list
    err = check_path_allowed(r"C:\NonExistent\Other\file.txt", roots, require_write=True)
    assert err is not None
    assert str(desktop) in err

