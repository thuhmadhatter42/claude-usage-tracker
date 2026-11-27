"""D2: the page's sections. `DashModel.build` does the aggregation once; each section is one
function `(model) -> str`, testable alone (R-136).

Input: `share.Report` objects exactly as `share.load_reports` returns them (E20), and the people
are E20's `share.people()` (RULING 35: one owner of "one person"; D2 only lays them out):
  user, host, install_id, generated (aware datetime), legacy (no account ids: counted, never split),
  accounts [{key_hash, number, email, uuid}], live {uuid: feed §1.7 account block},
  days {YYYY-MM-DD: {input, cache_5m, cache_1h, cache_read, output, calls, usd, unpriced,
        models {model id: {<COLS>, tokens, calls, usd}}, accounts {uuid: {tokens, usd}},
        projects {name: {tokens, usd}}}},
  blocks {uuid: [{start, end, tokens, calls, usd}]}.
Every time is compared as an instant (R-116); every number that can age carries `data-read-at` so the
page script greys it on the reader's clock (R-52, R-121)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from ju import formats, share, timeutil
from ju.constants import COLS
from ju.dash import meters as meters_mod
from ju.dash.meters import esc
from ju.dash import svg
from ju.ledger_query import project_group
from ju.timeutil import parse_iso

TYPES = (("input", "input"), ("cache_w", "cache write"), ("cache_read", "cache read"), ("output", "output"))
TOP_PROJECTS = 7
TOP_MODELS = 8
LEGACY_TEXT = "not reported yet (update jusage)"
USD_HEAD = "$ API-rate"
DOT = " \u00b7 "


def clock_html(iso_text: str, now: datetime) -> str:
    """A clock label: first paint in 24-hour on this Mac; the page script rewrites it to the
    reader's 12/24-hour setting (R-112)."""
    return (f'<time data-clock="{esc(iso_text)}" datetime="{esc(iso_text)}">'
            f'{esc(formats.clock(parse_iso(iso_text), now, True))}</time>')


def _day_tokens(a: dict) -> int:
    return sum(a.get(k, 0) for k in COLS)


def _api(v: float) -> str:
    return f'<span title="{esc(formats.API_RATE_LABEL)}">{formats.usd(v)}</span>'


@dataclass
class Person:
    """How the page lays out one of E20's people: a stable key, a colour slot, the day parts."""
    of: share.Person
    slot: int = 0

    @property
    def pid(self) -> str:         # the first install id, sorted: the person's key on the page
        return self.of.install_ids[0]

    @property
    def user(self) -> str:
        return self.of.user

    @property
    def reports(self) -> List[share.Report]:
        return self.of.reports

    @property
    def hosts(self) -> List[str]:
        return sorted({r.host for r in self.of.reports})

    @property
    def generated(self) -> str:   # newest report time, ISO
        return timeutil.iso(self.of.generated)

    def days(self) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = defaultdict(list)
        for r in self.of.reports:
            for d, a in r.days.items():
                out[d].append(a)
        return out


@dataclass
class Group:
    key: str                      # acct:<uuid>, or acct:legacy-<install id>-<n>
    label: str
    legacy: bool
    users: List[str] = field(default_factory=list)
    best: Optional[tuple] = None  # (rank, user, host, block)


@dataclass
class DashModel:
    now: datetime
    rules: dict
    notes: List[str]
    today: date
    people: List[Person]
    groups: List[Group]
    charts: List[dict]
    models30: List[tuple]          # (model id, totals dict)
    months: List[dict]
    unpriced_month: int
    emails: Dict[str, str]         # uuid -> email

    @property
    def multi(self) -> bool:
        return len(self.people) > 1

    def stale(self, iso_text: str) -> bool:
        return (self.now - parse_iso(iso_text)).total_seconds() >= self.rules["stale_after_s"]

    @classmethod
    def build(cls, reports: List[share.Report], now: datetime, rules: dict, notes: List[str]) -> "DashModel":
        rules["stale_after_s"]                          # required; a KeyError names it
        today = now.date()
        emails = {}
        for r in reports:
            for a in r.accounts:
                if a["email"]:
                    emails[a["uuid"]] = a["email"]
        people = sorted((Person(p) for p in share.people(reports, now)), key=lambda p: (p.user.lower(), p.pid))
        for i, p in enumerate(people):
            p.slot = i % 8
        groups = _groups(reports, now, rules)
        charts, models30, months, unpriced = _aggregate(people, today, emails)
        return cls(now=now, rules=rules, notes=list(notes), today=today, people=people, groups=groups,
                   charts=charts, models30=models30, months=months, unpriced_month=unpriced, emails=emails)


def _newest_read(block: dict) -> Optional[datetime]:
    times = [parse_iso(m["read_at"]) for m in block.get("meters") or []]
    return max(times) if times else None


def _groups(reports: List[share.Report], now: datetime, rules: dict) -> List[Group]:
    """Plan meters belong to an account, not to a person or a Mac: group by uuid and show the
    freshest usable reading once. A fresh reading always outranks a stale one; among equals the
    newest instant wins (R-127)."""
    stale_after = rules["stale_after_s"]
    groups: Dict[str, Group] = {}
    legacy_n = 0
    for r in reports:
        if r.legacy:              # no account ids, so its accounts cannot be named or matched: one row
            legacy_n += 1
            groups[f"legacy{legacy_n}"] = Group(key=f"acct:legacy-{r.key}-{legacy_n}",
                                                label=f"{r.user}'s accounts ({r.host})", legacy=True, users=[r.user])
            continue
        for uuid, block in r.live.items():
            g = groups.setdefault(uuid, Group(key=f"acct:{uuid}", label=block.get("email") or uuid, legacy=False))
            g.users.append(r.user)
            at = _newest_read(block)
            fresh = at is not None and (now - at).total_seconds() < stale_after
            rank = (fresh, at.timestamp() if at else float("-inf"))
            if g.best is None or rank > g.best[0]:
                g.best = (rank, r.user, r.host, block)
    return sorted(groups.values(), key=lambda g: (g.legacy, g.label.lower()))


def _aggregate(people: List[Person], today: date, emails: Dict[str, str]):
    lo7 = (today - timedelta(days=6)).isoformat()
    lo30 = (today - timedelta(days=29)).isoformat()
    month_key = today.isoformat()[:7]
    multi = len(people) > 1
    day_type = defaultdict(lambda: defaultdict(float))
    day_model = defaultdict(lambda: defaultdict(float))
    day_person = defaultdict(lambda: defaultdict(float))
    day_acct = defaultdict(lambda: defaultdict(float))
    day_proj = defaultdict(lambda: defaultdict(float))
    day_cost = defaultdict(float)
    model30 = defaultdict(lambda: defaultdict(float))
    unpriced = 0
    for p in people:
        for d, parts in p.days().items():
            for a in parts:
                day_type[d]["input"] += a.get("input", 0)
                day_type[d]["cache_w"] += a.get("cache_5m", 0) + a.get("cache_1h", 0)
                day_type[d]["cache_read"] += a.get("cache_read", 0)
                day_type[d]["output"] += a.get("output", 0)
                day_person[d][p.pid] += _day_tokens(a)
                day_cost[d] += a.get("usd", 0)
                if d[:7] == month_key:
                    unpriced += a.get("unpriced", 0)
                for acct, x in (a.get("accounts") or {}).items():
                    day_acct[d][f"{p.user} · {emails.get(acct, acct)}"] += x.get("tokens", 0)
                for pj, x in (a.get("projects") or {}).items():
                    name = project_group(pj)
                    day_proj[d][f"{p.user} · {name}" if multi else name] += x.get("tokens", 0)
                for mid, x in (a.get("models") or {}).items():
                    day_model[d][mid] += x.get("tokens", 0)
                    if d >= lo30:
                        for k in COLS:
                            model30[mid][k] += x.get(k, 0)
                        for k in ("tokens", "calls", "usd"):
                            model30[mid][k] += x.get(k, 0)

    days7 = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    days30 = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]

    def ranked(table, days):
        keys = {k for d in days for k, v in table[d].items() if v > 0}
        return sorted(keys, key=lambda k: (-sum(table[d].get(k, 0) for d in days), k))

    charts = []
    models7 = ranked(day_model, days7)[:TOP_MODELS]       # legend from the chart's own window (R-130)
    charts.append(_chart("By model, 7 days", "model", days7, [(m, formats.model_name(m)) for m in models7], day_model))
    charts.append(_chart("By type, 7 days", "type", days7, list(TYPES), day_type))
    projs = ranked(day_proj, days7)
    if projs:
        top = projs[:TOP_PROJECTS]
        folded = {}
        for d in days7:
            row = {k: day_proj[d].get(k, 0) for k in top}
            other = sum(v for k, v in day_proj[d].items() if k not in top)
            if other:
                row["other"] = other
            folded[d] = row
        series = [(k, k) for k in top] + ([("other", "other")] if len(projs) > TOP_PROJECTS else [])
        charts.append(_chart("By project, 7 days", "proj", days7, series, folded))
    accts = ranked(day_acct, days30)
    if len(accts) > 1:
        charts.append(_chart("By account, 30 days", "acct", days30, [(k, k) for k in accts[:8]], day_acct))
    if multi:
        charts.append(_chart("By person, 30 days", "person", days30, [(p.pid, p.user) for p in people], day_person))

    models30 = sorted(model30.items(), key=lambda kv: (-kv[1]["tokens"], kv[0]))[:TOP_MODELS]

    months = []
    all_days = sorted({d for p in people for d in p.days()}, reverse=True)
    by_month = defaultdict(list)
    for d in all_days:
        by_month[d[:7]].append(d)
    for ym in sorted(by_month, reverse=True)[:4]:
        months.append({"key": ym, "days": [(d, dict(day_type[d]), day_cost[d], dict(day_person[d])) for d in by_month[ym]]})
    return charts, models30, months, unpriced


def _chart(title, cid, days, series, table) -> dict:
    return {"title": title, "cid": cid, "days": days, "series": series,
            "values": {d: {k: table[d].get(k, 0) for k, _ in series} for d in days}}


# ---------------------------------------------------------------- sections

def today_panels(model: DashModel) -> str:
    if not model.people:
        return "<section class='panel today'><div class='who'><b>No reports yet</b></div>" \
               "<div class='sub'>Run <code>jusage share</code> on each Mac to fill this page.</div></section>"
    out = []
    tday = model.today.isoformat()
    lo7 = (model.today - timedelta(days=6)).isoformat()
    lo30 = (model.today - timedelta(days=29)).isoformat()
    for p in model.people:
        days = p.days()
        today_parts = days.get(tday, [])
        t1 = sum(_day_tokens(a) for a in today_parts)
        c1 = sum(a.get("usd", 0) for a in today_parts)
        calls = sum(a.get("calls", 0) for a in today_parts)

        def window(lo):
            parts = [a for d, ps in days.items() if d >= lo for a in ps]
            return sum(_day_tokens(a) for a in parts), sum(a.get("usd", 0) for a in parts)

        t7, c7 = window(lo7)
        t30, c30 = window(lo30)
        stats = [f"<div class='stat'><div class='v'>{formats.tokens(t1)}<small>tokens</small></div><div class='l'>today</div></div>",
                 f"<div class='stat'><div class='v'>{_api(c1)}</div><div class='l'>API-rate</div></div>",
                 f"<div class='stat sm'><div class='v'>{calls:,}</div><div class='l'>calls</div></div>"]
        for r in p.reports:
            if r.legacy:
                continue
            for uuid, bl in sorted(r.blocks.items()):
                if bl and parse_iso(bl[-1]["end"]) > model.now:
                    tag = f"{model.emails.get(uuid, uuid)} · " if len(r.blocks) > 1 else ""
                    stats.append(f"<div class='stat sm' data-k='acct:{esc(uuid)}'><div class='v'>{formats.tokens(bl[-1]['tokens'])}</div>"
                                 f"<div class='l'>{esc(tag)}5-hour window, since {clock_html(bl[-1]['start'], model.now)}</div></div>")
        models_today = defaultdict(float)
        accts_today = defaultdict(float)
        for a in today_parts:
            for mid, x in (a.get("models") or {}).items():
                models_today[mid] += x.get("tokens", 0)
            for acct, x in (a.get("accounts") or {}).items():
                accts_today[acct] += x.get("tokens", 0)
        if models_today:
            top = max(sorted(models_today), key=lambda k: models_today[k])
            stats.append(f"<div class='stat sm'><div class='v'>{esc(formats.model_name(top))}</div><div class='l'>busiest model today</div></div>")
        if len(accts_today) > 1:
            for acct in sorted(accts_today):
                stats.append(f"<div class='stat sm' data-k='acct:{esc(acct)}'><div class='v'>{formats.tokens(int(accts_today[acct]))}</div>"
                             f"<div class='l'>{esc(model.emails.get(acct, acct))}</div></div>")
        strip = (f"<div class='stat'><div class='v'>{formats.tokens(t7)}</div><div class='l'>7 days · {_api(c7)}</div></div>"
                 f"<div class='stat'><div class='v'>{formats.tokens(t30)}</div><div class='l'>30 days · {_api(c30)}</div></div>")
        logged = sorted({a["email"] for r in p.reports for a in r.accounts if a["email"]})
        sub = "logged into: " + (", ".join(logged) if logged else "no account seen yet")
        cls = "panel today person" + (" stale" if model.stale(p.generated) else "")
        out.append(f"<section class='{cls}' data-k='person:{esc(p.pid)}' data-read-at=\"{esc(p.generated)}\" "
                   f"style='border-top:3px solid var(--s{p.slot})'>"
                   f"<div class='who'><b>{esc(p.user)}</b><span>{esc(', '.join(p.hosts))} · report {clock_html(p.generated, model.now)}</span></div>"
                   f"<div class='sub'>{esc(sub)}</div><div class='stats'>{''.join(stats)}</div><div class='strip'>{strip}</div></section>")
    return "".join(out)


def everyone(model: DashModel) -> str:
    if not model.multi:
        return ""
    lo30 = (model.today - timedelta(days=29)).isoformat()
    per = [(p, sum(_day_tokens(a) for d, ps in p.days().items() if d >= lo30 for a in ps)) for p in model.people]
    total = sum(t for _, t in per)
    shares = "".join(f"<div class='stat sm' data-k='person:{esc(p.pid)}'><div class='v'>{(t / total * 100 if total else 0):.0f}%</div>"
                     f"<div class='l'>{esc(p.user)}</div></div>" for p, t in per)
    return (f"<section class='panel today' data-k='sec:everyone'><div class='who'><b>everyone</b><span>30 days</span></div>"
            f"<div class='stats'><div class='stat'><div class='v'>{formats.tokens(total)}</div><div class='l'>tokens, all of us</div></div>{shares}</div></section>")


def _pct(v) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _session_share(ss: dict, model: DashModel) -> str:
    cls = "ss" + (" stale" if model.stale(ss["as_of"]) else "")
    med = f"<span> · median of the last 7: {_pct(ss['median7'])}</span>" if ss.get("median7") is not None else ""
    return (f"<div class='{cls}' data-read-at=\"{esc(ss['as_of'])}\">This session: {_pct(ss['current'])} of the week"
            f" · last: {_pct(ss['last'])}{med}</div>")


def plan_meters(model: DashModel) -> str:
    if not model.groups:
        body = "<div class='sub'>No account readings yet. Run <code>jusage live</code> once to light these up.</div>"
    else:
        blocks = []
        for g in model.groups:
            if g.legacy:
                blocks.append(f"<div class='acct legacy' data-k='{esc(g.key)}'><div class='h'><b>{esc(g.label)}</b>"
                              f"<span>{LEGACY_TEXT}</span></div></div>")
                continue
            _, user, host, block = g.best
            newest = max(block.get("meters") or [], key=lambda m: parse_iso(m["read_at"]), default=None)
            bits = [f"read on {esc(user)}'s Mac ({esc(host)}) {clock_html(newest['read_at'], model.now)}"
                    if newest else "no reading yet"]
            who = sorted(set(g.users))
            if len(who) > 1:
                bits.append("shared by " + esc(", ".join(who)))
            err = block.get("error")
            err_html = f"<div class='aerr'>{esc(err['text'])}</div>" if err else ""
            if block.get("warning"):
                err_html += f"<div class='aerr'>{esc(block['warning'])}</div>"
            rows = "".join(meters_mod.meter_html(m, model.now, model.rules) for m in block.get("meters") or [])
            ss = _session_share(block["session_share"], model) if block.get("session_share") else ""
            blocks.append(f"<div class='acct' data-k='{esc(g.key)}'><div class='h'><b>{esc(g.label)}</b>"
                          f"<span>{DOT.join(bits)}</span></div>{err_html}<div class='meters'>{rows}</div>{ss}</div>")
        body = "".join(blocks)
    return (f"<section class='panel today' data-k='sec:plan'><div class='who'><b>Plan meters</b>"
            f"<span>Claude's own limits, one row per account</span></div>{body}</section>")


def charts(model: DashModel) -> str:
    out = []
    for c in model.charts:
        leg = "".join(f"<span><i style='background:var(--s{i})'></i>{esc(lab)}</span>" for i, (_, lab) in enumerate(c["series"]))
        out.append(f"<div class='panel chart' data-k='chart:{c['cid']}'><h3>{esc(c['title'])}</h3>"
                   f"{svg.stacked_svg(c['days'], c['series'], c['values'], c['cid'])}<div class='leg'>{leg}</div><div class='tip'></div></div>")
    return f"<div data-k='sec:charts'><h2>Tokens per day</h2><div class='charts'>{''.join(out)}</div></div>"


def models_table(model: DashModel) -> str:
    if not model.models30:
        body = "<p class='note'>Per-model detail appears after the next <code>jusage</code> run.</p>"
    else:
        total = sum(x["tokens"] for _, x in model.models30) or 1
        rows = "".join(
            f"<tr><td><i class='sw' style='background:var(--s{i})'></i>{esc(formats.model_name(mid))}</td>"
            f"<td>{int(x['calls']):,}</td><td>{formats.tokens(int(x['input']))}</td>"
            f"<td>{formats.tokens(int(x['cache_5m'] + x['cache_1h']))}</td><td>{formats.tokens(int(x['cache_read']))}</td>"
            f"<td>{formats.tokens(int(x['output']))}</td><td>{formats.tokens(int(x['tokens']))}</td>"
            f"<td>{x['tokens'] / total * 100:.0f}%</td><td>{_api(x['usd'])}</td></tr>"
            for i, (mid, x) in enumerate(model.models30))
        body = (f"<div class='wrap'><table><tr><th>model</th><th>calls</th><th>input</th><th>cache write</th><th>cache read</th>"
                f"<th>output</th><th>total</th><th>share</th><th title=\"{esc(formats.API_RATE_LABEL)}\">{USD_HEAD}</th></tr>{rows}</table></div>")
    return f"<div data-k='sec:models'><h2>Models, last 30 days</h2>{body}</div>"


def month_tables(model: DashModel) -> str:
    pcols = "".join(f"<th data-k='person:{esc(p.pid)}'>{esc(p.user)}</th>" for p in model.people) if model.multi else ""
    tables = []
    for mo in model.months:
        mdate = date.fromisoformat(mo["key"] + "-01")
        rows, mt, ptot = [], defaultdict(float), defaultdict(float)
        for d, types, cost, per in mo["days"]:
            dd = date.fromisoformat(d)
            tot = sum(types.get(k, 0) for k, _ in TYPES)
            for k, _ in TYPES:
                mt[k] += types.get(k, 0)
            mt["tot"] += tot
            mt["cost"] += cost
            cells = ""
            if model.multi:
                for p in model.people:
                    ptot[p.pid] += per.get(p.pid, 0)
                    cells += f"<td data-k='person:{esc(p.pid)}'>{formats.tokens(int(per.get(p.pid, 0)))}</td>"
            rows.append(f"<tr><td class='d'>{dd.day:02d}<span>{dd:%A}</span></td>{cells}"
                        + "".join(f"<td>{formats.tokens(int(types.get(k, 0)))}</td>" for k, _ in TYPES)
                        + f"<td>{formats.tokens(int(tot))}</td><td>{_api(cost)}</td></tr>")
        pt = "".join(f"<td data-k='person:{esc(p.pid)}'>{formats.tokens(int(ptot[p.pid]))}</td>" for p in model.people) if model.multi else ""
        rows.append(f"<tr class='tot'><td>{mdate:%B}</td>{pt}" + "".join(f"<td>{formats.tokens(int(mt[k]))}</td>" for k, _ in TYPES)
                    + f"<td>{formats.tokens(int(mt['tot']))}</td><td>{_api(mt['cost'])}</td></tr>")
        tables.append(f"<div class='month'><h3>{mdate:%B %Y}</h3><div class='wrap'><table><tr><th>day</th>{pcols}"
                      + "".join(f"<th>{lab}</th>" for _, lab in TYPES)
                      + f"<th>total</th><th title=\"{esc(formats.API_RATE_LABEL)}\">{USD_HEAD}</th></tr>{''.join(rows)}</table></div></div>")
    return f"<div data-k='sec:days'><h2>Every day</h2><div class='months'>{''.join(tables)}</div></div>"


def footnote(model: DashModel) -> str:
    parts = [f"“{USD_HEAD}” is what the same tokens would cost at Anthropic's pay-as-you-go rates: "
             f"{formats.API_RATE_LABEL}. It is the fair scale for splitting a shared plan.",
             "Cache reads are the conversation re-sent on every turn, so they dwarf everything else by design.",
             "Plan meters are Claude's own limits, read from the same place /usage gets them."]
    if model.unpriced_month:
        parts.append(f"{model.unpriced_month} calls this month were on models missing from pricing.json and count $0.")
    parts.append("Day totals are each person's own local calendar days.")
    notes = "".join(f"<li>{esc(n)}</li>" for n in model.notes)
    return (f"<div class='note' data-k='sec:note'>{esc(' '.join(parts))}"
            + (f"<ul class='notes'>{notes}</ul>" if notes else "") + "</div>")


def drawer(model: DashModel) -> str:
    def boxes(items):
        return "".join(f"<label><input type='checkbox' value='{esc(k)}' checked>{esc(lab)}</label>" for k, lab in items)

    parts = ["<div class='cfg-h'><b>Customize</b><button type='button' class='hk-btn' id='cfg-x'>Done</button></div>",
             "<h4>Theme</h4><div class='themes' id='themes'></div>",
             "<h4>Accent</h4><div id='accent-picker'></div>",
             "<h4>People</h4>" + boxes([(f"person:{p.pid}", p.user) for p in model.people])
             + (boxes([("sec:everyone", "everyone, 30 days")]) if model.multi else "")]
    accts = [(g.key, g.label) for g in model.groups if not g.legacy]
    if accts:
        parts.append("<h4>Accounts</h4>" + boxes(accts))
    parts.append("<h4>Sections</h4>" + boxes([("sec:plan", "plan meters"), ("sec:charts", "tokens per day"),
                                              ("sec:models", "models, last 30 days"), ("sec:days", "every day"),
                                              ("sec:note", "footnote")]))
    if model.charts:
        parts.append("<h4>Charts</h4>" + boxes([(f"chart:{c['cid']}", c["title"]) for c in model.charts]))
    parts.append("<p class='small'>Saved in this browser only. Everyone sees the same page; each of us picks a view.</p>"
                 "<button type='button' class='hk-btn' id='qk-open' title='List and change every shortcut.'>Quick Keys</button>")
    return f"<aside class='cfg' id='cfg' hidden>{''.join(parts)}</aside>"
