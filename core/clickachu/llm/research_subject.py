"""Preserve explicit user subjects through routing, search refinement and relevance checks."""

import re
from urllib.parse import unquote


def explicit_subject(text: str) -> str:
    match = re.fullmatch(
        r"\s*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
        r"(?:tell\s+me\s+about|what\s+is|who\s+is|give\s+me\s+(?:information|details)\s+"
        r"(?:about|on)|(?:latest\s+)?news\s+about)\s+(.+?)\s*[?.!]*\s*",
        text,
        re.I,
    )
    return match.group(1).strip(" ?.!") if match else ""


def words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", unquote(text).casefold())) - {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "and",
        "for",
        "to",
        "is",
        "are",
        "its",
    }


def preserve_subject(text: str, suggested_query: str) -> str:
    subject = explicit_subject(text)
    if subject and not words(subject) <= words(suggested_query):
        return subject
    return suggested_query or subject


def matches_subject(subject: str, text: str) -> bool:
    # Conservative exact token matching: Providence is not Provident. No fuzzy entity rewrites.
    return not subject or words(subject) <= words(text)
