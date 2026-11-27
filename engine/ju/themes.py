"""E27: themes.json validation. A bad file is a bring-up refusal that names the theme and key."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ju.contracts import JusageError

REQUIRED_KEYS = ("scheme", "bg", "sf", "sf2", "ink", "ink2", "mut", "line", "ok", "warn", "hot", "accent")
_COLOUR_KEYS = ("bg", "sf", "sf2", "ink", "ink2", "mut", "line", "ok", "warn", "hot", "accent")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ThemesInvalid(JusageError):
    """The message already starts with the file name: `themes.json: theme 'mono' lacks 'warn'`."""


def _bad(problem: str) -> ThemesInvalid:
    return ThemesInvalid(f"themes.json: {problem}")


def load_themes(path) -> dict:
    """Read and validate; returns the parsed object (`order` and `themes`)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _bad("file is missing") from None
    except (OSError, UnicodeDecodeError) as e:
        raise _bad(f"cannot be read ({e})") from None
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise _bad(f"is not valid JSON ({e})") from None
    if not isinstance(data, dict):
        raise _bad("top level must be an object")
    order = data.get("order")
    if not isinstance(order, list) or not order or not all(isinstance(i, str) for i in order):
        raise _bad("'order' must be a non-empty list of theme ids")
    themes = data.get("themes")
    if not isinstance(themes, dict):
        raise _bad("'themes' must be an object")
    for tid in order:
        if tid not in themes:
            raise _bad(f"order lists {tid!r} but there is no such theme")
    if "system" not in themes:
        raise _bad("theme 'system' is missing")
    for tid in list(order) + [t for t in themes if t not in order]:
        theme = themes[tid]
        if not isinstance(theme, dict):
            raise _bad(f"theme {tid!r} must be an object")
        if tid == "system":
            continue
        for key in REQUIRED_KEYS:
            if key not in theme:
                raise _bad(f"theme {tid!r} lacks {key!r}")
        if theme["scheme"] not in ("light", "dark"):
            raise _bad(f"theme {tid!r} scheme must be 'light' or 'dark', not {theme['scheme']!r}")
        for key in list(_COLOUR_KEYS) + [k for k in ("grid", "seg", "sel") if k in theme]:
            value = theme[key]
            if not (isinstance(value, str) and _HEX.match(value)):
                raise _bad(f"theme {tid!r} key {key!r} is not #RRGGBB: {value!r}")
    return data
