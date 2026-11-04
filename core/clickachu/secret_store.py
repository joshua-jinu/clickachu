"""API keys in Windows Credential Manager via `keyring` — never in files or the DB."""

import logging
from typing import Protocol

import keyring
from keyring.errors import KeyringError

log = logging.getLogger(__name__)

SERVICE = "Clickachu"
OPENROUTER_USER = "openrouter"


class SecretStore(Protocol):
    def get_openrouter_key(self) -> str | None: ...
    def set_openrouter_key(self, key: str) -> None: ...


class KeyringSecretStore:
    def get_openrouter_key(self) -> str | None:
        try:
            return keyring.get_password(SERVICE, OPENROUTER_USER)
        except KeyringError as exc:
            log.error("keyring read failed: %s", exc)
            return None

    def set_openrouter_key(self, key: str) -> None:
        keyring.set_password(SERVICE, OPENROUTER_USER, key)
