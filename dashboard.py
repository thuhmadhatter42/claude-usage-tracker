"""dashboard.py — render the shared usage page from the shared reports JSONs.
Pure stdlib. Called by claude_usage.py; nothing here touches the database."""
import datetime as dt, html, json
from collections import defaultdict
from pathlib import Path

THEMES = json.loads((Path(__file__).with_name("themes.json")).read_text())

COLS = ("input", "cache_5m", "cache_1h", "cache_read", "output")
TYPES = [("input", "input"), ("cache_w", "cache write"), ("cache_read", "cache read"), ("output", "output")]
# Validated categorical palette (dataviz reference instance), fixed slot order, light / dark.
PAL = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
       ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#4a3aa7", "#9085e9"), ("#e34948", "#e66767")]
SEGS = 24  # segments per channel meter
METER_NAMES = {"five_hour": "5 hours", "seven_day": "7 days", "seven_day_opus": "7 days, Opus",
               "seven_day_sonnet": "7 days, Sonnet", "seven_day_fable": "7 days, Fable"}

CSS = """
:root{color-scheme:light;--bg:#f2f1ec;--sf:#fbfaf7;--sf2:#f5f4ef;--ink:#151513;--ink2:#54524c;--mut:#8a877e;--line:#dedbd2;--grid:#e9e6de;--seg:#e3e0d7;--ok:#2f9e44;--warn:#e0a100;--hot:#d8352b;--sel:#d9e6f5;@LIGHT@}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--bg:#121311;--sf:#1a1b19;--sf2:#202120;--ink:#f1efe8;--ink2:#b9b6ad;--mut:#7d7a72;--line:#2b2c29;--grid:#262724;--seg:#2a2b28;--ok:#3fbf58;--warn:#f0b428;--hot:#f0483c;--sel:#2b3f57;@DARK@}}
:root[data-theme=dark]{color-scheme:dark;--bg:#121311;--sf:#1a1b19;--sf2:#202120;--ink:#f1efe8;--ink2:#b9b6ad;--mut:#7d7a72;--line:#2b2c29;--grid:#262724;--seg:#2a2b28;--ok:#3fbf58;--warn:#f0b428;--hot:#f0483c;--sel:#2b3f57;@DARK@}
*{box-sizing:border-box}::selection{background:var(--sel)}[hidden]{display:none!important}
.gear{background:var(--sf);color:var(--ink2);border:1px solid var(--line);border-radius:8px;padding:4px 10px;font:inherit;font-size:12.5px;cursor:pointer}.gear:hover{color:var(--ink)}
.cfg{position:fixed;top:0;right:0;bottom:0;width:min(320px,100vw);background:var(--sf);border-left:1px solid var(--line);padding:18px 20px 28px;overflow-y:auto;z-index:5;box-shadow:-12px 0 40px -20px rgba(0,0,0,.4)}
.cfg-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}.cfg-h b{font-size:15px}
.cfg h4{font-size:12px;font-weight:600;color:var(--mut);margin:18px 0 6px}
.cfg label{display:flex;align-items:center;gap:8px;font-size:13px;padding:4px 0;cursor:pointer}.cfg input{accent-color:var(--acc,var(--ok))}
.themes{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.th{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:inherit;font-size:12.5px;color:var(--ink);cursor:pointer;background:var(--sf2)}
.th.on{border-color:var(--acc,var(--ok));box-shadow:0 0 0 1px var(--acc,var(--ok))}
.th i{width:22px;height:14px;border-radius:4px;border:1px solid rgba(0,0,0,.15);position:relative;flex:none}.th i:after{content:"";position:absolute;left:4px;bottom:3px;width:10px;height:3px;border-radius:2px;background:var(--dot)}
.cfg .small{color:var(--mut);font-size:11.5px;margin-top:20px}
body{margin:0;padding:28px 20px 56px;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,"SF Pro Text",system-ui,sans-serif;font-feature-settings:"tnum" 1;-webkit-font-smoothing:antialiased}
main{max-width:1120px;margin:0 auto}
h1{font-size:20px;font-weight:600;letter-spacing:-.01em;margin:0}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.head span{color:var(--mut);font-size:12.5px}
h2{font-size:13px;font-weight:600;color:var(--ink2);margin:30px 0 8px}
.panel{background:var(--sf);border:1px solid var(--line);border-radius:12px}
.today{padding:16px 20px 14px;margin-bottom:12px}
.who{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}.who b{font-weight:600}.who span{color:var(--mut);font-size:12.5px}
.stats{display:flex;flex-wrap:wrap;gap:6px 34px;align-items:flex-end}
.stat{display:flex;flex-direction:column;gap:1px;min-width:88px}
.stat .v{font-size:26px;font-weight:600;line-height:1.1;letter-spacing:-.02em}.stat .v small{font-size:13px;font-weight:500;color:var(--ink2);margin-left:5px;letter-spacing:0}
.stat .l{font-size:12px;color:var(--mut)}
.stat.sm .v{font-size:17px}
.strip{display:flex;flex-wrap:wrap;gap:6px 34px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.strip .stat .v{font-size:16px}
.meters{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:8px 32px}
.meter{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;align-items:center;font-size:12px}
.meter .n{color:var(--ink2);white-space:nowrap}.meter .n b{color:var(--ink);font-weight:600;font-size:13px;margin-right:6px;display:inline-block;min-width:34px}
.meter .r{color:var(--mut);grid-column:2;white-space:nowrap}
.bar{display:flex;gap:2px;height:12px}.bar i{flex:1;background:var(--seg);border-radius:1.5px}
.bar i.on{background:var(--ok)}.bar i.on.w{background:var(--warn)}.bar i.on.h{background:var(--hot)}
.meter.err .bar i{opacity:.55}.meter.err .r{color:var(--warn)}
code{font:12px ui-monospace,Menlo,monospace;background:var(--sf2);padding:1px 5px;border-radius:4px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}
.chart{padding:12px 12px 8px;position:relative}
.chart h3{font-size:12.5px;font-weight:600;color:var(--ink2);margin:0 0 6px 4px}
.chart svg{width:100%;height:auto;display:block}
.leg{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--ink2);font-size:12px;margin:6px 4px 0}.leg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.tip{position:absolute;pointer-events:none;background:var(--sf2);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 6px 20px -6px rgba(0,0,0,.35);display:none;min-width:150px;z-index:2}
.tip b{display:block;margin-bottom:4px}.tip .r{display:flex;justify-content:space-between;gap:12px}
.bar-hit:hover rect.hl{fill:var(--sel);opacity:.35}
table{border-collapse:separate;border-spacing:0;width:100%;background:var(--sf);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}
th,td{padding:6px 12px;text-align:right;border-top:1px solid var(--line);white-space:nowrap}
tr:first-child th{border-top:0}th{color:var(--mut);font-weight:500;font-size:12px}td:first-child,th:first-child{text-align:left}
td.d span{color:var(--mut);margin-left:5px}
tr.tot td{font-weight:600;background:var(--sf2)}
.months{display:grid;grid-template-columns:1fr;gap:14px}@media(min-width:1260px){.months{grid-template-columns:1fr 1fr}}
.month h3{font-size:12.5px;font-weight:600;color:var(--ink2);margin:0 0 6px 4px}
.wrap{overflow-x:auto}.note{color:var(--mut);font-size:12px;margin-top:28px;line-height:1.55;max-width:72ch}
:focus-visible{outline:2px solid var(--ok);outline-offset:2px}
@media(max-width:600px){.months{grid-template-columns:1fr}.stat .v{font-size:22px}}
"""


def fmt_n(n):
    return (f"{n/1e9:.2f}B" if n >= 1e9 else f"{n/1e6:.1f}M" if n >= 1e6
            else f"{n/1e3:.0f}k" if n >= 1e3 else str(int(n)))


def short_model(m):
    return m.replace("claude-", "").replace("-20251001", "")


def until(iso):
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    left = t - dt.datetime.now().astimezone()
    h, m = divmod(max(int(left.total_seconds()) // 60, 0), 60)
    when = f"{t:%H:%M}" if t.date() == dt.date.today() else f"{t:%a %H:%M}"
    return f"resets {when}, in {h}h {m:02d}m"


def day_tot(a):
    return sum(a.get(k, 0) for k in COLS)


def nice_ceiling(v):
    """Smallest round number >= v*1.04, so the tallest bar nearly touches the top line."""
    v = v * 1.04 or 1
    mag = 10 ** (len(str(int(v))) - 1)
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if mag * m >= v:
            return mag * m
    return mag * 10


def stacked_svg(days, series, values, cid):
    W, H, L, B, T = 380, 190, 48, 22, 8
    n = max(len(days), 1)
    slot = (W - L - 6) / n
    bw = max(slot * 0.62, 3)
    top = max((sum(values[d].get(k, 0) for k, _ in series) for d in days), default=1) or 1
    ymax = nice_ceiling(top)
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    out = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='{cid}' data-chart='{cid}'>"]
    for i in range(5):
        v = ymax * i / 4
        out.append(f"<line x1='{L}' x2='{W-2}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='var(--grid)' stroke-width='1'/>"
                   f"<text x='{L-6}' y='{y(v)+3.5:.1f}' font-size='10.5' text-anchor='end' fill='var(--mut)'>{fmt_n(v)}</text>")
    for i, d in enumerate(days):
        x = L + i * slot + (slot - bw) / 2
        acc, segs = 0, []
        for si, (k, lab) in enumerate(series):
            v = values[d].get(k, 0)
            if v <= 0:
                continue
            y0, y1 = y(acc + v), y(acc)
            h = max(y1 - y0 - 1.5, 0.6)
            segs.append(f"<rect x='{x:.1f}' y='{y0:.1f}' width='{bw:.1f}' height='{h:.1f}' fill='var(--s{si})' rx='1'/>")
            acc += v
        dd = dt.date.fromisoformat(d)
        label = f"{dd:%a}"[:2] if n <= 14 else (str(dd.day) if dd.day in (1, 10, 20) or i == n - 1 else "")
        out.append(f"<g class='bar-hit' data-day='{d}'><rect class=hl x='{L + i*slot:.1f}' y='{T}' width='{slot:.1f}' height='{H-T-B}' fill='transparent' rx='3'/>"
                   + "".join(segs) + f"<text x='{L + i*slot + slot/2:.1f}' y='{H-7}' font-size='10.5' text-anchor='middle' fill='var(--mut)'>{label}</text></g>")
    out.append("</svg>")
    return "".join(out)


def forecast_text(fc):
    if not fc or not fc.get("ready"):
        return ""
    s = f"≈ ${fc['left_usd']:,.0f} of room left"
    if fc.get("wall_at") and fc.get("hours_to_wall") is not None:
        t = dt.datetime.fromisoformat(fc["wall_at"])
        s += f" · at this pace the wall is {t:%a %H:%M}"
    return s


def meter_html(name, pct, reset, tag="", err=None, fc=None, key=None):
    pct = max(0.0, min(100.0, float(pct or 0)))
    lit = round(pct / 100 * SEGS)
    segs = []
    for i in range(SEGS):
        cls = "on" if i < lit else ""
        if cls:
            frac = (i + 1) / SEGS
            cls += " h" if frac > 0.9 else " w" if frac > 0.7 else ""
        segs.append(f"<i class='{cls}'></i>")
    label = METER_NAMES.get(name, name.replace("_", " "))
    right = html.escape(err) if err else until(reset)
    ft = forecast_text(fc)
    if ft:
        right += f"<br>{html.escape(ft)}"
    shown = "—" if err else f"{pct:.0f}%"
    dk = f" data-k='acct:{html.escape(key)}'" if key else ""
    return (f"<div class='meter{' err' if err else ''}'{dk}><div class=n><b>{shown}</b>{html.escape(tag + label)}</div>"
            f"<div class=bar>{''.join(segs)}</div><div class=r>{right}</div></div>")


def render(reps, version):
    people = [(r["user"], f"{r['user']}@{r['host']}", r) for r in reps]
    multi = len(people) > 1
    today = dt.date.today()
    tday = today.isoformat()
    all_days = sorted({d for _, _, r in people for d in r["days"]})
    day_type = defaultdict(lambda: defaultdict(float))
    day_model = defaultdict(lambda: defaultdict(float))
    day_person = defaultdict(lambda: defaultdict(float))
    day_acct = defaultdict(lambda: defaultdict(float))
    day_proj = defaultdict(lambda: defaultdict(float))
    day_cost = defaultdict(float)
    model_tot = defaultdict(lambda: defaultdict(float))
    lo30 = (today - dt.timedelta(days=29)).isoformat()
    for u, label, r in people:
        for d, a in r["days"].items():
            day_type[d]["input"] += a.get("input", 0); day_type[d]["cache_w"] += a.get("cache_5m", 0) + a.get("cache_1h", 0)
            day_type[d]["cache_read"] += a.get("cache_read", 0); day_type[d]["output"] += a.get("output", 0)
            day_person[d][u] += day_tot(a); day_cost[d] += a.get("cost", 0)
            for acct, x in a.get("accounts", {}).items():
                day_acct[d][f"{label} · {acct}"] += x.get("tokens", 0)
            for pj, x in a.get("projects", {}).items():
                pj = "subagents" if pj.startswith("agent-") else pj      # worktree folders of spawned agents
                day_proj[d][(pj if not multi else f"{u} · {pj}")] += x.get("tokens", 0)
            for m, x in a.get("models", {}).items():
                t = x.get("tokens", sum(x.get(k, 0) for k in COLS))
                day_model[d][m] += t
                if d >= lo30:
                    for k in COLS:
                        model_tot[m][k] += x.get(k, 0)
                    model_tot[m]["tokens"] += t; model_tot[m]["cost"] += x.get("cost", 0); model_tot[m]["calls"] += x.get("calls", 0)
    models = sorted(model_tot, key=lambda m: -model_tot[m]["tokens"])
    light = "".join(f"--s{i}:{PAL[i][0]};" for i in range(8)); dark = "".join(f"--s{i}:{PAL[i][1]};" for i in range(8))
    theme_css, theme_btns = [], []
    for tid in THEMES["order"]:
        t = THEMES["themes"][tid]
        if t.get("scheme"):
            vars_ = "".join(f"--{k}:{t[k]};" for k in ("bg", "sf", "sf2", "ink", "ink2", "mut", "line", "grid", "seg", "ok", "warn", "hot", "sel"))
            theme_css.append(f":root[data-theme={tid}]{{color-scheme:{t['scheme']};{vars_}--acc:{t['accent']};{dark if t['scheme'] == 'dark' else light}}}")
            sw = f"background:{t['bg']};--dot:{t['ok']}"
        else:
            sw = "background:linear-gradient(90deg,#fbfaf7 50%,#1a1b19 50%);--dot:#2f9e44"
        theme_btns.append(f"<button class=th data-theme='{tid}'><i style='{sw}'></i>{html.escape(t['name'])}</button>")

    def window(r, n):
        lo = (today - dt.timedelta(days=n - 1)).isoformat()
        t = c = 0.0
        for d, a in r["days"].items():
            if d >= lo:
                t += day_tot(a); c += a.get("cost", 0)
        return t, c

    # ---- today panel, one per person ----
    strips = []
    for i, (u, label, r) in enumerate(people):
        a = r["days"].get(tday, {})
        t1, c1 = day_tot(a), a.get("cost", 0)
        t7, c7 = window(r, 7); t30, c30 = window(r, 30)
        blk = r.get("blocks") or {}
        blk = blk if isinstance(blk, dict) else {}          # pre-0.1.14 reports carried one merged list; ignore those
        top_model = max(a.get("models", {}).items(), key=lambda kv: kv[1].get("tokens", 0), default=None)
        stats = [f"<div class=stat><div class=v>{fmt_n(t1)}<small>tokens</small></div><div class=l>today</div></div>",
                 f"<div class=stat><div class=v>${c1:,.0f}</div><div class=l>api-equivalent</div></div>",
                 f"<div class='stat sm'><div class=v>{a.get('calls', 0):,}</div><div class=l>calls</div></div>"]
        now = dt.datetime.now().astimezone().isoformat(timespec="minutes")
        for acct, bl in sorted(blk.items()):
            if bl and bl[-1].get("end", "") > now:                  # only a window that is still open
                tag = f"{acct} · " if len(blk) > 1 else ""
                stats.append(f"<div class='stat sm' data-k='acct:{html.escape(acct)}'><div class=v>{fmt_n(bl[-1]['tokens'])}</div><div class=l>{html.escape(tag)}5-hour window, since {html.escape(str(bl[-1]['start'])[11:16])}</div></div>")
        if top_model:
            stats.append(f"<div class='stat sm'><div class=v>{html.escape(short_model(top_model[0]))}</div><div class=l>busiest model today</div></div>")
        if len(a.get("accounts", {})) > 1:
            for acct, x in sorted(a["accounts"].items()):
                stats.append(f"<div class='stat sm' data-k='acct:{html.escape(acct)}'><div class=v>{fmt_n(x['tokens'])}</div><div class=l>{html.escape(acct)}</div></div>")
        strip = (f"<div class=stat><div class=v>{fmt_n(t7)}</div><div class=l>7 days · ${c7:,.0f}</div></div>"
                 f"<div class=stat><div class=v>{fmt_n(t30)}</div><div class=l>30 days · ${c30:,.0f}</div></div>")
        live = r.get("live") or {}
        meters = []
        for acct, lv in live.items():
            tag = f"{acct} · " if len(live) > 1 else ""
            if lv.get("error") and not lv.get("meters"):
                meters.append(meter_html("meters", 0, None, tag, err="not reachable, run jusage live", key=acct))
                continue
            for m in lv.get("meters", []):
                meters.append(meter_html(m["name"], m["pct"], m.get("resets_at"), tag, fc=m.get("forecast"), key=acct))
        fetched = next((html.escape(str(lv["fetched"])[11:16]) for lv in live.values() if lv.get("fetched") and lv.get("meters")), None)
        meters_html = (f"<div class=meters>{''.join(meters)}</div>" if meters else
                       f"<div class=meters>{meter_html('meters', 0, None, err='run jusage live once to light these up')}</div>")
        strips.append(f"<section class='panel today' data-k='person:{html.escape(u)}' style='border-top:3px solid var(--s{i})'>"
                      f"<div class=who><b>{html.escape(label)}</b><span>{today:%A %-d %B}{' · meters as of ' + fetched if fetched else ''}</span></div>"
                      f"<div class=stats>{''.join(stats)}</div><div class=strip>{strip}</div>{meters_html}</section>")
    if multi:
        tot30 = sum(window(r, 30)[0] for _, _, r in people) or 1
        strips.append("<section class='panel today' data-k='sec:everyone'><div class=who><b>everyone</b><span>30 days</span></div><div class=stats>"
                      + f"<div class=stat><div class=v>{fmt_n(tot30)}</div><div class=l>tokens, all of us</div></div>"
                      + "".join(f"<div class='stat sm'><div class=v>{window(r,30)[0]/tot30*100:.0f}%</div><div class=l>{html.escape(u)}</div></div>" for u, _, r in people)
                      + "</div></section>")

    # ---- charts ----
    days7 = [(today - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    days30 = [(today - dt.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    charts = [("By model, 7 days", days7, [(m, short_model(m)) for m in models[:8]], day_model, "model"),
              ("By type, 7 days", days7, list(TYPES), day_type, "type")]
    proj_keys = sorted({k for d in days7 for k in day_proj[d]}, key=lambda k: -sum(day_proj[d][k] for d in days7))
    if proj_keys:
        top = proj_keys[:7]
        for d in days7:                       # fold the tail into "other" so colours stay fixed
            other = sum(v for k, v in day_proj[d].items() if k not in top)
            day_proj[d] = {k: day_proj[d].get(k, 0) for k in top} | ({"other": other} if other else {})
        charts.append(("By project, 7 days", days7, [(k, k) for k in top] + ([("other", "other")] if len(proj_keys) > 7 else []), day_proj, "proj"))
    acct_keys = sorted({k for d in days30 for k in day_acct[d]}, key=lambda k: -sum(day_acct[d][k] for d in days30))
    if len(acct_keys) > 1:
        charts.append(("By account and machine, 30 days", days30, [(k, k) for k in acct_keys[:8]], day_acct, "acct"))
    if multi:
        charts.append(("By person, 30 days", days30, [(u, u) for u, _, _ in people], day_person, "person"))
    chart_html, tipdata = [], {}
    for title, days, series, vals, cid in charts:
        leg = "".join(f"<span><i style='background:var(--s{i})'></i>{html.escape(lab)}</span>" for i, (k, lab) in enumerate(series))
        chart_html.append(f"<div class='panel chart' data-k='chart:{cid}'><h3>{title}</h3>{stacked_svg(days, series, vals, cid)}<div class=leg>{leg}</div><div class=tip></div></div>")
        tipdata[cid] = {"series": [[k, lab] for k, lab in series], "days": {d: {k: int(vals[d].get(k, 0)) for k, _ in series} for d in days}}

    # ---- models table ----
    tt = sum(model_tot[m]["tokens"] for m in models) or 1
    mrows = "".join(
        f"<tr><td><i style='display:inline-block;width:9px;height:9px;border-radius:2px;background:var(--s{i});margin-right:7px;vertical-align:-1px'></i>{html.escape(short_model(m))}</td>"
        f"<td>{int(x['calls']):,}</td><td>{fmt_n(x['input'])}</td><td>{fmt_n(x['cache_5m']+x['cache_1h'])}</td><td>{fmt_n(x['cache_read'])}</td>"
        f"<td>{fmt_n(x['output'])}</td><td>{fmt_n(x['tokens'])}</td><td>{x['tokens']/tt*100:.0f}%</td><td>${x['cost']:,.0f}</td></tr>"
        for i, m in enumerate(models[:8]) for x in [model_tot[m]])
    model_table = (f"<div class=wrap><table><tr><th>model</th><th>calls</th><th>input</th><th>cache write</th><th>cache read</th><th>output</th><th>total</th><th>share</th><th>$ api-equiv</th></tr>{mrows}</table></div>"
                   if mrows else "<p class=note>Per-model detail appears after the next <code>jusage</code> run.</p>")

    # ---- month tables ----
    months = defaultdict(list)
    for d in sorted(all_days, reverse=True):
        months[d[:7]].append(d)
    pcols = "".join(f"<th data-k='person:{html.escape(u)}'>{html.escape(u)}</th>" for u, _, _ in people) if multi else ""
    tables = []
    for ym in sorted(months, reverse=True)[:4]:
        mdate = dt.date.fromisoformat(ym + "-01")
        rows, mt = [], defaultdict(float)
        for d in months[ym]:
            dd = dt.date.fromisoformat(d); a = day_type[d]; tot = sum(a.values())
            for k in ("input", "cache_w", "cache_read", "output"): mt[k] += a[k]
            mt["tot"] += tot; mt["cost"] += day_cost[d]
            per = "".join(f"<td data-k='person:{html.escape(u)}'>{fmt_n(day_person[d][u])}</td>" for u, _, _ in people) if multi else ""
            rows.append(f"<tr><td class=d>{dd.day:02d}<span>{dd:%A}</span></td>{per}<td>{fmt_n(a['input'])}</td><td>{fmt_n(a['cache_w'])}</td>"
                        f"<td>{fmt_n(a['cache_read'])}</td><td>{fmt_n(a['output'])}</td><td>{fmt_n(tot)}</td><td>${day_cost[d]:,.0f}</td></tr>")
        ptot = "".join(f"<td data-k='person:{html.escape(u)}'>{fmt_n(sum(day_person[d][u] for d in months[ym]))}</td>" for u, _, _ in people) if multi else ""
        rows.append(f"<tr class=tot><td>{mdate:%B}</td>{ptot}<td>{fmt_n(mt['input'])}</td><td>{fmt_n(mt['cache_w'])}</td><td>{fmt_n(mt['cache_read'])}</td>"
                    f"<td>{fmt_n(mt['output'])}</td><td>{fmt_n(mt['tot'])}</td><td>${mt['cost']:,.0f}</td></tr>")
        tables.append(f"<div class=month><h3>{mdate:%B %Y}</h3><div class=wrap><table><tr><th>day</th>{pcols}<th>input</th><th>cache write</th><th>cache read</th><th>output</th><th>total</th><th>$ api-equiv</th></tr>{''.join(rows)}</table></div></div>")

    # ---- customize panel ----
    accts = sorted({a for _, _, r in people for a in r.get("accounts", [])})
    def boxes(kind, items):
        return "".join(f"<label><input type=checkbox value='{kind}:{html.escape(k)}' checked>{html.escape(lab)}</label>" for k, lab in items)
    cfg = ["<div class=cfg-h><b>Customize</b><button class=gear id=cfg-x>Done</button></div>",
           f"<h4>Theme</h4><div class=themes>{''.join(theme_btns)}</div>",
           "<h4>People</h4>" + boxes("person", [(u, u) for u, _, _ in people]) + (boxes("sec", [("everyone", "everyone, 30 days")]) if multi else "")]
    if accts:
        cfg.append("<h4>Accounts</h4>" + boxes("acct", [(a, a) for a in accts]))
    cfg.append("<h4>Sections</h4>" + boxes("sec", [("charts", "tokens per day"), ("models", "models, last 30 days"), ("days", "every day"), ("note", "footnote")]))
    cfg.append("<h4>Charts</h4>" + boxes("chart", [(cid, title) for title, _, _, _, cid in charts]))
    cfg.append("<p class=small>Saved in this browser only. Everyone sees the same page; each of us picks a view.</p>")
    cfg_html = f"<aside class=cfg id=cfg hidden>{''.join(cfg)}</aside>"

    js = """
const HK='jusage.hide',TK='jusage.theme';let hide=new Set();try{hide=new Set(JSON.parse(localStorage.getItem(HK)||'[]'))}catch(e){}
function applyHide(){document.querySelectorAll('[data-k]').forEach(e=>{e.hidden=e.dataset.k.split('|').some(k=>hide.has(k))});
document.querySelectorAll('#cfg input').forEach(i=>i.checked=!hide.has(i.value))}
function applyTheme(t){if(!t||t==='system')delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=t;
document.querySelectorAll('.th').forEach(b=>b.classList.toggle('on',(b.dataset.theme||'system')===(t||'system')))}
let theme='system';try{theme=localStorage.getItem(TK)||'system'}catch(e){}applyTheme(theme);applyHide();
document.getElementById('gear').onclick=()=>{document.getElementById('cfg').hidden=false};
document.getElementById('cfg-x').onclick=()=>{document.getElementById('cfg').hidden=true};
document.querySelectorAll('#cfg input').forEach(i=>i.addEventListener('change',()=>{i.checked?hide.delete(i.value):hide.add(i.value);try{localStorage.setItem(HK,JSON.stringify([...hide]))}catch(e){}applyHide()}));
document.querySelectorAll('.th').forEach(b=>b.onclick=()=>{applyTheme(b.dataset.theme);try{localStorage.setItem(TK,b.dataset.theme)}catch(e){}});
const D=%s;const F=n=>n>=1e9?(n/1e9).toFixed(2)+'B':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?Math.round(n/1e3)+'k':String(n);
document.querySelectorAll('.chart').forEach(ch=>{const svg=ch.querySelector('svg'),tip=ch.querySelector('.tip'),data=D[svg.dataset.chart];
svg.querySelectorAll('.bar-hit').forEach(g=>{g.addEventListener('mousemove',e=>{const d=g.dataset.day,v=data.days[d]||{};let tot=0;
const rows=data.series.map(([k,l],i)=>{tot+=v[k]||0;return [k,l,i]}).filter(([k])=>v[k]).map(([k,l,i])=>`<div class=r><span><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--s${i});margin-right:5px"></i>${l}</span><span>${F(v[k])}</span></div>`).reverse().join('');
const dd=new Date(d+'T12:00');tip.innerHTML=`<b>${dd.toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})} · ${F(tot)}</b>${rows||'<span style="color:var(--mut)">nothing</span>'}`;tip.style.display='block';
const r=ch.getBoundingClientRect();let x=e.clientX-r.left+14,y=e.clientY-r.top+14;if(x+tip.offsetWidth>r.width-8)x-=tip.offsetWidth+28;tip.style.left=x+'px';tip.style.top=y+'px';});
g.addEventListener('mouseleave',()=>tip.style.display='none');});});
""" % json.dumps(tipdata).replace("<", "\\u003c")   # a project folder named "</script>…" must not break out of the script tag

    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>jusage</title><style>{CSS.replace("@LIGHT@", light).replace("@DARK@", dark)}{''.join(theme_css)}</style></head><body><main>
<div class=head><h1>Claude usage</h1><span>shared · jusage {version}</span><button class=gear id=gear>Customize</button></div>
{''.join(strips)}
<div data-k='sec:charts'><h2>Tokens per day</h2><div class=charts>{''.join(chart_html)}</div></div>
<div data-k='sec:models'><h2>Models, last 30 days</h2>{model_table}</div>
<div data-k='sec:days'><h2>Every day</h2><div class=months>{''.join(tables)}</div></div>
<div class=note data-k='sec:note'>“$ api-equiv” is what the same tokens would cost at Anthropic's pay-as-you-go rates. On a Max plan nobody is billed that; it is the fair scale for splitting the plan. Cache reads are the conversation re-sent on every turn, so they dwarf everything else by design. The channel meters are Claude's own 5-hour and 7-day limits, read from the same place <code>/usage</code> gets them.</div>
{cfg_html}
</main><script>{js}</script></body></html>"""
