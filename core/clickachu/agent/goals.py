"""Controller-owned completion contracts for bounded requests, independent of app names."""

import re
from dataclasses import dataclass


def command_text(text):
    text = " ".join(text.strip(" \t\r\n.?!").split())
    return re.sub(r"^(?:(?:can|could|would) you |please |just )+", "", text, flags=re.I)


def control_label(text):
    numbers = dict(
        zip("zero one two three four five six seven eight nine".split(), "0123456789", strict=True)
    )
    return " ".join(numbers.get(word, word) for word in re.findall(r"\w+", text.casefold()))


@dataclass(frozen=True)
class RequestGoal:
    kind: str
    operation: str
    target: str = ""
    launch: bool = False
    control: str = ""

    def completed(self, name, args, result, verified):
        if self.kind == "ui_click" and name == "desktop_action" and result.ok:
            performed = (
                result.data.get("performed_action", {}) if isinstance(result.data, dict) else {}
            )
            app = self.target.casefold()
            return (
                performed.get("action") == "click"
                and performed.get("input_dispatched") is True
                and control_label(performed.get("target", "")) == control_label(self.control)
                and any(app in str(performed.get(k, "")).casefold() for k in ("window", "process"))
            )
        if not result.ok or verified is not True:
            return False
        if self.kind == "media" and name == "control_media" and isinstance(result.data, dict):
            expected = {"play": "playing", "pause": "paused", "status": None}[self.operation]
            same_target = not self.target or self.target.casefold() == args.app.casefold()
            return same_target and (expected is None or result.data.get("status") == expected)
        if self.kind == "window" and name == "open_app":
            return args.name.casefold() == self.target.casefold()
        return False

    def permits(self, name, args):
        if self.kind == "ui_click" and name == "desktop_action":
            return args.action == "click" and bool(args.ref)
        if self.kind != "media" or name != "control_media":
            return True
        return args.action in (self.operation, "status") and (
            not self.target or args.app.casefold() == self.target.casefold()
        )


def request_goal(text):
    """Recognize explicit, single-result commands; compound or creative tasks stay general."""
    text = command_text(text)
    if re.search(r"\b(?:don't|do not|never|instead|unless|except)\b", text, re.I):
        return None
    app = r"(?P<app>[\w][\w .&+-]{0,79}?)"
    inspect = re.fullmatch(
        rf"(?:inspect|show|read) (?:the )?(?:UI|controls|UI tree|interface) "
        rf"(?:of|in|for) {app}",
        text,
        re.I,
    )
    if inspect:
        return RequestGoal("inspection", "read", inspect.group("app").strip())
    for pattern in (
        rf"(?:open|launch) {app} (?:and|then) (?P<verb>click|press) (?P<label>.+)",
        rf"(?P<verb>click|press) (?P<label>.+?) (?:in|on) {app}",
    ):
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            continue
        label = match.group("label").strip()
        if match.group("verb").casefold() == "press" and "button" not in label.casefold():
            continue
        label = re.sub(r"^(?:the\s+)?(?:button\s+)?|\s+button$", "", label, flags=re.I)
        if label and not re.search(
            r"\b(?:and|then|twice|times|double|but)\b", label + " " + match.group("app"), re.I
        ):
            return RequestGoal("ui_click", "click", match.group("app").strip(), control=label)
    verb = r"(?P<verb>play|resume|start|pause|stop)"
    sound = r"(?:(?:the|some|my) )?(?:music|audio|playback|songs?|tracks?)"
    patterns = (
        rf"(?:open|launch|start) {app} (?:and|then) {verb}(?: {sound})?",
        rf"{verb} {sound}(?: (?:(?:that|which) is |currently )?(?:playing|running))? "
        rf"(?:on|in|from|using) {app}",
        rf"{verb} (?:playing )?{sound}",
        rf"(?P<verb>pause|resume) {app}",
    )
    for index, pattern in enumerate(patterns):
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            continue
        target = (match.groupdict().get("app") or "").strip()
        if target.casefold() in ("it", "this", "that"):
            target = ""
        if re.search(r"\b(?:and|then|but|task|conversation|work)\b", target, re.I):
            return None
        operation = "pause" if match.group("verb").casefold() in ("pause", "stop") else "play"
        return RequestGoal("media", operation, target, launch=index == 0)
    match = re.fullmatch(rf"(?:open|launch|start) (?:the app )?{app}", text, re.I)
    if match:
        target = match.group("app").strip()
        if not re.search(
            r"\b(?:and|then|file|folder|music|song|website|browser|it|this|that)\b|\.\w+$",
            target,
            re.I,
        ):
            return RequestGoal("window", "open", target)
    return None


def confirmed_context(previous, name, args, result, verified):
    context = dict(previous or {})
    if result.ok and verified is True and name == "control_media":
        context["media_app"] = args.app
    if result.ok and name in ("open_app", "observe_desktop") and isinstance(result.data, dict):
        if result.data.get("title"):
            context["window"] = result.data["title"]
    return context


def resume_requested(text):
    return bool(
        re.fullmatch(
            r"\s*(?:please\s+)?(?:continue|resume|carry on)"
            r"(?:\s+(?:the\s+)?(?:(?:previous|last|current)\s+)?task)?\s*[.!?]*\s*",
            text,
            re.I,
        )
    )
