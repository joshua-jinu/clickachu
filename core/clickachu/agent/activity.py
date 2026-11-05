"""Short progress descriptions derived from the tool that is actually about to run."""


def activity_for(tool_name: str, args) -> str:
    def short(value):
        return " ".join(str(value).split())[:100]

    if tool_name == "open_app":
        return f"Opening {short(args.name)}…"
    if tool_name == "observe_desktop":
        return f"Reading controls in {short(args.title)}…"
    if tool_name == "wait_for_ui":
        return f"Waiting for {short(args.text)} to {'disappear' if args.disappear else 'appear'}…"
    if tool_name == "desktop_action":
        if args.action == "fill":
            return "Entering text and waiting for results…" if args.submit else "Entering text…"
        return {
            "keys": "Pressing the requested keys…",
            "click": "Clicking the selected control…",
            "double_click": "Opening the selected item…",
            "scroll": "Scrolling…",
        }[args.action]
    if tool_name == "execute_code":
        return f"Running {short(args.language)}: {short(args.purpose)}"
    return {
        "discover_tools": "Loading the needed capabilities…",
        "list_windows": "Checking open windows…",
        "list_processes": "Reading running processes…",
        "read_file": "Reading the file…",
        "write_file": "Writing the file…",
        "web_search": "Searching public sources…",
        "web_fetch": "Reading the source…",
    }.get(tool_name, "Running the requested action…")
