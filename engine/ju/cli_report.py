"""E23: what the CLI prints. Tables of ledger rows, the 5-hour windows, the live meters (one block
per account, read from feed-shaped account blocks) and the split line. Every dollar column is
headed "$ API-rate" and every table ends with E19's label: it is never a bill (R-109).

Numbers go through E19 `formats`; this module only lays them out. The CLI runs on this Mac, so it
may print a clock time (formats.clock), which the feed itself never carries (RULING 4).
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import List, Optional, Sequence, TextIO

from ju import accounts, formats, ledger_query, timeutil

USD_HEAD = "$ API-rate"
BAR_CELLS = 20
H24 = True                       # the CLI prints a 24-hour clock
SOURCE_WORDS = {"service": "Claude usage service", "statusline": "Claude Code status line", "last_good": "last good"}


def _out(out: Optional[TextIO]) -> TextIO:
    return out if out is not None else sys.stdout


def _key(r, by: str, names: dict) -> str:
    if by == "account":
        return names[r["account"]]
    if by == "project":
        return ledger_query.project_group(r["project"])
    return r[by]


def print_table(rows: Sequence, by: str, pricing, *, title: str, out: Optional[TextIO] = None) -> None:
    """Rows grouped by day (in date order) or model / account / project (by tokens). A row group
    holding unpriced calls ends with `*`, and the footnote counts them."""
    out = _out(out)
    if by not in ("day", "model", "account", "project"):
        raise ValueError(f"cannot group by {by!r}")
    keys = sorted({r["account"] for r in rows})
    names = {k: accounts.short_name(k, keys) for k in keys}
    groups = {}
    for r in rows:
        groups.setdefault(_key(r, by, names), []).append(r)
    totals = {k: ledger_query.sum_rows(v, pricing) for k, v in groups.items()}
    items = sorted(totals.items()) if by == "day" else sorted(totals.items(), key=lambda kv: (-kv[1].tokens, kv[0]))
    width = max([28] + [len(k) for k, _ in items])
    head = (f"{by:<{width}} {'calls':>6} {'input':>8} {'cache-w':>8} {'cache-r':>8} {'output':>8} "
            f"{'total':>8} {USD_HEAD:>11}")
    print(f"\n{title}", file=out)
    print(head, file=out)
    print("-" * len(head), file=out)
    grand = ledger_query.Totals()

    def line(name, t, star):
        return (f"{name:<{width}} {t.calls:>6} {formats.tokens(t.input):>8} {formats.tokens(t.cache_5m + t.cache_1h):>8} "
                f"{formats.tokens(t.cache_read):>8} {formats.tokens(t.output):>8} {formats.tokens(t.tokens):>8} "
                f"{formats.usd(t.cost):>11}{'*' if star else ''}")

    for name, t in items:
        grand.add(t)
        print(line(name, t, t.unpriced > 0), file=out)
    print("-" * len(head), file=out)
    print(line("TOTAL", grand, False), file=out)
    if grand.unpriced:
        print(f"* {grand.unpriced} call{'s' if grand.unpriced != 1 else ''} on models missing from "
              f"pricing.json (their dollars are not in the total)", file=out)
    print(f"{USD_HEAD}: {formats.API_RATE_LABEL}.", file=out)


def print_blocks(rows: Sequence, pricing, *, last: int, now: datetime, out: Optional[TextIO] = None) -> None:
    """Each account's newest `last` 5-hour windows, the open one marked."""
    out = _out(out)
    keys = sorted({r["account"] for r in rows})
    for acct, bl in ledger_query.blocks_by_account(rows).items():
        print(f"\n5-hour windows, {accounts.short_name(acct, keys)} (last {last})", file=out)
        for b in bl[-last:]:
            t = ledger_query.sum_rows(b.rows, pricing)
            start, end = b.start.astimezone(), b.end.astimezone()
            mark = "  <- open" if now < b.end else ""
            print(f"{start:%a %m-%d %H:%M}–{end:%H:%M}  {formats.tokens(t.tokens):>8} tokens  "
                  f"{t.calls:>5} calls  {formats.usd(t.cost)}{mark}", file=out)
    print(f"\n{USD_HEAD}: {formats.API_RATE_LABEL}.", file=out)


def split_text(split: Optional[dict], missing: Sequence[str]) -> str:
    """'this period: Sam 71% ($412) · Lee 29% ($168)', then anyone whose report cannot be split."""
    bits = []
    parts = (split or {}).get("parts") or []
    if parts:
        bits.append("this period: " + " · ".join(
            f"{p['user']} {round(p['share'] * 100)}% ({formats.usd(p['usd'])})" for p in parts))
    bits += [f"{u}: not reported yet (update jusage)" for u in missing]
    return " · ".join(bits)


def print_split(split: Optional[dict], missing: Sequence[str], out: Optional[TextIO] = None) -> None:
    text = split_text(split, missing)
    if text:
        print(text, file=_out(out))


def _bar(pct: float) -> str:
    lit = max(0, min(BAR_CELLS, int(pct // (100 / BAR_CELLS))))
    return "█" * lit + "░" * (BAR_CELLS - lit)


def _fill(words: str, pace: dict, now: datetime) -> str:
    if "{runs_out}" in words:
        words = words.replace("{runs_out}", formats.clock(timeutil.parse_iso(pace["runs_out"]), now, H24))
    return words


def print_live(feed_like: dict, now: datetime, out: Optional[TextIO] = None) -> None:
    """One block per account (feed `accounts` shape): a `#N name` head with its error, then one
    line per meter with its source, then the forecast words and the split."""
    out = _out(out)
    keys = [a["key"] for a in feed_like["accounts"]]
    for a in feed_like["accounts"]:
        head = f"#{a['number']} {a.get('email') or accounts.short_name(a['key'], keys)}"
        err = a.get("error")
        if err:
            head += f"  {err['text']}"
            if err.get("until"):
                head += f" (next try {formats.clock(timeutil.parse_iso(err['until']), now, H24)})"
        print(head, file=out)
        if a.get("warning"):
            print(f"  ⚠ {a['warning']}", file=out)
        if not a["meters"]:
            print("  no reading yet", file=out)
        for m in a["meters"]:
            reset = (f"resets in {formats.countdown((timeutil.parse_iso(m['resets_at']) - now).total_seconds())}"
                     if m["resets_at"] else "no reset time")
            read = formats.clock(timeutil.parse_iso(m["read_at"]), now, H24) if m["read_at"] else "?"
            print(f"  {m['name']:<22} {_bar(m['pct'])} {m['pct']:5.1f}%   {reset}   "
                  f"({SOURCE_WORDS[m['source']]} {read}{', sources disagree' if m['disagree'] else ''})", file=out)
            print(f"  {'':<22} {_fill(m['pace']['words'], m['pace'], now)}", file=out)
            split_line = split_text(m.get("split"), [])
            if split_line:
                print(f"  {'':<22} {split_line}", file=out)
        for person in a.get("missing_people") or []:
            print(f"  {person}: not reported yet (update jusage)", file=out)
