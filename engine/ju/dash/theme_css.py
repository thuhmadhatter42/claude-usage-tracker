"""D5: theme colour tokens, generated from themes.json. The stylesheet (static/dash.css) holds no
colour of its own; every colour on the page is one of these tokens."""
from __future__ import annotations

from ju.themes import ThemesInvalid

# Chart palette (dataviz reference instance), fixed slot order, (light, dark). Slot colours stay
# fixed because charts fold their tail into "other" (R-130).
PAL = (("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
       ("#e87ba4", "#d55181"), ("#008300", "#3fa83f"), ("#4a3aa7", "#9085e9"), ("#e34948", "#e66767"))

# Keys of a theme that are not colour tokens.
_NOT_TOKENS = ("name", "scheme", "accent")


def _tokens(theme: dict) -> str:
    """`color-scheme` plus `--<key>:<value>;` for every colour key, then the chart palette for its
    scheme. The accent is the house accent (accent.js), not a theme colour (R-159)."""
    slot = 0 if theme["scheme"] == "light" else 1
    body = "".join(f"--{k}:{v};" for k, v in theme.items() if k not in _NOT_TOKENS)
    pal = "".join(f"--s{i}:{pair[slot]};" for i, pair in enumerate(PAL))
    return f"color-scheme:{theme['scheme']};{body}{pal}"


def css(themes: dict) -> str:
    """`themes` = the validated themes.json (E27). System follows the Mac: its `light` theme on
    plain :root, its `dark` theme under prefers-color-scheme: dark while no theme is picked."""
    all_themes = themes["themes"]
    system = all_themes["system"]
    for side in ("light", "dark"):
        tid = system.get(side)
        if tid not in all_themes or tid == "system" or all_themes[tid].get("scheme") != side:
            raise ThemesInvalid(f"themes.json: theme 'system' {side} must name a {side} theme, not {tid!r}")
    out = [":root{" + _tokens(all_themes[system["light"]]) + "}",
           "@media (prefers-color-scheme: dark){:root:not([data-theme]){"
           + _tokens(all_themes[system["dark"]]) + "}}"]
    for tid in themes["order"]:
        if tid != "system":
            out.append(f':root[data-theme="{tid}"]{{' + _tokens(all_themes[tid]) + "}")
    return "\n".join(out)
