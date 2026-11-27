"""E10: find the Claude Code login for one config folder (R-6 ... R-12).

The Keychain is read only, forever: the one command this module runs is
`security find-generic-password -s <service> -w`, with stdin closed so nothing can be written to
it. It never adds, changes or deletes an item and never renews a token (R-6, R-7). The token
lives only inside the `Login` that is returned; nothing here prints, logs or stores it.

Which item: Claude Code names it `Claude Code-credentials-<first 8 hex of sha256(value)>`, value
being `CLAUDE_SECURESTORAGE_CONFIG_DIR` if set for that account, else the `CLAUDE_CONFIG_DIR`
spelling, hashed verbatim (R-8). Spellings are tried in a fixed order and the FIRST item holding
a `claudeAiOauth` login wins; the latest expiry never decides. The unsuffixed
`Claude Code-credentials` item is read only under R-10's conditions.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Mapping, Optional

from ju import constants, timeutil
from ju.contracts import Login, LoginError

SERVICE_PREFIX = "Claude Code-credentials"
LEGACY_SERVICE = SERVICE_PREFIX            # the unsuffixed item (R-10)

# Remembered spellings for one folder (E4 `spellings[folder]`): {"config_dir": str|None, "securestorage": str|None}
Spellings = Mapping[str, Optional[str]]

EXPIRED_TEXT = "Open Claude Code on this account, then refresh"
_DETAIL_MAX = 200


@dataclass(frozen=True)
class ProbeResult:
    status: str                    # ok | absent | denied | unparsable | no_login
    login: Optional[Login]         # set only when status == "ok"
    detail: str = ""               # what `security` said when it refused; never a secret


# ---------------------------------------------------------------- naming (pure)

def _is_macos() -> bool:
    return sys.platform == "darwin"


def _home(env: Mapping[str, str]) -> str:
    return env.get("HOME") or os.path.expanduser("~")


def _expand(path: str, env: Mapping[str, str]) -> str:
    if path == "~" or path.startswith("~/"):
        return _home(env) + path[1:]
    return path


def _same_folder(a: str, b: str, env: Mapping[str, str]) -> bool:
    return os.path.realpath(_expand(a, env)) == os.path.realpath(_expand(b, env))


def _default_folder(env: Mapping[str, str]) -> str:
    return os.path.join(_home(env), ".claude")


def _service_for(spelling: str) -> str:
    return SERVICE_PREFIX + "-" + hashlib.sha256(spelling.encode()).hexdigest()[:8]


def _env_values(folder_key: str, env: Mapping[str, str]):
    """(securestorage, config_dir) from `env`, but only when they name THIS folder. With no
    CLAUDE_CONFIG_DIR set the environment is the default folder's."""
    cfg = env.get("CLAUDE_CONFIG_DIR") or None
    applies = _same_folder(cfg, folder_key, env) if cfg else _same_folder(_default_folder(env), folder_key, env)
    if not applies:
        return None, None
    return (env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR") or None), cfg


def candidate_services(folder_key: str, hints: Optional[Spellings], env: Mapping[str, str]) -> List[str]:
    """The hashed Keychain service names for `folder_key`, in the order they are tried. Pure."""
    hints = hints or {}
    env_secure, env_config = _env_values(folder_key, env)
    spellings = [
        hints.get("securestorage"), hints.get("config_dir"),         # remembered, first (R-9)
        env_secure, env_config,                                       # this run's environment, verbatim
        folder_key,                                                   # as given
        os.path.realpath(folder_key),                                 # resolved
        folder_key + "/",                                             # trailing slash
        os.path.join(_home(env), Path(folder_key).name),              # ~/<name>
    ]
    out: List[str] = []
    for sp in spellings:
        if not sp:
            continue
        name = _service_for(sp)
        if name not in out:
            out.append(name)
    return out


def _legacy_allowed(folder_key: str, hints: Optional[Spellings], env: Mapping[str, str]) -> bool:
    """R-10: only the default folder, and only when no securestorage / config-dir value
    (remembered or in this environment) applies to it. (The "no hashed login is ok" half is
    checked by the caller, which has just probed them.)"""
    if not _same_folder(_default_folder(env), folder_key, env):
        return False
    hints = hints or {}
    if hints.get("securestorage") or hints.get("config_dir"):
        return False
    env_secure, env_config = _env_values(folder_key, env)
    return not (env_secure or env_config)


# ---------------------------------------------------------------- reading

def _expires(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expiresAt is not a number")
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).astimezone()


def _parse_blob(service: str, text: str) -> ProbeResult:
    """The stored JSON -> a ProbeResult. A blob with only `mcpOAuth` is no login (R-11)."""
    try:
        obj = json.loads(text)
    except ValueError:
        return ProbeResult("unparsable", None)
    if not isinstance(obj, dict):
        return ProbeResult("unparsable", None)
    oauth = obj.get("claudeAiOauth")
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token:
        return ProbeResult("no_login", None)
    try:
        expires_at = _expires(oauth.get("expiresAt"))
    except (ValueError, OverflowError, OSError):
        return ProbeResult("unparsable", None)
    return ProbeResult("ok", Login(service=service, token=token, expires_at=expires_at,
                                   legacy=(service == LEGACY_SERVICE)))


def probe(service: str) -> ProbeResult:
    """One read of one Keychain item. A hung call (a Keychain prompt on screen) is killed after
    KEYCHAIN_TIMEOUT_S and raises LoginError(keychain_waiting)."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=constants.KEYCHAIN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise LoginError("keychain_waiting",
                         f"The Keychain did not answer in {constants.KEYCHAIN_TIMEOUT_S:g} s; "
                         "a Keychain prompt may be waiting on screen (click Always Allow)") from None
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if r.returncode == 44 or "could not be found" in err:
            return ProbeResult("absent", None)
        return ProbeResult("denied", None, (err or f"security exit {r.returncode}")[:_DETAIL_MAX])
    return _parse_blob(service, (r.stdout or "").strip())


def _checked(login: Login) -> Login:
    """An expired login stays expired until Claude Code refreshes it; it is never renewed here (R-7)."""
    if login.expires_at is not None and timeutil.now() >= login.expires_at:
        raise LoginError("login_expired", EXPIRED_TEXT)
    return login


def _failure(results: List[ProbeResult]) -> LoginError:
    """No item held a login: say which of the four reasons it was (R-11)."""
    statuses = [r.status for r in results]
    if "denied" in statuses:
        detail = next(r.detail for r in results if r.status == "denied")
        return LoginError("keychain_denied",
                          f"The Keychain refused to hand over the login ({detail}). "
                          "Unlock the Keychain or click Always Allow, then refresh")
    if "no_login" in statuses:
        return LoginError("login_missing",
                          "The Keychain item for this account holds no claude.ai login. Run `claude /login` there")
    if "unparsable" in statuses:
        return LoginError("login_missing",
                          "The stored login is not valid JSON. Run `claude /login` for this account")
    return LoginError("login_missing", "No claude.ai login found for this account. Run `claude /login` there")


def _find_in_file(folder_key: str) -> Login:
    """Non-macOS: `<dir>/.credentials.json`, read only."""
    f = Path(folder_key) / ".credentials.json"
    try:
        text = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LoginError("login_missing", "No claude.ai login found for this account "
                         "(.credentials.json is missing). Run `claude /login` there") from None
    except OSError as e:
        raise LoginError("keychain_denied", f"The login file could not be read ({e.strerror or 'read error'}). "
                         "Check its permissions, then refresh") from None
    result = _parse_blob(str(f), text)
    if result.status != "ok":
        raise _failure([result])
    return _checked(result.login)


def find_login(folder_key: str, hints: Optional[Spellings], env: Mapping[str, str]) -> Login:
    """The login for `folder_key`: the first hashed item holding one wins; failing that, the
    unsuffixed item under R-10 (`Login.legacy=True`, whose reading must pass both identity
    checks). One read per service. Raises LoginError(login_missing | login_expired |
    keychain_waiting | keychain_denied)."""
    if not _is_macos():
        return _find_in_file(folder_key)
    results: List[ProbeResult] = []
    for service in candidate_services(folder_key, hints, env):
        r = probe(service)
        if r.status == "ok":
            return _checked(r.login)
        results.append(r)
    if _legacy_allowed(folder_key, hints, env):
        r = probe(LEGACY_SERVICE)
        if r.status == "ok":
            return _checked(replace(r.login, legacy=True))
        results.append(r)
    raise _failure(results)
