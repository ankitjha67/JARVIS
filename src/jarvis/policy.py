"""Authorisation policy: risk tiers, argument-based escalation, taint, sensitive paths.

The decision is made here, in the core — never by the model and never by the UI (PRD §10).
"""

from __future__ import annotations

import os
import re
import sys
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
    "com.apple.tcc", "cookies",  # macOS privacy database and ~/Library/Cookies
}
_SENSITIVE_NAME_RE = re.compile(
    r"(?ix) ^(?: \.env(\..*)? | .*\.pem | .*\.key | .*\.pfx | .*\.p12 | .*\.kdbx | .*\.ppk "
    r"| id_(rsa|dsa|ecdsa|ed25519)(\.pub)? | \.netrc | \.npmrc | \.pypirc | \.git-credentials "
    r"| login\ data.* | cookies(\..*)? | wallet\.dat | ntuser\.dat.* | sam | security | system )$"
)
_WINDOWS_ROOTS = [r"c:\windows", r"c:\program files", r"c:\program files (x86)", r"c:\programdata"]
# /tmp, /var/folders and /usr/local stay writable; resolved paths show /etc and /var under /private.
_MACOS_ROOTS = ["/system", "/library", "/applications", "/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/lib",
                "/usr/libexec", "/usr/share", "/etc", "/private/etc", "/private/var/db", "/private/var/root",
                "/cores", "/opt/homebrew", "/usr/local/cellar"]
_SYSTEM_ROOTS = _WINDOWS_ROOTS if sys.platform == "win32" else _MACOS_ROOTS


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


# zsh / POSIX commands (macOS). Checked in addition to the lists above, which already cover rm, shutdown,
# sudo, git and output redirection.
_POSIX_REFUSE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(^|[\s;&|(`])(su|doas|pkexec)(\s|$)|with\s+administrator\s+privileges"),
     "Jarvis never elevates privileges. Run elevated commands yourself."),
    (re.compile(r"(?i)\bbase64\b[^|;&]*(-d\b|-D\b|--decode\b)[^|;&]*\|\s*(sh|bash|zsh|python3?|perl|ruby|osascript)\b"),
     "Encoded commands are refused because they cannot be reviewed."),
    (re.compile(r"(?i)\bsecurity\s+(find-(generic|internet)-password|find-certificate|find-identity|dump-keychain|"
                r"export|unlock-keychain|show-keychain-info)\b"),
     "Jarvis never reads the Keychain or other credential stores."),
]

_POSIX_T3: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(unlink|shred|srm|trash)\b|\bfind\b.*\s-delete\b"), "deletes files"),
    (re.compile(r"(?i)\b(diskutil|newfs\w*|mkfs\w*|fdisk|gpt|asr|dd)\b|\bhdiutil\s+(erase|convert|create)\b"),
     "modifies disks or volumes"),
    (re.compile(r"(?i)\b(reboot|halt|poweroff)\b|\bpmset\s+(sleepnow|displaysleepnow)\b|\bkillall\s+loginwindow\b"),
     "shuts down, restarts or sleeps the computer"),
    (re.compile(r"(?i)\blaunchctl\s+(load|unload|bootstrap|bootout|enable|disable|submit|remove|kickstart)\b|"
                r"\bcrontab\b|launchagents|launchdaemons|\blogin\s+item"),
     "creates persistence (launch agent, daemon, cron job or login item)"),
    (re.compile(r"(?i)\bdefaults\s+(write|delete|import)\b|\b(csrutil|spctl|nvram|tccutil|systemsetup|pfctl|"
                r"socketfilterfw|fdesetup|bless|kextload|kextunload|kmutil)\b|\bscutil\s+--set\b|"
                r"\bsecurity\s+(add|delete|import|set|authorizationdb)\S*|\bprofiles\s+(install|remove)\b|"
                r"\bxattr\s+-\w*[dc]\b|\bchflags\b|\bnetworksetup\s+-set\w+"),
     "changes security or system configuration"),
    (re.compile(r"(?i)\b(dscl|sysadminctl|dseditgroup|passwd|chpasswd|useradd|usermod|userdel)\b"),
     "changes user accounts"),
    (re.compile(r"(?i)\b(brew|port|gem|cargo|mas|apt|apt-get|dnf|yum|pacman|pipx)\b.*\b(install|uninstall|remove|"
                r"reinstall|upgrade|add)\b|\bsoftwareupdate\s+(-i|--install)\b|\binstaller\s+-pkg\b"),
     "installs or removes software"),
    (re.compile(r"(?i)\b(curl|wget|fetch)\b.*(\||;|&&).*\b(sh|zsh|perl|ruby|node|osascript)\b|\beval\b|"
                r"\bsource\s+<\(|(^|[\s;&|])\.\s+<\("), "downloads and executes code"),
    (re.compile(r"(?i)\bosascript\b|\bautomator\b|\bshortcuts\s+run\b"),
     "runs a script that can control other apps"),
    (re.compile(r"(?i)\b(chmod|chown|chgrp)\b"), "changes file permissions or ownership"),
    (re.compile(r"(?i)\b(tee|truncate)\b|\b(sed|perl)\s+(-\w+\s+)*-\w*i"), "overwrites file contents"),
]


def _posix(posix: bool | None) -> bool:
    return sys.platform != "win32" if posix is None else posix


def shell_refusal(command: str, posix: bool | None = None) -> str | None:
    for pat, why in [*_SHELL_REFUSE, *(_POSIX_REFUSE if _posix(posix) else [])]:
        if pat.search(command):
            return why
    return None


def classify_shell(command: str, posix: bool | None = None) -> tuple[Tier, str]:
    """`posix` picks the rule set for a zsh command line; by default it follows the OS Jarvis runs on."""
    is_posix = _posix(posix)
    hits = [why for pat, why in [*_SHELL_T3, *(_POSIX_T3 if is_posix else [])] if pat.search(command)]
    if hits:
        return Tier.T3, "This command " + "; ".join(dict.fromkeys(hits)) + "."
    return Tier.T2, f"Runs a {'shell' if is_posix else 'PowerShell'} command on your machine."


_EXECUTABLE_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".msi", ".msp", ".scr", ".hta", ".cpl", ".jar", ".lnk", ".reg", ".py", ".pyw", ".sh", ".url", ".pif",
    # macOS: bundles, installers, scripts and automations that run on a double click
    ".app", ".command", ".tool", ".pkg", ".mpkg", ".dmg", ".scpt", ".scptd", ".applescript", ".workflow",
    ".action", ".zsh", ".bash", ".terminal", ".inetloc", ".webloc", ".shortcut",
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
