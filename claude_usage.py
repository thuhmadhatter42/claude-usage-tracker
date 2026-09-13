#!/usr/bin/env python3
"""claude-usage — track Claude Code token usage across every account on this Mac,
and share a daily summary with the people you split a plan with, so it shows who used what.

Zero dependencies (Python 3.9+ stdlib). Never guesses a plan limit: it reports
tokens, 5-hour windows, and the equivalent first-party API cost, nothing more.

Commands
  scan        read every ~/.claude*/projects/**/*.jsonl into ~/.claude-usage/usage.db
  live        Claude's own 5-hour / 7-day meters, per account (reads your Keychain login)
  report      totals by day / model / account / project / 5-hour block
  share       write <user>@<host>.json into the shared reports folder
  dashboard   build one HTML page from every JSON in the reports folder
  update      scan + share + dashboard (the one to put on a schedule)
"""
import argparse, datetime as dt, functools, glob, json, os, socket, sqlite3, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = (HERE / "VERSION").read_text().strip()
HOME = Path.home()
STATE = HOME / ".claude-usage"
DB = STATE / "usage.db"
CONFIG = STATE / "config.json"
BLOCK_HOURS = 5
COLS = ("input", "cache_5m", "cache_1h", "cache_read", "output")


# ---------- config ----------
def load_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}


def state_dir():
    """~/.claude-usage, readable by this user only (0700); everything written there is 0600."""
    STATE.mkdir(mode=0o700, exist_ok=True)
    try:
        os.chmod(STATE, 0o700)
    except OSError:
        pass
    return STATE


def write_private(path, text):
    """Write a file only this user can read, atomically (temp file + rename)."""
    tmp = Path(str(path) + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def save_config(cfg):
    state_dir()
    write_private(CONFIG, json.dumps(cfg, indent=2) + "\n")


def reports_dir(args):
    d = getattr(args, "reports", None) or load_config().get("reports_dir")
    if not d:
        sys.exit("No reports folder set. Run once with --reports <folder> "
                 "(a folder you sync with the people you share a plan with) and it is remembered.")
    p = Path(os.path.expanduser(d))
    p.mkdir(parents=True, exist_ok=True)
    if getattr(args, "reports", None):
        cfg = load_config(); cfg["reports_dir"] = str(p); save_config(cfg)
    return p


def whoami(args):
    cfg = load_config()
    user = getattr(args, "user", None) or cfg.get("user") or os.environ.get("USER", "unknown")
    if getattr(args, "user", None):
        cfg["user"] = user; save_config(cfg)
    return user, socket.gethostname().split(".")[0]


def cmd_set_user(args):
    cfg = load_config(); cfg["user"] = args.name; save_config(cfg)
    print(f"you are shown as {args.name!r}")


# ---------- pricing ----------
def load_pricing():
    table = json.loads((HERE / "pricing.json").read_text())
    return {k: v for k, v in table.items() if not k.startswith("_")}


def rate_for(model, pricing):
    best = None
    for key in pricing:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return pricing.get(best)


def cost_usd(row, rate):
    if not rate:
        return None
    i, o, cr = rate["input"], rate["output"], rate["cache_read"]
    return (row["input"] * i + row["cache_5m"] * i * 1.25 + row["cache_1h"] * i * 2
            + row["cache_read"] * cr + row["output"] * o) / 1e6


# ---------- db ----------
def db():
    """One connection per process. 10 s busy timeout + WAL so the menu bar's poll and a CLI run
    never see 'database is locked'."""
    state_dir()
    c = sqlite3.connect(DB, timeout=10)
    try:
        os.chmod(DB, 0o600)
    except OSError:
        pass
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS records (
      key TEXT PRIMARY KEY, ts TEXT NOT NULL, day TEXT NOT NULL,
      account TEXT NOT NULL, model TEXT NOT NULL, project TEXT NOT NULL,
      session TEXT NOT NULL, input INTEGER NOT NULL, cache_5m INTEGER NOT NULL,
      cache_1h INTEGER NOT NULL, cache_read INTEGER NOT NULL, output INTEGER NOT NULL
    ) STRICT;
    CREATE INDEX IF NOT EXISTS records_ts ON records(ts);
    CREATE TABLE IF NOT EXISTS files (
      path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL
    ) STRICT;
    CREATE TABLE IF NOT EXISTS meter_log (
      ts TEXT NOT NULL, account TEXT NOT NULL, meter TEXT NOT NULL, pct REAL NOT NULL, resets_at TEXT,
      PRIMARY KEY (ts, account, meter)
    ) STRICT;
    """)
    # Rows from backup copies of a config dir (scanned before 0.1.4 excluded them) are deleted once here.
    c.execute("DELETE FROM records WHERE lower(account) LIKE '%.bak%' OR lower(account) LIKE '%backup%' OR lower(account) LIKE '%.old%'")
    c.commit()
    return c


# ---------- scan ----------
@functools.lru_cache(maxsize=None)
def config_dirs():
    """Every Claude Code config dir on this Mac: ~/.claude* with a projects/ folder, plus
    $CLAUDE_CONFIG_DIR. Backup copies (.bak / backup / .old in the name) are not accounts."""
    dirs = {p for p in HOME.glob(".claude*") if (p / "projects").is_dir()
            and not any(x in p.name.lower() for x in (".bak", "backup", ".old"))}
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env and (Path(env) / "projects").is_dir():
        dirs.add(Path(env).resolve())
    return tuple(sorted(dirs))


@functools.lru_cache(maxsize=None)
def acct_label(name):
    """'.claude' -> 'Sam #1', '.claude-work' -> 'Sam #2'. The number is assigned the first time a
    config dir is seen and remembered in config.json, so adding a dir later never renumbers the others."""
    cfg = load_config()
    numbers = cfg.setdefault("accounts", {})
    new = [d.name for d in config_dirs() if d.name not in numbers]
    if new:
        for n in sorted(new):
            numbers[n] = max(numbers.values(), default=0) + 1
        save_config(cfg)
    who = cfg.get("user") or os.environ.get("USER", "me")
    return f"{who} #{numbers[name]}" if name in numbers else name


def forecast(c, account, meter_name, pct_now, resets_at):
    """Learn what one percent of a limit costs, from logged readings vs api-equivalent spend
    between them, then say how much room is left and when the wall lands at the recent pace.
    Returns None until there are enough readings that moved the meter."""
    if pct_now is None:
        return None
    pricing = load_pricing()
    # readings belong to the same limit period when they share a reset time (to the minute)
    log = c.execute("SELECT ts, pct FROM meter_log WHERE account=? AND meter=? AND COALESCE(substr(resets_at,1,16),'')=? ORDER BY ts",
                    (account, meter_name, (resets_at or "")[:16])).fetchall()
    if len(log) < 2:
        return None
    rows = c.execute("SELECT ts, model, input, cache_5m, cache_1h, cache_read, output FROM records WHERE account=? AND ts>=? ORDER BY ts",
                     (account, log[0]["ts"])).fetchall()
    spend_at = []   # cumulative api-equivalent at each reading
    j, acc = 0, 0.0
    for r in log:
        while j < len(rows) and rows[j]["ts"] <= r["ts"]:
            acc += cost_usd(rows[j], rate_for(rows[j]["model"], pricing)) or 0; j += 1
        spend_at.append(acc)
    d_pct = log[-1]["pct"] - log[0]["pct"]
    d_spend = spend_at[-1] - spend_at[0]
    if d_pct < 2 or d_spend <= 0:
        return {"samples": len(log), "ready": False}
    per_pct = d_spend / d_pct                      # $ api-equivalent per 1% of this limit
    left = max(100 - pct_now, 0) * per_pct
    now = dt.datetime.now().astimezone()
    recent = c.execute("SELECT model, input, cache_5m, cache_1h, cache_read, output FROM records WHERE account=? AND ts>=?",
                       (account, (now - dt.timedelta(hours=24)).isoformat(timespec="seconds"))).fetchall()
    pace = sum(cost_usd(r, rate_for(r["model"], pricing)) or 0 for r in recent) / 24   # $/hour, last day
    hours = left / pace if pace > 0 else None
    wall = (now + dt.timedelta(hours=hours)).isoformat(timespec="minutes") if hours is not None else None
    return {"samples": len(log), "ready": True, "per_pct_usd": round(per_pct, 2), "left_usd": round(left, 0),
            "pace_usd_per_hour": round(pace, 2), "hours_to_wall": round(hours, 1) if hours is not None else None, "wall_at": wall}


def to_local(ts):
    d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d.astimezone()


def parse_file(path, account):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "assistant":
                continue
            m = o.get("message") or {}
            u = m.get("usage") if isinstance(m, dict) else None
            if not u or not o.get("timestamp"):
                continue
            model = m.get("model") or "unknown"
            if model.startswith("<"):        # <synthetic> etc.
                continue
            mid, rid = m.get("id"), o.get("requestId")
            key = f"{mid}:{rid}" if mid and rid else o.get("uuid")
            if not key:
                continue
            cc = u.get("cache_creation") or {}
            total_cc = u.get("cache_creation_input_tokens") or 0
            c1h = cc.get("ephemeral_1h_input_tokens") or 0
            c5m = cc.get("ephemeral_5m_input_tokens")
            if c5m is None:
                c5m = max(total_cc - c1h, 0)
            local = to_local(o["timestamp"])
            yield (key, local.isoformat(timespec="seconds"), local.strftime("%Y-%m-%d"),
                   account, model, Path(o.get("cwd") or "").name or "?",
                   o.get("sessionId") or "?",
                   u.get("input_tokens") or 0, c5m, c1h,
                   u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)


def scan(c, full=False):
    """Ingest every changed transcript file. Returns (files read, new rows, total rows)."""
    seen = dict(c.execute("SELECT path, size || ':' || mtime FROM files"))
    new_rows = files_read = 0
    for cdir in config_dirs():
        account = cdir.name
        for path in glob.glob(str(cdir / "projects" / "**" / "*.jsonl"), recursive=True):
            st = os.stat(path)
            sig = f"{st.st_size}:{st.st_mtime}"
            if seen.get(path) == sig and not full:
                continue
            files_read += 1
            rows = list(parse_file(path, account))
            cur = c.executemany("INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            new_rows += cur.rowcount if cur.rowcount > 0 else 0
            c.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (path, st.st_size, st.st_mtime))
    c.commit()
    return files_read, new_rows, c.execute("SELECT count(*) FROM records").fetchone()[0]


def cmd_scan(args):
    files_read, new_rows, n = scan(db(), full=args.full)
    print(f"scanned {files_read} changed files across {len(config_dirs())} config dir(s); "
          f"{new_rows} new records, {n} total")


# ---------- query helpers ----------
def since_date(spec):
    """'7d' | '30d' | 'YYYY-MM-DD' | None -> ISO date string or None."""
    if not spec:
        return None
    if spec.endswith("d") and spec[:-1].isdigit():
        return (dt.date.today() - dt.timedelta(days=int(spec[:-1]) - 1)).isoformat()
    return spec


def fetch(c, since=None, account=None):
    q = "SELECT * FROM records"
    conds, params = [], []
    if since:
        conds.append("day >= ?"); params.append(since)
    if account:
        conds.append("account = ?"); params.append(account)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return c.execute(q + " ORDER BY ts", params).fetchall()


def zero():
    return {k: 0 for k in COLS} | {"cost": 0.0, "calls": 0, "unpriced": 0}


def add(acc, row, pricing):
    for k in COLS:
        acc[k] += row[k]
    acc["calls"] += 1
    cst = cost_usd(row, rate_for(row["model"], pricing))
    if cst is None:
        acc["unpriced"] += 1
    else:
        acc["cost"] += cst


def total_tokens(a):
    return sum(a[k] for k in COLS)


def blocks(rows):
    """Group rows into 5-hour windows the way Claude's rate limit does: a window
    opens at the top of the hour of the first call after the previous window closed."""
    out, start, end = [], None, None
    for r in rows:
        t = dt.datetime.fromisoformat(r["ts"])
        if end is None or t >= end:
            start = t.replace(minute=0, second=0, microsecond=0)
            end = start + dt.timedelta(hours=BLOCK_HOURS)
            out.append({"start": start, "end": end, "rows": []})
        out[-1]["rows"].append(r)
    return out


def blocks_by_account(rows):
    """Each account has its own 5-hour clock; interleaving them would invent windows nobody has."""
    per = defaultdict(list)
    for r in rows:
        per[r["account"]].append(r)
    return {a: blocks(rs) for a, rs in sorted(per.items())}



# ---------- live meters (Claude's own 5-hour / 7-day gauges) ----------
LIVE = STATE / "live.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
LIVE_TTL_MIN = 15   # ClaudeBar saw 1-hour 429s from polling this endpoint; 15 min is plenty fresh


class LoginError(Exception):
    """No usable claude.ai login for a config dir. The message is what the user should do."""


def keychain_service(cdir):
    """Claude Code keeps each config dir's claude.ai login in the macOS Keychain under
    'Claude Code-credentials-<sha256(config dir)[:8]>'. The unsuffixed 'Claude Code-credentials'
    item is a leftover from older builds and can hold a different account's login, so it is never read."""
    import hashlib
    return "Claude Code-credentials-" + hashlib.sha256(str(cdir).encode()).hexdigest()[:8]


def read_credentials(cdir):
    """The credentials JSON Claude Code wrote for this config dir: the Keychain on macOS,
    <config dir>/.credentials.json elsewhere. Read only, never written."""
    if sys.platform == "darwin":
        import subprocess
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", keychain_service(cdir), "-w"],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise LoginError("the Keychain did not answer in 30 s; a permission dialog is probably waiting on screen (click Always Allow)") from None
        if r.returncode != 0:
            raise LoginError("no claude.ai login in the Keychain for this config dir; run `claude /login` there")
        raw = r.stdout.strip()
    else:
        f = cdir / ".credentials.json"
        if not f.exists():
            raise LoginError(f"no claude.ai login ({f} missing); run `claude /login` there")
        raw = f.read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise LoginError("the stored login is not valid JSON; run `claude /login` for this config dir") from None


def access_token(cdir):
    oauth = (read_credentials(cdir).get("claudeAiOauth") or {})
    tok = oauth.get("accessToken")
    if not tok:
        raise LoginError("the stored login has no access token; run `claude /login` for this config dir")
    exp = oauth.get("expiresAt")
    if exp and dt.datetime.now().timestamp() * 1000 >= float(exp):
        raise LoginError("Claude Code's login has expired; jusage never renews it (that would rotate the token "
                         "behind Claude Code's back). Open a Claude Code window on this account and it refreshes itself.")
    return tok


def fetch_live(cdir):
    """-> the usage endpoint's JSON. Raises LoginError (no/expired login) or RuntimeError (transport)."""
    import urllib.request, urllib.error
    tok = access_token(cdir)
    req = urllib.request.Request(USAGE_URL, headers={"Authorization": f"Bearer {tok}",
                                 "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json",
                                 "User-Agent": f"jusage/{VERSION}"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        # urllib would re-send the Authorization header to wherever a redirect points; never follow one.
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise LoginError("Anthropic rejected the stored login (401); open a Claude Code window on this account") from None
        if e.code == 429:
            raise RuntimeError("rate limited by Anthropic (429)") from None
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:200]}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(str(e)) from None


def iso_minutes(iso):
    """Any ISO-8601 the endpoint sends -> local time, to the second, one fixed shape for every consumer."""
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().isoformat(timespec="seconds")
    except ValueError:
        return None


def meters(data):
    """Normalise the endpoint's reply into [(name, pct, resets_at_local_iso)].
    The `limits` array is authoritative (kind session / weekly_all / weekly_scoped + scope.model);
    replies without it carry the older five_hour / seven_day objects instead."""
    out = []
    if not isinstance(data, dict):
        return out
    limits = [e for e in data.get("limits") or [] if isinstance(e, dict) and e.get("percent") is not None]
    if limits:
        seen = set()
        for e in limits:
            mdl = (e.get("scope") or {}).get("model") or {}
            model = (mdl.get("display_name") or mdl.get("displayName") or "").split(" ")[0].lower()
            name = {"session": "five_hour", "weekly_all": "seven_day"}.get(e.get("kind", "")) or (f"seven_day_{model}" if model else None)
            if name and name not in seen:
                out.append((name, float(e["percent"]), iso_minutes(e.get("resets_at")))); seen.add(name)
        return out
    for k in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
        v = data.get(k)
        if isinstance(v, dict) and v.get("utilization") is not None:
            out.append((k, float(v["utilization"]), iso_minutes(v.get("resets_at"))))
    return out


def until(iso):
    if not iso:
        return ""
    t = dt.datetime.fromisoformat(iso)
    left = t - dt.datetime.now().astimezone()
    h, m = divmod(max(int(left.total_seconds()) // 60, 0), 60)
    return f"resets in {h}h {m:02d}m ({t:%a %H:%M})"


def load_live():
    """live.json: {config dir name: {"fetched": iso, "data": last good reply | None, "error": str | None}}.
    `data` is the last reply that succeeded; `error` is set when the most recent attempt failed,
    so a reading with both is a stale one and every consumer says so."""
    if not LIVE.exists():
        return {}
    store = json.loads(LIVE.read_text())
    # entries written before 0.1.19 kept the error inside data; read them, write the one shape
    for v in store.values():
        d = v.get("data")
        if isinstance(d, dict) and ("error" in d or "stale" in d):
            v["error"] = d.get("error_now") or d.get("error")
            v["data"] = None if "error" in d else {k: x for k, x in d.items() if k not in ("stale", "error_now")}
        v.setdefault("error", None)
    return store


def refresh_live(c, force=False, log=print):
    """Fetch every account's meters unless the cached reading is younger than LIVE_TTL_MIN.
    Returns the store; log() gets one line per account (nothing when quiet)."""
    store = load_live()
    now = dt.datetime.now().astimezone()
    for cdir in config_dirs():
        prev = store.get(cdir.name)
        if prev and prev["data"] and not prev["error"] and not force:
            age = (now - dt.datetime.fromisoformat(prev["fetched"])).total_seconds() / 60
            if age < LIVE_TTL_MIN:
                log(cdir.name, prev, f"cached {age:.0f} min ago, --force to refetch")
                continue
        try:
            data = fetch_live(cdir)
        except (LoginError, RuntimeError) as e:
            store[cdir.name] = {"fetched": prev["fetched"] if prev else None, "data": prev["data"] if prev else None, "error": str(e)}
        else:
            store[cdir.name] = {"fetched": now.isoformat(timespec="seconds"), "data": data, "error": None}
            c.executemany("INSERT OR IGNORE INTO meter_log VALUES (?,?,?,?,?)",
                          [(now.isoformat(timespec="seconds"), cdir.name, n, p_, r_) for n, p_, r_ in meters(data)])
            c.commit()
        log(cdir.name, store[cdir.name], "")
    write_private(LIVE, json.dumps(store, indent=1) + "\n")
    return store


def live_view(c, store, with_forecast=True):
    """The one shape the menu bar, the shared report and the dashboard consume:
    {account label: {fetched, stale, error, meters: [{name, pct, resets_at, forecast}]}}."""
    out = {}
    for acct, v in store.items():
        ms = meters(v["data"]) if v["data"] else []
        out[acct_label(acct)] = {
            "fetched": v["fetched"], "stale": bool(v["error"] and ms), "error": v["error"],
            "meters": [{"name": n, "pct": p, "resets_at": r,
                        "forecast": forecast(c, acct, n, p, r) if with_forecast else None} for n, p, r in ms]}
    return out


def cmd_live(args):
    c = db()

    def show(name, entry, note):
        head = f"\n{acct_label(name)} ({name})"
        if note:
            head += f"  {note}"
        if entry["error"] and entry["data"]:
            head += f"  STALE reading from {entry['fetched'][11:16]}: {entry['error']}"
        print(head)
        if not entry["data"]:
            print(f"  {entry['error']}")
            return
        ms = meters(entry["data"])
        if not ms:
            print("  unexpected reply, raw keys:", ", ".join(entry["data"].keys()))
        for n, pct, reset in ms:
            bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
            print(f"  {n.replace('_', ' '):<26} {bar} {pct:5.1f}%   {until(reset)}")

    store = refresh_live(c, force=args.force, log=show)
    if args.raw:
        print(json.dumps(store, indent=1))


# ---------- menu bar feed ----------
def cmd_menu(args):
    """Everything the menu bar app shows, as one JSON object on stdout. Any failure exits non-zero
    with the reason on stderr, which the app shows in place of the numbers."""
    c = db()
    scan(c, full=False)
    store = refresh_live(c, force=args.force, log=lambda *a: None)
    pricing = load_pricing()
    today = dt.date.today().isoformat()
    rows = fetch(c, since_date("7d"))
    day, week, accts, models, projs = zero(), zero(), defaultdict(zero), defaultdict(zero), defaultdict(zero)
    for r in rows:
        add(week, r, pricing)
        if r["day"] == today:
            add(day, r, pricing); add(accts[r["account"]], r, pricing); add(models[r["model"]], r, pricing)
            add(projs["subagents" if r["project"].startswith("agent-") else r["project"]], r, pricing)
    windows = []
    for acct, bl in blocks_by_account(rows).items():
        b = bl[-1]
        a = zero()
        for r in b["rows"]: add(a, r, pricing)
        windows.append({"account": acct_label(acct), "start": b["start"].isoformat(timespec="minutes"), "end": b["end"].isoformat(timespec="minutes"),
                        "tokens": total_tokens(a), "cost": round(a["cost"], 2), "open": dt.datetime.now().astimezone() < b["end"]})
    live = live_view(c, store)
    # last three calendar months, newest first
    months = []
    first = dt.date.today().replace(day=1)
    for _ in range(3):
        key = first.strftime("%Y-%m")
        mrows = [r for r in fetch(c, key + "-01") if r["day"].startswith(key)]
        tot, mm, pp = zero(), defaultdict(zero), defaultdict(zero)
        for r in mrows:
            add(tot, r, pricing); add(mm[r["model"]], r, pricing); add(pp[r["project"]], r, pricing)
        months.append({"key": key, "label": first.strftime("%B %Y"), "tokens": total_tokens(tot), "cost": round(tot["cost"], 2), "calls": tot["calls"],
                       "types": {"input": tot["input"], "cache_w": tot["cache_5m"] + tot["cache_1h"], "cache_read": tot["cache_read"], "output": tot["output"]},
                       "models": sorted(({"model": m, "tokens": total_tokens(x), "cost": round(x["cost"], 2)} for m, x in mm.items()), key=lambda d: -d["tokens"]),
                       "projects": sorted(({"project": p_, "tokens": total_tokens(x), "cost": round(x["cost"], 2)} for p_, x in pp.items()), key=lambda d: -d["tokens"])[:8]})
        first = (first - dt.timedelta(days=1)).replace(day=1)
    cfg = load_config()
    out = {"version": VERSION, "user": cfg.get("user"), "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "today": {"tokens": total_tokens(day), "cost": round(day["cost"], 2), "calls": day["calls"]},
           "week": {"tokens": total_tokens(week), "cost": round(week["cost"], 2)},
           "windows": windows, "live": live, "months": months,
           "accounts": {acct_label(a_): {"tokens": total_tokens(x), "cost": round(x["cost"], 2)} for a_, x in accts.items()},
           "models": sorted(({"model": m, "tokens": total_tokens(x), "cost": round(x["cost"], 2)} for m, x in models.items()), key=lambda d: -d["tokens"]),
           "projects": sorted(({"project": p_, "tokens": total_tokens(x), "cost": round(x["cost"], 2)} for p_, x in projs.items()), key=lambda d: -d["tokens"])[:8],
           "dashboard": str(Path(cfg["reports_dir"]).parent / "dashboard.html") if cfg.get("reports_dir") else None,
           "people": other_people(cfg, today)}
    print(json.dumps(out))


def other_people(cfg, today):
    """Everyone else's shared report, condensed for the menu bar: today / 7d / 30d totals + their meters."""
    rd = cfg.get("reports_dir")
    if not rd or not Path(rd).is_dir():
        return []
    me = f"{cfg.get('user')}@{socket.gethostname().split('.')[0]}"
    out = []
    for p in sorted(Path(rd).glob("*.json")):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if f"{r.get('user')}@{r.get('host')}" == me:
            continue
        days = r.get("days", {})
        def span(n):
            lo = (dt.date.today() - dt.timedelta(days=n - 1)).isoformat()
            t = sum(sum(a.get(k, 0) for k in COLS) for d, a in days.items() if d >= lo)
            c = sum(a.get("cost", 0) for d, a in days.items() if d >= lo)
            return {"tokens": t, "cost": round(c, 2)}
        a = days.get(today, {})
        out.append({"user": r.get("user"), "host": r.get("host"), "updated": r.get("generated"),
                    "today": {"tokens": sum(a.get(k, 0) for k in COLS), "cost": round(a.get("cost", 0), 2), "calls": a.get("calls", 0)},
                    "week": span(7), "month": span(30), "live": r.get("live") or {}})
    return out


# ---------- report ----------
def fmt_n(n):
    return (f"{n/1e9:.2f}B" if n >= 1e9 else f"{n/1e6:.1f}M" if n >= 1e6
            else f"{n/1e3:.0f}k" if n >= 1e3 else str(int(n)))


def print_table(title, groups, pricing, key_name):
    print(f"\n{title}")
    hdr = f"{key_name:<28} {'calls':>6} {'input':>8} {'cache-w':>8} {'cache-r':>8} {'output':>8} {'total':>8} {'$ api':>9}"
    print(hdr); print("-" * len(hdr))
    tot = zero()
    for k, a in groups:
        for c_ in COLS: tot[c_] += a[c_]
        tot["cost"] += a["cost"]; tot["calls"] += a["calls"]; tot["unpriced"] += a["unpriced"]
        flag = "*" if a["unpriced"] else ""
        print(f"{k:<28} {a['calls']:>6} {fmt_n(a['input']):>8} {fmt_n(a['cache_5m']+a['cache_1h']):>8} "
              f"{fmt_n(a['cache_read']):>8} {fmt_n(a['output']):>8} {fmt_n(total_tokens(a)):>8} {a['cost']:>8.2f}{flag}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<28} {tot['calls']:>6} {fmt_n(tot['input']):>8} {fmt_n(tot['cache_5m']+tot['cache_1h']):>8} "
          f"{fmt_n(tot['cache_read']):>8} {fmt_n(tot['output']):>8} {fmt_n(total_tokens(tot)):>8} {tot['cost']:>8.2f}")
    if tot["unpriced"]:
        print(f"* {tot['unpriced']} calls on models missing from pricing.json (cost shown without them)")


def cmd_report(args):
    pricing = load_pricing()
    rows = fetch(db(), since_date(args.since), args.account)
    if not rows:
        print("no records — run `scan` first"); return
    by = args.by
    if by == "blocks":
        for acct, bl in blocks_by_account(rows).items():
            print(f"\n5-hour windows, {acct_label(acct)} (last {args.last})")
            for b in bl[-args.last:]:
                a = zero()
                for r in b["rows"]: add(a, r, pricing)
                live = " <- open" if dt.datetime.now().astimezone() < b["end"] else ""
                print(f"{b['start']:%a %m-%d %H:%M}–{b['end']:%H:%M}  {fmt_n(total_tokens(a)):>8} tokens  "
                      f"{a['calls']:>5} calls  ${a['cost']:.2f}{live}")
        return
    groups = defaultdict(zero)
    for r in rows:
        add(groups[r[by]], r, pricing)
    items = sorted(groups.items()) if by == "day" else sorted(groups.items(), key=lambda kv: -total_tokens(kv[1]))
    print_table(f"Claude Code usage by {by} (since {rows[0]['day']}, local time)", items, pricing, by)
    print("\n$ api = what this would cost at first-party API rates; on a Max plan it is a relative scale, not a bill.")


# ---------- share ----------
def build_summary(c, user, host, pricing, share_projects=False):
    """The shared report. Project folder names are included only when share_projects is set
    (`share --projects`, remembered); by default nothing about what you work on leaves the machine."""
    rows = fetch(c, since_date("90d"))
    days, models = defaultdict(zero), defaultdict(lambda: defaultdict(zero))
    accts = defaultdict(lambda: defaultdict(zero))
    projs = defaultdict(lambda: defaultdict(zero))
    accounts = set()
    for r in rows:
        add(days[r["day"]], r, pricing)
        add(models[r["day"]][r["model"]], r, pricing)
        add(accts[r["day"]][r["account"]], r, pricing)
        add(projs[r["day"]][r["project"]], r, pricing)
        accounts.add(r["account"])
    blk = {}
    for acct, bl in blocks_by_account(rows).items():
        blk[acct_label(acct)] = []
        for b in bl[-50:]:
            a = zero()
            for r in b["rows"]: add(a, r, pricing)
            blk[acct_label(acct)].append({"start": b["start"].isoformat(timespec="minutes"), "end": b["end"].isoformat(timespec="minutes"),
                                          "tokens": total_tokens(a), "calls": a["calls"], "cost": round(a["cost"], 2)})
    return {
        "user": user, "host": host, "tool_version": VERSION,
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "accounts": sorted(acct_label(a_) for a_ in accounts),
        "live": live_view(c, load_live()),
        "days": {d: {**{k: a[k] for k in COLS}, "calls": a["calls"], "cost": round(a["cost"], 2),
                     "models": {m: {**{k: x[k] for k in COLS}, "tokens": total_tokens(x), "calls": x["calls"], "cost": round(x["cost"], 2)}
                                for m, x in models[d].items()},
                     "accounts": {acct_label(a_): {"tokens": total_tokens(x), "cost": round(x["cost"], 2)} for a_, x in accts[d].items()},
                     "projects": ({p_: {"tokens": total_tokens(x), "cost": round(x["cost"], 2)}
                                   for p_, x in sorted(projs[d].items(), key=lambda kv: -total_tokens(kv[1]))[:10]}
                                  if share_projects else {})}
                 for d, a in sorted(days.items())},
        "blocks": blk,
    }


def cmd_share(args):
    user, host = whoami(args)
    cfg = load_config()
    if getattr(args, "projects", None) is not None:
        cfg["share_projects"] = bool(args.projects); save_config(cfg)
    share_projects = bool(cfg.get("share_projects"))
    out = reports_dir(args) / f"{user}@{host}.json"
    # temp file + rename: a sync client or another Mac's dashboard never sees a half-written report
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(build_summary(db(), user, host, load_pricing(), share_projects), indent=1) + "\n")
    os.replace(tmp, out)
    print(f"wrote {out}" + ("" if share_projects else "  (project names not shared; `share --projects` to include them)"))


# ---------- dashboard ----------
def cmd_dashboard(args):
    import dashboard
    rd = reports_dir(args)
    reps = []
    for p in sorted(rd.glob("*.json")):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError) as e:   # mid-sync or half-written file: skip it, say so
            print(f"skipping {p.name}: {e}", file=sys.stderr)
            continue
        missing = [k for k in ("user", "host", "days") if not isinstance(r, dict) or k not in r]
        if missing or not isinstance(r["days"], dict):
            print(f"skipping {p.name}: not a jusage report (missing {', '.join(missing) or 'day table'}; "
                  f"written by jusage {r.get('tool_version', '?') if isinstance(r, dict) else '?'})", file=sys.stderr)
            continue
        reps.append(r)
    if not reps:
        sys.exit(f"no reports in {rd} — run `share` first")
    out = Path(args.out) if args.out else rd.parent / "dashboard.html"
    out.write_text(dashboard.render(reps, VERSION))
    print(f"wrote {out}  ({len(reps)} people: {', '.join(r['user'] + '@' + r['host'] for r in reps)})")
    if args.open:
        import subprocess
        subprocess.run(["open", str(out)], check=False)


def cmd_update(args):
    args.full = False; args.force = False; args.raw = False
    cmd_scan(args); cmd_live(args); cmd_share(args); args.out = None; cmd_dashboard(args)


# ---------- main ----------
def main():
    p = argparse.ArgumentParser(prog="claude-usage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="ingest transcripts (incremental)"); s.add_argument("--full", action="store_true", help="re-read every file")
    lv = sub.add_parser("live", help="Claude's own 5-hour / 7-day meters for every account on this Mac")
    lv.add_argument("--raw", action="store_true", help="also dump the raw reply")
    lv.add_argument("--force", action="store_true", help="ignore the 15-minute cache")
    mj = sub.add_parser("menu-json", help="one JSON blob for the menu bar app (scan + live, quiet)")
    mj.add_argument("--force", action="store_true")
    r = sub.add_parser("report", help="print usage tables")
    r.add_argument("--since", default="7d", help="7d | 30d | YYYY-MM-DD | all")
    r.add_argument("--by", default="day", choices=["day", "model", "account", "project", "blocks"])
    r.add_argument("--account", help="only this config dir name, e.g. .claude-newaccount")
    r.add_argument("--last", type=int, default=12, help="how many 5h blocks (with --by blocks)")
    su = sub.add_parser("set-user", help="your name as shown to the others (remembered; `share --user` does the same)")
    su.add_argument("name")
    for name in ("share", "dashboard", "update"):
        x = sub.add_parser(name)
        x.add_argument("--reports", help="shared reports folder (remembered after first use)")
        x.add_argument("--user", help="your name as shown to the others (remembered)")
        if name == "share":
            x.add_argument("--projects", dest="projects", action="store_true", default=None,
                           help="include project folder names in the shared report (remembered)")
            x.add_argument("--no-projects", dest="projects", action="store_false", help="stop sharing project names (remembered)")
        else:
            x.add_argument("--out", help="dashboard path (default: <reports>/../dashboard.html)")
            x.add_argument("--open", action="store_true", help="open the page after building")
    args = p.parse_args()
    if args.cmd == "report" and args.since == "all":
        args.since = None
    {"scan": cmd_scan, "live": cmd_live, "menu-json": cmd_menu, "report": cmd_report, "share": cmd_share,
     "dashboard": cmd_dashboard, "update": cmd_update, "set-user": cmd_set_user}[args.cmd](args)


if __name__ == "__main__":
    main()
