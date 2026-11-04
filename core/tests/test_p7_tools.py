"""Tests for Phase 7 tools: Desktop, Files, Processes, and Browser (PLAN §13.2, §13.3, §15)."""

import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from clickachu.api.protocol import AllowedRoot, ProjectCommand, ProjectCreate
from clickachu.db.conn import Database
from clickachu.projects.profiles import ProjectStore
from clickachu.tools.base import Tier, ToolContext
from clickachu.tools.browser_tools import (
    BrowserClickArgs,
    BrowserClickTool,
    BrowserFillArgs,
    BrowserFillTool,
    BrowserOpenArgs,
    BrowserOpenTool,
    BrowserReadArgs,
    BrowserReadTool,
    BrowserScreenshotArgs,
    BrowserScreenshotTool,
    BrowserSearchArgs,
    BrowserSearchTool,
    BrowserSubmitArgs,
    BrowserSubmitTool,
)
from clickachu.tools.desktop import (
    ADMIN_ERROR_MESSAGE,
    FocusWindowArgs,
    FocusWindowTool,
    GetActiveContextArgs,
    GetActiveContextTool,
    InspectWindowArgs,
    InspectWindowTool,
    OpenAppArgs,
    OpenAppTool,
    TypeTextArgs,
    TypeTextTool,
    _resolve_app_path,
)
from clickachu.tools.files import (
    CreateFolderArgs,
    CreateFolderTool,
    MoveFileArgs,
    MoveFileTool,
    OpenFileArgs,
    OpenFileTool,
    OpenUrlArgs,
    OpenUrlTool,
    ReadFileArgs,
    ReadFileTool,
    TrashFileArgs,
    TrashFileTool,
    WriteFileArgs,
    WriteFileTool,
)
from clickachu.tools.policy import PolicyAction, TrustStore, decide
from clickachu.tools.process_tools import (
    GetProcessLogsArgs,
    GetProcessLogsTool,
    RunProjectCommandArgs,
    RunProjectCommandTool,
    StopProcessArgs,
    StopProcessTool,
)
from clickachu.watchers.process_manager import ProcessManager

# ================================================================
# Part 1: Tool definitions, Args, Tier, Worker, Preview, trust_key
# ================================================================


def test_tool_metadata_and_previews() -> None:
    # 1. get_active_context
    t_context = GetActiveContextTool()
    assert t_context.tier == Tier.READ
    assert t_context.worker == "com"
    assert t_context.preview(GetActiveContextArgs()) == "Get active window context"

    # 2. inspect_window
    t_inspect = InspectWindowTool()
    assert t_inspect.tier == Tier.READ
    assert t_inspect.worker == "com"
    args_inspect = InspectWindowArgs(title="Notepad", depth=2, max_nodes=50)
    assert t_inspect.preview(args_inspect) == "Inspect window 'Notepad' (depth=2, max_nodes=50)"
    with pytest.raises(ValidationError):
        InspectWindowArgs(title="Notepad", depth=4)  # max depth 3
    with pytest.raises(ValidationError):
        InspectWindowArgs(title="Notepad", max_nodes=200)  # max nodes 150

    # 3. open_app
    t_open_app = OpenAppTool()
    assert t_open_app.tier == Tier.LOW
    assert t_open_app.worker == "com"
    assert t_open_app.preview(OpenAppArgs(name="notepad")) == "Open application 'notepad'"

    # 4. focus_window
    t_focus = FocusWindowTool()
    assert t_focus.tier == Tier.LOW
    assert t_focus.worker == "com"
    assert t_focus.preview(FocusWindowArgs(title="Code")) == "Focus window 'Code'"

    # 5. type_text
    t_type = TypeTextTool()
    assert t_type.tier == Tier.CONFIRM
    assert t_type.worker == "com"
    assert t_type.preview(TypeTextArgs(window="Notepad", text="Hello")) == (
        "Type 'Hello' into window 'Notepad'"
    )
    assert t_type.preview(TypeTextArgs(window="Notepad", text="Hello", clear=True)) == (
        "Type 'Hello' into window 'Notepad' (clearing first)"
    )

    # 6. read_file
    t_read = ReadFileTool()
    assert t_read.tier == Tier.READ
    assert t_read.worker == "io"
    assert t_read.preview(ReadFileArgs(path=r"C:\test.txt")) == r"Read file C:\test.txt"
    with pytest.raises(ValidationError):
        ReadFileArgs(path=r"C:\test.txt", max_bytes=70000)  # max 64k (65536)

    # 7. open_file
    t_open_file = OpenFileTool()
    assert t_open_file.tier == Tier.LOW
    assert t_open_file.worker == "io"
    assert t_open_file.preview(OpenFileArgs(path=r"C:\test.pdf")) == r"Open file C:\test.pdf"

    # 8. open_url
    t_open_url = OpenUrlTool()
    assert t_open_url.tier == Tier.LOW
    assert t_open_url.worker == "io"
    assert t_open_url.preview(OpenUrlArgs(url="https://example.com")) == "Open URL https://example.com"

    # 9. create_folder
    t_create_folder = CreateFolderTool()
    assert t_create_folder.tier == Tier.CONFIRM
    assert t_create_folder.worker == "io"
    assert t_create_folder.writes_files is True
    assert t_create_folder.preview(CreateFolderArgs(path=r"C:\foo\bar")) == (
        r"Create folder C:\foo\bar"
    )

    # 10. move_file
    t_move = MoveFileTool()
    assert t_move.tier == Tier.CONFIRM
    assert t_move.worker == "io"
    assert t_move.writes_files is True
    assert t_move.preview(MoveFileArgs(src=r"C:\a.txt", dst=r"C:\b.txt")) == (
        r"Move C:\a.txt -> C:\b.txt"
    )

    # 11. write_file (unified diff preview)
    t_write = WriteFileTool()
    assert t_write.tier == Tier.CONFIRM
    assert t_write.worker == "io"
    assert t_write.writes_files is True
    diff_prev = t_write.preview(WriteFileArgs(path=r"C:\a.txt", content="hello\nworld\n"))
    assert (
        "--- a/C:\\a.txt" in diff_prev
        or "+++ b/C:\\a.txt" in diff_prev
        or "write_file" in diff_prev
    )

    # 12. trash_file (preview lists every path)
    t_trash = TrashFileTool()
    assert t_trash.tier == Tier.CONFIRM
    assert t_trash.worker == "io"
    assert t_trash.writes_files is True
    trash_prev = t_trash.preview(TrashFileArgs(paths=[r"C:\a.txt", r"C:\b.txt"]))
    assert r"- C:\a.txt" in trash_prev
    assert r"- C:\b.txt" in trash_prev

    # 13. get_process_logs
    t_logs = GetProcessLogsTool()
    assert t_logs.tier == Tier.READ
    assert t_logs.worker == "io"
    assert t_logs.preview(GetProcessLogsArgs(name="srv", lines=50)) == (
        "Get logs for process 'srv' (max 50 lines)"
    )

    # 14. stop_process
    t_stop = StopProcessTool()
    assert t_stop.tier == Tier.CONFIRM
    assert t_stop.worker == "io"
    assert t_stop.preview(StopProcessArgs(name="srv")) == "Stop process 'srv'"

    # 15. run_project_command (trust_key: <project>:<command>)
    t_proj = RunProjectCommandTool()
    assert t_proj.tier == Tier.CONFIRM
    assert t_proj.worker == "io"
    proj_args = RunProjectCommandArgs(project="TeamFuse", command="dev")
    assert t_proj.preview(proj_args) == "Run 'dev' in project 'TeamFuse'"
    assert t_proj.trust_key(proj_args) == "TeamFuse:dev"

    # 16. browser tools
    t_b_open = BrowserOpenTool()
    assert t_b_open.tier == Tier.LOW
    assert t_b_open.worker == "browser"
    assert t_b_open.preview(BrowserOpenArgs(url="https://example.com")) == (
        "Browse to https://example.com"
    )

    t_b_search = BrowserSearchTool()
    assert t_b_search.tier == Tier.LOW
    assert t_b_search.worker == "browser"
    assert t_b_search.preview(BrowserSearchArgs(query="ioredis retry")) == (
        "Search web for 'ioredis retry'"
    )

    t_b_read = BrowserReadTool()
    assert t_b_read.tier == Tier.READ
    assert t_b_read.worker == "browser"
    assert t_b_read.preview(BrowserReadArgs()) == "Read current web page"

    t_b_shot = BrowserScreenshotTool()
    assert t_b_shot.tier == Tier.READ
    assert t_b_shot.worker == "browser"
    assert t_b_shot.preview(BrowserScreenshotArgs()) == "Take browser screenshot"

    t_b_click = BrowserClickTool()
    assert t_b_click.tier == Tier.LOW
    assert t_b_click.worker == "browser"
    assert t_b_click.preview(BrowserClickArgs(ref="e12")) == "Click element e12"

    t_b_fill = BrowserFillTool()
    assert t_b_fill.tier == Tier.LOW
    assert t_b_fill.worker == "browser"
    assert t_b_fill.preview(BrowserFillArgs(ref="e3", text="abc")) == "Fill element e3 with 'abc'"

    t_b_submit = BrowserSubmitTool()
    assert t_b_submit.tier == Tier.CONFIRM
    assert t_b_submit.worker == "browser"
    assert "Submit" in t_b_submit.preview(BrowserSubmitArgs(ref="e4"))


# ================================================================
# Part 2: ProcessManager with script that prints and exits non-zero
# ================================================================


def test_process_manager_runs_captures_logs_and_handles_nonzero_exit() -> None:
    pm = ProcessManager()
    captured_logs: list[tuple[str, str]] = []
    captured_exits: list[tuple[str, int]] = []

    def log_cb(name: str, line: str) -> None:
        captured_logs.append((name, line))

    def exit_cb(name: str, code: int) -> None:
        captured_exits.append((name, code))

    pm.add_log_listener(log_cb)
    pm.add_exit_listener(exit_cb)

    # Run a python command that prints messages and exits with code 42
    script = (
        "import sys; print('Starting process test', flush=True); "
        "print('Error encountered in test', flush=True); sys.exit(42)"
    )
    cmd = f'"{sys.executable}" -u -c "{script}"'

    proc = pm.start(name="test-fail-proc", cmd=cmd, cwd=str(Path.cwd()))
    assert proc.pid > 0

    # Wait for process to terminate
    deadline = time.monotonic() + 5.0
    while pm.is_running("test-fail-proc") and time.monotonic() < deadline:
        time.sleep(0.1)

    assert not pm.is_running("test-fail-proc")
    logs = pm.get_logs("test-fail-proc")
    assert any("Starting process test" in line for line in logs)
    assert any("Error encountered in test" in line for line in logs)

    # Verify exit callback recorded non-zero exit code
    assert any(name == "test-fail-proc" and code == 42 for name, code in captured_exits)

    pm.remove_log_listener(log_cb)
    pm.remove_exit_listener(exit_cb)


# ================================================================
# Part 3: Policy denies paths outside roots & read-only write attempts
# ================================================================


def test_policy_denies_paths_outside_roots_and_readonly(tmp_path: Path) -> None:
    ro_folder = tmp_path / "readable_only"
    rw_folder = tmp_path / "writable_folder"
    ro_folder.mkdir()
    rw_folder.mkdir()

    roots = [
        AllowedRoot(id=1, path=str(ro_folder), can_write=False),
        AllowedRoot(id=2, path=str(rw_folder), can_write=True),
    ]

    # 1. read_file inside allowed root -> AUTO
    read_tool = ReadFileTool()
    inside_ro_file = str(ro_folder / "file.txt")
    outside_file = str(tmp_path / "outside.txt")

    dec_read_ok = decide(read_tool, ReadFileArgs(path=inside_ro_file), roots=roots)
    assert dec_read_ok.action == PolicyAction.AUTO

    # read_file outside allowed root -> DENY
    dec_read_deny = decide(read_tool, ReadFileArgs(path=outside_file), roots=roots)
    assert dec_read_deny.action == PolicyAction.DENY
    assert "outside allowed folders" in dec_read_deny.reason

    # 2. open_file outside allowed root -> DENY
    open_tool = OpenFileTool()
    dec_open_deny = decide(open_tool, OpenFileArgs(path=outside_file), roots=roots)
    assert dec_open_deny.action == PolicyAction.DENY

    # 3. write_file in read-only folder -> DENY
    write_tool = WriteFileTool()
    dec_write_ro = decide(
        write_tool,
        WriteFileArgs(path=inside_ro_file, content="new content"),
        roots=roots,
    )
    assert dec_write_ro.action == PolicyAction.DENY
    assert "can_write=0" in dec_write_ro.reason

    # write_file in writable folder -> ASK (CONFIRM tier)
    inside_rw_file = str(rw_folder / "file.txt")
    dec_write_rw = decide(
        write_tool,
        WriteFileArgs(path=inside_rw_file, content="new content"),
        roots=roots,
    )
    assert dec_write_rw.action == PolicyAction.ASK

    # 4. create_folder in read-only root -> DENY
    folder_tool = CreateFolderTool()
    dec_mkdir_ro = decide(
        folder_tool, CreateFolderArgs(path=str(ro_folder / "sub")), roots=roots
    )
    assert dec_mkdir_ro.action == PolicyAction.DENY

    # create_folder in writable root -> ASK
    dec_mkdir_rw = decide(
        folder_tool, CreateFolderArgs(path=str(rw_folder / "sub")), roots=roots
    )
    assert dec_mkdir_rw.action == PolicyAction.ASK

    # 5. trash_file with an outside path -> DENY
    trash_tool = TrashFileTool()
    dec_trash_outside = decide(
        trash_tool,
        TrashFileArgs(paths=[inside_rw_file, outside_file]),
        roots=roots,
    )
    assert dec_trash_outside.action == PolicyAction.DENY

    # 6. move_file from writable to read-only root -> DENY
    move_tool = MoveFileTool()
    dec_move_ro = decide(
        move_tool,
        MoveFileArgs(src=inside_rw_file, dst=str(ro_folder / "dest.txt")),
        roots=roots,
    )
    assert dec_move_ro.action == PolicyAction.DENY


def test_policy_sensitive_domain_and_trust_store(db: Database) -> None:
    # Sensitive domain forces ASK even for LOW tier tool
    b_open = BrowserOpenTool()
    dec_sens = decide(
        b_open,
        BrowserOpenArgs(url="https://paypal.com/signin"),
        sensitive_domains=["paypal.com", "stripe.com"],
    )
    assert dec_sens.action == PolicyAction.ASK
    assert "sensitive domain" in dec_sens.reason

    # Normal domain for LOW tier tool -> AUTO
    dec_norm = decide(
        b_open,
        BrowserOpenArgs(url="https://wikipedia.org"),
        sensitive_domains=["paypal.com"],
    )
    assert dec_norm.action == PolicyAction.AUTO

    # run_project_command: CONFIRM tier -> ASK unless trusted
    cmd_tool = RunProjectCommandTool()
    cmd_args = RunProjectCommandArgs(project="TeamFuse", command="dev")
    trust_store = TrustStore(db)

    dec_untrusted = decide(cmd_tool, cmd_args, trusted=trust_store)
    assert dec_untrusted.action == PolicyAction.ASK

    # After "Always" approval, trust_key "TeamFuse:dev" becomes AUTO
    trust_store.add("run_project_command", "TeamFuse:dev")
    dec_trusted = decide(cmd_tool, cmd_args, trusted=trust_store)
    assert dec_trusted.action == PolicyAction.AUTO
    assert dec_trusted.log_decision == "always"


# ================================================================
# Part 4: File operations end-to-end (read, write diff, folder, move, verify)
# ================================================================


def test_file_tools_execution_and_verification(db: Database, tmp_path: Path) -> None:
    ctx = ToolContext(run_id="r1", step_id="s1", snapshot=None, db=db)

    # 1. create_folder
    sub_dir = tmp_path / "test_dir"
    folder_tool = CreateFolderTool()
    folder_res = folder_tool.run(CreateFolderArgs(path=str(sub_dir)), ctx)
    assert folder_res.ok
    assert folder_tool.verify(CreateFolderArgs(path=str(sub_dir)), folder_res) is True
    assert sub_dir.is_dir()

    # 2. write_file
    target_file = sub_dir / "sample.txt"
    write_tool = WriteFileTool()
    content_v1 = "line 1\nline 2\n"
    write_res = write_tool.run(
        WriteFileArgs(path=str(target_file), content=content_v1), ctx
    )
    assert write_res.ok
    assert write_tool.verify(
        WriteFileArgs(path=str(target_file), content=content_v1), write_res
    ) is True
    assert target_file.read_text(encoding="utf-8") == content_v1

    # Unified diff preview when updating
    content_v2 = "line 1\nline 2 updated\nline 3\n"
    diff_preview = write_tool.preview(
        WriteFileArgs(path=str(target_file), content=content_v2)
    )
    assert "-line 2" in diff_preview
    assert "+line 2 updated" in diff_preview

    # 3. read_file
    read_tool = ReadFileTool()
    read_res = read_tool.run(ReadFileArgs(path=str(target_file)), ctx)
    assert read_res.ok
    assert read_res.untrusted_text == content_v1

    # 4. move_file
    moved_file = sub_dir / "renamed.txt"
    move_tool = MoveFileTool()
    move_res = move_tool.run(
        MoveFileArgs(src=str(target_file), dst=str(moved_file)), ctx
    )
    assert move_res.ok
    assert move_tool.verify(
        MoveFileArgs(src=str(target_file), dst=str(moved_file)), move_res
    ) is True
    assert not target_file.exists()
    assert moved_file.exists()


# ================================================================
# Part 5: run_project_command with ProjectStore integration
# ================================================================


def test_run_project_command_lifecycle(db: Database, tmp_path: Path) -> None:
    project_store = ProjectStore(db)
    script_file = tmp_path / "hello.py"
    script_file.write_text('print("Running project command")', encoding="utf-8")

    cmd_str = f'"{sys.executable}" "{script_file}"'
    project_store.create(
        ProjectCreate(
            name="TestProject",
            path=str(tmp_path),
            commands=[
                ProjectCommand(name="start", cmd=cmd_str, cwd=str(tmp_path))
            ],
        )
    )

    ctx = ToolContext(run_id="r1", step_id="s1", snapshot=None, db=db)
    cmd_tool = RunProjectCommandTool()
    args = RunProjectCommandArgs(project="TestProject", command="start")

    res = cmd_tool.run(args, ctx)
    assert res.ok
    assert "Started TestProject:start" in res.summary
    assert len(ctx.unverified_cleanup) == 1

    # Wait for execution and verify
    time.sleep(0.5)
    # verify checks whether it ran or port ready
    logs_res = GetProcessLogsTool().run(
        GetProcessLogsArgs(name="TestProject:start", lines=10), ctx
    )
    assert logs_res.ok
    assert any("Running project command" in line for line in logs_res.data)


# ================================================================
# Part 6: Desktop app resolution order & admin error catch
# ================================================================


def test_open_app_resolution_order() -> None:
    # Known apps resolve to .exe / .cmd
    notepad_res = _resolve_app_path("notepad")
    assert notepad_res is not None and "notepad" in notepad_res.lower()

    code_res = _resolve_app_path("code")
    assert code_res is not None


def test_desktop_tools_handle_admin_access_denied(
    monkeypatch: pytest.MonkeyPatch, db: Database
) -> None:
    from clickachu.automation import winapi

    class FakeAdminWin32(winapi.RealWin32):
        def foreground_window(self) -> int:
            return 9999

        def is_window(self, hwnd: int) -> bool:
            return True

        def window_pid(self, hwnd: int) -> int:
            return 8888

        def process_name(self, pid: int) -> str:
            import psutil
            raise psutil.AccessDenied(pid=pid)

    monkeypatch.setattr("clickachu.tools.desktop.RealWin32", FakeAdminWin32)

    tool = GetActiveContextTool()
    ctx = ToolContext(run_id="r1", step_id="s1", snapshot=None, db=db)
    res = tool.run(GetActiveContextArgs(), ctx)
    assert not res.ok
    assert res.summary == ADMIN_ERROR_MESSAGE
    assert res.untrusted_text == ADMIN_ERROR_MESSAGE
