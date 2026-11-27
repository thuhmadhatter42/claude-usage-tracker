"""D4: stacked day charts as inline SVG."""
from __future__ import annotations

import html
import math
from datetime import date

from ju import formats

_STEPS = (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10)


def nice_ceiling(v: float) -> float:
    """Smallest round number >= v * 1.04, so the tallest bar nearly touches the top line. Right for
    values below 1 as well (the magnitude comes from log10, not from the integer part)."""
    target = v * 1.04
    if target <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(target))
    for m in _STEPS:
        c = round(mag * m, 12)
        if c >= target:
            return c
    return round(mag * 10, 12)


def _axis(v: float) -> str:
    return formats.tokens(round(v)) if v >= 1 or v == 0 else f"{v:g}"


def stacked_svg(days, series, values, cid) -> str:
    """`days` ISO dates in order, `series` [(key, label)], `values` {day: {key: number}}. A series
    with nothing in a day draws nothing there; colours are slot-fixed `var(--s<i>)`."""
    W, H, L, B, T = 380, 190, 48, 22, 8
    n = max(len(days), 1)
    slot = (W - L - 6) / n
    bw = max(slot * 0.62, 3)
    top = max((sum(values.get(d, {}).get(k, 0) for k, _ in series) for d in days), default=0)
    ymax = nice_ceiling(top)

    def y(v):
        return T + (H - T - B) * (1 - v / ymax)

    cid_attr = html.escape(cid, quote=True)
    out = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='{cid_attr}' data-chart='{cid_attr}'>"]
    for i in range(5):
        v = ymax * i / 4
        out.append(f"<line x1='{L}' x2='{W - 2}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='var(--grid)' stroke-width='1'/>"
                   f"<text x='{L - 6}' y='{y(v) + 3.5:.1f}' font-size='10.5' text-anchor='end' fill='var(--mut)'>{_axis(v)}</text>")
    for i, d in enumerate(days):
        x = L + i * slot + (slot - bw) / 2
        acc, segs = 0, []
        for si, (k, _lab) in enumerate(series):
            v = values.get(d, {}).get(k, 0)
            if v <= 0:
                continue
            y0, y1 = y(acc + v), y(acc)
            h = max(y1 - y0 - 1.5, 0.6)
            segs.append(f"<rect x='{x:.1f}' y='{y0:.1f}' width='{bw:.1f}' height='{h:.1f}' fill='var(--s{si})' rx='1'/>")
            acc += v
        dd = date.fromisoformat(d)
        label = dd.strftime("%a")[:2] if n <= 14 else (str(dd.day) if dd.day in (1, 10, 20) or i == n - 1 else "")
        out.append(f"<g class='bar-hit' data-day='{d}'><rect class='hl' x='{L + i * slot:.1f}' y='{T}' "
                   f"width='{slot:.1f}' height='{H - T - B}' fill='transparent' rx='3'/>" + "".join(segs)
                   + f"<text x='{L + i * slot + slot / 2:.1f}' y='{H - 7}' font-size='10.5' text-anchor='middle' fill='var(--mut)'>{label}</text></g>")
    out.append("</svg>")
    return "".join(out)
