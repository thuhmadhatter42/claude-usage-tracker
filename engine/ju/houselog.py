"""E2: the engine's error log, `<state>/logs/engine.log`, rotating at 1 MB with 3 older files.

Derived from house-kit modules/logging/cli/houselog.py: same redaction rules and local-time-with-
offset stamps, with the engine additions (one flat line per entry, token-shaped strings and
"accessToken" values redacted, a log call never raises). Safe from many short-lived processes.

    houselog.configure(state)               # once, at start (tests and E24)
    houselog.log("error", "scan: bad file", path=p)
    houselog.log_path()
"""
from __future__ import annotations

import fcntl
import os
import re
import time
from pathlib import Path
from typing import Optional

from ju import fsutil

MAX_BYTES = 1024 * 1024
BACKUPS = 3
_LEVELS = {"debug": "DEBUG", "info": "INFO", "warning": "WARNING", "warn": "WARNING", "error": "ERROR"}

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-***"),
    (re.compile(r"am_[a-z]{2}_[A-Za-z0-9_\-]{8,}"), "am_***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]{8,}"), r"\1***"),
    (re.compile(r"""(?i)(accessToken|refreshToken|idToken)(["']?\s*[:=]\s*["']?)[^"'\s,&}]+"""), r"\1\2***"),
    (re.compile(r"""(?i)((?:access|refresh|id)_token|client_secret|secret|password|api[_-]?key)(["']?\s*[:=]\s*["']?)[^"'\s,&}]+"""), r"\1\2***"),
    (re.compile(r"""(?i)(x-api-key|authorization|cookie)(["']?\s*[:=]\s*["']?)[^"'\s,}]+"""), r"\1\2***"),
    # any remaining base64url-shaped run of 40+ characters is treated as a secret
    (re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9_\-])"), "***"),
]

_state: Optional[Path] = None


def redact(text: str) -> str:
    """Strip tokens, auth headers, cookies and passwords from anything that gets logged."""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def configure(state) -> None:
    global _state
    _state = Path(state)


def reset() -> None:
    global _state
    _state = None


def _state_dir(state=None) -> Path:
    if state is not None:
        return Path(state)
    if _state is not None:
        return _state
    return Path(os.path.expanduser("~")) / ".claude-usage"


def log_path(state=None) -> Path:
    return _state_dir(state) / "logs" / "engine.log"


def _stamp() -> str:
    t = time.localtime()
    off = time.strftime("%z", t)
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + off[:3] + ":" + off[3:]


def _flat(value) -> str:
    try:
        text = str(value)
    except Exception:
        text = "<unprintable>"
    return redact(text.replace("\r", "\\r").replace("\n", "\\n"))


def _pair(key, value) -> str:
    text = _flat(value)
    if not text or any(c in text for c in ' "='):
        text = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{key}={text}"


def _rotate(path: Path) -> None:
    oldest = path.with_name(f"{path.name}.{BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for i in range(BACKUPS - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))


def log(level: str, msg: str, *, state=None, **kv) -> bool:
    """Append one line. Returns True when it was written. Never raises: a log that cannot be
    written must not take a run down with it (the caller's own error path still reports)."""
    try:
        path = log_path(state)
        fsutil.ensure_state_dir(_state_dir(state))
        fsutil.ensure_state_dir(path.parent)
        line = " ".join(
            [_stamp(), _LEVELS.get(str(level).lower(), "INFO"), _flat(msg)] + [_pair(k, v) for k, v in kv.items()]
        ) + "\n"
        data = line.encode("utf-8", "replace")
        lock_fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size and size + len(data) > MAX_BYTES:
                _rotate(path)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, data)
            finally:
                os.close(fd)
        finally:
            os.close(lock_fd)
        return True
    except Exception:
        return False
