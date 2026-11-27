"""D1: the dashboard page. One self-contained HTML file (a file:// page loads nothing else): the
saved theme applied in <head> before any colour is drawn (no flash), theme CSS from themes.json,
the sections, the data block, the house web modules and the page script. The template is filled
by named placeholders from a closed set; nothing is %-formatted (R-128). Never touches the DB."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List

from ju import fsutil, share, timeutil
from ju.constants import HERE
from ju.dash import sections, theme_css
from ju.themes import load_themes

STATIC = Path(__file__).resolve().parent / "static"
THEMES_PATH = HERE.parent / "themes.json"
PLACEHOLDERS = ("csp", "head_theme", "house_css", "theme_css", "dash_css", "title", "body", "drawer", "data",
                "house_js", "dash_js")
HOUSE_CSS = ("glow.css", "accent.css", "quickkeys.css", "report.css")
HOUSE_JS = ("house-config.js", "errlog.js", "accent.js", "quickkeys.js", "report.js", "tips.js", "version.js")
THEME_KEY = "jusage.theme"
_SLOT = re.compile(r"\{\{(\w+)\}\}")


def template_text() -> str:
    return (STATIC / "page.html").read_text(encoding="utf-8")


def fill(template: str, values: dict) -> str:
    """Replace every {{name}} in one pass. A name in the template with no value, or a value with no
    place in the template, is a KeyError at build. Filled text is never re-scanned."""
    names = set(_SLOT.findall(template))
    if names != set(values):
        raise KeyError(f"template placeholders {sorted(names)} != values {sorted(values)}")
    return _SLOT.sub(lambda m: values[m.group(1)], template)


def _json_for_script(obj) -> str:
    """JSON inside a <script> element: `<` escaped so no text can close the element (R-128)."""
    return json.dumps(obj, sort_keys=True).replace("<", "\\u003c")


def _head_theme(themes: dict) -> str:
    ids = [t for t in themes["order"] if t != "system"]
    return ("(function(){var ok=" + _json_for_script(ids) + ";try{var t=localStorage.getItem('" + THEME_KEY + "');"
            "if(t&&ok.indexOf(t)>=0)document.documentElement.setAttribute('data-theme',t)}catch(e){}})();")


def _theme_list(themes: dict) -> list:
    out = []
    for tid in themes["order"]:
        t = themes["themes"][tid]
        if tid == "system":
            light, dark = themes["themes"][t["light"]], themes["themes"][t["dark"]]
            out.append({"id": tid, "name": t["name"], "bg": [light["bg"], dark["bg"]], "dot": light["ok"]})
        else:
            out.append({"id": tid, "name": t["name"], "bg": [t["bg"]], "dot": t["ok"]})
    return out


SCRIPT_SLOTS = ("head_theme", "house_js", "dash_js")      # the page's only executable scripts


def csp(values: dict) -> str:
    """The page's Content-Security-Policy. It needs no network (a file:// page, errUrl empty), so
    nothing loads or leaves; scripts run only when their exact text hashes to one of the page's own
    three, so a handler or script that report data smuggled in does nothing (review-security 1)."""
    hashes = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(values[k].encode("utf-8")).digest()).decode()
                      for k in SCRIPT_SLOTS)
    return (f"default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; img-src data:; "
            "base-uri 'none'; form-action 'none'")


def _read(names) -> str:
    return "\n".join((STATIC / "house" / n).read_text(encoding="utf-8") for n in names)


def render(reports: List[share.Report], notes: List[str], version: str, now: datetime, rules: dict) -> str:
    rules["stale_after_s"]                              # required by the page script; KeyError names it
    themes = load_themes(THEMES_PATH)
    model = sections.DashModel.build(reports, now, rules, notes)
    newest = max((r.generated for r in reports), default=None)
    data_time = timeutil.iso(newest) if newest else None
    title = f"Claude usage · jusage {sections.esc(version)} · "
    title += f"data as of {sections.clock_html(data_time, now)}" if data_time else "no reports yet"
    body = "\n".join([sections.plan_meters(model), sections.today_panels(model), sections.everyone(model),
                      sections.charts(model), sections.models_table(model), sections.month_tables(model),
                      sections.footnote(model)])
    data = {"version": version, "generated": data_time, "rules": rules, "themes": _theme_list(themes),
            "tips": {c["cid"]: {"series": [[k, lab] for k, lab in c["series"]],
                                "days": {d: {k: int(v) for k, v in vals.items()} for d, vals in c["values"].items()}}
                     for c in model.charts}}
    values = {
        "head_theme": _head_theme(themes),
        "house_css": _read(HOUSE_CSS),
        "theme_css": theme_css.css(themes),
        "dash_css": (STATIC / "dash.css").read_text(encoding="utf-8"),
        "title": title,
        "body": body,
        "drawer": sections.drawer(model),
        "data": _json_for_script(data),
        "house_js": _read(HOUSE_JS),
        "dash_js": (STATIC / "dash.js").read_text(encoding="utf-8"),
    }
    values["csp"] = csp(values)
    return fill(template_text(), values)


def write(path, html_text: str) -> None:
    """Atomic, 0600 (E3)."""
    fsutil.write_private(Path(path), html_text.encode("utf-8"))
