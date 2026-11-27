"""E22: `jusage doctor` (R-143) and the app's Run Doctor. Per account folder: every Keychain
spelling tried and what it held, the chosen item, the legacy-item warning, identity and the
weekly-reset fingerprint, the gate's state, the status-line backup and its capture age. Then one
verdict line per account: `OK`, `WARN` or `FAIL <what>: <why>`.

Read only. It probes the Keychain the way E10 does (same order, same stop at the first item that
holds a login, same unsuffixed-item rule) and never prints, logs or keeps a token: a probe's
login is looked at for its expiry and dropped.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from ju import accounts, feed, formats, fsutil, gate, keychain, timeutil, usage_client
from ju.config import ConfigReadOnly, ConfigStore
from ju.constants import STALE_AFTER_S
from ju.contracts import LoginError
from ju.live_store import LiveStore

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LOGIN_FIX = "run `claude /login` in this config folder"


def _minute(m: int) -> str:
    day, rest = divmod(m, 1440)
    return f"{_WEEKDAYS[day]} {rest // 60:02d}:{rest % 60:02d} UTC"


def _ago(seconds: float) -> str:
    return "just now" if seconds < 60 else f"{formats.countdown(seconds)} ago"


def _number(cfg, key: str) -> Optional[int]:
    try:
        return accounts.number_for(cfg, key)
    except ConfigReadOnly:
        return None                        # never refreshed yet: no number assigned


def _keychain(key: str, hints, env, now: datetime, probe: Callable) -> (List[str], Optional[str], Optional[str]):
    """(lines, chosen service or None, failure text or None). Same order as keychain.find_login."""
    lines = ["  Keychain items tried, in order (the first holding a login wins):"]
    services = list(keychain.candidate_services(key, hints, env))
    if keychain.legacy_allowed(key, hints, env):
        services.append(keychain.LEGACY_SERVICE)
    chosen = failure = None
    rows = []
    for service in services:
        try:
            r = probe(service)
        except LoginError as e:
            rows.append((service, f"no answer: {e.text}"))
            failure = e.text
            break
        status = r.status
        if r.status == "ok":
            exp = r.login.expires_at
            if exp is not None and now >= exp:
                status, failure = f"ok but expired {_ago((now - exp).total_seconds())}", "the login has expired; " + keychain.EXPIRED_TEXT
            else:
                status = "ok" + (f", expires in {formats.countdown((exp - now).total_seconds())}" if exp else "")
                chosen = service
            rows.append((service, status))
            break
        if r.status == "denied":
            status += f" ({r.detail})"
            failure = failure or f"the Keychain refused ({r.detail})"
        rows.append((service, status + (" [legacy unsuffixed item]" if service == keychain.LEGACY_SERVICE else "")))
    width = max(len(s) for s, _ in rows)
    for service, status in rows:
        mark = "→" if service == chosen else " "
        lines.append(f"    {mark} {service:<{width}}  {status}")
    tried = {s for s, _ in rows}
    for service in services:
        if service not in tried:
            lines.append(f"      {service:<{width}}  not tried (an earlier item answered)")
    lines.append(f"  chosen: {chosen or 'none'}")
    if chosen == keychain.LEGACY_SERVICE:
        lines.append("  ⚠ using the legacy unsuffixed item, which names no account; its readings must pass both identity checks")
    if chosen is None and failure is None:
        failure = f"no claude.ai login found; {LOGIN_FIX}"
    return lines, chosen, failure


def _credentials_file(key: str) -> (List[str], Optional[str], Optional[str]):
    f = Path(key) / ".credentials.json"
    if f.is_file():
        return [f"  login file: {f} present"], str(f), None
    return [f"  login file: {f} missing"], None, f"no login file; {LOGIN_FIX}"


def run(state, env: Mapping[str, str], *, store: LiveStore, now: Optional[datetime] = None,
        probe: Optional[Callable] = None) -> List[str]:
    state = Path(state)
    now = now or timeutil.now()
    probe = probe or keychain.probe
    cfg = ConfigStore.load(state)
    keys = [accounts.folder_key(p) for p in accounts.discover_folders(env, cfg)]
    if not keys:
        return [f"FAIL accounts: no Claude Code config folder found (looked for ~/.claude* in {env.get('HOME', '~')})"]
    keys.sort(key=lambda k: (_number(cfg, k) is None, _number(cfg, k) or 0, k))
    live = store.load(state, keys)
    out: List[str] = []
    verdicts: List[str] = []
    for key in keys:
        n = _number(cfg, key)
        name = f"#{n} {key}" if n is not None else key
        ident = accounts.read_identity(key, cfg)
        out.append("")
        out.append(f"{name}  ({ident.email + ', uuid ' + ident.uuid if ident else 'no oauthAccount in .claude.json'})")
        if sys.platform == "darwin":
            lines, chosen, failure = _keychain(key, cfg.spellings.get(key), env, now, probe)
        else:
            lines, chosen, failure = _credentials_file(key)
        out += lines
        warns = []
        if chosen == keychain.LEGACY_SERVICE:
            warns.append("login from the legacy unsuffixed Keychain item")

        acc = live.account(key)
        anchor = acc.reset_anchor
        if anchor["minute_of_week"] is not None:
            out.append(f"  reset fingerprint: learned {_minute(anchor['minute_of_week'])} "
                       f"({anchor['confirmations']} confirmation{'s' if anchor['confirmations'] != 1 else ''})")
        elif anchor["candidate"] is not None:
            out.append(f"  reset fingerprint: learning, {_minute(anchor['candidate'])} seen "
                       f"{anchor['candidate_count']} of 2")
        else:
            out.append("  reset fingerprint: not learned yet")

        meters = usage_client.parse(acc.data).meters if acc.data is not None else []
        d = gate.due(acc, cfg.read_every_s, now, False, meters)
        read = f"last good reading {_ago((now - acc.fetched).total_seconds())}" if acc.fetched else "never read"
        out.append(f"  gate: {'due now' if d.due else 'next read ' + timeutil.iso(d.next_at)} ({d.reason}); {read}")
        if acc.error is not None:
            out.append(f"  last error {acc.error.kind}: {acc.error.text} (at {timeutil.iso(acc.error.at)})")
            warns.append(f"last read failed ({acc.error.kind})")
        if acc.blocked_until is not None and acc.blocked_until > now:
            why = "Retry-After from Anthropic" if acc.retry_after_explicit else f"backoff step {acc.backoff_step}"
            out.append(f"  blocked until {timeutil.iso(acc.blocked_until)} ({why})")

        enabled = bool((cfg.statusline.get(key) or {}).get("enabled", False))
        try:
            cap = feed.read_capture(state, key)
        except fsutil.CorruptFile as e:
            cap, cap_text = None, f"capture unreadable ({e})"
        else:
            cap_text = "no capture yet"
        if cap is not None:
            at = timeutil.parse_iso(cap["captured_at"])
            age = (now - at).total_seconds()
            cap_text = f"last capture {_ago(age)} ({timeutil.iso(at)})"
            if enabled and age > STALE_AFTER_S:
                warns.append("status-line capture older than 15 minutes")
        elif enabled:
            warns.append("status-line backup is on but nothing was captured")
        out.append(f"  status line: {'on' if enabled else 'off'}; {cap_text}")

        if failure:
            verdicts.append(f"FAIL {name}: {failure}")
        elif warns:
            verdicts.append(f"WARN {name}: " + "; ".join(warns))
        else:
            verdicts.append(f"OK {name}: login, identity and gate are healthy")
    out.append("")
    return out + verdicts
