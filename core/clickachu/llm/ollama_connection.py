"""Explicit Ollama endpoints and Windows client environment cleanup."""

import ipaddress
import os
from urllib.parse import urlsplit, urlunsplit


def normalize_endpoint(value: str) -> str:
    value = value.strip()
    explicit_scheme = "://" in value
    parts = urlsplit(value if explicit_scheme else f"http://{value}")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter an Ollama host or an http(s) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Ollama URLs must not contain credentials, a query, or a fragment")
    port = parts.port  # Also validates malformed ports.
    host = parts.netloc
    if not explicit_scheme and port is None:
        host += ":11434"
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parts.scheme, host, path, "", ""))


def is_local_endpoint(value: str) -> bool:
    host = urlsplit(value).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _clear_user_host() -> None:
    """Remove only the current user's persisted override, never machine-wide settings."""
    if os.name != "nt":
        return
    import ctypes
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, "OLLAMA_HOST")
    except FileNotFoundError:
        return
    # Notify applications that launch future processes. Existing shells keep their own copy.
    result = ctypes.c_size_t()
    from ctypes import wintypes

    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        ctypes.c_wchar_p,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    send.restype = wintypes.LPARAM
    send(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, ctypes.byref(result))


def clear_client_host() -> str | None:
    os.environ.pop("OLLAMA_HOST", None)
    try:
        _clear_user_host()
    except OSError as exc:
        return f"Local endpoint saved, but could not clear saved OLLAMA_HOST: {exc}"
    return None
