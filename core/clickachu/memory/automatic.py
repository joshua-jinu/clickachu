"""Conservative, local extraction of durable first-person facts, without an LLM call.

Only the user's chat message belongs here, never a selection, retrieved page or tool output.
Ambiguous prose remains conversational context; explicit Remember supports more complex notes.
"""

import re
from dataclasses import dataclass

from clickachu.api.protocol import MemoryCreate
from clickachu.context.redact import contains_secret

_TRANSIENT = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|right now|for now|this time|this session|"
    r"temporarily|pretend|hypothetically|for example|if I|would prefer|might prefer)\b",
    re.I,
)
_PRIVATE = re.compile(
    r"\b(?:password|passcode|secret|api.?key|access.?token|private.?key|otp|"
    r"credit.?card|bank.?account)\b",
    re.I,
)
_OPT_OUT = re.compile(r"\b(?:don['’]?t|do not|never)\s+(?:save|store|remember)\b", re.I)
_PERSONAL_QUESTION = re.compile(
    r"^\s*(?:what (?:do you (?:know|remember) about me|is my name|are my preferences|"
    r"do I (?:like|prefer|dislike))|"
    r"what['’]s my name|who am i|where (?:do i (?:live|study|work)|am i based)|"
    r"(?:do you (?:know|remember)|tell me) (?:my (?:name|preferences)|about me))\b",
    re.I,
)
_PATTERNS = (
    (r"(?:my name is|call me)\s+(.+)", "name", "User's name is", "fact"),
    (r"(?:I (?:live|am based)|I['’]m based) in\s+(.+)", "location", "User lives in", "fact"),
    (
        r"(?:I (?:study at|am studying at|am a student at)|"
        r"I['’]m (?:studying|a student) at)\s+(.+)",
        "school",
        "User studies at",
        "fact",
    ),
    (r"I work as\s+(.+)", "occupation", "User works as", "fact"),
    (r"I work (?:at|for)\s+(.+)", "employer", "User works at", "fact"),
    (r"I prefer\s+(.+)", "preference", "User prefers", "preference"),
    (r"I (?:like|love)\s+(.+)", "like", "User likes", "preference"),
    (
        r"I (?:dislike|hate|don['’]t like|do not like)\s+(.+)",
        "dislike",
        "User dislikes",
        "preference",
    ),
    (r"my (?:favourite|favorite) (\w+ is .+)", "favorite", "User's favorite", "preference"),
)
_RULES = [
    (re.compile(pattern, re.I), key, prefix, category)
    for pattern, key, prefix, category in _PATTERNS
]


@dataclass(frozen=True)
class PersonalFact:
    data: MemoryCreate
    slot: str | None = None


def normalized(text: str) -> str:
    return " ".join(text.casefold().strip(" .!\n").split())


def asks_about_user(text: str) -> bool:
    return bool(_PERSONAL_QUESTION.search(text))


def queried_slots(text: str) -> set[str]:
    if not asks_about_user(text):
        return set()
    lower = text.casefold()
    for pattern, slots in (
        (r"\bname\b", {"name"}),
        (r"\b(?:live|based)\b", {"location"}),
        (r"\bstudy\b", {"school"}),
        (r"\bwork\b", {"occupation", "employer"}),
    ):
        if re.search(pattern, lower):
            return slots
    return set()


def memory_slot(content: str) -> str | None:
    """Only unambiguous single-valued fields replace an older note automatically."""
    lower = normalized(content)
    for _, key, prefix, _ in _RULES:
        if key not in ("preference", "like", "dislike", "favorite"):
            if lower.startswith(prefix.casefold() + " "):
                return key
    if re.fullmatch(
        r"user prefers (?:(?:very|more|mostly|generally) )?"
        r"(?:short|concise|brief|detailed|long|thorough) (?:answers|replies|responses)",
        lower,
    ):
        return "response_length"
    favorite = re.match(r"user's favorite (\w+) is ", lower)
    if favorite:
        return "favorite:" + favorite[1].replace("colour", "color")
    return None


def personal_facts(user_text: str) -> list[PersonalFact]:
    """Extract at most three clear leading statements; never scan through a pasted document."""
    text = user_text.strip()
    if (
        not text
        or len(text) > 1600
        or contains_secret(text)
        or _OPT_OUT.search(text)
        or _PRIVATE.search(text)
        or "```" in text
        or text.startswith((">", '"', "“", "'"))
    ):
        return []
    text = re.sub(
        r"^(?:(?:hi|hello|hey)[,!]?\s+|actually[, ]+|by the way[, ]+|btw[, ]+)",
        "",
        text,
        flags=re.I,
    )
    result: list[PersonalFact] = []
    for sentence in re.split(
        r"(?<=[.!?])\s+|[\n;]+\s*|,\s+(?=I\b|my\b)|\s+and\s+(?=I\b|my\b)", text, flags=re.I
    ):
        sentence = sentence.strip()
        if not sentence:
            continue
        if _TRANSIENT.search(sentence) or "?" in sentence:
            break
        sentence = sentence.rstrip(".!")
        matched = False
        for rule, _, prefix, category in _RULES:
            match = rule.fullmatch(sentence)
            if not match:
                continue
            value = match[1].strip(" ,.!")
            # Pronouns and task requests do not identify a durable preference or fact.
            if (
                len(value) < 2
                or len(value) > 140
                or ":" in value
                or '"' in value
                or re.search(
                    r"\b(?:because|but|please|can you|could you|when I|if|unless)\b", value, re.I
                )
                or re.match(
                    r"(?:it|this|that|you|to (?:open|search|click|run|write))\b", value, re.I
                )
            ):
                break
            content = f"{prefix} {value}."
            result.append(
                PersonalFact(MemoryCreate(content=content, category=category), memory_slot(content))
            )
            matched = True
            break
        if not matched or len(result) >= 3:
            break
    return result
