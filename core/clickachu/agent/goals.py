"""Controller-owned completion contracts for bounded requests, independent of app names."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit


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
    content: str = ""
    artist: str = ""

    def completed(self, name, args, result, verified):
        if self.kind == "app_destination" and result.ok and isinstance(result.data, dict):
            if name == "open_app":
                return verified is True and args.name.casefold() == self.content.casefold()
            if name in ("desktop_action", "observe_desktop", "wait_for_ui"):
                title = result.data.get("title", "")
                same_app = (
                    self.target.casefold() in title.casefold()
                    or self.target.casefold() in result.data.get("process", "").casefold()
                )
                # Typed address/search values alone do not establish that a page loaded.
                documents = [
                    e.get("text", "")
                    for e in result.data.get("entries", [])
                    if e.get("type") == "Document"
                ]
                if re.match(r"https?://|(?:[\w-]+\.)+[A-Za-z]{2,}(?:/|$)", self.content, re.I):
                    target = urlsplit(
                        self.content if "://" in self.content else "https://" + self.content
                    )
                    for entry in result.data.get("entries", []):
                        if entry.get("type") not in ("Edit", "ComboBox") or not re.search(
                            r"address", entry.get("text", ""), re.I
                        ):
                            continue
                        value = str(entry.get("value") or "")
                        shown = urlsplit(value if "://" in value else "https://" + value)
                        if (shown.hostname or "").removeprefix("www.") == (
                            target.hostname or ""
                        ).removeprefix("www.") and (shown.path or "/").startswith(
                            target.path or "/"
                        ):
                            return same_app and any(
                                d
                                and not re.search(
                                    r"new tab|loading|cannot|error|unreachable", d, re.I
                                )
                                for d in documents
                            )
                    return False
                return same_app and any(
                    media_name_matches(self.content, document)
                    and not re.search(r"\b(?:search|results)\b", document, re.I)
                    for document in documents
                )
            return False
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
        if (
            self.kind in ("media", "media_selection")
            and name == "control_media"
            and isinstance(result.data, dict)
        ):
            expected = {"play": "playing", "pause": "paused", "status": None}[self.operation]
            same_target = not self.target or self.target.casefold() == args.app.casefold()
            return (
                same_target
                and (expected is None or result.data.get("status") == expected)
                and (
                    self.kind != "media_selection"
                    or (
                        media_name_matches(self.content, result.data.get("title", ""))
                        and (
                            not self.artist
                            or media_name_matches(
                                self.artist, result.data.get("artist", ""), ignore_article=True
                            )
                        )
                    )
                )
            )
        if self.kind == "window" and name == "open_app":
            return args.name.casefold() == self.target.casefold()
        return False

    def permits(self, name, args):
        if self.kind == "app_destination" and name == "open_app":
            return args.name.casefold() in (self.target.casefold(), self.content.casefold())
        if (
            self.target
            and self.kind in ("window", "ui_click", "media_selection")
            and name == "open_app"
        ):
            return self.target.casefold() == args.name.casefold()
        if self.kind == "ui_click" and name == "desktop_action":
            return args.action == "click" and bool(args.ref)
        if self.kind == "media_selection" and name == "control_media":
            # Transport controls cannot select content. Never resume an unrelated queue.
            return args.action == "status" and args.app.casefold() == self.target.casefold()
        if self.kind != "media" or name != "control_media":
            return True
        return args.action in (self.operation, "status") and (
            not self.target or args.app.casefold() == self.target.casefold()
        )


def media_name_matches(requested, actual, *, ignore_article=False):
    def words(value):
        if not isinstance(value, str):
            return []
        normalized = unicodedata.normalize("NFKD", value).casefold()
        return re.findall(r"\w+", "".join(c for c in normalized if not unicodedata.combining(c)))

    expected, found = words(requested), words(actual)
    # Ignore an optional leading article in artist names, never arbitrary missing title words.
    if ignore_article and expected[:1] == ["the"]:
        expected = expected[1:]
    return bool(expected) and any(
        found[i : i + len(expected)] == expected for i in range(len(found))
    )


def requested_launch_target(text):
    """Literal initial app target in a compound desktop request, not an inferred app choice."""
    match = re.match(r"(?:open|launch) ([\w .&+-]{1,80}?) (?:and|then) ", command_text(text), re.I)
    if not match:
        return ""
    target = match[1].strip()
    if target.casefold() in ("browser", "the browser") or re.search(
        r"\b(?:my|this|that|file|folder|document|website|url)\b", target, re.I
    ):
        return ""
    return target


def requested_destination(text):
    if not requested_launch_target(text):
        return ""
    match = re.search(r" (?:and|then) (?:open|visit|go to|navigate to) (.+?)[.!?]*$", text, re.I)
    destination = match[1].strip() if match else ""
    if re.search(r"\b(?:and|then)\b", destination, re.I) or len(destination) > 100:
        return ""
    return destination


def _one_typo(left, right):
    if left == right:
        return True
    if len(left) == len(right):
        changes = [i for i, pair in enumerate(zip(left, right, strict=True)) if pair[0] != pair[1]]
        return len(changes) == 1 or (
            len(changes) == 2
            and changes[1] == changes[0] + 1
            and left[changes[0]] == right[changes[1]]
            and left[changes[1]] == right[changes[0]]
        )
    if abs(len(left) - len(right)) == 1:
        longer, shorter = (left, right) if len(left) > len(right) else (right, left)
        return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))
    return False


def _media_text(text, context):
    if not context.get("media_app"):
        return text
    first, _, rest = text.partition(" ")
    # Closed action vocabulary, confirmed media context, no fuzzy song/app-name rewriting.
    verbs = ("play", "pause", "resume", "stop")
    if len(first) >= 4 and first.casefold() not in verbs:
        matches = [verb for verb in verbs if _one_typo(first.casefold(), verb)]
        if len(matches) == 1:
            return matches[0] + (" " + rest if rest else "")
    return text


def _selection_goal(text, context):
    app, body = "", text
    opened = re.fullmatch(r"(?:open|launch) ([\w .&+-]{1,80}?) (?:and|then) (.+)", body, re.I)
    if opened:
        app, body = opened[1].strip(), opened[2]
    scoped = re.fullmatch(r"(.+) (on|in|using) ([\w .&+-]{1,80})", body, re.I)
    if scoped and scoped[1].casefold() != "play":
        candidate = scoped[3].strip()
        known = candidate.casefold() in (app.casefold(), context.get("media_app", "").casefold())
        if (scoped[2].casefold() != "in" or known) and not re.search(
            r"^the\b|\bby\b",
            candidate,
            re.I,
        ):
            body, app = scoped[1], candidate
    match = re.fullmatch(
        r"(?:find|search for) (?:the )?(?:(?:music|song|track) )?(.+?)"
        r" (?:and|then) (?:start playing|play|start) (?:it|the song|the track)",
        body,
        re.I,
    )
    if match is None:
        match = re.fullmatch(r"play (?:the )?(?:(?:song|track) )?(.+)", body, re.I)
    if not match:
        return None
    content = match[1].strip(' "“”')
    if content.casefold() in (
        "music",
        "audio",
        "playback",
        "some music",
        "my music",
        "the music",
        "it",
        "this",
        "that",
        "songs",
        "song",
        "tracks",
        "track",
    ):
        return None
    if re.search(r"\b(?:and|then|but)\b", content, re.I) or len(content) > 200:
        return None
    artist = ""
    quoted = re.fullmatch(r'["“](.+)["”](?: by (.+))?', match[1].strip(), re.I)
    by = re.fullmatch(r"(.+?) by (.+)", content, re.I) if not quoted else None
    if quoted:
        content, artist = quoted[1], quoted[2] or ""
    if by:
        content, artist = by[1].strip(' "“”'), by[2].strip(' "“”')
    return RequestGoal(
        "media_selection",
        "play",
        app or context.get("media_app", ""),
        launch=bool(opened),
        content=content,
        artist=artist,
    )


def request_goal(text, context=None):
    """Recognize explicit, single-result commands; compound or creative tasks stay general."""
    context = context or {}
    text = _media_text(command_text(text), context)
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
    bare = re.fullmatch(r"(play|pause|stop|resume)(?: it)?", text, re.I)
    if bare and (bare[1].casefold() != "resume" or context.get("media_app")):
        operation = "pause" if bare[1].casefold() in ("pause", "stop") else "play"
        return RequestGoal("media", operation, context.get("media_app", ""))
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
    if selection := _selection_goal(text, context):
        return selection
    if destination := requested_destination(text):
        return RequestGoal(
            "app_destination", "open", requested_launch_target(text), content=destination
        )
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
    if result.ok and verified is True and name == "control_media" and args.action != "status":
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
