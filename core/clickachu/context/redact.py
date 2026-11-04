"""Secret redaction for captured text (PLAN §10 step 6). Runs before anything reaches the LLM."""

import re

REDACTED = "[REDACTED]"

# (?<![\w-]) rather than \b so `risk-…` / `task-…` never look like an `sk-` key.
_OPENAI_KEY = re.compile(r"(?<![\w-])sk-(?=[\w-]*\d)[\w-]{20,}")
_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})")
_AWS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/-]{20,}=*")
_PEM_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    re.DOTALL,
)
# A selection cut off mid-key has no END line; drop everything after the BEGIN.
_PEM_UNTERMINATED = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*", re.DOTALL)
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>(?:password|passwd|token|secret|api[_-]?key)["']?\s*[:=]\s*)
    (?P<value>"[^"\n]*"|'[^'\n]*'|[^\s"',;&]+)
    """
)

_SIMPLE = (
    _OPENAI_KEY,
    _GOOGLE_API_KEY,
    _GITHUB_TOKEN,
    _AWS_KEY_ID,
    _JWT,
    _PEM_BLOCK,
    _PEM_UNTERMINATED,
)


def _assignment(match: re.Match[str]) -> str:
    if match["value"] == REDACTED:
        return match[0]
    return match["key"] + REDACTED


def contains_secret(text: str) -> bool:
    return redact(text) != text


def redact(text: str) -> str:
    for pattern in _SIMPLE:
        text = pattern.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m[1] + REDACTED, text)
    return _ASSIGNMENT.sub(_assignment, text)
