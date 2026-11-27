"""jusage engine: one command per run, then exit (E24).

    claude_usage.py [--state-dir D] <command> ...      (--version prints VERSION)

Commands: scan [--full] · live [--raw] [--force] · menu-json [--force] ·
report --by day|model|account|project|blocks [--since] [--account] [--last] · set-user NAME ·
share [--reports] [--user] [--projects|--no-projects] · dashboard [--reports] [--out] [--open] ·
update [--reports] [--user] [--out] [--open] [--projects|--no-projects] · doctor ·
statusline on|off|status [--folder K | --all] · config get [KEY] | read-every SECONDS | python PATH

Exit codes: 0 done; 2 bring-up refused (one stderr line `jusage: <file>: <problem>`); 1 any other
failure (one stderr line; the full trace goes to logs/engine.log). `menu-json` prints feed schema
2 and nothing else on stdout.

This file wires the engine's chairs together and owns nothing else: the start order (state folder
0700, bring-up, config edit), the one gate wiring (RULING 14), and the adapters to E15 (status
line), E20 (sharing) and D1 (dashboard page), which are imported only when a run needs them.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ENGINE = str(Path(__file__).resolve().parent)
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)                        # RULING 5: the package is `ju`, found here

import argparse                                         # noqa: E402
import importlib                                        # noqa: E402
import json                                             # noqa: E402
import os                                               # noqa: E402
import random                                           # noqa: E402
import sqlite3                                          # noqa: E402
import subprocess                                       # noqa: E402
import traceback                                        # noqa: E402
from dataclasses import dataclass, field               # noqa: E402
from datetime import datetime, timedelta               # noqa: E402
from typing import Callable, Dict, List, Optional, Tuple      # noqa: E402

from ju import (accounts, bringup, cli_report, config, constants, doctor, feed, fsutil, gate,  # noqa: E402
                houselog, identity, keychain, ledger_db, ledger_query, ledger_scan, pricing as pricing_mod,
                timeutil, usage_client)
from ju.config import ConfigError, ConfigStore          # noqa: E402
from ju.contracts import BringUpRefused, JusageError, Meter, Reading  # noqa: E402
from ju.live_store import LiveStore, StoreIO, log_meters  # noqa: E402

USAGE_URL = constants.USAGE_URL          # read at call time, so tests point it at a FakeUsageServer
LEGACY_LOGIN_WARNING = "login read from the legacy Keychain item, which names no account"
DASHBOARD_NAME = "dashboard.html"


class UsageError(Exception):
    """A command line that does not parse: exit 1, one line."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(f"{self.prog}: {message}")


# ---------------------------------------------------------------- wiring (one place)

def version() -> str:
    return (constants.REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def live_store() -> LiveStore:
    """E13 with E3's file calls and E2's log (RULING 14)."""
    return LiveStore(StoreIO(fsutil.write_private, fsutil.move_aside_corrupt, fsutil.read_json,
                             fsutil.CorruptFile, houselog.log, usage_client.parse))


def _login_warning(login) -> Optional[str]:
    return LEGACY_LOGIN_WARNING if login.legacy else None


def _spelled_folder(env, keys) -> Optional[str]:
    """The folder this run's CLAUDE_CONFIG_DIR / CLAUDE_SECURESTORAGE_CONFIG_DIR describe (R-9)."""
    if env.get("CLAUDE_CONFIG_DIR"):
        key = accounts.folder_key(env["CLAUDE_CONFIG_DIR"])
    elif env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR"):
        key = accounts.folder_key(Path(env.get("HOME") or os.path.expanduser("~")) / ".claude")
    else:
        return None
    return key if key in keys else None


def _statusline_enabled(cfg) -> List[str]:
    """Folders whose status-line backup E15 recorded as on (config.json `statusline`)."""
    return [k for k, v in sorted(cfg.statusline.items()) if isinstance(v, dict) and v.get("enabled")]


def _module(name: str):
    """A wave-2 neighbour (E15 ju.statusline, E20 ju.share, D1 ju.dash.page), imported when used."""
    return importlib.import_module(name)


@dataclass
class Run:
    """One command's context. `prepare()` is the start order every data command shares."""
    state: Path
    env: Dict[str, str]
    args: argparse.Namespace
    now: datetime = field(default_factory=timeutil.now)
    cfg: object = None
    keys: List[str] = field(default_factory=list)
    _conn: Optional[sqlite3.Connection] = None

    def prepare(self, edit: Optional[Callable] = None) -> None:
        """Config edit (install id once, RULING 12; folders, numbers, identities, spellings; plus the
        command's own `edit(cfg)`), then a frozen snapshot and the folder keys in account order."""
        with ConfigStore.edit(self.state) as cfg:
            config.install_id(cfg)
            keys = [accounts.folder_key(p) for p in accounts.discover_folders(self.env, cfg)]
            config.remember_folders(cfg, keys)
            for k in keys:
                accounts.number_for(cfg, k)
                accounts.read_identity(k, cfg)
            spelled = _spelled_folder(self.env, keys)
            if spelled:
                config.remember_spellings(cfg, spelled, self.env)
            if edit is not None:
                edit(cfg)
        self.cfg = ConfigStore.load(self.state)
        self.keys = accounts.ordered(keys, self.cfg)

    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = ledger_db.open_db(self.state, self.keys, log=houselog.log)
            except ledger_db.DbTooNew as e:
                raise BringUpRefused(f"jusage: usage.db: {e}") from None
            except sqlite3.Error as e:
                raise BringUpRefused(f"jusage: usage.db: cannot open or migrate ({e}); the file is untouched") from None
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def pricing(self):
        return pricing_mod.load(constants.HERE.parent / "pricing.json")

    def scan(self, full: bool = False) -> ledger_scan.ScanStats:
        return ledger_scan.scan(self.conn(), self.keys, full=full, log=houselog.log)

    def deps(self) -> gate.GateDeps:
        cfg, env = self.cfg, self.env

        def read_uuid(key):
            ident = accounts.read_identity(key, cfg)
            return ident.uuid if ident else None

        return gate.GateDeps(
            store=live_store(),
            find_login=lambda key: keychain.find_login(key, cfg.spellings.get(key), env),
            read_uuid=read_uuid,
            fetch=lambda token: usage_client.fetch(token, USAGE_URL),
            parse=usage_client.parse,
            check_stable=identity.check_stable,
            observe=identity.observe,
            login_warning=_login_warning,
            open_db=lambda state: ledger_db.open_db(state, self.keys, log=houselog.log),
            read_every_s=cfg.read_every_s,
            now=timeutil.now,
            rand=random.random,
            log=houselog.log)

    def refresh(self, manual: bool) -> gate.RefreshResult:
        """RULING 28: with the status-line backup on, accepted captures land first, so the gate
        reads the endpoint only for accounts that had none within the cadence. A manual refresh
        reads the endpoint."""
        if not manual:
            self.apply_captures()
        return gate.refresh(self.state, self.keys, manual=manual, deps=self.deps())

    def apply_captures(self) -> List[str]:
        """For each folder whose backup is on: a capture newer than the stored reading that E15's
        `backup_reading(acc, cap, others, now, key=)` accepts becomes that folder's reading in
        live.json (source "statusline") and in meter_log. Done under the gate's fetch lock, so it
        is one more write through the gate's door. Rejected captures are logged and never count."""
        on = [k for k in self.keys if k in _statusline_enabled(self.cfg)]
        if not on:
            return []
        sl = _module("ju.statusline")
        store = live_store()
        accepted = []
        fd = gate.fetch_lock(self.state)
        try:
            live = store.load(self.state, self.keys)
            uuids = {k: (accounts.read_identity(k, self.cfg) or (None, None))[1] for k in self.keys}
            for key in on:
                try:
                    cap = feed.read_capture(self.state, key)
                except fsutil.CorruptFile as e:
                    houselog.log("error", "status-line capture unreadable; not used", folder=key, why=str(e))
                    continue
                if cap is None:
                    continue
                acc = live.account(key)
                at = timeutil.parse_iso(cap["captured_at"])
                if acc.fetched is not None and at <= acc.fetched:
                    continue
                others = {k: live.account(k).reset_anchor["minute_of_week"] for k in self.keys
                          if k != key and (uuids[k] is None or uuids[k] != uuids[key])
                          and live.account(k).reset_anchor["minute_of_week"] is not None}
                reading, reason = sl.backup_reading(acc, cap, others, self.now, key=key, primary=True)
                if reading is None:
                    houselog.log("info", "status-line capture rejected", folder=key, reason=reason)
                    continue
                live.put(key, feed.with_capture(acc, reading))
                accepted.append((key, reading))
            if accepted:
                store.save(self.state, live)
                for key, reading in accepted:
                    log_meters(self.conn(), key, reading)
        finally:
            os.close(fd)                      # closing the descriptor releases the flock
        return [k for k, _ in accepted]

    # ------------------------------------------------ E15 adapter (status-line capture)

    def statusline_view(self) -> feed.StatuslineFn:
        """Per account block: the freshest capture of its folders, judged by E15. While the service
        read fails, `backup_reading(acc, capture, fingerprints, now)` decides whether it stands in
        (fingerprints = the other accounts' learned reset minutes); while both are fresh,
        `disagree(service_reading, capture)` marks session and weekly. No capture: E15 is not asked."""
        state = self.state

        def view(block, others, now):
            caps = []
            for k in block.folders:
                try:
                    c = feed.read_capture(state, k)
                except fsutil.CorruptFile as e:
                    houselog.log("error", "status-line capture unreadable; not used", folder=k, why=str(e))
                    continue
                if c is not None:
                    caps.append((timeutil.parse_iso(c["captured_at"]), c))
            if not caps:
                return feed.NO_CAPTURE
            at, capture = max(caps, key=lambda x: x[0])
            sl = _module("ju.statusline")
            acc = block.state
            if acc.error is not None:
                reading, reason = sl.backup_reading(acc, capture, others, now)
                if reading is None:
                    houselog.log("info", "status-line capture not used", folder=block.key, reason=reason)
                return feed.StatuslineView(reading, reason, frozenset(), at)
            fresh = (acc.data is not None and acc.fetched is not None
                     and (now - acc.fetched).total_seconds() <= constants.STALE_AFTER_S
                     and (now - at).total_seconds() <= constants.STALE_AFTER_S)
            if not fresh:
                return feed.StatuslineView(None, None, frozenset(), at)
            service = Reading(folder=block.key, fetched=acc.fetched, meters=list(usage_client.parse(acc.data).meters),
                              flags=[], uuid=acc.identity_uuid, source="service")
            try:
                cap_reading = sl.capture_to_reading(capture, acc.identity_uuid)
            except JusageError as e:
                houselog.log("error", "status-line capture damaged; not compared", folder=block.key, why=str(e))
                return feed.StatuslineView(None, None, frozenset(), at)
            differ = frozenset(feed.STATUSLINE_METERS) if sl.disagree(service, cap_reading) else frozenset()
            return feed.StatuslineView(None, None, differ, at)

        return view

    # ------------------------------------------------ E20 adapter (sharing)

    def own_windows(self, uuid: Optional[str]) -> List[dict]:
        """This Mac's 5-hour windows of the last 8 days for the folders logged into `uuid`, priced, in the
        report's block shape E20's meter_split reads: [{"start": ISO, "end": ISO, "usd"}]."""
        folders = [k for k in self.keys if (accounts.read_identity(k, self.cfg) or (None, None))[1] == uuid]
        since = (self.now.astimezone().date() - timedelta(days=8)).isoformat()
        p = self.pricing()
        out = []
        for k in folders:
            for b in ledger_query.blocks(ledger_query.rows(self.conn(), since, k)):
                out.append({"start": timeutil.iso(b.start), "end": timeutil.iso(b.end),
                            "usd": ledger_query.sum_rows(b.rows, p).cost})
        return sorted(out, key=lambda w: timeutil.parse_iso(w["start"]))

    def report_parts(self, refresh: gate.RefreshResult) -> Tuple[Dict[str, dict], Dict[str, dict]]:
        """(live, session_share) for the report, both keyed by account uuid (RULING 21): live is each
        account's feed §1.7 block, built by the same E21 path as the feed; an account with no known
        uuid cannot be matched by anyone and is left out. No account at all -> ({}, {}) (RULING 50)."""
        blocks = feed.account_blocks(cfg=self.cfg, refresh=refresh, conn=self.conn(), now=self.now,
                                     views=[feed.NO_CAPTURE] * len(refresh.accounts),
                                     split_view=lambda m, u, n: feed.NO_SPLIT)
        live = {b["uuid"]: b for b in blocks if b["uuid"]}
        return live, {u: b["session_share"] for u, b in live.items() if b["session_share"]}

    def share(self, refresh: gate.RefreshResult, *, write: Optional[bool], changed: bool = False):
        """Write this Mac's report (write=True always, None = when E20's rule says, False never) and
        read everyone's. Returns (reports, notes, written_at, rewritten). Raises ConfigError when no
        shared folder is set or it is missing (only --reports creates one, R-119)."""
        sh = _module("ju.share")
        rd = config.reports_dir(self.cfg, None, create=False)
        mine = rd / sh.report_id(self.cfg)[2]
        last = datetime.fromtimestamp(mine.stat().st_mtime).astimezone() if mine.exists() else None
        due = write if write is not None else (last is None or sh.should_rewrite(last, changed, self.now))
        if due:
            live, shares = self.report_parts(refresh)
            report = sh.build_report(self.conn(), self.cfg, live, self.pricing(),
                                     projects=self.cfg.share_projects, session_share=shares, now=self.now)
            sh.write_report(rd, report, self.cfg)
            last = datetime.fromtimestamp(mine.stat().st_mtime).astimezone() if mine.exists() else self.now
        reports, notes = sh.load_reports(rd, self.cfg.install_id)
        return reports, list(notes), last, bool(due)

    def people(self, reports) -> List[dict]:
        sh = _module("ju.share")
        return [p.to_feed() for p in sh.people(reports, self.now) if p.install_id != self.cfg.install_id]

    def split_view(self, reports) -> feed.SplitFn:
        sh = _module("ju.share")
        windows: Dict[Optional[str], List[dict]] = {}

        def view(meter: Meter, uuid: Optional[str], now: datetime) -> feed.SplitView:
            if uuid not in windows:
                windows[uuid] = self.own_windows(uuid)
            split = sh.meter_split(meter, uuid, windows[uuid], reports, now,
                                   me=(self.cfg.user, self.cfg.install_id))
            if split is None:
                return feed.NO_SPLIT
            return feed.SplitView(split.to_feed(), list(split.missing))

        return view

    # ------------------------------------------------ D1 adapter (dashboard page)

    def dashboard_path(self) -> Path:
        return self.state / DASHBOARD_NAME

    def write_dashboard(self, out: Optional[Path] = None) -> Tuple[Path, int]:
        """The page from every report in the shared folder, this Mac's own included (the page shows
        everyone; the feed alone takes this Mac's numbers from the ledger). Returns (path, reports).
        No report at all -> ConfigError, nothing written."""
        sh, page = _module("ju.share"), _module("ju.dash.page")
        rd = config.reports_dir(self.cfg, None, create=False)
        reports, notes = sh.load_reports(rd, None)
        if not reports:
            raise ConfigError(f"no reports in {rd}; run `jusage share` first")
        path = Path(out) if out is not None else self.dashboard_path()
        page.write(path, page.render(reports, list(notes), version(), self.now, feed.rules(self.cfg)))
        return path, len(reports)


# ---------------------------------------------------------------- commands

def cmd_scan(run: Run) -> int:
    run.prepare()
    s = run.scan(full=run.args.full)
    extra = "".join([f", {s.gone} gone" if s.gone else "", f", {s.unreadable} unreadable (see engine.log)" if s.unreadable else ""])
    print(f"scanned {s.files} file{'s' if s.files != 1 else ''}: {s.new} new response{'s' if s.new != 1 else ''}, "
          f"{s.total} in the ledger{extra}")
    return 0


def _account_feed(run: Run, refresh: gate.RefreshResult, reports=None) -> dict:
    view = run.statusline_view()
    splits = run.split_view(reports) if reports is not None else (lambda m, u, n: feed.NO_SPLIT)
    blocks = feed.account_blocks(cfg=run.cfg, refresh=refresh, conn=run.conn(), now=run.now,
                                 views=feed.views(refresh, view, run.now), split_view=splits)
    return {"accounts": blocks}


def cmd_live(run: Run) -> int:
    run.prepare()
    refresh = run.refresh(manual=run.args.force)
    reports = None
    if run.cfg.reports_dir:
        reports = run.share(refresh, write=False)[0]
    cli_report.print_live(_account_feed(run, refresh, reports), run.now)
    if run.args.raw:
        print(json.dumps({k: a.data for k, a in sorted(refresh.live.accounts.items())}, indent=1))
    return 0


def cmd_menu_json(run: Run) -> int:
    run.prepare()
    stats = run.scan()
    refresh = run.refresh(manual=run.args.force)
    reports, people, notes = None, [], []
    share = {"written_at": None, "error": None}
    dashboard = None
    if run.cfg.reports_dir:
        try:
            reports, notes, written, rewritten = run.share(refresh, write=None,
                                                           changed=bool(refresh.fetched) or stats.new > 0)
            share["written_at"] = feed._iso(written)
            people = run.people(reports)
            if rewritten:
                run.write_dashboard()
        except (JusageError, OSError) as e:
            houselog.log("error", "menu-json: shared report not updated", error=str(e), trace=traceback.format_exc())
            share["error"] = str(e)
            reports = None
        if run.dashboard_path().exists():
            dashboard = str(run.dashboard_path())
    out = feed.build_feed(cfg=run.cfg, refresh=refresh, conn=run.conn(), pricing=run.pricing(), now=run.now,
                          version=version(), statusline_view=run.statusline_view(),
                          split_view=run.split_view(reports) if reports is not None else (lambda m, u, n: feed.NO_SPLIT),
                          people=people, notes=notes, share=share, dashboard=dashboard)
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


def _resolve_account(run: Run, name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    key = accounts.folder_key(name)
    if key in run.keys:
        return key
    matches = [k for k in run.keys if accounts.short_name(k, run.keys) == name]
    if len(matches) == 1:
        return matches[0]
    raise ConfigError(f"no account folder named {name!r}; known: {', '.join(accounts.short_name(k, run.keys) for k in run.keys)}")


def cmd_report(run: Run) -> int:
    run.prepare()
    a = run.args
    since = ledger_query.since_date(a.since)
    account = _resolve_account(run, a.account)
    rows = ledger_query.rows(run.conn(), since, account)
    if not rows:
        print("no records; run `jusage scan` first")
        return 0
    if a.by == "blocks":
        cli_report.print_blocks(rows, run.pricing(), last=a.last, now=run.now)
    else:
        cli_report.print_table(rows, a.by, run.pricing(),
                               title=f"Claude Code usage by {a.by} (since {rows[0]['day']}, local time)")
    return 0


def cmd_set_user(run: Run) -> int:
    name = run.args.name.strip()
    if not name:
        raise ConfigError("set-user needs a name")

    def edit(cfg):
        cfg.user = name
    run.prepare(edit)
    print(f"your name in shared reports: {name}")
    return 0


def _sharing_edit(args) -> Callable:
    """--user, --projects/--no-projects and --reports (which creates the folder, R-119) are
    remembered in the same config edit, before anything is built."""
    def edit(cfg):
        if getattr(args, "user", None):
            cfg.user = args.user.strip()
        if getattr(args, "projects", None) is not None:
            cfg.share_projects = bool(args.projects)
        if getattr(args, "reports", None):
            config.reports_dir(cfg, args.reports, create=True)
    return edit


def _share_and_report(run: Run, refresh) -> None:
    _, notes, _, _ = run.share(refresh, write=True)
    print(f"wrote {config.reports_dir(run.cfg, None, create=False) / _module('ju.share').report_id(run.cfg)[2]}"
          + ("" if run.cfg.share_projects else "  (project names not shared; `--projects` includes them)"))
    for n in notes:
        print(n)


def cmd_share(run: Run) -> int:
    run.prepare(_sharing_edit(run.args))
    run.scan()
    _share_and_report(run, run.refresh(manual=False))
    return 0


def _open(path: Path) -> None:
    r = subprocess.run(["/usr/bin/open", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not open {path}: {(r.stderr or '').strip() or 'open failed'}")


def _dashboard(run: Run) -> int:
    path, n = run.write_dashboard(Path(os.path.expanduser(run.args.out)) if run.args.out else None)
    print(f"wrote {path}  ({n} report{'s' if n != 1 else ''})")
    if run.args.open:
        _open(path)
    return 0


def cmd_dashboard(run: Run) -> int:
    run.prepare(_sharing_edit(run.args))
    return _dashboard(run)


def cmd_update(run: Run) -> int:
    run.prepare(_sharing_edit(run.args))
    s = run.scan()
    print(f"scanned: {s.new} new, {s.total} in the ledger")
    refresh = run.refresh(manual=False)
    cli_report.print_live(_account_feed(run, refresh), run.now)
    _share_and_report(run, refresh)
    return _dashboard(run)


def cmd_doctor(run: Run) -> int:
    for line in doctor.run(run.state, run.env, store=live_store()):
        print(line)
    return 0


def cmd_statusline(run: Run) -> int:
    a = run.args
    run.prepare()
    if a.action == "status":
        for k in run.keys:
            on = k in _statusline_enabled(run.cfg)
            try:
                cap = feed.read_capture(run.state, k)
            except fsutil.CorruptFile:
                cap_text = "capture unreadable"
            else:
                cap_text = (f"last capture {timeutil.iso(timeutil.parse_iso(cap['captured_at']))}"
                            if cap else "no capture yet")
            print(f"{k}  {'on' if on else 'off'}  {cap_text}")
        return 0
    if bool(a.folder) == bool(a.all):
        raise UsageError(f"statusline {a.action} needs --folder <config folder> or --all")
    if a.all:
        targets = list(run.keys) if a.action == "on" else _statusline_enabled(run.cfg)
    else:
        key = accounts.folder_key(a.folder)
        if key not in run.keys:
            raise ConfigError(f"{a.folder} is not a Claude Code config folder on this Mac")
        targets = [key]
    sl = _module("ju.statusline")
    act = sl.turn_on if a.action == "on" else sl.turn_off
    failed = []
    for k in targets:
        try:
            act(run.state, k)
        except sl.StatuslineRefused as e:
            failed.append(f"statusline {a.action}: {k}: {e}")
            continue
        print(f"statusline {a.action}: {k}")
    if failed:
        for line in failed:
            houselog.log("error", line)
            print(f"jusage: {line}", file=sys.stderr)
        return 1
    return 0


def cmd_config(run: Run) -> int:
    a = run.args
    if a.action == "get":
        cfg = ConfigStore.load(run.state)
        if a.key is None:
            print(json.dumps(cfg.raw, indent=1, sort_keys=True, ensure_ascii=False))
            return 0
        value = cfg.get(a.key)
        print("" if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        return 0
    if a.action == "read-every":
        try:
            seconds = int(a.value)
        except ValueError:
            raise ConfigError(f"read-every needs whole seconds from {constants.READ_EVERY_MIN} "
                              f"to {constants.READ_EVERY_MAX}, not {a.value!r}") from None
        with ConfigStore.edit(run.state) as cfg:
            config.set_read_every(cfg, seconds)
        print(f"read every {seconds} s")
        return 0
    path = a.value
    if not os.path.isabs(path) or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise ConfigError(f"python must be the absolute path of an executable, not {path!r}")
    with ConfigStore.edit(run.state) as cfg:
        cfg.python = path
    print(f"python: {path}")
    return 0


COMMANDS = {"scan": cmd_scan, "live": cmd_live, "menu-json": cmd_menu_json, "report": cmd_report,
            "set-user": cmd_set_user, "share": cmd_share, "dashboard": cmd_dashboard, "update": cmd_update,
            "doctor": cmd_doctor, "statusline": cmd_statusline, "config": cmd_config}


def parser() -> argparse.ArgumentParser:
    p = _Parser(prog="jusage", description="Claude Code usage: the engine behind the jusage app.")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    p.add_argument("--state-dir", help="state folder (default ~/.claude-usage; tests and the dev build)")
    sub = p.add_subparsers(dest="cmd", parser_class=_Parser)
    s = sub.add_parser("scan", help="read new transcripts into the ledger")
    s.add_argument("--full", action="store_true", help="re-read every file")
    lv = sub.add_parser("live", help="Claude's own meters for every account on this Mac")
    lv.add_argument("--raw", action="store_true", help="also print the stored replies")
    lv.add_argument("--force", action="store_true", help="read now (a manual refresh: Retry-After still wins)")
    mj = sub.add_parser("menu-json", help="the app's feed (schema 2) on stdout")
    mj.add_argument("--force", action="store_true", help="a manual refresh")
    r = sub.add_parser("report", help="usage tables")
    r.add_argument("--by", default="day", choices=["day", "model", "account", "project", "blocks"])
    r.add_argument("--since", default="7d", help="Nd | YYYY-MM-DD | all (default 7d)")
    r.add_argument("--account", help="one config folder (path or name, e.g. .claude-newaccount)")
    r.add_argument("--last", type=int, default=12, help="how many 5-hour windows (with --by blocks)")
    su = sub.add_parser("set-user", help="your name in shared reports (remembered)")
    su.add_argument("name")
    for name in ("share", "dashboard", "update"):
        x = sub.add_parser(name, help={"share": "write this Mac's report into the shared folder",
                                       "dashboard": "build the page from every shared report",
                                       "update": "scan, read meters, share and rebuild the page"}[name])
        x.add_argument("--reports", help="shared reports folder (created, then remembered)")
        if name != "dashboard":
            x.add_argument("--user", help="your name in shared reports (remembered)")
            x.add_argument("--projects", dest="projects", action="store_true", default=None,
                           help="include project folder names (remembered)")
            x.add_argument("--no-projects", dest="projects", action="store_false", help="stop sharing project names")
        if name != "share":
            x.add_argument("--out", help=f"page path (default ~/.claude-usage/{DASHBOARD_NAME})")
            x.add_argument("--open", action="store_true", help="open the page after building")
    sub.add_parser("doctor", help="per account: Keychain items, identity, gate, status line")
    sl = sub.add_parser("statusline", help="Claude Code's status line as a backup reading")
    sl.add_argument("action", choices=["on", "off", "status"])
    sl.add_argument("--folder", help="one config folder")
    sl.add_argument("--all", action="store_true", help="every folder (off: every folder that is on)")
    c = sub.add_parser("config", help="engine settings")
    csub = c.add_subparsers(dest="action", parser_class=_Parser)
    g = csub.add_parser("get", help="print config.json, or one key")
    g.add_argument("key", nargs="?")
    re_ = csub.add_parser("read-every", help=f"seconds between reads ({constants.READ_EVERY_MIN}-{constants.READ_EVERY_MAX})")
    re_.add_argument("value")
    py = csub.add_parser("python", help="record the Python the app and the jusage command run")
    py.add_argument("value")
    return p


def _line(e: BaseException) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return text or f"{type(e).__name__} (details in {houselog.log_path()})"


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        args = parser().parse_args(argv)
        if args.version:
            print(version())
            return 0
        if args.cmd is None:
            raise UsageError("jusage: give a command (scan, live, menu-json, report, share, dashboard, update, "
                             "doctor, statusline, config, set-user) or --help")
        if args.cmd == "config" and args.action is None:
            raise UsageError("jusage config: give get, read-every or python")
    except UsageError as e:
        print(_line(e), file=sys.stderr)
        return 1
    state = constants.state_dir(args.state_dir)
    houselog.configure(state)
    run = Run(state=state, env=dict(os.environ), args=args)
    try:
        fsutil.ensure_state_dir(state)               # RULING 11: tighten to 0700 before bring-up checks it
        bringup.check(state)
        return COMMANDS[args.cmd](run)
    except BringUpRefused as e:
        houselog.log("error", "bring-up refused", command=args.cmd, line=e.line)
        print(e.line, file=sys.stderr)
        return 2
    except UsageError as e:
        print(_line(e), file=sys.stderr)
        return 1
    except Exception as e:                            # the one boundary: log the trace, one line, exit 1
        houselog.log("error", f"{args.cmd} failed", error=_line(e), trace=traceback.format_exc())
        print(f"jusage: {_line(e)}", file=sys.stderr)
        return 1
    finally:
        run.close()


if __name__ == "__main__":
    sys.exit(main())
