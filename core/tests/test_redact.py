import pytest

from clickachu.context.redact import REDACTED, redact

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA7bq98Vq\nabc123def456\n"
    "-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("key is sk-abc123DEF456ghi789JKL012mno345", "sk-abc123"),
        ("gemini AIzaSyDaGmWKa4JsXZ-HjGw7ISLn_3namBGewQe here", "AIzaSyDa"),
        ("OPENAI=sk-proj-Ab12Cd34Ef56Gh78Ij90Kl12Mn34", "sk-proj"),
        ("sk-ant-api03-Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0", "api03"),
        ("token ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "ghp_a1B2"),
        ("github_pat_11ABCDEFG0abcdefghij_klmnopqrstuvwxyz0123", "github_pat_11"),
        ("aws AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpM",
            "eyJhbGci",
        ),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789", "abcdefghijkl"),
        (f"before\n{PEM}\nafter", "MIIEowIB"),
        ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC", "MIIEvQ"),
        ("password=hunter2", "hunter2"),
        ("PASSWORD: 'correct horse battery'", "correct horse"),
        ('{"token": "abc.def-123"}', "abc.def-123"),
        ("db_password = s3cr3t!", "s3cr3t"),
        ("api_key=zzz999", "zzz999"),
    ],
)
def test_secrets_are_redacted(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert REDACTED in out


def test_assignment_keeps_key_and_surrounding_text() -> None:
    assert redact("curl -d password=hunter2 -H x") == f"curl -d password={REDACTED} -H x"
    assert redact(f"before\n{PEM}\nafter") == f"before\n{REDACTED}\nafter"


@pytest.mark.parametrize(
    "text",
    [
        "The risk-assessment-framework-2024-final-version is ready",
        "see task-force-recommendations-2023-annual-report",
        "pip install sk-learn-is-a-great-library-for-ml",  # no digit: not a key
        "sk-short1",
        "max_tokens=2048 and token_count = 5",
        "The tokens are cheap; the token is a word.",
        "Reset your password at the settings page.",
        "password: ",
        "eyJ is a prefix",
        "eyJhbGciOiJIUzI1NiJ9 alone is only a header",
        "-----BEGIN PUBLIC KEY-----\nMFwwDQYJKoZIhvcNAQEBBQADSwAwSA\n-----END PUBLIC KEY-----",
        "AKIA is an AWS prefix, AKIAshort is not a key",
        "ghp_short",
        "Bearer of bad news",
        "plain prose with no secrets at all",
    ],
)
def test_false_positive_guards(text: str) -> None:
    assert redact(text) == text


def test_redaction_is_idempotent() -> None:
    once = redact("password=hunter2 and sk-abc123DEF456ghi789JKL012mno345")
    assert redact(once) == once
