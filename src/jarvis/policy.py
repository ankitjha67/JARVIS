"""Authorisation policy: risk tiers, argument-based escalation, taint, sensitive paths.

The decision is made here, in the core — never by the model and never by the UI (PRD §10).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Literal


class Tier(IntEnum):
    T0 = 0  # observe
    T1 = 1  # reversible
    T2 = 2  # consequential
    T3 = 3  # destructive / system-level


@dataclass(frozen=True)
class Decision:
    action: Literal["allow", "ask"]
    tier: Tier
    reason: str
    can_remember: bool


def decide(tool_name: str, tier: Tier, reason: str, *, tainted: bool, session_grants: set[str],
           autonomy: str = "guarded", sensitive: bool = False) -> Decision:
    """Map a classified tool call to allow / ask.

    Guarded (default): T0 always runs. T1 runs unless the run has ingested untrusted content (SEC-1).
    T2 asks unless granted for the session and the run is clean. T3 always asks.

    Trusted (the user's explicit choice in Settings, for hands-off desktop and browser work): T1 and
    non-sensitive T2 run without asking even after untrusted content was read. Sensitive tools
    (shell, sending email, calls, writing files, external servers) and every T3 still ask.
    """
    if tier == Tier.T0:
        return Decision("allow", tier, reason, False)
    if autonomy == "trusted" and (tier == Tier.T1 or (tier == Tier.T2 and not sensitive)):
        return Decision("allow", tier, "Trusted mode.", False)
    if tier == Tier.T1:
        if tainted:
            return Decision(
                "ask", tier,
                "This run has read outside content (a screen, web page, file, email, clipboard or command output), "
                "so every action needs your approval.",
                False,
            )
        return Decision("allow", tier, reason, False)
    if tier == Tier.T2:
        if not tainted and tool_name in session_grants:
            return Decision("allow", tier, "Allowed for this session.", False)
        return Decision("ask", tier, reason or "This action changes data or runs code.", not tainted)
    return Decision("ask", tier, reason or "This action is destructive or system-level.", False)


# ---------------------------------------------------------------------------------------------
# Sensitive paths (SEC-6): tools may not read or write these.
# ---------------------------------------------------------------------------------------------

_SENSITIVE_DIR_PARTS = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "credentials", "keychains",
    "user data",  # Chromium profiles (cookies, saved logins)
    "profiles",  # Firefox profiles
    "protect", "vault",  # DPAPI master keys / Windows Vault
}
_SENSITIVE_NAME_RE = re.compile(
    r"(?ix) ^(?: \.env(\..*)? | .*\.pem | .*\.key | .*\.pfx | .*\.p12 | .*\.kdbx | .*\.ppk "
    r"| id_(rsa|dsa|ecdsa|ed25519)(\.pub)? | \.netrc | \.npmrc | \.pypirc | \.git-credentials "
    r"| login\ data.* | cookies(\..*)? | wallet\.dat | ntuser\.dat.* | sam | security | system )$"
)
_SYSTEM_ROOTS = [r"c:\windows", r"c:\program files", r"c:\program files (x86)", r"c:\programdata"]


def canonical(path: str) -> Path:
    """Expand and resolve a user/model supplied path before any policy check (SEC-7)."""
    expanded = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    return Path(expanded).resolve(strict=False)


def is_sensitive_path(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    if any(p in _SENSITIVE_DIR_PARTS for p in parts[:-1]) or (parts and parts[-1] in {".ssh", ".gnupg", ".aws"}):
        return True
    return bool(_SENSITIVE_NAME_RE.match(path.name))


def is_system_path(path: Path) -> bool:
    low = str(path).lower()
    return any(low == root or low.startswith(root + os.sep) for root in _SYSTEM_ROOTS)


# ---------------------------------------------------------------------------------------------
# Shell classification (SEC-3, SEC-4, SEC-9)
# ---------------------------------------------------------------------------------------------

_SHELL_REFUSE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)-verb\s+runas|\brunas(\.exe)?\b|\bsudo\b|\bgsudo\b"),
     "Jarvis never elevates privileges. Run elevated commands yourself."),
    (re.compile(r"(?i)-e(nc(odedcommand)?)?\s+[A-Za-z0-9+/=]{20,}"),
     "Encoded commands are refused because they cannot be reviewed."),
]

_SHELL_T3: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(remove-item|rm|rmdir|rd|del|erase|ri)\b"), "deletes files"),
    # `format` only as a command followed by a drive, not the -Format parameter of other cmdlets
    (re.compile(r"(?i)\b(format-volume|clear-disk|diskpart|initialize-disk|remove-partition)\b|"
                r"(?<![-\w])format(\.com)?\s+[a-z]:"),
     "modifies disks or volumes"),
    (re.compile(r"(?i)\b(stop-computer|restart-computer|shutdown(\.exe)?|logoff)\b"), "shuts down or restarts"),
    (re.compile(r"(?i)\breg(\.exe)?\s+(add|delete|import)\b|\b(set|new|remove)-itemproperty\b.*\b(hklm|hkcu|registry)|"
                r"\b(hklm|hkcu):", ), "changes the registry"),
    (re.compile(r"(?i)\b(schtasks|register-scheduledtask|new-scheduledtask|new-service|sc(\.exe)?\s+(create|config|delete))\b"),
     "creates persistence (scheduled task or service)"),
    (re.compile(r"(?i)currentversion\\run|startup\\|shell:startup"), "adds a startup entry"),
    (re.compile(r"(?i)\b(set-mppreference|add-mppreference|netsh\s+(advfirewall|firewall)|"
                r"(new|set|remove|disable)-netfirewall\w*|set-executionpolicy|bcdedit|vssadmin|wbadmin|cipher\s+/w)\b"),
     "changes security or boot configuration"),
    (re.compile(r"(?i)\b(net(\.exe)?\s+(user|localgroup)|new-localuser|add-localgroupmember|set-localuser)\b"),
     "changes user accounts"),
    (re.compile(r"(?i)\b(winget|choco|scoop|pip3?|uv|npm|pnpm|yarn|msiexec)(\.exe)?\b.*\b(install|add|uninstall|remove|/i|/x)\b|"
                r"\b(install|uninstall)-(module|package)\b"), "installs or removes software"),
    (re.compile(r"(?i)\b(invoke-webrequest|iwr|curl|wget|invoke-restmethod|irm|start-bitstransfer|certutil)\b.*"
                r"(\||;|&&).*\b(iex|invoke-expression|powershell|pwsh|cmd|start-process|bash|python)\b|"
                r"\b(iex|invoke-expression)\b"), "downloads and executes code"),
    (re.compile(r"(?i)\b(git)\b.*\b(push\s+.*--force|reset\s+--hard|clean\s+-[a-z]*f)"), "discards git history or files"),
    (re.compile(r"(?i)\b(clear-content|clear-recyclebin|out-file|set-content)\b|(?<![0-9&])>(?!&)"),
     "overwrites file contents"),
]


def shell_refusal(command: str) -> str | None:
    for pat, why in _SHELL_REFUSE:
        if pat.search(command):
            return why
    return None


def classify_shell(command: str) -> tuple[Tier, str]:
    hits = [why for pat, why in _SHELL_T3 if pat.search(command)]
    if hits:
        return Tier.T3, "This command " + "; ".join(dict.fromkeys(hits)) + "."
    return Tier.T2, "Runs a PowerShell command on your machine."


_EXECUTABLE_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".msi", ".msp", ".scr", ".hta", ".cpl", ".jar", ".lnk", ".reg", ".py", ".pyw", ".sh", ".url", ".pif",
}


def is_executable_file(path: Path) -> bool:
    return path.suffix.lower() in _EXECUTABLE_EXT


# ---------------------------------------------------------------------------------------------
# Web URLs (SEC-7): only plain http(s) with a real host name
# ---------------------------------------------------------------------------------------------

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def normalise_web_url(raw: str) -> str | None:
    """Return a safe http(s) URL, or None. A bare domain is treated as https."""
    from urllib.parse import urlsplit

    raw = raw.strip()
    if any(c in raw for c in ' "<>\n\r\t\\'):
        return None
    url = raw if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw) else "https://" + raw
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError for things like "javascript:alert(1)" read as host:port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    if not (_HOST_RE.match(host) or re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host)):
        return None
    return url
