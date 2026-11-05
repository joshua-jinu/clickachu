"""Mode router (PLAN §11): classify a run as `ask` or `agent`."""

import asyncio
import logging
import re

from clickachu.api.protocol import RunAction, RunMode
from clickachu.llm.json_ask import make_ask_json
from clickachu.llm.provider import LLMError, LLMProvider

log = logging.getLogger(__name__)

_WEB_LOOKUP = re.compile(
    r"\b(?:(?:search|check|browse|look|verify|research)\s*"
    r"(?:(?:it|this|that)\s+)?(?:online|the\s+(?:web|internet)|on\s+the\s+(?:web|internet))"
    r"|look\s+(?:it|this|that)\s+up|google\s+(?:it|this|that)"
    r"|(?:search|look|check|find|verify|research)\b[^.!?\n]{0,80}\bonline)\b",
    re.IGNORECASE,
)
_NO_ACTION = re.compile(
    r"\b(?:don't|do\s+not|never)\s+(?:search|check|browse|look|google|do\s+anything)\b",
    re.IGNORECASE,
)


def web_lookup_requested(text: str) -> bool:
    """Explicit lookup intent, shared by routing and tool selection."""
    return bool(_WEB_LOOKUP.search(text)) and not bool(_NO_ACTION.search(text))


_SCREEN_QUESTION = re.compile(
    r"\b(?:(?:on|in|at)\s+(?:my|the)\s+(?:screen|display|monitor|desktop)\b"
    r"|(?:see|read|look\s+at|describe|check|view)\s+(?:my|the)\s+(?:screen|display|desktop)\b"
    r"|what(?:['’]s|\s+is|\s+am\s+i|\s+are\s+you)\s+(?:currently\s+)?"
    r"(?:visible|showing|on\s+screen|looking\s+at|seeing)\b"
    r"|what\s+(?:windows|apps|applications|programs)\s+(?:are\s+|do\s+i\s+have\s+)?open\b)",
    re.IGNORECASE,
)


def screen_question_requested(text: str) -> bool:
    """Questions about the user's own screen are local observation, never a web search."""
    return bool(_SCREEN_QUESTION.search(text)) and not bool(
        re.search(
            r"\b(?:screen\s*(?:saver|time|resolution|size|reader)|how\s+(?:do|can|to))\b",
            text,
            re.IGNORECASE,
        )
    )


_NOTEPAD_WRITE_VERB = r"\b(?:write|type|put|draft|compose|insert|add|paste|enter|jot)\b"


def notepad_write_requested(text: str) -> bool:
    """A bounded unsaved-editor task; explicit file/save requests use the general agent."""
    return (
        bool(re.search(r"\b(?:notepad|nodepad|note\s+pad)(?:\.exe)?\b", text, re.IGNORECASE))
        and bool(re.search(_NOTEPAD_WRITE_VERB, text, re.IGNORECASE))
        and not bool(re.search(r"\b(?:save|file|folder|path)\b", text, re.IGNORECASE))
        # "Add Notepad to the taskbar/startup" is app management, not document writing.
        and not bool(
            re.search(r"\b(?:pin|taskbar|startup|start\s+menu|uninstall)\b", text, re.IGNORECASE)
        )
        and not bool(
            re.match(r"\s*(?:how\s+(?:do|can|to)|explain|don't|do\s+not)", text, re.IGNORECASE)
        )
    )


def lookup_query(text: str, history: list[dict[str, str]]) -> str:
    stop = set(
        (
            "can could would you please check checkonline online internet web search look up "
            "tell me about for and the it this that what is are who do on of"
        ).split()
    )

    def terms(value: str) -> str:
        return " ".join(w for w in re.findall(r"[\w.+#-]+", value) if w.lower() not in stop).strip(
            " ."
        )[:400]

    query = terms(text)
    if not query:
        for message in reversed(history):
            if message["role"] == "user" and (query := terms(message["content"])):
                break
    return query


ROUTER_TIMEOUT_S = 3.0

ROUTER_SYSTEM = """\
You are a mode classifier.
- "agent": The user wants actions on their PC: apps, websites, files, or project commands.
- "ask": The user wants conversation, explanations, advice, code, or information.
Reply with JSON only: {"mode": "ask"} or {"mode": "agent"}"""

# Imperative PC / file / browser action patterns (PLAN §11 step 2).
_AGENT_HEURISTIC = re.compile(
    r"^\s*(?:(?:please|can\s+you|could\s+you|would\s+you|hey|ok(?:ay)?|just)[,\s]+)*"
    r"(?:"
    r"o+p+e+n\b|start\b|run\b|launch\b|play\b|"
    r"do\s+(?:it|this|that)?\b|execute\b|proceed\b|go\s+ahead\b|"
    r"list\s+(?:all\s+)?(?:my\s+)?(?:open\s+)?(?:windows?|files?|processes?)\b|"
    r"list\b|"
    r"find\s+(?:my\s+|the\s+|all\s+|latest\s+)?files?\b|"
    r"find\s+my\b|"
    r"search\s+(?:for\s+)?files?\b|"
    r"move\b|c+r+e+a+t+e\s+(?:a\s+)?(?:new\s+)?(?:folder|file|doc|script)\b|mkdir\b|"
    r"w+r+i+t+e\s+(?:a\s+|to\s+)?(?:file|doc|recipe|recepie|note|script)?\b|trash\b|delete\s+(?:the\s+)?file\b|"
    r"save\s+(?:it|this|as|to)?\b|"
    r"focus\b|switch\s+to\b|t+y+p+e\s+(?:in)?\b|"
    r"click\b|go\s+to\b|search\s+the\s+web\b|browse\b|"
    r"take\s+(?:a\s+)?screenshot\b|screenshot\b|"
    r"kill\b|terminate\b|stop\s+process\b"
    r")",
    re.IGNORECASE,
)

# Phrases indicating PC/browser tool use anywhere in the prompt (unless a "what/how/why" question).
_AGENT_PHRASE = re.compile(
    r"\b(?:"
    r"notepad|calc(?:ulator)?|powershell|cmd(?:\.exe)?|terminal|explorer|word|excel|vscode|code|"
    r"find\s+files?\b|create\s+(?:a\s+)?(?:new\s+)?(?:folder|file|doc|script)\b|"
    r"search\s+the\s+web\b|open\s+(?:it|notepad|app|file)\b|list\s+windows\b|"
    r"save\s+(?:it|this|as|to)\b|write\s+(?:a|to|the)\b|type\s+(?:in|text)\b|"
    r"do\s+it\b|go\s+ahead\b|proceed\b"
    r")\b",
    re.IGNORECASE,
)

# Actions combined with filesystem storage destinations.
_LOCATION_ACTION = re.compile(
    r"\b(?:onedrive|desktop|downloads|documents)\b.*(?:\bsave\b|\bwrite\b|\bcreate\b|\bput\b|\bfile\b|\bopen\b)"
    r"|(?:\bsave\b|\bwrite\b|\bcreate\b|\bput\b|\bfile\b|\bopen\b).*\b(?:onedrive|desktop|downloads|documents)\b",
    re.IGNORECASE,
)
_CONCEPTUAL_QUESTION = re.compile(
    r"^\s*(?:what|why|how\s+does|how\s+do\s+i|explain|define|who|when)\b",
    re.IGNORECASE,
)

# Fast greetings and identity checks that need no router call.
_GREETING_OR_IDENTITY = re.compile(
    r"^\s*(?:"
    r"hello\b|hi\b|hey\b|howdy\b|greetings\b|good\s+(?:morning|afternoon|evening|day)\b|"
    r"what['’]?s\s+up\b|sup\b|"
    r"who\s+(?:are\s+you|is\s+this|made\s+you|created\s+you)\b|"
    r"what\s+(?:are\s+you|model\b|can\s+you\s+do|is\s+your\s+name|version)\b|"
    r"introduce\s+yourself\b|tell\s+me\s+about\s+yourself\b|"
    r"are\s+you\s+(?:an?\s+)?(?:ai|robot|bot|human|real|model)\b"
    r")",
    re.IGNORECASE,
)


def heuristic_mode(
    text: str, *, mode: RunMode | None = None, action: RunAction | None = None
) -> RunMode | None:
    """Steps 1 & 2 of PLAN §11: explicit mode/chip or fast PC-verb heuristics; None if undecided."""
    if mode is not None:
        return mode
    if action is not None:
        return "agent" if action in ("fix", "do") else "ask"
    stripped = text.strip().strip("\"'“”`")
    if not stripped:
        return "ask"
    if _NO_ACTION.search(stripped):
        return "ask"
    if web_lookup_requested(stripped) or notepad_write_requested(stripped):
        return "agent"
    if screen_question_requested(stripped):
        return "agent"
    if re.match(
        r"^\s*(?:(?:please|can\s+you|could\s+you|would\s+you|hey)[,\s]+)*"
        r"(?:organize|organise|rename|convert|automate|download|install|uninstall|"
        r"create|make|build|export|extract|compress|unzip|backup)\b",
        stripped,
        re.IGNORECASE,
    ):
        return "agent"
    if _AGENT_HEURISTIC.search(stripped):
        return "agent"
    if _LOCATION_ACTION.search(stripped):
        return "agent"
    if not _CONCEPTUAL_QUESTION.search(stripped) and _AGENT_PHRASE.search(stripped):
        return "agent"
    if _GREETING_OR_IDENTITY.search(stripped):
        return "ask"
    return None


async def route_mode(
    text: str,
    *,
    mode: RunMode | None = None,
    action: RunAction | None = None,
    provider: LLMProvider | None = None,
    router_model: str = "",
    timeout_s: float = ROUTER_TIMEOUT_S,
    is_ollama: bool = False,
) -> RunMode:
    """Resolve `ask` vs `agent` per PLAN §11 (explicit -> heuristics -> router_model -> `ask`)."""
    fast = heuristic_mode(text, mode=mode, action=action)
    if fast is not None:
        return fast
    # For local models via Ollama: if heuristics didn't match imperative PC actions,
    # avoid a slow local LLM roundtrip (and potential classification hallucinations)
    # and default immediately to ask mode.
    if is_ollama or getattr(provider, "_is_ollama", False):
        return "ask"
    if provider is None or not router_model.strip():
        return "ask"
    stripped = text.strip().strip("\"'“”`")
    ask = make_ask_json(provider, router_model, max_tokens=40)
    try:
        reply = await asyncio.wait_for(ask(ROUTER_SYSTEM, stripped), timeout=timeout_s)
    except (TimeoutError, LLMError) as exc:
        log.info("router fallback to ask (%s)", exc)
        return "ask"
    if reply and reply.get("mode") == "agent":
        return "agent"
    return "ask"
