"""Separate a writing brief from desktop execution and reject assistant meta-text."""

import re


def writing_brief(request: str) -> str:
    match = re.search(
        r"\b(?:write|type|draft|compose|insert|put|add|paste|enter|jot(?:\s+down)?)\b\s*(.*)",
        request,
        flags=re.IGNORECASE | re.DOTALL,
    )
    brief = match.group(1) if match else request
    brief = re.sub(r"^\s*(?:me|us)\s+", "", brief, flags=re.IGNORECASE)
    brief = re.sub(
        r"\b(?:in|into|using|on|to)\s+(?:an?\s+|the\s+)?(?:empty\s+|open\s+)?"
        r"(?:notepad|nodepad|note\s+pad)(?:\s+(?:window|app|document|tab))?\b",
        "",
        brief,
        flags=re.IGNORECASE,
    )
    return brief.strip(" ?.\n")


def factual_subject(request):
    """Factual writing is retrieval plus composition, not a claim from model memory."""
    if re.search(r"\b(?:offline|without searching|don't search|do not search)\b", request, re.I):
        return ""
    brief = writing_brief(request)
    match = re.search(
        r"\b(?:description|overview|profile|background|information|facts|summary|details|report|article)"
        r"\s+(?:about|on|of)\s+(.+)|^about\s+(.+)",
        brief,
        re.I,
    )
    subject = next((g for g in match.groups() if g), "").strip(" ?.!") if match else ""
    if re.search(r"\b(?:this|that|above|selected|myself|my|our)\b", subject, re.I):
        return ""
    return subject[:300]


def clean_document(text: str) -> str:
    text = re.sub(
        r"<(?:think|thought)>.*?</(?:think|thought)>", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    text = re.sub(r"^```(?:\w+)?\s*\n([\s\S]*?)\n```$", r"\1", text).strip()
    text = re.sub(r"</?voice>", "", text, flags=re.IGNORECASE).strip()
    if re.search(
        r"(?:\bI\s+(?:can(?:not|['’]t)|am\s+unable)\s+(?:to\s+)?(?:open|access|interact|control)"
        r"|\byou\s+can\s+copy\s+and\s+paste\s+this|\bas\s+an?\s+AI\b)",
        text,
        re.IGNORECASE,
    ):
        raise ValueError("The model returned assistant instructions instead of document content.")
    if not text:
        raise ValueError("The model returned an empty document.")
    return text
