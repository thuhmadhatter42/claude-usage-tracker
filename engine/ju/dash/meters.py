"""D3: one plan meter, the app's three meanings on 24 segments: lit = used, dim = the forecast at
the reset, the white line = share of the period gone. Every lit segment takes the pace state's
colour (R-125); position never colours. The text here is the first paint only: the page script
(D6) rewrites countdowns, clocks and greying on the reader's clock from the data attributes."""
from __future__ import annotations

import html

from ju import formats
from ju.timeutil import parse_iso

SEGS = 24


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def _segments(pct: float) -> int:
    return int(min(max(pct, 0.0), 100.0) * SEGS / 100 + 0.5)


def split_text(split) -> str:
    """'Split: Sam 71% ($412) · Lee 29% ($168)', shares of API-rate spend in the period (E20)."""
    parts = [f"{p['user']} {int(p['share'] * 100 + 0.5)}% ({formats.usd(p['usd'])})" for p in split["parts"]]
    return "Split: " + " · ".join(parts)


def meter_html(meter_block: dict, now, rules: dict) -> str:
    """`meter_block` = one feed §1.7 meter. `now` = the data time the page is built for; `rules` =
    the feed's rules (stale_after_s is required)."""
    stale_after = rules["stale_after_s"]
    m, pace = meter_block, meter_block["pace"]
    pct = float(m["pct"])
    read_at = parse_iso(m["read_at"])
    resets_at = parse_iso(m["resets_at"]) if m.get("resets_at") else None
    reset_due = bool(pace["reset_due"]) or (resets_at is not None and resets_at <= now)
    stale = (now - read_at).total_seconds() >= stale_after
    state = None if reset_due else pace["state"]

    lit = _segments(pct)
    land = lit
    if pace["forecast"] and pace.get("landing") is not None and not reset_due:
        land = max(lit, _segments(float(pace["landing"])))
    segs = "".join('<i class="%s"></i>' % ("u" if i < lit else "f" if i < land else "") for i in range(SEGS))
    tick = "" if reset_due else '<span class="tick" style="left:%.1f%%"></span>' % (min(max(pace["e"], 0.0), 1.0) * 100)

    if reset_due:
        rt = formats.RESET_DUE_TEXT
    elif resets_at is not None:
        rt = "resets in " + formats.countdown((resets_at - now).total_seconds())
    else:
        rt = ""
    right = [f'<span class="rt">{esc(rt)}</span>']

    attrs = [f'data-read-at="{esc(m["read_at"])}"']
    if m.get("resets_at"):
        attrs.append(f'data-reset="{esc(m["resets_at"])}"')
    attrs.append(f'data-state="{state or "none"}"')
    if not reset_due:
        words = pace["words"]
        if pace.get("runs_out") and "{runs_out}" in words:
            attrs.append(f'data-runs-out="{esc(pace["runs_out"])}" data-words="{esc(words)}"')
            words = words.replace("{runs_out}", "in " + formats.countdown((parse_iso(pace["runs_out"]) - now).total_seconds()))
        right.append(f'<span class="pw">{esc(words)}</span>')
    if m.get("split"):
        right.append(f'<span class="sp" title="{esc(formats.API_RATE_LABEL)}">{esc(split_text(m["split"]))}</span>')

    cls = "meter" + (" stale" if stale else "") + (" reset-due" if reset_due else "")
    return (f'<div class="{cls}" {" ".join(attrs)}><div class="n"><b class="pct">{pct:.0f}%</b>{esc(m["name"])}</div>'
            f'<div class="bar">{segs}{tick}</div><div class="r">{"<br>".join(right)}</div></div>')
