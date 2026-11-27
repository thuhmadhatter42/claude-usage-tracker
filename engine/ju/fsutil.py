"""E3: private files. Every file jusage writes is 0600 in a 0700 folder, replaced atomically; a
file that will not parse is moved aside, never overwritten, never silently dropped."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ju.contracts import JusageError


class CorruptFile(JusageError):
    """A file that exists but is not the JSON object we wrote."""

    def __init__(self, path, why: str = ""):
        super().__init__(f"{path}: unreadable{(' (' + why + ')') if why else ''}")
        self.path = str(path)


def ensure_state_dir(p) -> Path:
    """Create the folder (and parents) as 0700, or tighten an existing one to 0700."""
    p = Path(p)
    if p.exists() and not p.is_dir():
        raise NotADirectoryError(f"{p} is not a folder")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def write_private(path, data: bytes) -> None:
    """Replace `path` with `data`: temp file in the same folder (0600), fsync, rename, fsync the
    folder. A failure leaves the old file untouched and no temp file behind."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("write_private takes bytes")
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                fd = -1
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        finally:
            if fd != -1:
                os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def move_aside_corrupt(path) -> Path:
    """Rename `path` to `<name>.corrupt`, or `.corrupt.1`, `.corrupt.2`, ... Never overwrites an
    earlier one: the name is claimed with a hard link, which fails if it exists."""
    path = Path(path)
    if not os.path.lexists(path):
        raise FileNotFoundError(str(path))
    n = 0
    while True:
        target = path.with_name(path.name + (".corrupt" if n == 0 else f".corrupt.{n}"))
        try:
            os.link(path, target)
        except FileExistsError:
            n += 1
            continue
        os.unlink(path)
        return target


def read_json(path) -> dict:
    """The file's JSON object. Missing -> FileNotFoundError. Anything else wrong (bad bytes, bad
    JSON, not an object) -> CorruptFile."""
    raw = Path(path).read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise CorruptFile(path, str(e)) from None
    if not isinstance(value, dict):
        raise CorruptFile(path, "top level is not an object")
    return value
