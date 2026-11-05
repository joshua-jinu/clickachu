"""Recognize routine gestures from fresh observed controls, never model-supplied labels."""

import re

from clickachu.tools import computer
from clickachu.tools.policy import PolicyAction, PolicyDecision, is_sensitive_domain

_CONSEQUENTIAL = re.compile(
    r"\b(?:buy|purchase|pay|checkout|subscribe|order|transfer|send|post|publish|share|submit|"
    r"delete|remove|uninstall|install|erase|format|reset|discard|overwrite|replace|save|export|"
    r"upload|password|passcode|sign in|log in|permission|security|privacy|administrator|"
    r"allow access|grant access|confirm|accept|agree|yes|ok|apply|close|quit)\b",
    re.I,
)
_SEARCH = re.compile(
    r"\b(?:search|find|address|what do you want to play|what do you want to listen)\b", re.I
)
_ROUTINE = re.compile(
    r"^(?:play|pause|resume|stop|next|previous|skip|shuffle|repeat|mute|unmute|volume|"
    r"search|find|home|go back|go forward|back|forward|browse|library|your library|"
    r"queue|lyrics|now playing|expand|collapse|scroll|zoom|show all|see all|"
    r"songs|tracks|artists|albums|playlists|liked songs|settings|preferences|more options|menu)"
    r"(?:\b|$)",
    re.I,
)


def desktop_decision(args, run_id, sensitive_domains):
    def ask(reason):
        return PolicyDecision(PolicyAction.ASK, reason)

    if not run_id or computer.observation_error(args, run_id):
        return ask("desktop target needs a fresh observation")
    obs = computer._observations[args.observation_id]
    if is_sensitive_domain(obs.title, sensitive_domains) or any(
        domain.casefold() in obs.title.casefold() for domain in sensitive_domains if domain
    ):
        return ask("sensitive window")
    if args.action == "scroll":
        return PolicyDecision(PolicyAction.AUTO, "routine scrolling")
    if args.action == "keys" and args.keys.upper() in (
        "^F",
        "^L",
        "{ESC}",
        "{ESCAPE}",
        "{TAB}",
        "{PGDN}",
        "{PGUP}",
        "{UP}",
        "{DOWN}",
        "{LEFT}",
        "{RIGHT}",
    ):
        return PolicyDecision(PolicyAction.AUTO, "routine keyboard navigation")
    control = obs.controls.get(args.ref)
    if not control:
        return ask("unidentified desktop target or keyboard shortcut")
    label, kind = control[3].strip(), control[2]
    # Check the whole label before navigation prefixes (e.g. 'Play and purchase').
    if not label or _CONSEQUENTIAL.search(label):
        return ask("consequential or ambiguous desktop control")
    if args.action == "fill" and re.match(
        r"\s*(?:javascript|data|file|powershell|cmd|shell|ms-settings):", args.text, re.I
    ):
        return ask("active content or system command")
    if args.action == "fill" and _SEARCH.search(label) and kind in ("Edit", "ComboBox"):
        return PolicyDecision(PolicyAction.AUTO, "routine search or address entry")
    if args.action in ("click", "double_click") and _ROUTINE.search(label):
        return PolicyDecision(PolicyAction.AUTO, "routine navigation or playback")
    if (
        args.action in ("click", "double_click")
        and (
            kind in ("TabItem", "ListItem", "DataItem", "TreeItem")
            or (kind == "Button" and re.fullmatch(r"[0-9.+*/=−×÷-]+", label))
        )
        and not re.search(r"\.(?:exe|msi|bat|cmd|ps1|reg|vbs|lnk)\b", label, re.I)
    ):
        return PolicyDecision(PolicyAction.AUTO, "routine item selection")
    if (
        args.action == "keys"
        and _SEARCH.search(label)
        and args.keys.upper()
        in (
            "{ENTER}",
            "{ESC}",
            "{ESCAPE}",
            "^A",
            "{TAB}",
        )
    ):
        return PolicyDecision(PolicyAction.AUTO, "routine search keyboard navigation")
    return ask("confirm this desktop change")
