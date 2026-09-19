#!/usr/bin/env python3
"""claude-usage — track Claude Code token usage across every account on this Mac,
and share a daily summary with the people you split a plan with, so it shows who used what.

Zero dependencies (Python 3.9+ stdlib). Never guesses a plan limit: it reports
tokens, 5-hour windows, and the equivalent first-party API cost, nothing more.

Commands
  scan        read every ~/.claude*/projects/**/*.jsonl into ~/.claude-usage/usage.db
  live        Claude's own 5-hour / 7-day meters, per account (reads your Keychain login)
  report      totals by day / model / account / project / 5-hour block
  share       write <user>@<host>-<id>.json into the shared reports folder
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


def install_id():
    """Four random hex chars made once per install and kept in config.json. Part of the shared
    report's file name, so two people with the same name and the same Mac name never overwrite
    each other's report (Apple's default host names collide constantly)."""
    cfg = load_config()
    if not cfg.get("install_id"):
        import secrets
        cfg["install_id"] = secrets.token_hex(2); save_config(cfg)
    return cfg["install_id"]


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
def account_identity(cdir):
    """Which claude.ai account a config dir is logged into, from the `oauthAccount` block Claude Code
    itself writes into <config dir>/.claude.json. Nothing is fetched and no token is read. None when
    the file or the block is missing — a meter is still a meter, it just falls back to '<you> #n'."""
    # Claude Code writes ~/.claude.json for the default dir and <dir>/.claude.json when
    # CLAUDE_CONFIG_DIR is set; a Mac can have both, so the most recently written one that names an account wins.
    cands = [Path(cdir) / ".claude.json"] + ([HOME / ".claude.json"] if Path(cdir) == HOME / ".claude" else [])
    found = []
    for f in cands:
        try:
            o = (json.loads(f.read_text()).get("oauthAccount") or {})
            if o.get("emailAddress") and o.get("accountUuid"):
                found.append((f.stat().st_mtime, {"email": o["emailAddress"], "id": o["accountUuid"]}))
        except (OSError, ValueError, AttributeError):
            continue
    return max(found)[1] if found else None


def identity_for(name):
    """The identity of the config dir called `name` on this Mac, or None."""
    for d in config_dirs():
        if d.name == name:
            return account_identity(d)
    return None


@functools.lru_cache(maxsize=None)
def acct_label(name):
    """The account's email when this Mac knows it, else '.claude' -> 'Sam #1', '.claude-work' -> 'Sam #2'.
    The email is what makes two people's reports line up: the same plan account is one row on the
    dashboard instead of '<me> #2' on one Mac and '<you> #1' on the other. The number is still assigned the first time a
    config dir is seen and remembered in config.json, so the fallback never renumbers the others."""
    cfg = load_config()
    numbers = cfg.setdefault("accounts", {})
    new = [d.name for d in config_dirs() if d.name not in numbers]
    if new:
        for n in sorted(new):
            numbers[n] = max(numbers.values(), default=0) + 1
        save_config(cfg)
    ident = identity_for(name)
    if ident:
        return ident["email"]
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
    hours = min(left / pace, 24 * 365) if pace > 0 else None      # capped: a near-idle day would overflow timedelta
    wall = (now + dt.timedelta(hours=hours)).isoformat(timespec="seconds") if hours is not None else None
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
SHARE_EVERY_MIN = 20   # how old this Mac's shared report may get before menu-json rewrites it


class LoginError(Exception):
    """No usable claude.ai login for a config dir. The message is what the user should do."""


def keychain_candidates(cdir):
    """Every Keychain service name this config dir's login might sit under, most specific first.
    Claude Code names the item 'Claude Code-credentials-<sha256(<config dir as it was given>)[:8]>',
    so the hash depends on the exact spelling of the path: with or without a trailing slash,
    symlink or real path, $CLAUDE_CONFIG_DIR verbatim. Every spelling is tried. For the default
    ~/.claude the unsuffixed 'Claude Code-credentials' item is tried last: some builds (2.1.27x seen)
    keep the default dir's login there and leave the hashed item holding only mcpOAuth."""
    import hashlib
    cdir = Path(cdir)
    spellings = [str(cdir), str(cdir.resolve()), str(cdir) + "/", os.path.expanduser("~/" + cdir.name)]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        try:
            if Path(env).expanduser().resolve() == cdir.resolve():
                spellings += [env, os.path.expanduser(env), env.rstrip("/")]
        except OSError:
            pass
    names = []
    for sp in spellings:
        n = "Claude Code-credentials-" + hashlib.sha256(sp.encode()).hexdigest()[:8]
        if n not in names:
            names.append(n)
    if cdir.resolve() == (HOME / ".claude").resolve():
        names.append("Claude Code-credentials")
    return names


def keychain_service(cdir):
    return keychain_candidates(cdir)[0]


def probe_keychain(cdir):
    """Read every candidate item. -> list of {service, status, creds, expires_at}; status is one of
    absent · denied:<security error> · unparsable · no-login (JSON but no claudeAiOauth token) · ok."""
    import subprocess
    out = []
    for svc in keychain_candidates(cdir):
        row = {"service": svc, "status": "absent", "creds": None, "expires_at": None}
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", svc, "-w"],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise LoginError("the Keychain did not answer in 30 s; a permission dialog is probably waiting on screen (click Always Allow)") from None
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            row["status"] = "absent" if (r.returncode == 44 or "could not be found" in err) else "denied:" + (err or f"security exit {r.returncode}")
            out.append(row); continue
        try:
            creds = json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            row["status"] = "unparsable"; out.append(row); continue
        row["creds"] = creds
        oauth = (creds.get("claudeAiOauth") or {})
        if oauth.get("accessToken"):
            row["status"] = "ok"; row["expires_at"] = float(oauth.get("expiresAt") or 0)
        else:
            row["status"] = "no-login"
        out.append(row)
    return out


def read_credentials(cdir, want_source=False):
    """The credentials JSON Claude Code wrote for this config dir. macOS: every candidate Keychain
    item is read and the login expiring furthest in the future wins, so a stale blob in one item
    never hides a live login in another. Elsewhere: <config dir>/.credentials.json. Read only,
    never written. Raises LoginError with the real reason (not logged in vs Keychain refused)."""
    if sys.platform == "darwin":
        rows = probe_keychain(cdir)
        ok = [r for r in rows if r["status"] == "ok"]
        if ok:
            best = max(ok, key=lambda r: r["expires_at"])
            return (best["creds"], best["service"]) if want_source else best["creds"]
        denied = [r for r in rows if r["status"].startswith("denied:")]
        if denied:
            raise LoginError("the Keychain refused to hand over the login (" + denied[0]["status"][7:] +
                             "); unlock the Keychain or click Always Allow, then run `jusage doctor`")
        if any(r["status"] == "no-login" for r in rows):
            raise LoginError("the Keychain item for this config dir holds no claude.ai login; run `claude /login` there (`jusage doctor` shows what was found)")
        if any(r["status"] == "unparsable" for r in rows):
            raise LoginError("the stored login is not valid JSON; run `claude /login` for this config dir")
        raise LoginError("no claude.ai login in the Keychain for this config dir; run `claude /login` there (`jusage doctor` lists the item names tried)")
    f = Path(cdir) / ".credentials.json"
    if not f.exists():
        raise LoginError("no claude.ai login (.credentials.json missing in this config dir); run `claude /login` there")
    try:
        creds = json.loads(f.read_text())
    except json.JSONDecodeError:
        raise LoginError("the stored login is not valid JSON; run `claude /login` for this config dir") from None
    return (creds, str(f)) if want_source else creds


def login_warning(cdir):
    """A one-line caveat when the login came from the unsuffixed legacy item, which carries no
    account identity and on a multi-account Mac can belong to another account. None otherwise."""
    try:
        _, src = read_credentials(cdir, want_source=True)
    except LoginError:
        return None
    if src == "Claude Code-credentials":
        return "login read from the legacy Keychain item, which names no account: if these numbers look like another account's, run `claude /login` in this config dir"
    return None


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


def when(iso):
    """Any ISO string from any Mac -> an aware local datetime, or None. Reports arrive from other
    time zones, so their times are compared as instants, never as text."""
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return None


def iso_minutes(iso):
    """Any ISO-8601 the endpoint sends -> local time, to the second, one fixed shape for every consumer."""
    t = when(iso)
    return t.isoformat(timespec="seconds") if t else None


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


RESET_DUE = "reset due, waiting for a fresh reading"


def until(iso):
    """A countdown that never floors to '0h 00m': once the reset time is behind us the number on
    screen is last period's, and saying so is the only honest thing until the next fetch lands."""
    if not iso:
        return ""
    t = dt.datetime.fromisoformat(iso)
    left = (t - dt.datetime.now().astimezone()).total_seconds()
    if left <= 0:
        return RESET_DUE
    h, m = divmod(int(left) // 60, 60)
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


def reset_passed(data, now):
    """True when any meter in a cached reply has already reset. Such a reading is not just old,
    it is wrong (it shows the finished period), so it is refetched whatever the cache says."""
    for _, _, r in meters(data):
        try:
            if r and dt.datetime.fromisoformat(r) <= now:
                return True
        except ValueError:
            continue
    return False


def refresh_live(c, force=False, log=print):
    """Fetch every account's meters unless the cached reading is younger than LIVE_TTL_MIN
    (a reading whose reset time has passed is refetched anyway).
    Returns the store; log() gets one line per account (nothing when quiet)."""
    store = load_live()
    now = dt.datetime.now().astimezone()
    for cdir in config_dirs():
        prev, note = store.get(cdir.name), ""
        if prev and prev["data"] and not prev["error"] and not force:
            age = (now - dt.datetime.fromisoformat(prev["fetched"])).total_seconds() / 60
            if reset_passed(prev["data"], now):
                note = "reset passed, refetching"      # a new period behind the cache: the old % is a lie
            elif age < LIVE_TTL_MIN:
                log(cdir.name, prev, f"cached {age:.0f} min ago, --force to refetch")
                continue
        try:
            data = fetch_live(cdir)
        except (LoginError, RuntimeError) as e:
            store[cdir.name] = {"fetched": prev["fetched"] if prev else None, "data": prev["data"] if prev else None, "error": str(e)}
        else:
            store[cdir.name] = {"fetched": now.isoformat(timespec="seconds"), "data": data, "error": None,
                                "warning": login_warning(cdir)}
            c.executemany("INSERT OR IGNORE INTO meter_log VALUES (?,?,?,?,?)",
                          [(now.isoformat(timespec="seconds"), cdir.name, n, p_, r_) for n, p_, r_ in meters(data)])
            c.commit()
        log(cdir.name, store[cdir.name], note)
    write_private(LIVE, json.dumps(store, indent=1) + "\n")
    return store


def live_view(c, store, with_forecast=True):
    """The one shape the menu bar, the shared report and the dashboard consume:
    {account label: {fetched, stale, error, account, meters: [{name, pct, resets_at, forecast}]}}.
    `account` is the claude.ai identity (or None): it is what lets the dashboard show one row per
    plan account instead of one per person per Mac."""
    out = {}
    for acct, v in store.items():
        ms = meters(v["data"]) if v["data"] else []
        out[acct_label(acct)] = {
            "fetched": v["fetched"], "stale": bool(v["error"] and ms), "error": v["error"],
            "warning": v.get("warning"), "account": identity_for(acct),
            "meters": [{"name": n, "pct": p, "resets_at": r,
                        "forecast": forecast(c, acct, n, p, r) if with_forecast else None} for n, p, r in ms]}
    return out


def cmd_live(args):
    c = db()
    reports = split_reports(c, load_config())

    def show(name, entry, note):
        head = f"\n{acct_label(name)} ({name})"
        if note:
            head += f"  {note}"
        if entry["error"] and entry["data"]:
            head += f"  STALE reading from {entry['fetched'][11:16]}: {entry['error']}"
        print(head)
        if entry.get("warning"):
            print(f"  ⚠ {entry['warning']}")
        if not entry["data"]:
            print(f"  {entry['error']}")
            return
        ms = meters(entry["data"])
        if not ms:
            print("  unexpected reply, raw keys:", ", ".join(entry["data"].keys()))
        for n, pct, reset in ms:
            bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
            print(f"  {n.replace('_', ' '):<26} {bar} {pct:5.1f}%   {until(reset)}")
            line = split_text(meter_split(n, reset, acct_label(name), reports))
            if line:
                print(f"  {'':<26} {line}")

    store = refresh_live(c, force=args.force, log=show)
    if args.raw:
        print(json.dumps(store, indent=1))


# ---------- who used the meter ----------
def blocks_summary(c, pricing):
    """{account label: last 50 five-hour windows} — the part of the shared report that says WHEN
    spend happened. Its own function because the split needs it without building a whole report."""
    blk = {}
    for acct, bl in blocks_by_account(fetch(c, since_date("90d"))).items():
        rows = []
        for b in bl[-50:]:
            a = zero()
            for r in b["rows"]: add(a, r, pricing)
            rows.append({"start": b["start"].isoformat(timespec="minutes"), "end": b["end"].isoformat(timespec="minutes"),
                         "tokens": total_tokens(a), "calls": a["calls"], "cost": round(a["cost"], 2)})
        blk[acct_label(acct)] = rows
    return blk


def meter_split(meter_name, resets_at, account_label, reports):
    """Who spent what inside this meter's current period (resets_at back 5 h / 7 d, up to now).
    Anthropic meters an ACCOUNT, not a person: two people on one plan get one number. The honest
    apportionment is each person's api-equivalent spend inside that period, read from the 5-hour
    windows everyone shares — a share of spend, never a share of the percentage, because nobody
    outside Anthropic knows how the percentage is weighted.
    -> {"period_start", "parts": [{user, cost, share}] desc, "missing": [user]} or None.
    `missing` is whoever shares windows but none under this account label: a report written before
    0.1.22 labels accounts '<them> #1', so it cannot match, and saying so beats counting them as zero."""
    reset = when(resets_at)
    if not reset:
        return None
    start = reset - (dt.timedelta(hours=BLOCK_HOURS) if meter_name == "five_hour" else dt.timedelta(days=7))
    end = dt.datetime.now().astimezone()
    parts, missing = [], []
    for r in reports:
        blk = r.get("blocks") if isinstance(r.get("blocks"), dict) else None
        if not blk:
            continue
        user = r.get("user") or "someone"
        if account_label not in blk:
            missing.append(user)
            continue
        cost = 0.0
        for b in blk[account_label]:
            b0, b1 = when(b.get("start")), when(b.get("end"))
            if not b0 or not b1 or b1 <= b0:
                continue
            # a window the period boundary cuts counts by the fraction inside it. The divisor is the
            # window as far as it has actually run: an open window's cost is what it has spent so far,
            # and dividing that by a full five hours would discount it a second time.
            overlap = (min(end, b1) - max(start, b0)).total_seconds()
            span = (min(end, b1) - b0).total_seconds()
            if overlap > 0 and span > 0:
                cost += (b.get("cost") or 0) * overlap / span
        if cost > 0:
            parts.append({"user": user, "cost": round(cost, 2)})
    total = sum(p["cost"] for p in parts)
    for p in parts:
        p["share"] = round(p["cost"] / total, 4) if total else 0.0
    parts.sort(key=lambda p: -p["cost"])
    if not parts and not missing:
        return None
    return {"period_start": start.isoformat(timespec="minutes"), "parts": parts, "missing": sorted(set(missing))}


def split_text(sp):
    """One line of plain text for a split — the wording the CLI and the dashboard both use."""
    if not sp:
        return ""
    bits = [f"{p['user']} {p['share'] * 100:.0f}% (${p['cost']:,.0f})" for p in sp.get("parts") or []]
    out = ["this period: " + " · ".join(bits)] if bits else []
    out += [f"{u}: not reported yet (update jusage)" for u in sp.get("missing") or []]
    return " · ".join(out)


def split_reports(c, cfg, reports=None):
    """What meter_split reads: my own windows straight from the db — the report I shared can be
    twenty minutes old — then everyone else's. [] when no folder is shared with anyone."""
    rd = cfg.get("reports_dir")
    if not rd or not Path(os.path.expanduser(rd)).is_dir():
        return []
    if reports is None:
        reports = load_reports(os.path.expanduser(rd))
    me = install_id()
    return ([{"user": cfg.get("user") or report_id(cfg)[0], "blocks": blocks_summary(c, load_pricing())}]
            + [r for r in reports if r.get("install_id") != me])


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
    rd = Path(os.path.expanduser(cfg["reports_dir"])) if cfg.get("reports_dir") else None
    # The app polls this every 300 s, so refreshing the shared report and the dashboard here is what
    # keeps both within SHARE_EVERY_MIN of live for everyone who runs the app (`update` is for cron).
    # It must never cost the menu bar its numbers, so the failure is reported in the feed, not raised.
    share_error, shared_at = None, None
    if rd:
        try:
            mine = rd / report_id(cfg)[2]
            fresh = mine.exists() and (dt.datetime.now().timestamp() - mine.stat().st_mtime) / 60 <= SHARE_EVERY_MIN
            if not fresh:
                share(c, cfg, rd)
                build_dashboard(rd)
            shared_at = dt.datetime.fromtimestamp(mine.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        except Exception as e:
            share_error = f"{type(e).__name__}: {e}"
    # the shared folder is read once here and handed to both consumers of it
    reports = load_reports(rd) if rd and rd.is_dir() else []
    splittable = split_reports(c, cfg, reports)
    for label, lv in live.items():
        for m in lv["meters"]:
            m["split"] = meter_split(m["name"], m["resets_at"], label, splittable)
    out = {"version": VERSION, "user": cfg.get("user"), "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "share_error": share_error, "shared_at": shared_at,
           "today": {"tokens": total_tokens(day), "cost": round(day["cost"], 2), "calls": day["calls"]},
           "week": {"tokens": total_tokens(week), "cost": round(week["cost"], 2)},
           "windows": windows, "live": live, "months": months,
           "accounts": {acct_label(a_): {"tokens": total_tokens(x), "cost": round(x["cost"], 2)} for a_, x in accts.items()},
           "models": sorted(({"model": m, "tokens": total_tokens(x), "cost": round(x["cost"], 2)} for m, x in models.items()), key=lambda d: -d["tokens"]),
           "projects": sorted(({"project": p_, "tokens": total_tokens(x), "cost": round(x["cost"], 2)} for p_, x in projs.items()), key=lambda d: -d["tokens"])[:8],
           "dashboard": str(Path(cfg["reports_dir"]).parent / "dashboard.html") if cfg.get("reports_dir") else None,
           "people": other_people(reports, today)}
    print(json.dumps(out))


def other_people(reports, today):
    """Everyone else's shared report, condensed for the menu bar: today / 7d / 30d totals + their meters.
    Takes the already-loaded reports: the shared folder is read once per run, by the caller."""
    me = install_id()
    now = dt.datetime.now().astimezone()
    out = []
    for r in reports:
        if r.get("install_id") == me:
            continue
        days = r.get("days", {})
        def span(n):
            lo = (dt.date.today() - dt.timedelta(days=n - 1)).isoformat()
            t = sum(sum(a.get(k, 0) for k in COLS) for d, a in days.items() if d >= lo)
            c = sum(a.get("cost", 0) for d, a in days.items() if d >= lo)
            return {"tokens": t, "cost": round(c, 2)}
        a = days.get(today, {})
        gen = r.get("generated")
        try:                                   # how stale their page is; nothing runs on their Mac on our say-so
            age = round((now - dt.datetime.fromisoformat(gen)).total_seconds() / 3600, 2) if gen else None
        except ValueError:
            age = None
        out.append({"user": r.get("user"), "host": r.get("host"), "updated": gen, "age_hours": age,
                    "today": {"tokens": sum(a.get(k, 0) for k in COLS), "cost": round(a.get("cost", 0), 2), "calls": a.get("calls", 0)},
                    "week": span(7), "month": span(30), "live": r.get("live") or {},
                    "account_ids": r.get("account_ids") or {}})
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
    return {
        "user": user, "host": host, "install_id": install_id(), "tool_version": VERSION,
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "accounts": sorted(acct_label(a_) for a_ in accounts),
        # label -> claude.ai account uuid, so another Mac can tell "the same account" from "the same name"
        "account_ids": {acct_label(d.name): i["id"] for d in config_dirs() for i in [account_identity(d)] if i},
        "live": live_view(c, load_live()),
        "days": {d: {**{k: a[k] for k in COLS}, "calls": a["calls"], "cost": round(a["cost"], 2),
                     "models": {m: {**{k: x[k] for k in COLS}, "tokens": total_tokens(x), "calls": x["calls"], "cost": round(x["cost"], 2)}
                                for m, x in models[d].items()},
                     "accounts": {acct_label(a_): {"tokens": total_tokens(x), "cost": round(x["cost"], 2)} for a_, x in accts[d].items()},
                     "projects": ({p_: {"tokens": total_tokens(x), "cost": round(x["cost"], 2)}
                                   for p_, x in sorted(projs[d].items(), key=lambda kv: -total_tokens(kv[1]))[:10]}
                                  if share_projects else {})}
                 for d, a in sorted(days.items())},
        "blocks": blocks_summary(c, pricing),
    }


def report_id(cfg):
    """(your name, this Mac's short host name, the file name `share` writes). One function, so
    menu-json can find the file `share` wrote without guessing at the name."""
    user = cfg.get("user") or os.environ.get("USER", "unknown")
    host = socket.gethostname().split(".")[0]
    return user, host, f"{user}@{host}-{install_id()}.json"


def share(c, cfg, rd):
    """Write this Mac's report into the shared folder and return (path, name of the old-style file
    removed or None). Prints nothing — menu-json calls this too and its stdout is the app's JSON."""
    rd = Path(rd)
    rd.mkdir(parents=True, exist_ok=True)
    user, host, name = report_id(cfg)
    out = rd / name
    # temp file + rename: a sync client or another Mac's dashboard never sees a half-written report
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(build_summary(c, user, host, load_pricing(), bool(cfg.get("share_projects"))), indent=1) + "\n")
    os.replace(tmp, out)
    old = rd / f"{user}@{host}.json"          # the pre-0.1.21 name of this same report
    if old.exists():
        old.unlink()
        return out, old.name
    return out, None


def cmd_share(args):
    whoami(args)                              # --user, if given, is remembered before the report is built
    cfg = load_config()
    if getattr(args, "projects", None) is not None:
        cfg["share_projects"] = bool(args.projects); save_config(cfg)
    rd = reports_dir(args)
    out, removed = share(db(), cfg, rd)
    if removed:
        print(f"removed {removed} (this report is now {out.name})")
    print(f"wrote {out}" + ("" if cfg.get("share_projects") else "  (project names not shared; `share --projects` to include them)"))


# ---------- dashboard ----------
def load_reports(rd):
    """Every usable report in the shared folder, newest one per Mac. Two files can describe the same
    Mac — 0.1.21 renamed <user>@<host>.json to <user>@<host>-<id>.json, and a reinstall makes a new id —
    so the older `generated` is ignored and said so on stderr. Never deleted: other people's files are theirs."""
    best = {}
    for p in sorted(Path(rd).glob("*.json")):
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
        key = (r["user"], r["host"])
        prev = best.get(key)
        if prev is None:
            best[key] = (p, r)
            continue
        newer, older = ((p, r), prev) if (r.get("generated") or "") > (prev[1].get("generated") or "") else (prev, (p, r))
        print(f"ignoring {older[0].name}: {newer[0].name} is a newer report for {key[0]}@{key[1]}", file=sys.stderr)
        best[key] = newer
    return [r for _, r in best.values()]


def build_dashboard(rd, out=None):
    """Render every report in the folder into one page; returns (path, reports).
    RuntimeError when the folder holds none — the caller decides whether that is fatal."""
    import dashboard
    reps = load_reports(rd)
    if not reps:
        raise RuntimeError(f"no reports in {rd} — run `share` first")
    out = Path(out) if out else Path(rd).parent / "dashboard.html"
    out.write_text(dashboard.render(reps, VERSION))
    return out, reps


def cmd_dashboard(args):
    rd = reports_dir(args)
    try:
        out, reps = build_dashboard(rd, args.out)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"wrote {out}  ({len(reps)} people: {', '.join(r['user'] + '@' + r['host'] for r in reps)})")
    if args.open:
        import subprocess
        subprocess.run(["open", str(out)], check=False)


def cmd_update(args):
    args.full = False; args.force = False; args.raw = False
    cmd_scan(args); cmd_live(args); cmd_share(args); args.out = None; cmd_dashboard(args)


def cmd_doctor(args):
    """Per config dir: which Keychain items were tried, what each held, which one is used, and the
    account .claude.json names. The first thing to run when a meter says STALE or 'no login'."""
    for cdir in config_dirs():
        ident = account_identity(cdir)
        print(f"\n{cdir}  ({ident['email'] if ident else 'no oauthAccount in .claude.json'})")
        if sys.platform != "darwin":
            f = cdir / ".credentials.json"
            print(f"  {f}: {'present' if f.exists() else 'missing'}")
            continue
        try:
            rows = probe_keychain(cdir)
        except LoginError as e:
            print(f"  {e}"); continue
        ok = [r for r in rows if r["status"] == "ok"]
        chosen = max(ok, key=lambda r: r["expires_at"])["service"] if ok else None
        now_ms = dt.datetime.now().timestamp() * 1000
        for r in rows:
            mark = "→" if r["service"] == chosen else " "
            exp = ""
            if r["expires_at"]:
                left = (r["expires_at"] - now_ms) / 60000
                exp = f"  expires {'in %.0f min' % left if left > 0 else '%.0f min AGO' % -left}"
            print(f"  {mark} {r['service']:<36} {r['status']}{exp}")
        if not chosen:
            print("  no usable login: run `claude /login` in this config dir")
        elif chosen == "Claude Code-credentials":
            print("  ⚠ using the legacy unsuffixed item; it names no account (see README, Keychain)")


# ---------- main ----------
def main():
    p = argparse.ArgumentParser(prog="claude-usage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="ingest transcripts (incremental)"); s.add_argument("--full", action="store_true", help="re-read every file")
    lv = sub.add_parser("live", help="Claude's own 5-hour / 7-day meters for every account on this Mac")
    lv.add_argument("--raw", action="store_true", help="also dump the raw reply")
    lv.add_argument("--force", action="store_true", help="ignore the 15-minute cache")
    sub.add_parser("doctor", help="per config dir: Keychain items tried, what each held, which is used")
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
     "dashboard": cmd_dashboard, "update": cmd_update, "set-user": cmd_set_user, "doctor": cmd_doctor}[args.cmd](args)


if __name__ == "__main__":
    main()
